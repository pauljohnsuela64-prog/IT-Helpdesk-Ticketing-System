"""Tkinter technician viewing, creation, and status changes through repositories."""
from datetime import datetime
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import messagebox, ttk

from gui_styles import ALTERNATE_ROW, BODY_FONT, BUTTON_GAP, WINDOW_PADDING

from input_validation import validate_text
from gui_permissions import PERMISSION_DENIED, SessionPermissions, require_permission
from technician_repository import (
    TECHNICIAN_STATUSES, TechnicianCreateError, TechnicianReadError, TechnicianUpdateError,
    add_technician, get_technician, get_technicians, update_technician_status, validate_technician_id,
)


TECHNICIAN_COLUMNS = (
    ('technician_id', 'Technician ID', 105, 100),
    ('full_name', 'Full Name', 200, 160),
    ('email', 'Email', 270, 220),
    ('status', 'Status', 100, 90),
    ('created_at', 'Created At', 160, 150),
)


def technician_row_values(technician):
    values = []
    for field, _, _, _ in TECHNICIAN_COLUMNS:
        value = technician.get(field)
        if value is None or value == '':
            value = '-'
        elif field == 'created_at' and isinstance(value, datetime):
            value = value.strftime('%Y-%m-%d %H:%M')
        values.append(''.join(character if character.isprintable() else repr(character)[1:-1]
                              for character in str(value)))
    return tuple(values)


