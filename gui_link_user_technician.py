"""Admin-only linking dialog; repositories enforce role and link safeguards."""
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import messagebox, ttk

from gui_permissions import PERMISSION_DENIED, require_permission
from user_repository import (
    NO_AVAILABLE_TECHNICIANS, UserManagementPermissionError, UserReadError,
    UserTechnicianLinkError, get_available_technicians, get_user, link_user_technician,
)


class LinkUserTechnicianDialog:
    def __init__(self, manager, user_id):
        self.manager = manager
        self.user_id = user_id
        self._results = Queue()
        self._loading = True
        self._saving = False
        self._closed = False
        self._poll_id = None
        self._current = None
        self._technicians = []
        self.window = tk.Toplevel(manager.window)
        self.window.title('Link Technician Account')
        self.window.geometry('650x400')
        self.window.minsize(600, 370)
        self.window.resizable(True, True)
        self.window.transient(manager.window)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
        self.window.protocol('WM_DELETE_WINDOW', self.cancel)
        self.window.bind('<Escape>', lambda event: self.cancel())
        content = ttk.Frame(self.window, padding=24, style='Helpdesk.TFrame')
        content.grid(row=0, column=0, sticky='nsew')
        content.columnconfigure(0, weight=1)
        content.rowconfigure(4, weight=1)
        ttk.Label(content, text='Link Technician Account', style='Helpdesk.Section.TLabel').grid(
            row=0, column=0, sticky='w', pady=(0, 16))
        self.summary = tk.StringVar(master=self.window, value=f'User ID: {user_id}')
        ttk.Label(content, textvariable=self.summary, style='Helpdesk.Status.TLabel', wraplength=570).grid(
            row=1, column=0, sticky='ew', pady=(0, 16))
        ttk.Label(content, text='Active Technician', style='Helpdesk.Status.TLabel').grid(row=2, column=0, sticky='w')
        self.technician_combo = ttk.Combobox(content, state='disabled', values=(), font=('Segoe UI', 11))
        self.technician_combo.grid(row=3, column=0, sticky='ew', pady=(6, 12))
        self.feedback = tk.StringVar(master=self.window, value='Loading account and available technicians...')
        ttk.Label(content, textvariable=self.feedback, style='Helpdesk.Status.TLabel', wraplength=570).grid(
            row=4, column=0, sticky='ew', pady=(0, 12))
        buttons = ttk.Frame(content, style='Helpdesk.TFrame')
        buttons.grid(row=5, column=0, sticky='ew')
        buttons.columnconfigure(0, weight=1)
        self.cancel_button = ttk.Button(buttons, text='Cancel', command=self.cancel, style='Helpdesk.TButton')
        self.cancel_button.grid(row=0, column=1, padx=(0, 10))
        self.save_button = ttk.Button(buttons, text='Save Link', command=self.save, style='Helpdesk.TButton')
        self.save_button.grid(row=0, column=2)
        self.save_button.state(['disabled'])
        self.window.grab_set()
        Thread(target=self._load_data, daemon=True).start()
        self._poll_id = self.window.after(100, self._check_load)

    @property
    def is_open(self):
        return not self._closed

    @property
    def is_saving(self):
        return self._saving

    def _load_data(self):
        if not self.manager.permissions.allows('manage_users'):
            self._results.put((None, None, PERMISSION_DENIED))
            return
        try:
            user = get_user(self.user_id, self.manager.user['user_id'])
            if user is None:
                raise UserTechnicianLinkError('No user found with that ID.')
            if user['role'] != 'Technician':
                raise UserTechnicianLinkError('Only Technician accounts can be linked to a technician record.')
            if user['technician_id'] is not None:
                raise UserTechnicianLinkError('This account is already linked to a technician record.')
            technicians = get_available_technicians(self.manager.user['user_id'])
        except (UserReadError, UserTechnicianLinkError, UserManagementPermissionError, ValueError) as error:
            self._results.put((None, None, str(error)))
        except Exception:
            self._results.put((None, None, 'Unable to load technician links. Cancel and try again.'))
        else:
            self._results.put((user, technicians, None))

    def _check_load(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            user, technicians, error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.window.after(100, self._check_load)
            return
        self._loading = False
        if error is not None:
            self.feedback.set(error)
            return
        self._current = user
        self._technicians = technicians
        self.summary.set(f'User ID: {user["user_id"]}\nUsername: {user["username"]}\nFull Name: {user["full_name"]}')
        self.technician_combo.configure(values=tuple(f'{tech["full_name"]} (ID: {tech["technician_id"]})' for tech in technicians),
                                       state='readonly' if technicians else 'disabled')
        self.save_button.state(['!disabled'] if technicians and self.manager.permissions.allows('manage_users') else ['disabled'])
        self.feedback.set('Choose one available Active technician.' if technicians else NO_AVAILABLE_TECHNICIANS)

    def save(self):
        if self._closed or self._loading or self._saving or self._current is None:
            return
        if not require_permission(self.manager.permissions, 'manage_users', self.window):
            return
        selection = self.technician_combo.current()
        if not 0 <= selection < len(self._technicians):
            self.feedback.set('Please select one of the listed Active technicians.')
            return
        technician = self._technicians[selection]
        if not messagebox.askyesno('Confirm Technician Link',
                                  f'Link {self._current["username"]} (User ID: {self.user_id}) to '
                                  f'{technician["full_name"]} (Technician ID: {technician["technician_id"]})?',
                                  parent=self.window, default=messagebox.NO):
            self.feedback.set('Link cancelled. No changes saved.')
            return
        self._saving = True
        self.technician_combo.configure(state='disabled')
        self.save_button.state(['disabled'])
        self.cancel_button.state(['disabled'])
        self.feedback.set('Saving technician link...')
        Thread(target=self._link, args=(technician['technician_id'],), daemon=True).start()
        self._poll_id = self.window.after(100, self._check_save)

    def _link(self, technician_id):
        if not self.manager.permissions.allows('manage_users'):
            self._results.put(PERMISSION_DENIED)
            return
        try:
            link_user_technician(self.user_id, technician_id, self.manager.user['user_id'])
        except (UserTechnicianLinkError, UserManagementPermissionError, ValueError) as error:
            self._results.put(str(error))
        except Exception:
            self._results.put('Link could not be confirmed. Cancel and refresh before retrying.')
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
        self._saving = False
        if error is not None:
            self.feedback.set(error)
            self.technician_combo.configure(state='readonly')
            self.cancel_button.state(['!disabled'])
            self.save_button.state(['!disabled'] if self.manager.permissions.allows('manage_users') else ['disabled'])
            return
        self.cancel()
        self.manager._request_refresh()
        messagebox.showinfo('Technician Linked', 'Technician linked successfully.', parent=self.manager.window)

    def focus(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_set()

    def cancel(self):
        if self._closed or self._saving:
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
