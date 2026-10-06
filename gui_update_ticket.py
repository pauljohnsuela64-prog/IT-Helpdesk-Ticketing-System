"""Tkinter Update Ticket dialog using existing repositories and validation."""
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import messagebox, ttk

from input_validation import validate_text
from gui_permissions import PERMISSION_DENIED, SessionPermissions, require_permission
from technician_repository import TechnicianReadError, get_active_technicians
from ticket_access import ADMIN_ASSIGNMENT_ONLY, TicketAccessError, check_session_ticket_access
from ticket_repository import (
    CATEGORIES, PRIORITIES, STATUSES, TicketReadError, TicketUpdateError,
    get_ticket, update_ticket_for_user,
)
from user_repository import INVALID_TECHNICIAN_LINK, UserReadError, get_linked_active_technician, public_user


class UpdateTicketDialog:
    def __init__(self, parent, ticket_id, on_updated, permissions=None, user=None):
        self.parent = parent
        self.user = public_user(user) if user is not None else None
        self.permissions = permissions if permissions is not None else SessionPermissions(self.user)
        self.ticket_id = ticket_id
        self.on_updated = on_updated
        self._results = Queue()
        self._loading = True
        self._saving = False
        self._closed = False
        self._poll_id = None
        self._ticket = None
        self._technicians = []
        self._widgets = []

        self.window = tk.Toplevel(parent)
        self.window.title(f'Update Ticket #{ticket_id}')
        self.window.geometry('720x800')
        self.window.minsize(620, 740)
        self.window.resizable(True, True)
        self.window.transient(parent)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
        self.window.protocol('WM_DELETE_WINDOW', self.cancel)
        self.window.bind('<Escape>', lambda event: self.cancel())

        self.feedback = tk.StringVar(master=self.window, value='Loading ticket and active technicians...')
        self.summary = tk.StringVar(master=self.window, value=f'Ticket ID: {ticket_id}')
        self.fields = {}
        content = ttk.Frame(self.window, padding=24, style='Helpdesk.TFrame')
        content.grid(row=0, column=0, sticky='nsew')
        content.columnconfigure(0, weight=1)
        content.rowconfigure(2, weight=1)
        ttk.Label(content, text='Update Ticket', style='Helpdesk.Section.TLabel').grid(
            row=0, column=0, sticky='w', pady=(0, 12),
        )
        ttk.Label(content, textvariable=self.summary, wraplength=650,
                  style='Helpdesk.Status.TLabel').grid(row=1, column=0, sticky='ew', pady=(0, 16))
        self.form = ttk.Frame(content, style='Helpdesk.TFrame')
        self.form.grid(row=2, column=0, sticky='nsew')
        self.form.columnconfigure(1, weight=1)
        self.form.rowconfigure(4, weight=1)
        ttk.Label(content, textvariable=self.feedback, wraplength=650,
                  style='Helpdesk.Status.TLabel').grid(row=3, column=0, sticky='ew', pady=(12, 12))
        buttons = ttk.Frame(content, style='Helpdesk.TFrame')
        buttons.grid(row=4, column=0, sticky='ew')
        buttons.columnconfigure(0, weight=1)
        self.cancel_button = ttk.Button(buttons, text='Cancel', command=self.cancel,
                                        style='Helpdesk.TButton')
        self.cancel_button.grid(row=0, column=1, padx=(0, 8))
        self.save_button = ttk.Button(buttons, text='Save Changes', command=self.save,
                                      style='Helpdesk.TButton')
        self.save_button.grid(row=0, column=2)
        self.save_button.state(['disabled'])
        self.window.grab_set()
        Thread(target=self._load_ticket, daemon=True).start()
        self._poll_id = self.window.after(100, self._check_load)

    @property
    def is_open(self):
        return not self._closed

    @property
    def is_saving(self):
        return self._saving

    def _load_ticket(self):
        """Read fresh details in the worker; never read Treeview or Tk variables."""
        if not self.permissions.allows('update_ticket'):
            self._results.put((None, PERMISSION_DENIED))
            return
        try:
            ticket = get_ticket(self.ticket_id)
            if ticket is None:
                self._results.put((None, 'No ticket found with that ID. Close this dialog and refresh tickets.'))
                return
            check_session_ticket_access(ticket, self.user, 'update')
            if self.user['role'] == 'Technician':
                linked = get_linked_active_technician(self.user['user_id'])
                if linked is None or linked['technician_id'] != self.user.get('technician_id'):
                    raise TicketAccessError(INVALID_TECHNICIAN_LINK)
                technicians = []
            else:
                technicians = get_active_technicians()
        except (TicketReadError, TechnicianReadError, UserReadError, TicketAccessError, ValueError) as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Unable to load ticket details. Close this dialog and try again.'))
        else:
            self._results.put(((ticket, technicians), None))

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
        self._ticket, self._technicians = result
        self._build_fields()
        self.summary.set(
            f'Ticket ID: {self.ticket_id}\n'
            f'Created: {self._ticket["created_at"]}    Updated: {self._ticket["updated_at"]}\n'
            f'Resolved: {self._ticket["resolved_at"] or "Not resolved"}'
        )
        self.feedback.set('Only Admin users can change technician assignments.'
                          if self.user['role'] == 'Technician' else
                          'Choose an active technician, keep the current assignment, or unassign.'
                          if self._technicians else
                          'No active technicians are available. You can keep the current assignment or unassign.')
        self.save_button.state(['!disabled'])
        self.employee_entry.focus_set()

    def _build_fields(self):
        labels = (('employee_name', 'Employee Name'), ('department', 'Department'),
                  ('category', 'Category'), ('subject', 'Subject'), ('description', 'Description'),
                  ('priority', 'Priority'), ('status', 'Status'), ('assigned_to', 'Assigned Technician'))
        choices = {'category': CATEGORIES, 'priority': PRIORITIES, 'status': STATUSES}
        for row, (field, label) in enumerate(labels):
            ttk.Label(self.form, text=label, style='Helpdesk.Status.TLabel').grid(
                row=row, column=0, sticky='nw', padx=(0, 16), pady=(0, 12),
            )
            if field == 'description':
                frame = ttk.Frame(self.form)
                frame.grid(row=row, column=1, sticky='nsew', pady=(0, 12))
                frame.columnconfigure(0, weight=1)
                frame.rowconfigure(0, weight=1)
                self.description = tk.Text(frame, height=7, width=40, wrap='word',
                                           font=('Segoe UI', 10), padx=8, pady=8)
                self.description.insert('1.0', self._ticket[field])
                self.description.grid(row=0, column=0, sticky='nsew')
                scrollbar = ttk.Scrollbar(frame, orient='vertical', command=self.description.yview)
                scrollbar.grid(row=0, column=1, sticky='ns')
                self.description.configure(yscrollcommand=scrollbar.set)
                self._widgets.append((self.description, 'normal'))
                continue
            self.fields[field] = tk.StringVar(master=self.window, value=self._ticket[field] or '')
            if field == 'assigned_to' and self.user['role'] == 'Technician':
                widget = ttk.Entry(self.form, textvariable=self.fields[field], state='readonly', font=('Segoe UI', 10))
                self.assignee = widget
                normal_state = 'readonly'
            elif field == 'assigned_to':
                current = self._ticket[field] or 'Unassigned'
                names = (f'Keep current: {current}', 'Unassign technician') + tuple(
                    f'{technician["full_name"]} (ID: {technician["technician_id"]})'
                    for technician in self._technicians
                )
                widget = ttk.Combobox(self.form, textvariable=self.fields[field], values=names,
                                      state='readonly', font=('Segoe UI', 10))
                widget.current(0)
                self.assignee = widget
                normal_state = 'readonly'
            elif field in choices:
                widget = ttk.Combobox(self.form, textvariable=self.fields[field], values=choices[field],
                                      state='readonly', font=('Segoe UI', 10))
                normal_state = 'readonly'
            else:
                widget = ttk.Entry(self.form, textvariable=self.fields[field], font=('Segoe UI', 10))
                normal_state = 'normal'
            widget.grid(row=row, column=1, sticky='ew', pady=(0, 12))
            self._widgets.append((widget, normal_state))
            if field == 'employee_name':
                self.employee_entry = widget

    def _validated_changes(self):
        values = {
            'employee_name': validate_text(self.fields['employee_name'].get(), 'Employee name', 100),
            'department': validate_text(self.fields['department'].get(), 'Department', 100),
            'subject': validate_text(self.fields['subject'].get(), 'Subject', 150),
            'description': validate_text(self.description.get('1.0', 'end-1c'), 'Description'),
        }
        for field, choices in (('category', CATEGORIES), ('priority', PRIORITIES), ('status', STATUSES)):
            value = self.fields[field].get()
            if value not in choices:
                raise ValueError(f'Choose one of the listed {field} options.')
            values[field] = value
        changes = {field: value for field, value in values.items() if value != self._ticket[field]}
        if self.user['role'] == 'Technician':
            if self.fields['assigned_to'].get() != (self._ticket['assigned_to'] or ''):
                raise TicketAccessError(ADMIN_ASSIGNMENT_ONLY)
            return changes, None
        selection = self.assignee.current()
        if not 0 <= selection < len(self._technicians) + 2:
            raise ValueError('Choose a listed technician, keep current, or unassign.')
        technician_id = None
        if selection == 1 and self._ticket['assigned_to'] is not None:
            changes['assigned_to'] = None
        elif selection >= 2:
            technician_id = self._technicians[selection - 2]['technician_id']
        return changes, technician_id

    def _confirmation_text(self, changes, technician_id):
        text = f'Save changes to Ticket #{self.ticket_id}?'
        status = changes.get('status', self._ticket['status'])
        if technician_id is not None and self._ticket['status'] == 'Open' and status == 'Open':
            text += '\n\nAssigning an active technician will change Open to Assigned.'
        if status != self._ticket['status']:
            text += ('\n\nResolution time will be set when saved.' if status == 'Resolved'
                     else '\n\nResolution time will be cleared.')
        return text

    def _set_saving(self, saving):
        self._saving = saving
        for widget, normal_state in self._widgets:
            widget.configure(state='disabled' if saving else normal_state)
        for button in (self.save_button, self.cancel_button):
            button.state(['disabled'] if saving else ['!disabled'])

    def save(self):
        if self._closed or self._loading or self._saving or self._ticket is None:
            return
        if not require_permission(self.permissions, 'update_ticket', self.window):
            return
        try:
            check_session_ticket_access(self._ticket, self.user, 'update')
            changes, technician_id = self._validated_changes()
        except (ValueError, TicketAccessError) as error:
            self.feedback.set(str(error))
            return
        if not changes and technician_id is None:
            self.feedback.set('No changes made.')
            return
        if not messagebox.askyesno('Confirm Ticket Update', self._confirmation_text(changes, technician_id),
                                  parent=self.window, default=messagebox.NO):
            self.feedback.set('Update cancelled. No changes saved.')
            return
        self._set_saving(True)
        self.feedback.set('Saving ticket changes...')
        Thread(target=self._save_ticket, args=(changes, technician_id), daemon=True).start()
        self._poll_id = self.window.after(100, self._check_save)

    def _save_ticket(self, changes, technician_id):
        """Reuse the transaction that checks technicians and logs ticket history."""
        if not self.permissions.allows('update_ticket'):
            self._results.put((None, PERMISSION_DENIED))
            return
        try:
            if self.user is None:
                raise ValueError('Please log in again before updating a ticket.')
            saved = update_ticket_for_user(self.ticket_id, self.user['user_id'], changes,
                                          session_technician_id=self.user.get('technician_id'), technician_id=technician_id)
        except (TicketUpdateError, ValueError) as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Ticket update could not be confirmed. '
                                    'Use Refresh to check the ticket before retrying.'))
        else:
            self._results.put((saved, None))

    def _check_save(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            saved, error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.window.after(100, self._check_save)
            return
        self._set_saving(False)
        if error is not None:
            self.feedback.set(error)
            return
        if not saved:
            self.feedback.set('No changes made. The ticket already has these values.')
            return
        self._close()
        self.on_updated()
        messagebox.showinfo('Ticket Updated', f'Ticket #{self.ticket_id} updated successfully.', parent=self.parent)

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