class TechnicianManagementWindow:
    def __init__(self, parent, on_change=None, permissions=None):
        self.parent = parent
        self.permissions = permissions if permissions is not None else SessionPermissions()
        self._on_change = on_change
        self._results = Queue()
        self._loading = False
        self._closed = False
        self._poll_id = None
        self._refresh_pending = False
        self._child_dialog = None
        self.window = tk.Toplevel(parent)
        self.window.title('Technician Management')
        self.window.geometry('1000x600')
        self.window.minsize(850, 450)
        self.window.resizable(True, True)
        self.window.transient(parent)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.bind('<Escape>', lambda event: self.close())
        self._build_widgets()
        self.window.grab_set()
        self.refresh()

    @property
    def is_open(self):
        return not self._closed

    @property
    def is_saving(self):
        return (self._child_dialog is not None and self._child_dialog.is_open
                and self._child_dialog.is_saving)

    def _build_widgets(self):
        content = ttk.Frame(self.window, padding=WINDOW_PADDING, style='Helpdesk.TFrame')
        content.grid(row=0, column=0, sticky='nsew')
        content.columnconfigure(0, weight=1)
        content.rowconfigure(2, weight=1)
        ttk.Label(content, text='Technician Management', style='Helpdesk.Section.TLabel').grid(
            row=0, column=0, sticky='w', pady=(0, 16),
        )
        controls = ttk.Frame(content, style='Helpdesk.TFrame')
        controls.grid(row=1, column=0, sticky='ew', pady=(0, 16))
        controls.columnconfigure(2, weight=1)
        self.add_button = ttk.Button(controls, text='Add Technician', command=self.open_add,
                                     style='Helpdesk.TButton')
        self.add_button.grid(row=0, column=0, padx=(0, BUTTON_GAP))
        self.change_button = ttk.Button(controls, text='Change Technician Status', command=self.open_status,
                                        style='Helpdesk.TButton')
        self.change_button.grid(row=0, column=1, padx=(0, BUTTON_GAP))
        self.refresh_button = ttk.Button(controls, text='Refresh', command=self.refresh, style='Helpdesk.TButton')
        self.refresh_button.grid(row=0, column=3, padx=(0, BUTTON_GAP))
        self.close_button = ttk.Button(controls, text='Close', command=self.close, style='Helpdesk.TButton')
        self.close_button.grid(row=0, column=4)
        if not self.permissions.allows('manage_technicians'):
            for button in (self.add_button, self.change_button, self.refresh_button):
                button.state(['disabled'])
        table = ttk.Frame(content)
        table.grid(row=2, column=0, sticky='nsew')
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=tuple(column[0] for column in TECHNICIAN_COLUMNS),
                                 show='headings', selectmode='browse', style='Helpdesk.Treeview')
        for field, heading, width, minimum in TECHNICIAN_COLUMNS:
            self.tree.heading(field, text=heading)
            self.tree.column(field, width=width, minwidth=minimum, stretch=field in ('full_name', 'email'), anchor='w')
        self.tree.tag_configure('alternate', background=ALTERNATE_ROW)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.feedback = tk.StringVar(master=self.window, value='Ready.')
        ttk.Label(content, textvariable=self.feedback, wraplength=800, style='Helpdesk.Status.TLabel').grid(
            row=3, column=0, sticky='ew', pady=(12, 0),
        )

    def refresh(self):
        if self._closed or self._loading:
            return
        if not require_permission(self.permissions, 'manage_technicians', self.window):
            return
        self._loading = True
        self.feedback.set('Loading technicians...')
        self.refresh_button.state(['disabled'])
        Thread(target=self._load_technicians, daemon=True).start()
        self._poll_id = self.window.after(100, self._check_refresh)

    def _load_technicians(self):
        if not self.permissions.allows('manage_technicians'):
            self._results.put((None, PERMISSION_DENIED))
            return
        try:
            technicians = get_technicians()
        except TechnicianReadError as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Please try Refresh again.'))
        else:
            self._results.put((technicians, None))

    def _check_refresh(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            technicians, error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.window.after(100, self._check_refresh)
            return
        self._loading = False
        if self._refresh_pending:
            self._refresh_pending = False
            self.refresh()
            return
        self.refresh_button.state(['!disabled'] if self.permissions.allows('manage_technicians') else ['disabled'])
        if error is not None:
            self.feedback.set(f'Unable to load technicians. {error}')
        else:
            self._display_technicians(technicians)

    def _display_technicians(self, technicians):
        selection = self.tree.selection()
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        for index, technician in enumerate(technicians):
            self.tree.insert('', 'end', iid=str(technician['technician_id']), values=technician_row_values(technician),
                             tags=('alternate',) if index % 2 else ())
        if selection and self.tree.exists(selection[0]):
            self.tree.selection_set(selection[0])
            self.tree.focus(selection[0])
        count = len(technicians)
        self.feedback.set(f'{count} technician{"s" if count != 1 else ""} loaded.' if count else
                          'No technicians found. Choose Add Technician to add one.')

    def _request_refresh(self):
        if self._closed:
            return
        if self._loading:
            self._refresh_pending = True
        else:
            self.refresh()

    def _notify_change(self):
        if not self._closed and self._on_change is not None:
            self._on_change()

    def _focus_child(self):
        if self._child_dialog is not None and self._child_dialog.is_open:
            self._child_dialog.focus()
            return True
        return False

    def open_add(self):
        if self._closed or not require_permission(self.permissions, 'manage_technicians', self.window):
            return
        if not self._focus_child():
            self._child_dialog = AddTechnicianDialog(self)

    def open_status(self):
        if self._closed or not require_permission(self.permissions, 'manage_technicians', self.window):
            return
        if self._focus_child():
            return
        selection = self.tree.selection()
        if len(selection) != 1:
            messagebox.showinfo('Select a Technician', 'Please select one technician row before changing status.',
                                parent=self.window)
            return
        try:
            technician_id = int(selection[0])
            validate_technician_id(technician_id)
        except (TypeError, ValueError):
            messagebox.showinfo('Select a Technician', 'Please Refresh and select a valid technician row.',
                                parent=self.window)
            return
        self._child_dialog = ChangeTechnicianStatusDialog(self, technician_id)

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
        if self._child_dialog is not None and self._child_dialog.is_open:
            self._child_dialog.cancel()
        self._closed = True
        if self._poll_id is not None:
            self.window.after_cancel(self._poll_id)
            self._poll_id = None
        self.window.grab_release()
        self.window.destroy()


class _TechnicianDialog:
    """Shared modal, worker-result, and closing behavior for the two small forms."""

    def __init__(self, manager, title, geometry):
        self.manager = manager
        self.parent = manager.window
        self._results = Queue()
        self._loading = False
        self._saving = False
        self._closed = False
        self._poll_id = None
        self._widgets = []
        self.window = tk.Toplevel(self.parent)
        self.window.title(title)
        self.window.geometry(geometry)
        self.window.resizable(True, True)
        self.window.transient(self.parent)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
        self.window.protocol('WM_DELETE_WINDOW', self.cancel)
        self.window.bind('<Escape>', lambda event: self.cancel())
        content = ttk.Frame(self.window, padding=WINDOW_PADDING, style='Helpdesk.TFrame')
        content.grid(row=0, column=0, sticky='nsew')
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)
        ttk.Label(content, text=title, style='Helpdesk.Section.TLabel').grid(
            row=0, column=0, sticky='w', pady=(0, 16),
        )
        self.form = ttk.Frame(content, style='Helpdesk.TFrame')
        self.form.grid(row=1, column=0, sticky='nsew')
        self.form.columnconfigure(1, weight=1)
        self.feedback = tk.StringVar(master=self.window, value='')
        ttk.Label(content, textvariable=self.feedback, wraplength=480, style='Helpdesk.Status.TLabel').grid(
            row=2, column=0, sticky='ew', pady=(12, 16),
        )
        buttons = ttk.Frame(content, style='Helpdesk.TFrame')
        buttons.grid(row=3, column=0, sticky='ew')
        buttons.columnconfigure(0, weight=1)
        self.cancel_button = ttk.Button(buttons, text='Cancel', command=self.cancel, style='Helpdesk.TButton')
        self.cancel_button.grid(row=0, column=1, padx=(0, 8))
        self.save_button = ttk.Button(buttons, text='Save Changes', command=self.save, style='Helpdesk.TButton')
        self.save_button.grid(row=0, column=2)
        self.window.grab_set()

    @property
    def is_open(self):
        return not self._closed

    @property
    def is_saving(self):
        return self._saving

    def _set_saving(self, saving):
        self._saving = saving
        for widget, normal_state in self._widgets:
            widget.configure(state='disabled' if saving else normal_state)
        for button in (self.save_button, self.cancel_button):
            button.state(['disabled'] if saving else ['!disabled'])

    def _start_save(self, target, args):
        self._set_saving(True)
        self.feedback.set('Saving technician...')
        Thread(target=target, args=args, daemon=True).start()
        self._poll_id = self.window.after(100, self._check_save)

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
        self.manager._request_refresh()
        self.manager._notify_change()
        title, text = self._success_message(result)
        messagebox.showinfo(title, text, parent=self.parent)

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
        if self.manager.is_open:
            self.manager.window.grab_set()
            self.manager.focus()


