"""Tkinter ticket viewing, creation, search, and updates: python gui_app.py."""
from datetime import datetime
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import messagebox, ttk

from gui_create_ticket import CreateTicketDialog
from gui_update_ticket import UpdateTicketDialog
from ticket_repository import TicketReadError, get_tickets, search_tickets


# Field, heading, preferred width, minimum width.
TICKET_COLUMNS = (
    ('ticket_id', 'Ticket ID', 80, 70),
    ('employee_name', 'Employee', 145, 120),
    ('department', 'Department', 120, 100),
    ('category', 'Category', 110, 90),
    ('subject', 'Subject', 260, 200),
    ('priority', 'Priority', 90, 80),
    ('status', 'Status', 115, 100),
    ('assigned_to', 'Assigned To', 155, 130),
    ('created_at', 'Created At', 160, 150),
)
BACKGROUND = '#f4f6fa'


def ticket_row_values(ticket):
    """Format repository data for a single readable table row."""
    values = []
    for field, _, _, _ in TICKET_COLUMNS:
        value = ticket.get(field)
        if value is None or value == '':
            value = 'Unassigned' if field == 'assigned_to' else '-'
        elif field == 'created_at' and isinstance(value, datetime):
            value = value.strftime('%Y-%m-%d %H:%M')
        # Keep multiline text and control characters from disrupting table rows.
        values.append(''.join(
            character if character.isprintable() else repr(character)[1:-1]
            for character in str(value)
        ))
    return tuple(values)


