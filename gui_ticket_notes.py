"""Tkinter ticket notes using shared validation and existing safe repositories."""
from datetime import datetime
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import messagebox, ttk

from input_validation import validate_comment_text
from gui_permissions import PERMISSION_DENIED, SessionPermissions, require_permission
from technician_repository import TechnicianReadError, get_active_technicians
from ticket_comment_repository import (
    TicketCommentCreateError, TicketCommentDeleteError, TicketCommentReadError,
    add_ticket_comment_for_user, delete_ticket_comment, get_ticket_comments, validate_comment_id,
)
from ticket_repository import TicketReadError, get_ticket
from user_repository import INVALID_TECHNICIAN_LINK, UserReadError, get_linked_active_technician, public_user


NOTE_COLUMNS = (
    ('comment_id', 'Comment ID', 100, 90),
    ('created_at', 'Date/Time', 180, 170),
    ('technician_name', 'Technician', 190, 160),
    ('comment_text', 'Comment', 600, 360),
)


def _readable(value, multiline=False):
    if value is None or value == '':
        return '-'
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    return ''.join(character if character.isprintable() or (multiline and character in '\n\t')
                   else repr(character)[1:-1] for character in str(value))


def note_row_values(note):
    values = {**note, 'technician_name': note.get('technician_name') or 'Unknown technician'}
    return tuple(_readable(values.get(field)) for field, _, _, _ in NOTE_COLUMNS)


def _ticket_summary(ticket_id, ticket):
    return (f'Ticket ID: {ticket_id}\nEmployee: {_readable(ticket.get("employee_name"))}\n'
            f'Subject: {_readable(ticket.get("subject"))}')