class AddTechnicianDialog(_TechnicianDialog):
    def __init__(self, manager):
        super().__init__(manager, 'Add Technician', '550x320')
        self.window.minsize(500, 300)
        self.fields = {}
        self.save_button.configure(text='Save')
        for row, (field, label) in enumerate((('full_name', 'Full Name'), ('email', 'Email'))):
            ttk.Label(self.form, text=label, style='Helpdesk.Status.TLabel').grid(
                row=row, column=0, sticky='w', padx=(0, 16), pady=(0, 12),
            )
            self.fields[field] = tk.StringVar(master=self.window, value='')
            entry = ttk.Entry(self.form, textvariable=self.fields[field], font=BODY_FONT)
            entry.grid(row=row, column=1, sticky='ew', pady=(0, 12))
            self._widgets.append((entry, 'normal'))
        self._widgets[0][0].focus_set()

    def save(self):
        if self._closed or self._saving:
            return
        if not require_permission(self.manager.permissions, 'manage_technicians', self.window):
            return
        try:
            full_name = validate_text(self.fields['full_name'].get(), 'Full name', 100)
            email = validate_text(self.fields['email'].get(), 'Email', 150)
        except ValueError as error:
            self.feedback.set(str(error))
            return
        self._start_save(self._add_technician, (full_name, email))

    def _add_technician(self, full_name, email):
        if not self.manager.permissions.allows('manage_technicians'):
            self._results.put((None, PERMISSION_DENIED))
            return
        try:
            technician_id = add_technician(full_name, email)
        except (TechnicianCreateError, ValueError) as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Technician creation could not be confirmed. '
                                    'Use Refresh to check technicians before retrying.'))
        else:
            self._results.put((technician_id, None))

    def _success_message(self, technician_id):
        return 'Technician Added', f'Technician created successfully.\nNew Technician ID: {technician_id}'