class TicketViewer:
    """GUI interaction only; the existing repository handles database access."""

    def __init__(self, root):
        self.root = root
        self._results = Queue()
        self._loading = False
        self._closed = False
        self._poll_id = None
        self._create_dialog = None
        self._update_dialog = None
        self._refresh_pending = False
        self._active_search = ''
        self._loading_search = ''

        root.title('IT Help Desk Ticketing System')
        root.geometry('1240x720')
        root.minsize(900, 480)
        root.resizable(True, True)
        root.configure(background=BACKGROUND)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        root.protocol('WM_DELETE_WINDOW', self.close)

        self._configure_styles()
        self._build_widgets()
        self.refresh_tickets()

    def _configure_styles(self):
        style = ttk.Style(self.root)
        themes = style.theme_names()
        if 'vista' in themes:
            style.theme_use('vista')
        elif 'clam' in themes:
            style.theme_use('clam')
        style.configure('Helpdesk.TFrame', background=BACKGROUND)
        style.configure('Helpdesk.Title.TLabel', background=BACKGROUND,
                        foreground='#182a43', font=('Segoe UI', 21, 'bold'))
        style.configure('Helpdesk.Subtitle.TLabel', background=BACKGROUND,
                        foreground='#607086', font=('Segoe UI', 11))
        style.configure('Helpdesk.Section.TLabel', background=BACKGROUND,
                        foreground='#182a43', font=('Segoe UI', 13, 'bold'))
        style.configure('Helpdesk.Status.TLabel', background=BACKGROUND,
                        foreground='#526176', font=('Segoe UI', 10))
        style.configure('Helpdesk.TButton', padding=(16, 8), font=('Segoe UI', 10))
        style.configure('Helpdesk.Treeview', rowheight=30, font=('Segoe UI', 10),
                        background='white', fieldbackground='white')
        style.configure('Helpdesk.Treeview.Heading', font=('Segoe UI', 10, 'bold'))

    def _build_widgets(self):
        content = ttk.Frame(self.root, padding=(24, 20, 24, 16), style='Helpdesk.TFrame')
        content.grid(row=0, column=0, sticky='nsew')
        content.columnconfigure(0, weight=1)
        content.rowconfigure(3, weight=1)

        header = ttk.Frame(content, style='Helpdesk.TFrame')
        header.grid(row=0, column=0, sticky='ew', pady=(0, 22))
        ttk.Label(header, text='IT HELP DESK TICKETING SYSTEM',
                  style='Helpdesk.Title.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(header, text='Ticket Management',
                  style='Helpdesk.Subtitle.TLabel').grid(row=1, column=0, sticky='w', pady=(6, 0))

        toolbar = ttk.Frame(content, style='Helpdesk.TFrame')
        toolbar.grid(row=1, column=0, sticky='ew', pady=(0, 12))
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, text='Tickets', style='Helpdesk.Section.TLabel').grid(
            row=0, column=0, sticky='w',
        )
        self.create_button = ttk.Button(toolbar, text='Create Ticket', command=self.open_create_ticket,
                                        style='Helpdesk.TButton')
        self.create_button.grid(row=0, column=1, sticky='e', padx=(0, 10))
        self.update_button = ttk.Button(toolbar, text='Update Ticket', command=self.open_update_ticket,
                                        style='Helpdesk.TButton')
        self.update_button.grid(row=0, column=2, sticky='e', padx=(0, 10))
        self.refresh_button = ttk.Button(toolbar, text='Refresh', command=self.refresh_tickets,
                                         style='Helpdesk.TButton')
        self.refresh_button.grid(row=0, column=3, sticky='e')

        search_area = ttk.Frame(content, style='Helpdesk.TFrame')
        search_area.grid(row=2, column=0, sticky='ew', pady=(0, 12))
        search_area.columnconfigure(1, weight=1)
        ttk.Label(search_area, text='Search tickets', style='Helpdesk.Status.TLabel').grid(
            row=0, column=0, sticky='w', padx=(0, 12),
        )
        self.search_term = tk.StringVar(master=self.root, value='')
        self.search_entry = ttk.Entry(search_area, textvariable=self.search_term, font=('Segoe UI', 10))
        self.search_entry.grid(row=0, column=1, sticky='ew', padx=(0, 10))
        self.search_entry.bind('<Return>', self.perform_search)
        self.search_button = ttk.Button(search_area, text='Search', command=self.perform_search,
                                        style='Helpdesk.TButton')
        self.search_button.grid(row=0, column=2, padx=(0, 8))
        self.clear_search_button = ttk.Button(search_area, text='Clear Search', command=self.clear_search,
                                              style='Helpdesk.TButton')
        self.clear_search_button.grid(row=0, column=3)

        table = ttk.Frame(content)
        table.grid(row=3, column=0, sticky='nsew')
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=tuple(column[0] for column in TICKET_COLUMNS),
                                 show='headings', selectmode='browse', style='Helpdesk.Treeview')
        for field, heading, width, minimum in TICKET_COLUMNS:
            self.tree.heading(field, text=heading)
            self.tree.column(field, width=width, minwidth=minimum,
                             stretch=field == 'subject', anchor='w')
        self.tree.tag_configure('alternate', background='#f0f4fa')
        self.tree.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)

        self.status = tk.StringVar(master=self.root, value='Ready.')
        ttk.Label(content, textvariable=self.status, style='Helpdesk.Status.TLabel',
                  wraplength=820, anchor='w').grid(row=4, column=0, sticky='ew', pady=(12, 0))

    def perform_search(self, event=None):
        if not self._closed:
            self._active_search = self.search_term.get().strip()
            self.search_term.set(self._active_search)
            self._request_refresh()
        return 'break'

    def clear_search(self):
        if self._closed:
            return
        self.search_term.set('')
        self._active_search = ''
        self._request_refresh()

    def refresh_tickets(self):
        if self._closed or self._loading:
            return
        self._loading = True
        self._loading_search = self._active_search
        self.status.set('Searching tickets...' if self._active_search else 'Loading tickets...')
        self.refresh_button.state(['disabled'])
        Thread(target=self._load_tickets, args=(self._loading_search,), daemon=True).start()
        self._poll_id = self.root.after(100, self._check_refresh)

    def _load_tickets(self, search_term=''):
        """The worker reads data and queues results; it never calls Tkinter."""
        try:
            tickets = search_tickets(search_term) if search_term else get_tickets()
        except TicketReadError as error:
            self._results.put((None, str(error)))
        except Exception:
            # An unexpected reader failure must not leave Refresh disabled forever.
            self._results.put((None, 'Please try Refresh again.'))
        else:
            self._results.put((tickets, None))

    def _check_refresh(self):
        """Apply worker results on Tkinter's main thread."""
        self._poll_id = None
        if self._closed:
            return
        try:
            tickets, error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.root.after(100, self._check_refresh)
            return
        self._loading = False
        if self._refresh_pending or self._loading_search != self._active_search:
            # Discard an older result and load the most recently submitted search.
            self._refresh_pending = False
            self.refresh_tickets()
            return
        self.refresh_button.state(['!disabled'])
        if error is not None:
            # Leave the last successful table visible when a refresh fails.
            self.status.set(f'Unable to load tickets. {error}')
        else:
            self._display_tickets(tickets)

    def open_create_ticket(self):
        if self._closed:
            return
        if self._update_dialog is not None and self._update_dialog.is_open:
            self._update_dialog.focus()
            return
        if self._create_dialog is not None and self._create_dialog.is_open:
            self._create_dialog.focus()
            return
        self._create_dialog = CreateTicketDialog(self.root, self._refresh_after_creation)

    def open_update_ticket(self):
        if self._closed:
            return
        for dialog in (self._create_dialog, self._update_dialog):
            if dialog is not None and dialog.is_open:
                dialog.focus()
                return
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo('Select a Ticket', 'Please select a ticket row before clicking Update Ticket.',
                                parent=self.root)
            return
        self._update_dialog = UpdateTicketDialog(self.root, int(selection[0]), self._request_refresh)

    def _refresh_after_creation(self):
        self._request_refresh()

    def _request_refresh(self):
        if self._closed:
            return
        if self._loading:
            self._refresh_pending = True
        else:
            self.refresh_tickets()

    def _display_tickets(self, tickets):
        selection = self.tree.selection()
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        for index, ticket in enumerate(tickets):
            self.tree.insert('', 'end', iid=str(ticket['ticket_id']), values=ticket_row_values(ticket),
                             tags=('alternate',) if index % 2 else ())
        if selection and self.tree.exists(selection[0]):
            self.tree.selection_set(selection[0])
            self.tree.focus(selection[0])
        count = len(tickets)
        if self._active_search:
            self.status.set(f'{count} matching ticket{"s" if count != 1 else ""} found.'
                            if count else 'No matching tickets found.')
        else:
            self.status.set(f'{count} ticket{"s" if count != 1 else ""} loaded.'
                            if count else 'No tickets found.')

    def close(self):
        if self._closed:
            return
        dialogs = (self._create_dialog, self._update_dialog)
        for dialog in dialogs:
            if dialog is not None and dialog.is_open and dialog.is_saving:
                dialog.focus()
                return
        for dialog in dialogs:
            if dialog is not None and dialog.is_open:
                dialog.cancel()
        self._closed = True
        if self._poll_id is not None:
            self.root.after_cancel(self._poll_id)
            self._poll_id = None
        self.root.destroy()


def main():
    root = tk.Tk()
    TicketViewer(root)
    root.mainloop()


if __name__ == '__main__':
    main()
