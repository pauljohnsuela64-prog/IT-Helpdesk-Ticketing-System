"""Login screen using repository authentication, with no SQL or password logging."""
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import ttk

from gui_create_account import CreateAccountDialog
from input_validation import validate_text
from password_security import validate_password
from user_repository import UserAuthenticationError, authenticate_user, public_user


INVALID_CREDENTIALS = 'Invalid username or password.'


class LoginScreen:
    def __init__(self, root, on_authenticated):
        self.root = root
        self.on_authenticated = on_authenticated
        self._results = Queue()
        self._authenticating = False
        self._closed = False
        self._poll_id = None
        self._account_dialog = None
        root.title('Login — IT Help Desk Ticketing System')
        root.geometry('540x430')
        root.minsize(460, 380)
        root.resizable(True, True)
        root.configure(background='#f4f6fa')
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style(root)
        style.configure('Login.TFrame', background='#f4f6fa')
        style.configure('Login.TLabel', background='#f4f6fa', foreground='#526176', font=('Segoe UI', 10))
        style.configure('Login.Title.TLabel', background='#f4f6fa', foreground='#182a43', font=('Segoe UI', 18, 'bold'))
        style.configure('Login.TButton', padding=(16, 8), font=('Segoe UI', 10))
        self.content = ttk.Frame(root, padding=32, style='Login.TFrame')
        self.content.grid(row=0, column=0, sticky='nsew')
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(6, weight=1)
        ttk.Label(self.content, text='IT HELP DESK\nTICKETING SYSTEM', style='Login.Title.TLabel').grid(
            row=0, column=0, sticky='w', pady=(0, 10),
        )
        ttk.Label(self.content, text='Sign in to continue', style='Login.TLabel').grid(
            row=1, column=0, sticky='w', pady=(0, 18),
        )
        self.username = tk.StringVar(master=root, value='')
        self.password = tk.StringVar(master=root, value='')
        self.feedback = tk.StringVar(master=root, value='')
        ttk.Label(self.content, text='Username', style='Login.TLabel').grid(row=2, column=0, sticky='w')
        self.username_entry = ttk.Entry(self.content, textvariable=self.username, font=('Segoe UI', 11))
        self.username_entry.grid(row=3, column=0, sticky='ew', pady=(6, 12))
        ttk.Label(self.content, text='Password', style='Login.TLabel').grid(row=4, column=0, sticky='w')
        self.password_entry = ttk.Entry(self.content, textvariable=self.password, show='*', font=('Segoe UI', 11))
        self.password_entry.grid(row=5, column=0, sticky='ew', pady=(6, 8))
        self.password_entry.bind('<Return>', self.attempt_login)
        ttk.Label(self.content, textvariable=self.feedback, wraplength=465, style='Login.TLabel').grid(
            row=6, column=0, sticky='ew', pady=(0, 12),
        )
        buttons = ttk.Frame(self.content, style='Login.TFrame')
        buttons.grid(row=7, column=0, sticky='ew')
        buttons.columnconfigure(0, weight=1)
        self.create_account_button = ttk.Button(buttons, text='Create Account', command=self.open_create_account,
                                                style='Login.TButton')
        self.create_account_button.grid(row=0, column=1, padx=(0, 10))
        self.login_button = ttk.Button(buttons, text='Login', command=self.attempt_login, style='Login.TButton')
        self.login_button.grid(row=0, column=2, padx=(0, 10))
        self.exit_button = ttk.Button(buttons, text='Exit', command=self.close, style='Login.TButton')
        self.exit_button.grid(row=0, column=3)
        self.username_entry.focus_set()

    def _set_busy(self, busy):
        self._authenticating = busy
        for widget in (self.username_entry, self.password_entry):
            widget.configure(state='disabled' if busy else 'normal')
        self.login_button.state(['disabled'] if busy else ['!disabled'])
        self.create_account_button.state(['disabled'] if busy else ['!disabled'])

    def open_create_account(self):
        if self._closed or self._authenticating:
            return
        if self._account_dialog is not None and self._account_dialog.is_open:
            self._account_dialog.focus()
            return
        self.password.set('')
        self._account_dialog = CreateAccountDialog(self.root)

    def attempt_login(self, event=None):
        if self._closed or self._authenticating:
            return 'break'
        if self._account_dialog is not None and self._account_dialog.is_open:
            self._account_dialog.focus()
            return 'break'
        username, password = self.username.get().strip(), self.password.get()
        self.password.set('')
        try:
            validate_text(username, 'Username', 50)
            validate_password(password)
        except ValueError:
            self.feedback.set(INVALID_CREDENTIALS)
            return 'break'
        self.username.set(username)
        self._set_busy(True)
        self.feedback.set('Signing in...')
        Thread(target=self._authenticate, args=(username, password), daemon=True).start()
        self._poll_id = self.root.after(100, self._check_login)
        return 'break'

    def _authenticate(self, username, password):
        try:
            user = authenticate_user(username, password)
        except UserAuthenticationError as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Unable to sign in. Please try again.'))
        else:
            self._results.put((public_user(user) if user is not None else None, None))

    def _check_login(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            user, error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.root.after(100, self._check_login)
            return
        self._set_busy(False)
        self.password.set('')
        if error is not None:
            self.feedback.set(error)
        elif user is None:
            self.feedback.set(INVALID_CREDENTIALS)
            self.password_entry.focus_set()
        else:
            self.on_authenticated(public_user(user))

    def dispose(self):
        """Remove this screen without destroying the application's Tk root."""
        if self._closed:
            return
        if self._account_dialog is not None and self._account_dialog.is_open:
            self._account_dialog.cancel()
        self._closed = True
        self.username.set('')
        self.password.set('')
        if self._poll_id is not None:
            self.root.after_cancel(self._poll_id)
            self._poll_id = None
        self.content.destroy()

    def close(self):
        if not self._closed:
            if self._account_dialog is not None and self._account_dialog.is_open and self._account_dialog.is_saving:
                self._account_dialog.focus()
                return
            self.dispose()
            self.root.destroy()