class ChangeTechnicianStatusDialog(_TechnicianDialog):
    def __init__(self, manager, technician_id):
        super().__init__(manager, 'Change Technician Status', '580x440')
        self.window.minsize(530, 400)
        self.technician_id = technician_id
        self._current = None
        self._loading = True
        self.summary = tk.StringVar(master=self.window, value=f'Technician ID: {technician_id}')
        ttk.Label(self.form, textvariable=self.summary, wraplength=480, style='Helpdesk.Status.TLabel').grid(
            row=0, column=0, columnspan=2, sticky='ew', pady=(0, 16),
        )
        ttk.Label(self.form, text='Status', style='Helpdesk.Status.TLabel').grid(
            row=1, column=0, sticky='w', padx=(0, 16),
        )
        self.status = tk.StringVar(master=self.window, value='')
        self.status_combo = ttk.Combobox(self.form, textvariable=self.status, values=TECHNICIAN_STATUSES,
                                         state='disabled', font=BODY_FONT)
        self.status_combo.grid(row=1, column=1, sticky='ew')
        self._widgets.append((self.status_combo, 'readonly'))
        self.save_button.state(['disabled'])
        self.feedback.set('Loading current technician information...')
        self.cancel_button.focus_set()
        Thread(target=self._load_technician, daemon=True).start()
        self._poll_id = self.window.after(100, self._check_load)

    def _load_technician(self):
        if not self.manager.permissions.allows('manage_technicians'):
            self._results.put((None, PERMISSION_DENIED))
            return
        try:
            technician = get_technician(self.technician_id)
        except (TechnicianReadError, ValueError) as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Unable to load technician details. Cancel and try again.'))
        else:
            self._results.put((technician, None))

    def _check_load(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            technician, error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.window.after(100, self._check_load)
            return
        self._loading = False
        if error is not None:
            self.feedback.set(error)
            return
        if technician is None:
            self._close()
            self.manager._request_refresh()
            messagebox.showinfo('Technician Not Found', 'No technician found with that ID.', parent=self.parent)
            return
        self._current = technician
        self.summary.set(f'Technician ID: {self.technician_id}\nFull Name: {technician["full_name"]}\n'
                         f'Email: {technician["email"]}\nCurrent Status: {technician["status"]}')
        self.status.set(technician['status'])
        self.status_combo.configure(state='readonly')
        self.save_button.state(['!disabled'])
        self.feedback.set('Choose Active or Inactive. Existing ticket assignments will be kept.')

    def save(self):
        if self._closed or self._loading or self._saving or self._current is None:
            return
        if not require_permission(self.manager.permissions, 'manage_technicians', self.window):
            return
        status = self.status.get()
        if status not in TECHNICIAN_STATUSES:
            self.feedback.set('Choose Active or Inactive.')
            return
        if status == self._current['status']:
            self.feedback.set('No status change made.')
            return
        confirmation = (f'Change status for {self._current["full_name"]} (ID: {self.technician_id})?\n\n'
                        f'{self._current["status"]} -> {status}\n\n'
                        'Inactive technicians remain on existing tickets and are unavailable for new assignments.')
        if not messagebox.askyesno('Confirm Technician Status Change', confirmation,
                                  parent=self.window, default=messagebox.NO):
            self.feedback.set('Status change cancelled. No changes saved.')
            return
        self._target_status = status
        self._start_save(self._change_status, (status,))

    def _change_status(self, status):
        if not self.manager.permissions.allows('manage_technicians'):
            self._results.put((None, PERMISSION_DENIED))
            return
        try:
            changed = update_technician_status(self.technician_id, status)
        except (TechnicianUpdateError, ValueError) as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Technician status change could not be confirmed. '
                                    'Use Refresh to check technicians before retrying.'))
        else:
            self._results.put((changed, None))

    def _success_message(self, changed):
        if not changed:
            return 'No Changes', f'Technician #{self.technician_id} already has status {self._target_status}.'
        return 'Technician Status Changed', f'Technician #{self.technician_id} changed to {self._target_status} successfully.'
