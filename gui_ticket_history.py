"""Read-only Tkinter ticket history using the existing ticket repositories."""
from datetime import datetime
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import ttk

from ticket_history_repository import TicketHistoryReadError, get_ticket_history
from ticket_repository import TicketReadError, get_ticket


HISTORY_COLUMNS = (
    ('created_at', 'Date/Time', 180, 170),
    ('action', 'Action', 210, 180),
    ('details', 'Details', 480, 300),
    ('performed_by', 'Performed By', 230, 180),
)


def _readable(value, multiline=False):
    if value is None or value == '':
        return '-'
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    return ''.join(character if character.isprintable() or (multiline and character in '\n\t')
                   else repr(character)[1:-1] for character in str(value))


def history_performed_by(entry):
    if (entry.get('performed_by_user_id') is None or not entry.get('performed_by_full_name')
            or not entry.get('performed_by_role')):
        return 'System / Legacy'
    return f'{_readable(entry["performed_by_full_name"])} ({_readable(entry["performed_by_role"])})'


def history_row_values(entry):
    return tuple(history_performed_by(entry) if field == 'performed_by' else _readable(entry.get(field))
                 for field, _, _, _ in HISTORY_COLUMNS)


class TicketHistoryWindow:
    """A modeless viewer fixed to one ticket ID until closed."""

    def __init__(self, parent, ticket_id):
        self.parent = parent
        self.ticket_id = ticket_id
        self._results = Queue()
        self._loading = False
        self._closed = False
        self._poll_id = None
        self._entries = {}
        self.window = tk.Toplevel(parent)
        self.window.title(f'Ticket History #{ticket_id}')
        self.window.geometry('1250x700')
        self.window.minsize(850, 560)
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

    def _build_widgets(self):
        content = ttk.Frame(self.window, padding=24, style='Helpdesk.TFrame')
        content.grid(row=0, column=0, sticky='nsew')
        content.columnconfigure(0, weight=1)
        content.rowconfigure(2, weight=3)
        content.rowconfigure(3, weight=1)
        header = ttk.Frame(content, style='Helpdesk.TFrame')
        header.grid(row=0, column=0, sticky='ew', pady=(0, 12))
        ttk.Label(header, text=f'Ticket History — Ticket #{self.ticket_id}',
                  style='Helpdesk.Section.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 8))
        ttk.Label(header, textvariable=self.summary, wraplength=760, justify='left',
                  style='Helpdesk.Status.TLabel').grid(row=1, column=0, sticky='w')
        controls = ttk.Frame(content, style='Helpdesk.TFrame')
        controls.grid(row=1, column=0, sticky='ew', pady=(0, 12))
        controls.columnconfigure(0, weight=1)
        self.refresh_button = ttk.Button(controls, text='Refresh', command=self.refresh, style='Helpdesk.TButton')
        self.refresh_button.grid(row=0, column=1, padx=(0, 10))
        self.close_button = ttk.Button(controls, text='Close', command=self.close, style='Helpdesk.TButton')
        self.close_button.grid(row=0, column=2)
        table = ttk.Frame(content)
        table.grid(row=2, column=0, sticky='nsew')
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=tuple(column[0] for column in HISTORY_COLUMNS),
                                 show='headings', selectmode='browse', style='Helpdesk.Treeview')
        for field, heading, width, minimum in HISTORY_COLUMNS:
            self.tree.heading(field, text=heading)
            self.tree.column(field, width=width, minwidth=minimum, stretch=field == 'details', anchor='w')
        self.tree.tag_configure('alternate', background='#f0f4fa')
        self.tree.grid(row=0, column=0, sticky='nsew')
        self.tree.bind('<<TreeviewSelect>>', self._show_selected_details)
        vertical = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        details_area = ttk.Frame(content, style='Helpdesk.TFrame')
        details_area.grid(row=3, column=0, sticky='nsew', pady=(12, 0))
        details_area.columnconfigure(0, weight=1)
        details_area.rowconfigure(1, weight=1)
        ttk.Label(details_area, text='Selected Activity Details', style='Helpdesk.Status.TLabel').grid(
            row=0, column=0, sticky='w', pady=(0, 6),
        )
        self.details = tk.Text(details_area, height=6, width=60, wrap='word', font=('Segoe UI', 10), padx=8, pady=8)
        self.details.grid(row=1, column=0, sticky='nsew')
        details_scrollbar = ttk.Scrollbar(details_area, orient='vertical', command=self.details.yview)
        details_scrollbar.grid(row=1, column=1, sticky='ns')
        self.details.configure(yscrollcommand=details_scrollbar.set)
        self._set_details('Select an activity to read its full details.')
        ttk.Label(content, textvariable=self.feedback, wraplength=760, style='Helpdesk.Status.TLabel').grid(
            row=4, column=0, sticky='ew', pady=(12, 0),
        )

    def refresh(self):
        if self._closed or self._loading:
            return
        self._loading = True
        self.feedback.set('Loading ticket history...')
        self.refresh_button.state(['disabled'])
        Thread(target=self._load_history, daemon=True).start()
        self._poll_id = self.window.after(100, self._check_refresh)

    def _load_history(self):
        """Only repository reads run in this worker; it never accesses Tkinter."""
        try:
            ticket = get_ticket(self.ticket_id)
            entries = get_ticket_history(self.ticket_id) if ticket is not None else []
        except (TicketReadError, TicketHistoryReadError, ValueError) as error:
            self._results.put((None, None, str(error)))
        except Exception:
            self._results.put((None, None, 'Please try Refresh again.'))
        else:
            self._results.put((ticket, entries, None))

    def _check_refresh(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            ticket, entries, error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.window.after(100, self._check_refresh)
            return
        self._loading = False
        self.refresh_button.state(['!disabled'])
        if error is not None:
            # Preserve the last successful information and rows after a read failure.
            self.feedback.set(f'Unable to load ticket history. {error}')
            return
        if ticket is None:
            self.summary.set(f'Ticket ID: {self.ticket_id}\nTicket not found.')
            self._display_history([])
            self.feedback.set('This ticket no longer exists. Close this window or try Refresh again.')
            return
        self.summary.set(f'Ticket ID: {self.ticket_id}\n'
                         f'Employee: {_readable(ticket.get("employee_name"))}\n'
                         f'Subject: {_readable(ticket.get("subject"))}')
        self._display_history(entries)

    def _display_history(self, entries):
        selection = self.tree.selection()
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self._entries = {str(entry['history_id']): entry for entry in entries}
        # Preserve the repository's chronological order, including its ID tie-break.
        for index, entry in enumerate(entries):
            self.tree.insert('', 'end', iid=str(entry['history_id']), values=history_row_values(entry),
                             tags=('alternate',) if index % 2 else ())
        if entries:
            selected_id = selection[0] if selection and selection[0] in self._entries else str(entries[0]['history_id'])
            self.tree.selection_set(selected_id)
            self.tree.focus(selected_id)
            self._show_selected_details()
        else:
            self._set_details('No history found for this ticket.')
        count = len(entries)
        self.feedback.set(f'{count} history {"entry" if count == 1 else "entries"} loaded.' if count else
                          'No history found for this ticket.')

    def _show_selected_details(self, event=None):
        if self._closed:
            return
        selection = self.tree.selection()
        entry = self._entries.get(selection[0]) if selection else None
        if entry is None:
            return
        self._set_details(f'{_readable(entry.get("created_at"))} | {_readable(entry.get("action"))}\n'
                          f'Performed By: {history_performed_by(entry)}\n\n'
                          f'{_readable(entry.get("details"), multiline=True)}')

    def _set_details(self, text):
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', text)
        self.details.configure(state='disabled')

    def focus(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_set()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._poll_id is not None:
            self.window.after_cancel(self._poll_id)
            self._poll_id = None
        self.window.destroy()
