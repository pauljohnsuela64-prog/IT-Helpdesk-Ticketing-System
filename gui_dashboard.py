"""Dashboard cards and asynchronous loading, without SQL or database writes."""
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import ttk

from dashboard_repository import DashboardReadError, get_dashboard_statistics


# Repository key, card title, optional exact ticket status ('' clears the filter).
DASHBOARD_CARDS = (
    ('total_tickets', 'Total Tickets', ''),
    ('open_tickets', 'Open', 'Open'),
    ('assigned_tickets', 'Assigned', 'Assigned'),
    ('in_progress_tickets', 'In Progress', 'In Progress'),
    ('resolved_tickets', 'Resolved', 'Resolved'),
    ('closed_tickets', 'Closed', 'Closed'),
    ('critical_tickets', 'Critical Priority', None),
    ('active_technicians', 'Active Technicians', None),
)


class DashboardPanel:
    """Global statistics refresh independently of the existing ticket viewer."""

    def __init__(self, parent, on_status_filter):
        self._on_status_filter = on_status_filter
        self._results = Queue()
        self._loading = False
        self._closed = False
        self._poll_id = None
        self._refresh_pending = False
        self.frame = ttk.Frame(parent, style='Helpdesk.TFrame')
        self.frame.columnconfigure(0, weight=1)
        ttk.Label(self.frame, text='Help Desk Dashboard', style='Helpdesk.Section.TLabel').grid(
            row=0, column=0, sticky='w', pady=(0, 10),
        )
        ttk.Label(self.frame, text='Counts across all tickets', style='Helpdesk.Status.TLabel').grid(
            row=0, column=1, sticky='e', pady=(0, 10),
        )
        cards = ttk.Frame(self.frame, style='Helpdesk.TFrame')
        cards.grid(row=1, column=0, columnspan=2, sticky='ew')
        self.counts = {}
        for column in range(4):
            cards.columnconfigure(column, weight=1, uniform='dashboard')
        for index, (field, title, status) in enumerate(DASHBOARD_CARDS):
            card = ttk.Frame(cards, padding=(16, 10), relief='solid', borderwidth=1,
                             style='Dashboard.Card.TFrame')
            card.grid(row=index // 4, column=index % 4, sticky='nsew',
                      padx=4, pady=(0, 8))
            card.columnconfigure(0, weight=1)
            title_label = ttk.Label(card, text=title, style='Dashboard.Title.TLabel')
            title_label.grid(row=0, column=0, sticky='w')
            self.counts[field] = tk.StringVar(master=self.frame, value='—')
            count_label = ttk.Label(card, textvariable=self.counts[field], style='Dashboard.Count.TLabel')
            count_label.grid(row=1, column=0, sticky='w', pady=(4, 0))
            if status is not None:
                card.configure(takefocus=True)
                for widget in (card, title_label, count_label):
                    widget.configure(cursor='hand2')
                    widget.bind('<Button-1>', lambda event, selected=status, tile=card: self._select_status(selected, tile))
                for key in ('<Return>', '<space>'):
                    card.bind(key, lambda event, selected=status, tile=card: self._select_status(selected, tile))
        self.filter_label = tk.StringVar(master=self.frame, value='Status filter: All statuses')
        ttk.Label(self.frame, textvariable=self.filter_label, style='Helpdesk.Status.TLabel').grid(
            row=2, column=0, sticky='w',
        )
        self.feedback = tk.StringVar(master=self.frame, value='Click a status card to filter tickets.')
        ttk.Label(self.frame, textvariable=self.feedback, wraplength=580, justify='right',
                  style='Helpdesk.Status.TLabel').grid(row=2, column=1, sticky='e')

    def _select_status(self, status, card):
        if not self._closed:
            card.focus_set()
            self._on_status_filter(status)

    def set_status_filter(self, status):
        if not self._closed:
            self.filter_label.set(f'Status filter: {status or "All statuses"}')

    def refresh(self):
        if self._closed:
            return
        if self._loading:
            # A save during a read must trigger a new read after the old one finishes.
            self._refresh_pending = True
            return
        self._loading = True
        self.feedback.set('Refreshing dashboard...')
        Thread(target=self._load_statistics, daemon=True).start()
        self._poll_id = self.frame.after(100, self._check_refresh)

    def _load_statistics(self):
        try:
            counts = get_dashboard_statistics()
        except DashboardReadError as error:
            self._results.put((None, str(error)))
        except Exception:
            self._results.put((None, 'Please try Refresh again.'))
        else:
            self._results.put((counts, None))

    def _check_refresh(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            counts, error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.frame.after(100, self._check_refresh)
            return
        self._loading = False
        if self._refresh_pending:
            self._refresh_pending = False
            self.refresh()
            return
        if error is not None:
            self.feedback.set(f'Unable to load dashboard. {error}')
            return
        for field, value in counts.items():
            self.counts[field].set(str(value))
        self.feedback.set('Click a status card to filter tickets.')

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._poll_id is not None:
            self.frame.after_cancel(self._poll_id)
            self._poll_id = None
