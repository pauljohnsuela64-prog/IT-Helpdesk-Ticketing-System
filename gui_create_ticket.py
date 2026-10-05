"""Tkinter Create Ticket dialog using shared validation and repository logic."""
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import messagebox, ttk

from input_validation import validate_text
from ticket_repository import CATEGORIES, PRIORITIES, TicketCreateError, create_ticket


class CreateTicketDialog:
    def __init__(self, parent, on_created):
        self.parent = parent
        self.on_created = on_created
        self._results = Queue()
        self._saving = False
        self._closed = False
        self._poll_id = None
        self._widgets = []

        self.window = tk.Toplevel(parent)
        self.window.title('Create Ticket')
        self.window.geometry('650x660')
        self.window.minsize(560, 600)
        self.window.resizable(True, True)
        self.window.transient(parent)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
        self.window.protocol('WM_DELETE_WINDOW', self.cancel)
        self.window.bind('<Escape>', lambda event: self.cancel())

        self.fields = {
            field: tk.StringVar(master=self.window, value='Medium' if field == 'priority' else '')
            for field in ('employee_name', 'department', 'category', 'subject', 'priority')
        }
        self.feedback = tk.StringVar(master=self.window, value='')
        self._build_widgets()
        self.window.grab_set()
        self.employee_entry.focus_set()

    @property
    def is_open(self):
        return not self._closed

    @property
    def is_saving(self):
        return self._saving

    def _build_widgets(self):
        content = ttk.Frame(self.window, padding=24, style='Helpdesk.TFrame')
        content.grid(row=0, column=0, sticky='nsew')
        content.columnconfigure(1, weight=1)
        content.rowconfigure(5, weight=1)
        ttk.Label(content, text='Create Ticket', style='Helpdesk.Section.TLabel').grid(
            row=0, column=0, columnspan=2, sticky='w', pady=(0, 20),
        )
        for row, (field, label) in enumerate((('employee_name', 'Employee Name'),
                                             ('department', 'Department'), ('category', 'Category'),
                                             ('subject', 'Subject')), start=1):
            ttk.Label(content, text=label, style='Helpdesk.Status.TLabel').grid(
                row=row, column=0, sticky='w', padx=(0, 16), pady=(0, 12),
            )
            if field == 'category':
                widget = ttk.Combobox(content, textvariable=self.fields[field], values=CATEGORIES,
                                      state='readonly', font=('Segoe UI', 10))
                normal_state = 'readonly'
            else:
                widget = ttk.Entry(content, textvariable=self.fields[field], font=('Segoe UI', 10))
                normal_state = 'normal'
            widget.grid(row=row, column=1, sticky='ew', pady=(0, 12))
            self._widgets.append((widget, normal_state))
            if field == 'employee_name':
                self.employee_entry = widget

        ttk.Label(content, text='Description', style='Helpdesk.Status.TLabel').grid(
            row=5, column=0, sticky='nw', padx=(0, 16), pady=(0, 12),
        )
        description_frame = ttk.Frame(content)
        description_frame.grid(row=5, column=1, sticky='nsew', pady=(0, 12))
        description_frame.columnconfigure(0, weight=1)
        description_frame.rowconfigure(0, weight=1)
        self.description = tk.Text(description_frame, height=8, width=40, wrap='word',
                                   font=('Segoe UI', 10), padx=8, pady=8)
        self.description.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(description_frame, orient='vertical', command=self.description.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.description.configure(yscrollcommand=scrollbar.set)
        self._widgets.append((self.description, 'normal'))

        ttk.Label(content, text='Priority', style='Helpdesk.Status.TLabel').grid(
            row=6, column=0, sticky='w', padx=(0, 16), pady=(0, 12),
        )
        priority = ttk.Combobox(content, textvariable=self.fields['priority'], values=PRIORITIES,
                                state='readonly', font=('Segoe UI', 10))
        priority.grid(row=6, column=1, sticky='ew', pady=(0, 12))
        self._widgets.append((priority, 'readonly'))
        ttk.Label(content, textvariable=self.feedback, wraplength=550,
                  style='Helpdesk.Status.TLabel').grid(row=7, column=0, columnspan=2,
                                                       sticky='ew', pady=(0, 12))
        buttons = ttk.Frame(content, style='Helpdesk.TFrame')
        buttons.grid(row=8, column=0, columnspan=2, sticky='ew')
        buttons.columnconfigure(0, weight=1)
        self.cancel_button = ttk.Button(buttons, text='Cancel', command=self.cancel,
                                        style='Helpdesk.TButton')
        self.cancel_button.grid(row=0, column=1, padx=(0, 8))
        self.save_button = ttk.Button(buttons, text='Save', command=self.save,
                                      style='Helpdesk.TButton')
        self.save_button.grid(row=0, column=2)

    def _validated_values(self):
        employee = validate_text(self.fields['employee_name'].get(), 'Employee name', 100)
        department = validate_text(self.fields['department'].get(), 'Department', 100)
        category = self.fields['category'].get()
        if category not in CATEGORIES:
            raise ValueError('Choose one of the listed categories.')
        subject = validate_text(self.fields['subject'].get(), 'Subject', 150)
        description = validate_text(self.description.get('1.0', 'end-1c'), 'Description')
        priority = self.fields['priority'].get()
        if priority not in PRIORITIES:
            raise ValueError('Choose one of the listed priorities.')
        return employee, department, category, subject, description, priority

    def _set_saving(self, saving):
        self._saving = saving
        for widget, normal_state in self._widgets:
            widget.configure(state='disabled' if saving else normal_state)
        for button in (self.save_button, self.cancel_button):
            button.state(['disabled'] if saving else ['!disabled'])

    def save(self):
        if self._closed or self._saving:
            return
        try:
            values = self._validated_values()
        except ValueError as error:
            self.feedback.set(str(error))
            return
        self._set_saving(True)
        self.feedback.set('Saving ticket...')
        Thread(target=self._save_ticket, args=(values,), daemon=True).start()
        self._poll_id = self.window.after(100, self._check_save)

    def _save_ticket(self, values):
        """The worker uses only repository data; Tkinter stays on the main thread."""
        try:
            ticket_id = create_ticket(*values)
        except (TicketCreateError, ValueError) as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Ticket creation could not be confirmed. '
                                    'Use Refresh to check tickets before retrying.'))
        else:
            self._results.put((ticket_id, None))

    def _check_save(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            ticket_id, error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.window.after(100, self._check_save)
            return
        if error is not None:
            self._set_saving(False)
            self.feedback.set(error)
            return
        self._saving = False
        self._close()
        self.on_created()
        messagebox.showinfo('Ticket Created', f'Ticket created successfully.\nNew Ticket ID: {ticket_id}',
                            parent=self.parent)

    def focus(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_set()

    def cancel(self):
        # Once Save starts, wait for its result so Cancel cannot hide a committed ticket.
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
