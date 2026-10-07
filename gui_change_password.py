"""Masked self-service password form for one authenticated GUI session."""
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import messagebox, ttk

from gui_styles import BODY_FONT, BUTTON_GAP, WINDOW_PADDING

from gui_permissions import PERMISSION_DENIED, SessionPermissions, require_permission
from user_repository import UserPasswordChangeError, change_password, validate_password_change, validate_user_id


class ChangePasswordDialog:
    def __init__(self, parent, user_id, permissions=None):
        validate_user_id(user_id)
        self._user_id = user_id
        self.permissions = permissions if permissions is not None else SessionPermissions()
        self._results = Queue()
        self._saving = False
        self._closed = False
        self._poll_id = None
        self.window = tk.Toplevel(parent)
        self.window.title('Change Password')
        self.window.geometry('620x360')
        self.window.minsize(560, 340)
        self.window.resizable(True, True)
        self.window.transient(parent)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
        self.window.protocol('WM_DELETE_WINDOW', self.cancel)
        self.window.bind('<Escape>', lambda event: self.cancel())
        content = ttk.Frame(self.window, padding=WINDOW_PADDING, style='Helpdesk.TFrame')
        content.grid(row=0, column=0, sticky='nsew')
        content.columnconfigure(1, weight=1)
        content.rowconfigure(4, weight=1)
        ttk.Label(content, text='Change Password', style='Helpdesk.Section.TLabel').grid(
            row=0, column=0, columnspan=2, sticky='w', pady=(0, 18))
        self.current_password = tk.StringVar(master=self.window, value='')
        self.new_password = tk.StringVar(master=self.window, value='')
        self.confirmation = tk.StringVar(master=self.window, value='')
        self.entries = []
        for row, (label, variable) in enumerate((
                ('Current Password', self.current_password),
                ('New Password', self.new_password),
                ('Confirm New Password', self.confirmation)), start=1):
            ttk.Label(content, text=label, style='Helpdesk.Status.TLabel').grid(
                row=row, column=0, sticky='w', padx=(0, 16), pady=(0, 12))
            entry = ttk.Entry(content, textvariable=variable, show='*', font=BODY_FONT)
            entry.grid(row=row, column=1, sticky='ew', pady=(0, 12))
            entry.bind('<Return>', self.save)
            self.entries.append(entry)
        self.feedback = tk.StringVar(master=self.window, value='')
        ttk.Label(content, textvariable=self.feedback, wraplength=540, style='Helpdesk.Status.TLabel').grid(
            row=4, column=0, columnspan=2, sticky='ew', pady=(4, 12))
        buttons = ttk.Frame(content, style='Helpdesk.TFrame')
        buttons.grid(row=5, column=0, columnspan=2, sticky='ew')
        buttons.columnconfigure(0, weight=1)
        self.cancel_button = ttk.Button(buttons, text='Cancel', command=self.cancel, style='Helpdesk.TButton')
        self.cancel_button.grid(row=0, column=1, padx=(0, BUTTON_GAP))
        self.save_button = ttk.Button(buttons, text='Save', command=self.save, style='Helpdesk.TButton')
        self.save_button.grid(row=0, column=2)
        self.window.grab_set()
        self.entries[0].focus_set()

    @property
    def is_open(self):
        return not self._closed

    @property
    def is_saving(self):
        return self._saving

    def _clear_passwords(self):
        for variable in (self.current_password, self.new_password, self.confirmation):
            variable.set('')

    def _set_busy(self, busy):
        self._saving = busy
        for entry in self.entries:
            entry.configure(state='disabled' if busy else 'normal')
        for button in (self.save_button, self.cancel_button):
            button.state(['disabled'] if busy else ['!disabled'])

    def save(self, event=None):
        if self._closed or self._saving:
            return 'break'
        if not require_permission(self.permissions, 'change_password', self.window):
            self._clear_passwords()
            return 'break'
        current, new, confirmation = self.current_password.get(), self.new_password.get(), self.confirmation.get()
        try:
            validate_password_change(current, new, confirmation)
        except ValueError as error:
            self.feedback.set(str(error))
            return 'break'
        self._clear_passwords()
        self._set_busy(True)
        self.feedback.set('Changing password...')
        Thread(target=self._change_password, args=(current, new, confirmation), daemon=True).start()
        self._poll_id = self.window.after(100, self._check_save)
        return 'break'

    def _change_password(self, current, new, confirmation):
        if not self.permissions.allows('change_password'):
            self._results.put(PERMISSION_DENIED)
            return
        try:
            change_password(self._user_id, current, new, confirmation)
        except (UserPasswordChangeError, ValueError) as error:
            self._results.put(str(error))
        except Exception:
            self._results.put('Unable to change password. Please try again.')
        else:
            self._results.put(None)

    def _check_save(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.window.after(100, self._check_save)
            return
        self._set_busy(False)
        if error is not None:
            self.feedback.set(error)
            self.entries[0].focus_set()
            return
        messagebox.showinfo('Password Changed', 'Password changed successfully.', parent=self.window)
        self.cancel()

    def focus(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_set()

    def cancel(self):
        if self._closed or self._saving:
            return
        self._closed = True
        self._clear_passwords()
        if self._poll_id is not None:
            self.window.after_cancel(self._poll_id)
            self._poll_id = None
        self.window.grab_release()
        self.window.destroy()
