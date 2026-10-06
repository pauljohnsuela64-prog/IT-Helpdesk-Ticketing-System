"""Guarded account creation from Login; repositories own authentication and SQL."""
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import messagebox, ttk

from input_validation import validate_text
from password_security import validate_password
from user_repository import (
    USER_ROLES, UserAuthenticationError, UserCreateError, UserReadError,
    authorize_account_creation, create_account, get_user_count, validate_user_details,
)


AUTHORIZATION_FAILED = 'Unable to authorize account creation. Please check the Admin credentials.'


class CreateAccountDialog:
    """One modal flow: check account count, authorize if needed, then create."""

    def __init__(self, parent):
        self.parent = parent
        self._results = Queue()
        self._closed = False
        self._busy = False
        self._saving = False
        self._poll_id = None
        self._authorization = None
        self._first_account = False
        self._stage = 'checking'
        self._fields = {}
        self._widgets = []
        self.window = tk.Toplevel(parent)
        self.window.title('Create Account — IT Help Desk')
        self.window.geometry('620x650')
        self.window.minsize(560, 610)
        self.window.resizable(True, True)
        self.window.transient(parent)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
        self.window.protocol('WM_DELETE_WINDOW', self.cancel)
        self.window.bind('<Escape>', lambda event: self.cancel())
        content = ttk.Frame(self.window, padding=28, style='Login.TFrame')
        content.grid(row=0, column=0, sticky='nsew')
        content.columnconfigure(0, weight=1)
        content.rowconfigure(2, weight=1)
        ttk.Label(content, text='Create Account', style='Login.Title.TLabel').grid(
            row=0, column=0, sticky='w', pady=(0, 12))
        self.explanation = tk.StringVar(master=self.window, value='Checking application accounts...')
        ttk.Label(content, textvariable=self.explanation, wraplength=530, style='Login.TLabel').grid(
            row=1, column=0, sticky='ew', pady=(0, 18))
        self.form = ttk.Frame(content, style='Login.TFrame')
        self.form.grid(row=2, column=0, sticky='nsew')
        self.form.columnconfigure(1, weight=1)
        self.feedback = tk.StringVar(master=self.window, value='')
        ttk.Label(content, textvariable=self.feedback, wraplength=530, style='Login.TLabel').grid(
            row=3, column=0, sticky='ew', pady=(12, 16))
        buttons = ttk.Frame(content, style='Login.TFrame')
        buttons.grid(row=4, column=0, sticky='ew')
        buttons.columnconfigure(0, weight=1)
        self.cancel_button = ttk.Button(buttons, text='Cancel', command=self.cancel, style='Login.TButton')
        self.cancel_button.grid(row=0, column=1, padx=(0, 8))
        self.action_button = ttk.Button(buttons, text='Create Account', command=self.save, style='Login.TButton')
        self.action_button.grid(row=0, column=2)
        self.window.grab_set()
        self.cancel_button.focus_set()
        self._check_accounts()

    @property
    def is_open(self):
        return not self._closed

    @property
    def is_saving(self):
        return self._saving

    def _clear_form(self):
        for value in self._fields.values():
            value.set('')
        self._fields = {}
        self._widgets = []
        for child in self.form.winfo_children():
            child.destroy()

    def _entry(self, row, field, label, masked=False):
        ttk.Label(self.form, text=label, style='Login.TLabel').grid(
            row=row, column=0, sticky='w', padx=(0, 16), pady=(0, 16))
        self._fields[field] = tk.StringVar(master=self.window, value='')
        entry = ttk.Entry(self.form, textvariable=self._fields[field], font=('Segoe UI', 11),
                          **({'show': '*'} if masked else {}))
        entry.grid(row=row, column=1, sticky='ew', pady=(0, 16))
        self._widgets.append((entry, 'normal'))
        return entry

    def _show_authorization(self):
        self._stage = 'authorize'
        self._clear_form()
        self.explanation.set('An existing Active Admin must authorize creation of another account.')
        username = self._entry(0, 'admin_username', 'Admin username')
        password = self._entry(1, 'admin_password', 'Admin password', masked=True)
        password.bind('<Return>', self.authorize)
        self.action_button.configure(text='Authorize', command=self.authorize)
        self.action_button.state(['!disabled'])
        username.focus_set()

    def _show_account_form(self, first_account):
        self._stage = 'create'
        self._first_account = first_account
        self._clear_form()
        self.explanation.set('The first account will automatically be an Active Admin.' if first_account else
                             'Admin authorization verified. New accounts start as Active.')
        username = self._entry(0, 'username', 'Username')
        self._entry(1, 'full_name', 'Full Name')
        ttk.Label(self.form, text='Role', style='Login.TLabel').grid(
            row=2, column=0, sticky='w', padx=(0, 16), pady=(0, 16))
        self._fields['role'] = tk.StringVar(master=self.window, value='Admin' if first_account else 'Technician')
        role = ttk.Combobox(self.form, textvariable=self._fields['role'], state='readonly',
                            values=('Admin',) if first_account else USER_ROLES, font=('Segoe UI', 11))
        role.grid(row=2, column=1, sticky='ew', pady=(0, 16))
        self._widgets.append((role, 'readonly'))
        self._entry(3, 'password', 'Password', masked=True)
        confirmation = self._entry(4, 'confirmation', 'Confirm Password', masked=True)
        confirmation.bind('<Return>', self.save)
        self.action_button.configure(text='Create Account', command=self.save)
        self.action_button.state(['!disabled'])
        username.focus_set()

    def _set_busy(self, busy, saving=False):
        self._busy = busy
        self._saving = busy and saving
        for widget, state in self._widgets:
            widget.configure(state='disabled' if busy else state)
        self.action_button.state(['disabled'] if busy else ['!disabled'])
        self.cancel_button.state(['disabled'] if self._saving else ['!disabled'])

    def _start(self, worker, args, check, message, saving=False):
        self._set_busy(True, saving=saving)
        self.feedback.set(message)
        Thread(target=worker, args=args, daemon=True).start()
        self._poll_id = self.window.after(100, check)

    def _result(self, check):
        self._poll_id = None
        if self._closed:
            return None
        try:
            result = self._results.get_nowait()
        except Empty:
            self._poll_id = self.window.after(100, check)
            return None
        self._set_busy(False)
        return result

    def _check_accounts(self):
        if not self._closed and not self._busy:
            self._start(self._load_count, (), self._check_count, 'Checking application accounts...')

    def _load_count(self):
        try:
            count = get_user_count()
        except UserReadError as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Unable to check application accounts. Please try again.'))
        else:
            self._results.put((count, None))

    def _check_count(self):
        result = self._result(self._check_count)
        if result is None:
            return
        count, error = result
        if error is not None:
            self.feedback.set(error)
            self.action_button.configure(text='Retry', command=self._check_accounts)
            return
        self.feedback.set('')
        if count == 0:
            self._show_account_form(first_account=True)
        else:
            self._show_authorization()

    def authorize(self, event=None):
        if self._closed or self._busy or self._stage != 'authorize':
            return 'break'
        username = self._fields['admin_username'].get()
        password = self._fields['admin_password'].get()
        self._fields['admin_password'].set('')
        try:
            username = validate_text(username, 'Username', 50)
            validate_password(password)
        except ValueError:
            self.feedback.set(AUTHORIZATION_FAILED)
            return 'break'
        self._start(self._authorize, (username, password), self._check_authorization, 'Checking Admin authorization...')
        return 'break'

    def _authorize(self, username, password):
        try:
            authorization = authorize_account_creation(username, password)
        except UserAuthenticationError as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Unable to verify Admin authorization. Please try again.'))
        else:
            if self._closed and authorization is not None:
                authorization.revoke()
            self._results.put((authorization, None))

    def _check_authorization(self):
        result = self._result(self._check_authorization)
        if result is None:
            return
        authorization, error = result
        if error is not None or authorization is None:
            self.feedback.set(error if error is not None else AUTHORIZATION_FAILED)
            return
        self._authorization = authorization
        self.feedback.set('')
        self._show_account_form(first_account=False)

    def save(self, event=None):
        if self._closed or self._busy or self._stage != 'create':
            return 'break'
        password = self._fields['password'].get()
        confirmation = self._fields['confirmation'].get()
        self._fields['password'].set('')
        self._fields['confirmation'].set('')
        try:
            username, full_name, role = validate_user_details(
                self._fields['username'].get(), self._fields['full_name'].get(),
                'Admin' if self._first_account else self._fields['role'].get())
            validate_password(password)
            if password != confirmation:
                raise ValueError('Password confirmation does not match. Please try again.')
        except ValueError as error:
            self.feedback.set(str(error))
            return 'break'
        self._start(self._create, (username, full_name, role, password), self._check_creation,
                    'Creating account...', saving=True)
        return 'break'

    def _create(self, username, full_name, role, password):
        if self._closed:
            return
        try:
            user_id = create_account(username, full_name, role, password, authorization=self._authorization)
        except (UserCreateError, ValueError) as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Account creation could not be confirmed. Check the username before retrying.'))
        else:
            self._results.put((user_id, None))

    def _check_creation(self):
        result = self._result(self._check_creation)
        if result is None:
            return
        _, error = result
        if error is not None:
            self.feedback.set(error)
            return
        self._close()
        messagebox.showinfo('Account Created', 'Account created successfully.', parent=self.parent)

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
        for value in self._fields.values():
            value.set('')
        if self._authorization is not None:
            self._authorization.revoke()
            self._authorization = None
        if self._poll_id is not None:
            self.window.after_cancel(self._poll_id)
            self._poll_id = None
        self.window.grab_release()
        self.window.destroy()