class TicketNotesWindow:
    """Modeless notes for one ticket, with modal add/delete forms."""

    def __init__(self, parent, ticket_id, focus_other_dialog=None, permissions=None, user=None):
        self.parent = parent
        self.user = public_user(user) if user is not None else None
        self.permissions = permissions if permissions is not None else SessionPermissions()
        self.ticket_id = ticket_id
        self._focus_other_dialog = focus_other_dialog
        self._results = Queue()
        self._loading = False
        self._closed = False
        self._poll_id = None
        self._refresh_pending = False
        self._child_dialog = None
        self._ticket = None
        self._notes = {}
        self.window = tk.Toplevel(parent)
        self.window.title(f'Ticket Notes #{ticket_id}')
        self.window.geometry('1120x720')
        self.window.minsize(900, 580)
        self.window.resizable(True, True)
        self.window.transient(parent)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.bind('<Escape>', lambda event: self.close())
        self.summary = tk.StringVar(master=self.window, value=f'Ticket ID: {ticket_id}')
        self.feedback = tk.StringVar(master=self.window, value='Ready.')
        self._build_widgets()
        self.refresh()

    @property
    def is_open(self):
        return not self._closed

    @property
    def has_open_dialog(self):
        return self._child_dialog is not None and self._child_dialog.is_open

    @property
    def is_saving(self):
        return self.has_open_dialog and self._child_dialog.is_saving

    def _build_widgets(self):
        content = ttk.Frame(self.window, padding=24, style='Helpdesk.TFrame')
        content.grid(row=0, column=0, sticky='nsew')
        content.columnconfigure(0, weight=1)
        content.rowconfigure(2, weight=3)
        content.rowconfigure(3, weight=1)
        ttk.Label(content, text=f'Ticket Notes — Ticket #{self.ticket_id}', style='Helpdesk.Section.TLabel').grid(
            row=0, column=0, sticky='w', pady=(0, 12),
        )
        header = ttk.Frame(content, style='Helpdesk.TFrame')
        header.grid(row=1, column=0, sticky='ew', pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, textvariable=self.summary, wraplength=790, justify='left',
                  style='Helpdesk.Status.TLabel').grid(row=0, column=0, sticky='w')
        controls = ttk.Frame(header, style='Helpdesk.TFrame')
        controls.grid(row=1, column=0, sticky='ew', pady=(12, 0))
        controls.columnconfigure(2, weight=1)
        self.add_button = ttk.Button(controls, text='Add Note', command=self.open_add, style='Helpdesk.TButton')
        self.add_button.grid(row=0, column=0, padx=(0, 10))
        self.delete_button = ttk.Button(controls, text='Delete Note', command=self.open_delete, style='Helpdesk.TButton')
        self.delete_button.grid(row=0, column=1, padx=(0, 10))
        self.refresh_button = ttk.Button(controls, text='Refresh', command=self.refresh, style='Helpdesk.TButton')
        self.refresh_button.grid(row=0, column=3, padx=(0, 10))
        self.close_button = ttk.Button(controls, text='Close', command=self.close, style='Helpdesk.TButton')
        self.close_button.grid(row=0, column=4)
        self._enable_actions(False)
        table = ttk.Frame(content)
        table.grid(row=2, column=0, sticky='nsew')
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=tuple(column[0] for column in NOTE_COLUMNS),
                                 show='headings', selectmode='browse', style='Helpdesk.Treeview')
        for field, heading, width, minimum in NOTE_COLUMNS:
            self.tree.heading(field, text=heading)
            self.tree.column(field, width=width, minwidth=minimum, stretch=field == 'comment_text', anchor='w')
        self.tree.tag_configure('alternate', background='#f0f4fa')
        self.tree.grid(row=0, column=0, sticky='nsew')
        self.tree.bind('<<TreeviewSelect>>', self._show_selected_note)
        vertical = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        area = ttk.Frame(content, style='Helpdesk.TFrame')
        area.grid(row=3, column=0, sticky='nsew', pady=(12, 0))
        area.columnconfigure(0, weight=1)
        area.rowconfigure(1, weight=1)
        ttk.Label(area, text='Selected Note', style='Helpdesk.Status.TLabel').grid(
            row=0, column=0, sticky='w', pady=(0, 6),
        )
        self.details = tk.Text(area, height=6, width=60, wrap='word', font=('Segoe UI', 10), padx=8, pady=8)
        self.details.grid(row=1, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(area, orient='vertical', command=self.details.yview)
        scrollbar.grid(row=1, column=1, sticky='ns')
        self.details.configure(yscrollcommand=scrollbar.set)
        self._set_details('Select a note to read its full text.')
        ttk.Label(content, textvariable=self.feedback, wraplength=790, style='Helpdesk.Status.TLabel').grid(
            row=4, column=0, sticky='ew', pady=(12, 0),
        )

    def _enable_actions(self, enabled):
        for button, action in ((self.add_button, 'add_note'), (self.delete_button, 'delete_note')):
            button.state(['!disabled'] if enabled and self.permissions.allows(action) else ['disabled'])

    def refresh(self):
        if self._closed or self._loading:
            return
        self._loading = True
        self.feedback.set('Loading ticket notes...')
        self.refresh_button.state(['disabled'])
        Thread(target=self._load_notes, daemon=True).start()
        self._poll_id = self.window.after(100, self._check_refresh)

    def _load_notes(self):
        try:
            ticket = get_ticket(self.ticket_id)
            notes = get_ticket_comments(self.ticket_id) if ticket is not None else []
        except (TicketReadError, TicketCommentReadError, ValueError) as error:
            self._results.put((None, None, str(error)))
        except Exception:
            self._results.put((None, None, 'Please try Refresh again.'))
        else:
            self._results.put((ticket, notes, None))

    def _check_refresh(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            ticket, notes, error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.window.after(100, self._check_refresh)
            return
        self._loading = False
        if self._refresh_pending:
            self._refresh_pending = False
            self.refresh()
            return
        self.refresh_button.state(['!disabled'])
        if error is not None:
            self.feedback.set(f'Unable to load ticket notes. {error}')
            return
        self._ticket = ticket
        self._enable_actions(ticket is not None)
        if ticket is None:
            self.summary.set(f'Ticket ID: {self.ticket_id}\nTicket not found.')
            self._display_notes([])
            self.feedback.set('This ticket no longer exists. Close this window or try Refresh again.')
            return
        self.summary.set(_ticket_summary(self.ticket_id, ticket))
        self._display_notes(notes)

    def _display_notes(self, notes):
        selection = self.tree.selection()
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self._notes = {str(note['comment_id']): note for note in notes}
        for index, note in enumerate(notes):
            self.tree.insert('', 'end', iid=str(note['comment_id']), values=note_row_values(note),
                             tags=('alternate',) if index % 2 else ())
        if selection and selection[0] in self._notes:
            self.tree.selection_set(selection[0])
            self.tree.focus(selection[0])
            self._show_selected_note()
        else:
            self._set_details('Select a note to read its full text.' if notes else 'No notes found for this ticket.')
        count = len(notes)
        self.feedback.set(f'{count} note{"s" if count != 1 else ""} loaded.' if count else 'No notes found for this ticket.')

    def _show_selected_note(self, event=None):
        if self._closed:
            return
        selection = self.tree.selection()
        note = self._notes.get(selection[0]) if selection else None
        if note is not None:
            self._set_details(f'Comment ID: {note["comment_id"]} | {_readable(note.get("created_at"))}\n'
                              f'Technician: {_readable(note.get("technician_name") or "Unknown technician")}\n\n'
                              f'{_readable(note.get("comment_text"), multiline=True)}')

    def _set_details(self, text):
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', text)
        self.details.configure(state='disabled')

    def _request_refresh(self):
        if self._closed:
            return
        if self._loading:
            self._refresh_pending = True
        else:
            self.refresh()

    def _refresh_after_deletion(self, comment_id):
        if self._closed:
            return
        row_id = str(comment_id)
        self._notes.pop(row_id, None)
        if self.tree.exists(row_id):
            self.tree.delete(row_id)
        self._set_details('Select a note to read its full text.')
        self._request_refresh()

    def _focus_child(self):
        if self.has_open_dialog:
            self._child_dialog.focus()
            return True
        return False

    def _can_open_dialog(self):
        if self._closed or self._focus_child():
            return False
        if self._focus_other_dialog is not None and self._focus_other_dialog():
            return False
        if self._ticket is None:
            self.feedback.set('Please Refresh to load an existing ticket first.')
            return False
        return True

    def open_add(self):
        if self._closed or not require_permission(self.permissions, 'add_note', self.window):
            return
        if self._can_open_dialog():
            self._child_dialog = AddNoteDialog(self)

    def open_delete(self):
        if self._closed or not require_permission(self.permissions, 'delete_note', self.window):
            return
        if not self._can_open_dialog():
            return
        selection = self.tree.selection()
        if len(selection) != 1:
            messagebox.showinfo('Select a Note', 'Please select one note before clicking Delete Note.', parent=self.window)
            return
        try:
            comment_id = int(selection[0])
            validate_comment_id(comment_id)
        except (TypeError, ValueError):
            messagebox.showinfo('Select a Note', 'Please Refresh and select a valid note.', parent=self.window)
            return
        self._child_dialog = DeleteNoteDialog(self, comment_id)

    def focus(self):
        if self._focus_child():
            return
        self.window.deiconify()
        self.window.lift()
        self.window.focus_set()

    def cancel(self):
        self.close()

    def close(self):
        if self._closed:
            return
        if self.is_saving:
            self._child_dialog.focus()
            return
        if self.has_open_dialog:
            self._child_dialog.cancel()
        self._closed = True
        if self._poll_id is not None:
            self.window.after_cancel(self._poll_id)
            self._poll_id = None
        self.window.destroy()


class _NoteDialog:
    """Shared modal loading, save-result, and closing behavior for note actions."""

    def __init__(self, owner, title, button_text):
        self.owner = owner
        self.parent = owner.window
        self.ticket_id = owner.ticket_id
        self._results = Queue()
        self._loading = True
        self._saving = False
        self._closed = False
        self._ready = False
        self._poll_id = None
        self._widgets = []
        self.window = tk.Toplevel(self.parent)
        self.window.title(title)
        self.window.geometry('680x620')
        self.window.minsize(600, 520)
        self.window.resizable(True, True)
        self.window.transient(self.parent)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
        self.window.protocol('WM_DELETE_WINDOW', self.cancel)
        self.window.bind('<Escape>', lambda event: self.cancel())
        content = ttk.Frame(self.window, padding=24, style='Helpdesk.TFrame')
        content.grid(row=0, column=0, sticky='nsew')
        content.columnconfigure(0, weight=1)
        content.rowconfigure(2, weight=1)
        ttk.Label(content, text=title, style='Helpdesk.Section.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 12))
        self.summary = tk.StringVar(master=self.window, value=f'Ticket ID: {self.ticket_id}')
        ttk.Label(content, textvariable=self.summary, wraplength=540, justify='left',
                  style='Helpdesk.Status.TLabel').grid(row=1, column=0, sticky='ew', pady=(0, 12))
        self.form = ttk.Frame(content, style='Helpdesk.TFrame')
        self.form.grid(row=2, column=0, sticky='nsew')
        self.form.columnconfigure(0, weight=1)
        self.feedback = tk.StringVar(master=self.window, value='Loading...')
        ttk.Label(content, textvariable=self.feedback, wraplength=540, style='Helpdesk.Status.TLabel').grid(
            row=3, column=0, sticky='ew', pady=(12, 12),
        )
        buttons = ttk.Frame(content, style='Helpdesk.TFrame')
        buttons.grid(row=4, column=0, sticky='ew')
        buttons.columnconfigure(0, weight=1)
        self.cancel_button = ttk.Button(buttons, text='Cancel', command=self.cancel, style='Helpdesk.TButton')
        self.cancel_button.grid(row=0, column=1, padx=(0, 8))
        self.save_button = ttk.Button(buttons, text=button_text, command=self.save, style='Helpdesk.TButton')
        self.save_button.grid(row=0, column=2)
        self.save_button.state(['disabled'])
        self.window.grab_set()
        self.cancel_button.focus_set()

    @property
    def is_open(self):
        return not self._closed

    @property
    def is_saving(self):
        return self._saving

    def _build_text(self, row, readonly=False):
        self.form.rowconfigure(row, weight=1)
        area = ttk.Frame(self.form)
        area.grid(row=row, column=0, sticky='nsew')
        area.columnconfigure(0, weight=1)
        area.rowconfigure(0, weight=1)
        self.text = tk.Text(area, height=9, width=50, wrap='word', font=('Segoe UI', 10), padx=8, pady=8)
        self.text.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(area, orient='vertical', command=self.text.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.text.configure(yscrollcommand=scrollbar.set, state='disabled' if readonly else 'normal')
        self._widgets.append((self.text, 'disabled' if readonly else 'normal'))

    def _check_load(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            result, error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.window.after(100, self._check_load)
            return
        self._loading = False
        if error is not None:
            self.feedback.set(error)
            return
        if self._apply_load(result):
            self._ready = True
            self.save_button.state(['!disabled'])

    def _start_save(self, target, args, message):
        self._set_saving(True)
        self.feedback.set(message)
        Thread(target=target, args=args, daemon=True).start()
        self._poll_id = self.window.after(100, self._check_save)

    def _set_saving(self, saving):
        self._saving = saving
        for widget, normal_state in self._widgets:
            widget.configure(state='disabled' if saving else normal_state)
        for button in (self.save_button, self.cancel_button):
            button.state(['disabled'] if saving else ['!disabled'])

    def _check_save(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            result, error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.window.after(100, self._check_save)
            return
        self._set_saving(False)
        if error is not None:
            self.feedback.set(error)
            return
        self._close()
        self._after_save(result)
        title, message = self._success_message(result)
        messagebox.showinfo(title, message, parent=self.parent)

    def focus(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_set()

    def cancel(self):
        if not self._saving:
            self._close()

    def _close(self):
        if self._closed:
            return
        self._closed = True
        if self._poll_id is not None:
            self.window.after_cancel(self._poll_id)
            self._poll_id = None
        self.window.grab_release()
        self.window.destroy()
        if self.owner.is_open:
            self.owner.focus()


class AddNoteDialog(_NoteDialog):
    def __init__(self, owner):
        super().__init__(owner, f'Add Note — Ticket #{owner.ticket_id}', 'Save Note')
        self._technicians = []
        ttk.Label(self.form, text='Technician', style='Helpdesk.Status.TLabel').grid(
            row=0, column=0, sticky='w', pady=(0, 6),
        )
        self.author = None
        self.author_name = None
        if owner.user is not None and owner.user['role'] == 'Technician':
            self.author_name = tk.StringVar(master=self.window, value='Checking your technician link...')
            ttk.Label(self.form, textvariable=self.author_name, style='Helpdesk.Status.TLabel').grid(
                row=1, column=0, sticky='ew', pady=(0, 12))
        else:
            self.author = ttk.Combobox(self.form, values=(), state='disabled', font=('Segoe UI', 10))
            self.author.grid(row=1, column=0, sticky='ew', pady=(0, 12))
            self._widgets.append((self.author, 'readonly'))
        ttk.Label(self.form, text='Note', style='Helpdesk.Status.TLabel').grid(row=2, column=0, sticky='w', pady=(0, 6))
        self._build_text(3)
        Thread(target=self._load_data, daemon=True).start()
        self._poll_id = self.window.after(100, self._check_load)

    def _load_data(self):
        if not self.owner.permissions.allows('add_note'):
            self._results.put((None, PERMISSION_DENIED))
            return
        try:
            ticket = get_ticket(self.ticket_id)
            technicians = []
            if ticket is not None:
                if self.owner.user is None:
                    raise ValueError('Please log in again before adding a note.')
                if self.owner.user['role'] == 'Technician':
                    linked = get_linked_active_technician(self.owner.user['user_id'])
                    if linked is None:
                        raise ValueError(INVALID_TECHNICIAN_LINK)
                    technicians = [linked]
                else:
                    technicians = get_active_technicians()
        except (TicketReadError, TechnicianReadError, UserReadError, ValueError) as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Unable to load note author choices. Cancel and try again.'))
        else:
            self._results.put(((ticket, technicians), None))

    def _apply_load(self, result):
        ticket, self._technicians = result
        if ticket is None:
            self._close()
            self.owner._request_refresh()
            messagebox.showinfo('Ticket Not Found', 'No ticket found with that ID. No note was saved.', parent=self.parent)
            return False
        self.summary.set(_ticket_summary(self.ticket_id, ticket))
        if self.owner.user is not None and self.owner.user['role'] == 'Technician':
            if len(self._technicians) != 1:
                self.feedback.set(INVALID_TECHNICIAN_LINK)
                return False
            tech = self._technicians[0]
            self.author_name.set(f'{tech["full_name"]} (ID: {tech["technician_id"]})')
            self.feedback.set('Your linked technician will be the note author. Enter the note.')
            return True
        names = tuple(f'{tech["full_name"]} (ID: {tech["technician_id"]})' for tech in self._technicians)
        self.author.configure(values=names, state='readonly' if names else 'disabled')
        if not names:
            self.feedback.set('No active technicians are available. Cancel and add or reactivate a technician, then reopen Add Note.')
            return False
        self.feedback.set('Select an Active technician and enter the note.')
        return True

    def save(self):
        if self._closed or self._loading or self._saving or not self._ready:
            return
        if not require_permission(self.owner.permissions, 'add_note', self.window):
            return
        technician_id = None
        if self.owner.user is None:
            self.feedback.set('Please log in again before adding a note.')
            return
        if self.owner.user['role'] == 'Technician':
            if len(self._technicians) != 1:
                self.feedback.set(INVALID_TECHNICIAN_LINK)
                return
        else:
            selection = self.author.current()
            if not 0 <= selection < len(self._technicians):
                self.feedback.set('Please select one of the listed Active technicians.')
                return
            technician_id = self._technicians[selection]['technician_id']
        try:
            note = validate_comment_text(self.text.get('1.0', 'end-1c'))
        except ValueError as error:
            self.feedback.set(str(error))
            return
        self._start_save(self._add_note, (technician_id, note), 'Saving note...')

    def _add_note(self, technician_id, note):
        if not self.owner.permissions.allows('add_note'):
            self._results.put((None, PERMISSION_DENIED))
            return
        try:
            if self.owner.user is None:
                raise ValueError('Please log in again before adding a note.')
            comment_id = add_ticket_comment_for_user(self.ticket_id, self.owner.user['user_id'], note,
                                                    technician_id=technician_id)
        except (TicketCommentCreateError, ValueError) as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Note creation could not be confirmed. Cancel and Refresh notes before retrying.'))
        else:
            self._results.put((comment_id, None))

    def _after_save(self, result):
        self.owner._request_refresh()

    def _success_message(self, comment_id):
        return 'Note Added', f'Note #{comment_id} added successfully to ticket #{self.ticket_id}.'


class DeleteNoteDialog(_NoteDialog):
    def __init__(self, owner, comment_id):
        super().__init__(owner, 'Confirm Note Deletion', 'Permanently Delete')
        self.comment_id = comment_id
        self._note = None
        ttk.Label(self.form, text='This note deletion is permanent and cannot be undone.',
                  foreground='#9c2b2b', font=('Segoe UI', 11, 'bold'), wraplength=540,
                  style='Helpdesk.Status.TLabel').grid(row=0, column=0, sticky='ew', pady=(0, 12))
        self._build_text(1, readonly=True)
        Thread(target=self._load_data, daemon=True).start()
        self._poll_id = self.window.after(100, self._check_load)

    def _load_data(self):
        if not self.owner.permissions.allows('delete_note'):
            self._results.put((None, PERMISSION_DENIED))
            return
        try:
            notes = get_ticket_comments(self.ticket_id)
            note = next((note for note in notes if note['comment_id'] == self.comment_id
                         and note['ticket_id'] == self.ticket_id), None)
        except (TicketCommentReadError, ValueError) as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Unable to load the selected note. Cancel and Refresh notes.'))
        else:
            self._results.put((note, None))

    def _apply_load(self, note):
        if note is None:
            self._close()
            self.owner._refresh_after_deletion(self.comment_id)
            messagebox.showinfo('Note Not Found', 'No note found with that Comment ID for this ticket.', parent=self.parent)
            return False
        self._note = note
        self.summary.set(f'Ticket ID: {self.ticket_id}\nComment ID: {self.comment_id}\n'
                         f'Technician: {_readable(note.get("technician_name") or "Unknown technician")}\n'
                         f'Date/Time: {_readable(note.get("created_at"))}')
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', _readable(note.get('comment_text'), multiline=True))
        self.text.configure(state='disabled')
        self.feedback.set('Choose Cancel to keep the note, or Permanently Delete to confirm.')
        return True

    def save(self):
        # This button is explicit confirmation; neither loading nor Enter deletes.
        if self._closed or self._loading or self._saving or not self._ready:
            return
        if not require_permission(self.owner.permissions, 'delete_note', self.window):
            return
        self._start_save(self._delete_note, (), 'Deleting note...')

    def _delete_note(self):
        if not self.owner.permissions.allows('delete_note'):
            self._results.put((None, PERMISSION_DENIED))
            return
        try:
            deleted = delete_ticket_comment(self.ticket_id, self.comment_id)
        except (TicketCommentDeleteError, ValueError) as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Note deletion could not be confirmed. Cancel and Refresh notes before retrying.'))
        else:
            self._results.put((deleted, None))

    def _after_save(self, result):
        self.owner._refresh_after_deletion(self.comment_id)

    def _success_message(self, deleted):
        if not deleted:
            return 'Note Not Found', 'No note found with that Comment ID for this ticket. It may already have been deleted.'
        return 'Note Deleted', f'Note #{self.comment_id} deleted successfully from ticket #{self.ticket_id}.'
