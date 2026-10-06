"""Admin-only public account viewing and status changes through repositories."""
from datetime import datetime
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import messagebox, ttk

from gui_permissions import PERMISSION_DENIED, SessionPermissions, require_permission
from user_repository import (
    USER_STATUSES, UserManagementPermissionError, UserReadError, UserUpdateError,
    get_user, get_users, public_user, update_user_status, validate_user_id,
)


USER_COLUMNS = (
    ('user_id', 'User ID', 90, 80),
    ('username', 'Username', 170, 130),
    ('full_name', 'Full Name', 240, 180),
    ('role', 'Role', 120, 100),
    ('status', 'Status', 110, 100),
    ('created_at', 'Created At', 180, 160),
)


def _readable(value):
    if value is None or value == '':
        return '-'
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M')
    return ''.join(character if character.isprintable() else repr(character)[1:-1]
                   for character in str(value))


def user_row_values(user):
    return tuple(_readable(user.get(field)) for field, _, _, _ in USER_COLUMNS)


class UserManagementWindow:
    def __init__(self, parent, user, permissions=None):
        self.parent = parent
        self.user = public_user(user)
        self.permissions = permissions if permissions is not None else SessionPermissions()
        self._results = Queue()
        self._loading = False
        self._closed = False
        self._poll_id = None
        self._refresh_pending = False
        self._child_dialog = None
        self.window = tk.Toplevel(parent)
        self.window.title('User Management')
        self.window.geometry('1060x610')
        self.window.minsize(900, 470)
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
        return self._child_dialog is not None and self._child_dialog.is_open and self._child_dialog.is_saving

    def _build_widgets(self):
        content = ttk.Frame(self.window, padding=24, style='Helpdesk.TFrame')
        content.grid(row=0, column=0, sticky='nsew')
        content.columnconfigure(0, weight=1)
        content.rowconfigure(2, weight=1)
        ttk.Label(content, text='Application User Management', style='Helpdesk.Section.TLabel').grid(
            row=0, column=0, sticky='w', pady=(0, 16))
        controls = ttk.Frame(content, style='Helpdesk.TFrame')
        controls.grid(row=1, column=0, sticky='ew', pady=(0, 16))
        controls.columnconfigure(1, weight=1)
        self.change_button = ttk.Button(controls, text='Change User Status', command=self.open_status,
                                        style='Helpdesk.TButton')
        self.change_button.grid(row=0, column=0, padx=(0, 10))
        self.change_button.state(['disabled'])
        self.refresh_button = ttk.Button(controls, text='Refresh', command=self.refresh, style='Helpdesk.TButton')
        self.refresh_button.grid(row=0, column=2, padx=(0, 10))
        self.close_button = ttk.Button(controls, text='Close', command=self.close, style='Helpdesk.TButton')
        self.close_button.grid(row=0, column=3)
        table = ttk.Frame(content)
        table.grid(row=2, column=0, sticky='nsew')
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=tuple(column[0] for column in USER_COLUMNS), show='headings',
                                 selectmode='browse', style='Helpdesk.Treeview')
        for field, heading, width, minimum in USER_COLUMNS:
            self.tree.heading(field, text=heading)
            self.tree.column(field, width=width, minwidth=minimum, stretch=field in ('username', 'full_name'), anchor='w')
        self.tree.tag_configure('alternate', background='#f0f4fa')
        self.tree.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.feedback = tk.StringVar(master=self.window, value='Ready.')
        ttk.Label(content, textvariable=self.feedback, wraplength=950, style='Helpdesk.Status.TLabel').grid(
            row=3, column=0, sticky='ew', pady=(12, 0))

    def refresh(self):
        if self._closed or self._loading:
            return
        if not require_permission(self.permissions, 'manage_users', self.window):
            return
        self._loading = True
        self.feedback.set('Loading application users...')
        self.refresh_button.state(['disabled'])
        self.change_button.state(['disabled'])
        Thread(target=self._load_users, daemon=True).start()
        self._poll_id = self.window.after(100, self._check_refresh)

    def _load_users(self):
        if not self.permissions.allows('manage_users'):
            self._results.put((None, PERMISSION_DENIED))
            return
        try:
            users = get_users(self.user['user_id'])
        except (UserReadError, UserManagementPermissionError, ValueError) as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Unable to load application users. Please try Refresh again.'))
        else:
            self._results.put(([public_user(user) for user in users], None))

    def _check_refresh(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            users, error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.window.after(100, self._check_refresh)
            return
        self._loading = False
        if self._refresh_pending:
            self._refresh_pending = False
            self.refresh()
            return
        allowed = self.permissions.allows('manage_users')
        self.refresh_button.state(['!disabled'] if allowed else ['disabled'])
        self.change_button.state(['!disabled'] if allowed and error is None else ['disabled'])
        if error is not None:
            self.feedback.set(error)
            return
        self._display_users(users)

    def _display_users(self, users):
        selection = self.tree.selection()
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        for index, user in enumerate(users):
            self.tree.insert('', 'end', iid=str(user['user_id']), values=user_row_values(user),
                             tags=('alternate',) if index % 2 else ())
        if selection and self.tree.exists(selection[0]):
            self.tree.selection_set(selection[0])
            self.tree.focus(selection[0])
        count = len(users)
        self.feedback.set(f'{count} user{"s" if count != 1 else ""} loaded.' if count else 'No application users found.')

    def _request_refresh(self):
        if self._closed:
            return
        if self._loading:
            self._refresh_pending = True
        else:
            self.refresh()

    def _focus_child(self):
        if self._child_dialog is not None and self._child_dialog.is_open:
            self._child_dialog.focus()
            return True
        return False

    def open_status(self):
        if self._closed or not require_permission(self.permissions, 'manage_users', self.window):
            return
        if self._focus_child():
            return
        selection = self.tree.selection()
        if len(selection) != 1:
            messagebox.showinfo('Select a User', 'Please select one user row before changing status.', parent=self.window)
            return
        try:
            user_id = int(selection[0])
            validate_user_id(user_id)
        except (TypeError, ValueError):
            messagebox.showinfo('Select a User', 'Please Refresh and select a valid user row.', parent=self.window)
            return
        self._child_dialog = ChangeUserStatusDialog(self, user_id)

    def focus(self):
        if not self._focus_child():
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


class ChangeUserStatusDialog:
    def __init__(self, manager, user_id):
        self.manager = manager
        self.parent = manager.window
        self.user_id = user_id
        self._results = Queue()
        self._loading = True
        self._saving = False
        self._closed = False
        self._poll_id = None
        self._current = None
        self.window = tk.Toplevel(self.parent)
        self.window.title('Change User Status')
        self.window.geometry('620x490')
        self.window.minsize(560, 440)
        self.window.resizable(True, True)
        self.window.transient(self.parent)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
        self.window.protocol('WM_DELETE_WINDOW', self.cancel)
        self.window.bind('<Escape>', lambda event: self.cancel())
        content = ttk.Frame(self.window, padding=24, style='Helpdesk.TFrame')
        content.grid(row=0, column=0, sticky='nsew')
        content.columnconfigure(1, weight=1)
        content.rowconfigure(1, weight=1)
        ttk.Label(content, text='Change User Status', style='Helpdesk.Section.TLabel').grid(
            row=0, column=0, columnspan=2, sticky='w', pady=(0, 16))
        self.summary = tk.StringVar(master=self.window, value=f'User ID: {user_id}')
        ttk.Label(content, textvariable=self.summary, wraplength=530, style='Helpdesk.Status.TLabel').grid(
            row=1, column=0, columnspan=2, sticky='nw', pady=(0, 16))
        ttk.Label(content, text='Status', style='Helpdesk.Status.TLabel').grid(
            row=2, column=0, sticky='w', padx=(0, 16))
        self.status = tk.StringVar(master=self.window, value='')
        self.status_combo = ttk.Combobox(content, textvariable=self.status, values=USER_STATUSES,
                                         state='disabled', font=('Segoe UI', 10))
        self.status_combo.grid(row=2, column=1, sticky='ew')
        self.feedback = tk.StringVar(master=self.window, value='Loading current user information...')
        ttk.Label(content, textvariable=self.feedback, wraplength=530, style='Helpdesk.Status.TLabel').grid(
            row=3, column=0, columnspan=2, sticky='ew', pady=(16, 16))
        buttons = ttk.Frame(content, style='Helpdesk.TFrame')
        buttons.grid(row=4, column=0, columnspan=2, sticky='ew')
        buttons.columnconfigure(0, weight=1)
        self.cancel_button = ttk.Button(buttons, text='Cancel', command=self.cancel, style='Helpdesk.TButton')
        self.cancel_button.grid(row=0, column=1, padx=(0, 8))
        self.save_button = ttk.Button(buttons, text='Save Changes', command=self.save, style='Helpdesk.TButton')
        self.save_button.grid(row=0, column=2)
        self.save_button.state(['disabled'])
        self.window.grab_set()
        self.cancel_button.focus_set()
        Thread(target=self._load_user, daemon=True).start()
        self._poll_id = self.window.after(100, self._check_load)

    @property
    def is_open(self):
        return not self._closed

    @property
    def is_saving(self):
        return self._saving

    def _load_user(self):
        if not self.manager.permissions.allows('manage_users'):
            self._results.put((None, PERMISSION_DENIED))
            return
        try:
            user = get_user(self.user_id, self.manager.user['user_id'])
        except (UserReadError, UserManagementPermissionError, ValueError) as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Unable to load user details. Cancel and try again.'))
        else:
            self._results.put((public_user(user) if user is not None else None, None))

    def _check_load(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            user, error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.window.after(100, self._check_load)
            return
        self._loading = False
        if error is not None:
            self.feedback.set(error)
            return
        if user is None:
            self._close()
            self.manager._request_refresh()
            messagebox.showinfo('User Not Found', 'No user found with that ID.', parent=self.parent)
            return
        self._current = public_user(user)
        self.summary.set('\n'.join(f'{label}: {_readable(user.get(field))}' for label, field in
                                   (('User ID', 'user_id'), ('Username', 'username'), ('Full Name', 'full_name'),
                                    ('Role', 'role'), ('Current Status', 'status'))))
        self.status.set(user['status'])
        self.status_combo.configure(state='readonly')
        self.save_button.state(['!disabled'] if self.manager.permissions.allows('manage_users') else ['disabled'])
        self.feedback.set('Choose Active or Inactive. Self-deactivation and disabling the last Active Admin are blocked.')

    def save(self):
        if self._closed or self._loading or self._saving or self._current is None:
            return
        if not require_permission(self.manager.permissions, 'manage_users', self.window):
            return
        status = self.status.get()
        if status not in USER_STATUSES:
            self.feedback.set('Choose Active or Inactive.')
            return
        if status == self._current['status']:
            self.feedback.set('No status change made.')
            return
        confirmation = (f'Change status for {_readable(self._current["username"])} (User ID: {self.user_id})?\n\n'
                        f'{self._current["status"]} -> {status}\n\nInactive accounts cannot sign in.')
        if not messagebox.askyesno('Confirm User Status Change', confirmation, parent=self.window, default=messagebox.NO):
            self.feedback.set('Status change cancelled. No changes saved.')
            return
        self._saving = True
        self.status_combo.configure(state='disabled')
        self.save_button.state(['disabled'])
        self.cancel_button.state(['disabled'])
        self.feedback.set('Saving user status...')
        Thread(target=self._change_status, args=(status,), daemon=True).start()
        self._poll_id = self.window.after(100, self._check_save)

    def _change_status(self, status):
        if not self.manager.permissions.allows('manage_users'):
            self._results.put((None, PERMISSION_DENIED))
            return
        try:
            changed = update_user_status(self.user_id, status, self.manager.user['user_id'])
        except (UserUpdateError, UserManagementPermissionError, ValueError) as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'User status change could not be confirmed. Use Refresh before retrying.'))
        else:
            self._results.put((changed, None))

    def _check_save(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            changed, error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.window.after(100, self._check_save)
            return
        self._saving = False
        self.status_combo.configure(state='readonly')
        self.save_button.state(['!disabled'] if self.manager.permissions.allows('manage_users') else ['disabled'])
        self.cancel_button.state(['!disabled'])
        if error is not None:
            self.feedback.set(error)
            return
        self._close()
        self.manager._request_refresh()
        messagebox.showinfo('User Status Changed' if changed else 'No Changes',
                            f'User #{self.user_id} status changed successfully.' if changed else
                            'The account already has the selected status. No changes were needed.', parent=self.parent)

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
