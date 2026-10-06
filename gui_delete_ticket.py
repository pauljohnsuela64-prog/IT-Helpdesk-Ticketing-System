"""Confirmation and feedback for deleting one ticket through the repository."""
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import messagebox, ttk

from gui_permissions import PERMISSION_DENIED, SessionPermissions, require_permission
from ticket_repository import TicketDeleteError, TicketReadError, delete_ticket, get_ticket


def deletion_summary(ticket):
    """Show identifying information without letting control characters hide labels."""
    lines = []
    for label, field in (('Ticket ID', 'ticket_id'), ('Employee Name', 'employee_name'),
                         ('Subject', 'subject'), ('Status', 'status'), ('Assigned Technician', 'assigned_to')):
        value = ticket.get(field)
        if value is None or value == '':
            value = 'Unassigned' if field == 'assigned_to' else '-'
        value = ''.join(character if character.isprintable() else repr(character)[1:-1]
                        for character in str(value))
        lines.append(f'{label}: {value}')
    return '\n'.join(lines)


class DeleteTicketDialog:
    """Opening previews the selected ID; only Permanently Delete confirms a write."""

    def __init__(self, parent, ticket_id, on_deleted, permissions=None):
        self.parent = parent
        self.permissions = permissions if permissions is not None else SessionPermissions()
        self.ticket_id = ticket_id
        self.on_deleted = on_deleted
        self._results = Queue()
        self._loading = True
        self._deleting = False
        self._closed = False
        self._poll_id = None
        self._ticket = None

        self.window = tk.Toplevel(parent)
        self.window.title('Confirm Ticket Deletion')
        self.window.geometry('680x520')
        self.window.minsize(620, 460)
        self.window.resizable(True, True)
        self.window.transient(parent)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
        self.window.protocol('WM_DELETE_WINDOW', self.cancel)
        self.window.bind('<Escape>', lambda event: self.cancel())
        self.summary = tk.StringVar(master=self.window, value=f'Ticket ID: {ticket_id}')
        self.feedback = tk.StringVar(master=self.window, value='Loading current ticket information...')

        content = ttk.Frame(self.window, padding=24, style='Helpdesk.TFrame')
        content.grid(row=0, column=0, sticky='nsew')
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)
        ttk.Label(content, text=f'Delete Ticket #{ticket_id}', style='Helpdesk.Section.TLabel').grid(
            row=0, column=0, sticky='w', pady=(0, 16),
        )
        ttk.Label(content, textvariable=self.summary, wraplength=580, justify='left',
                  style='Helpdesk.Status.TLabel').grid(row=1, column=0, sticky='nw', pady=(0, 16))
        ttk.Label(content, text='This deletion is permanent and cannot be undone.\n'
                               'The ticket and its activity history and notes will be removed.',
                  wraplength=580, justify='left', foreground='#9c2b2b',
                  font=('Segoe UI', 11, 'bold'), style='Helpdesk.Status.TLabel').grid(
            row=2, column=0, sticky='ew', pady=(0, 16),
        )
        ttk.Label(content, textvariable=self.feedback, wraplength=580,
                  style='Helpdesk.Status.TLabel').grid(row=3, column=0, sticky='ew', pady=(0, 16))
        buttons = ttk.Frame(content, style='Helpdesk.TFrame')
        buttons.grid(row=4, column=0, sticky='ew')
        buttons.columnconfigure(0, weight=1)
        self.cancel_button = ttk.Button(buttons, text='Cancel', command=self.cancel, style='Helpdesk.TButton')
        self.cancel_button.grid(row=0, column=1, padx=(0, 8))
        self.delete_button = ttk.Button(buttons, text='Permanently Delete', command=self.confirm_delete,
                                        style='Helpdesk.TButton')
        self.delete_button.grid(row=0, column=2)
        self.delete_button.state(['disabled'])
        self.window.grab_set()
        self.cancel_button.focus_set()
        Thread(target=self._load_ticket, daemon=True).start()
        self._poll_id = self.window.after(100, self._check_load)

    @property
    def is_open(self):
        return not self._closed

    @property
    def is_saving(self):
        # Match the existing dialogs so the main window waits for pending writes.
        return self._deleting

    def _load_ticket(self):
        """Load fresh details; the worker never accesses Tkinter widgets."""
        if not self.permissions.allows('delete_ticket'):
            self._results.put((None, PERMISSION_DENIED))
            return
        try:
            ticket = get_ticket(self.ticket_id)
        except (TicketReadError, ValueError) as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Unable to load ticket details. Cancel and try again.'))
        else:
            self._results.put((ticket, None))

    def _check_load(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            ticket, error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.window.after(100, self._check_load)
            return
        self._loading = False
        if error is not None:
            self.feedback.set(error)
            return
        if ticket is None:
            self._show_missing_ticket()
            return
        self._ticket = ticket
        self.summary.set(deletion_summary(ticket))
        self.feedback.set('Choose Cancel to keep this ticket, or Permanently Delete to confirm.')
        self.delete_button.state(['!disabled'] if self.permissions.allows('delete_ticket') else ['disabled'])
        self.cancel_button.focus_set()

    def confirm_delete(self):
        # This button is the explicit confirmation. Enter is not bound to deletion.
        if self._closed or self._loading or self._deleting or self._ticket is None:
            return
        if not require_permission(self.permissions, 'delete_ticket', self.window):
            return
        self._deleting = True
        self.delete_button.state(['disabled'])
        self.cancel_button.state(['disabled'])
        self.feedback.set('Deleting ticket...')
        Thread(target=self._delete_ticket, daemon=True).start()
        self._poll_id = self.window.after(100, self._check_delete)

    def _delete_ticket(self):
        """Delete only the immutable selected ID; existing cascades handle notes."""
        if not self.permissions.allows('delete_ticket'):
            self._results.put((None, PERMISSION_DENIED))
            return
        try:
            deleted = delete_ticket(self.ticket_id)
        except (TicketDeleteError, ValueError) as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Ticket deletion could not be confirmed. '
                                    'Use Refresh to check the ticket before retrying.'))
        else:
            self._results.put((deleted, None))

    def _check_delete(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            deleted, error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.window.after(100, self._check_delete)
            return
        self._deleting = False
        if error is not None:
            self.feedback.set(error)
            self.delete_button.state(['!disabled'] if self.permissions.allows('delete_ticket') else ['disabled'])
            self.cancel_button.state(['!disabled'])
            self.cancel_button.focus_set()
            return
        if not deleted:
            self._show_missing_ticket()
            return
        self._close()
        self.on_deleted(self.ticket_id)
        messagebox.showinfo('Ticket Deleted', f'Ticket #{self.ticket_id} deleted successfully.', parent=self.parent)

    def _show_missing_ticket(self):
        self._close()
        self.on_deleted(self.ticket_id)
        messagebox.showinfo('Ticket Not Found', 'No ticket found with that ID. It may already have been deleted.',
                            parent=self.parent)

    def focus(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_set()

    def cancel(self):
        if not self._deleting:
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
