"""Tkinter ticket and technician management: python gui_app.py."""
from datetime import datetime
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import messagebox, ttk

from gui_change_password import ChangePasswordDialog
from gui_create_ticket import CreateTicketDialog
from gui_dashboard import DashboardPanel
from gui_session import HelpDeskApplication
from gui_delete_ticket import DeleteTicketDialog
from gui_permissions import SessionPermissions, require_permission
from gui_reports import ReportsWindow
from report_repository import ReportPermissionError
from gui_ticket_history import TicketHistoryWindow
from gui_ticket_notes import TicketNotesWindow
from gui_technicians import TechnicianManagementWindow
from gui_update_ticket import UpdateTicketDialog
from gui_users import UserManagementWindow
from ticket_repository import (
    MISSING_TECHNICIAN_LINK, STATUSES, TicketReadError, get_assigned_tickets,
    get_tickets, search_tickets, validate_ticket_id,
)
from user_repository import public_user


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

    def __init__(self, root, user=None, on_logout=None):
        self.root = root
        self.user = public_user(user) if user is not None else None
        self.permissions = SessionPermissions(self.user)
        self._on_logout = on_logout
        self._results = Queue()
        self._loading = False
        self._closed = False
        self._poll_id = None
        self._create_dialog = None
        self._update_dialog = None
        self._delete_dialog = None
        self._technician_window = None
        self._user_window = None
        self._password_dialog = None
        self._history_window = None
        self._notes_window = None
        self._reports_window = None
        self._refresh_pending = False
        self._active_search = ''
        self._loading_search = ''
        self._active_status = ''
        self._assigned_view = False
        self._loading_assigned_view = False
        self.all_tickets_button = None
        self.assigned_tickets_button = None
        self.view_status = None

        root.title('IT Help Desk Ticketing System')
        root.geometry('1240x840')
        root.minsize(900, 640)
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
                        foreground='#182a43', font=('Segoe UI', 18, 'bold'))
        style.configure('Helpdesk.Subtitle.TLabel', background=BACKGROUND,
                        foreground='#607086', font=('Segoe UI', 11))
        style.configure('Helpdesk.Section.TLabel', background=BACKGROUND,
                        foreground='#182a43', font=('Segoe UI', 13, 'bold'))
        style.configure('Helpdesk.Status.TLabel', background=BACKGROUND,
                        foreground='#526176', font=('Segoe UI', 10))
        style.configure('Helpdesk.TButton', padding=(16, 8), font=('Segoe UI', 10))
        style.configure('Helpdesk.Toolbar.TButton', padding=(10, 7), font=('Segoe UI', 10))
        style.configure('Helpdesk.TMenubutton', padding=(12, 7), font=('Segoe UI', 10))
        style.configure('Helpdesk.Treeview', rowheight=30, font=('Segoe UI', 10),
                        background='white', fieldbackground='white')
        style.configure('Helpdesk.Treeview.Heading', font=('Segoe UI', 10, 'bold'))
        style.configure('Dashboard.Card.TFrame', background='white')
        style.configure('Dashboard.Title.TLabel', background='white',
                        foreground='#526176', font=('Segoe UI', 10))
        style.configure('Dashboard.Count.TLabel', background='white',
                        foreground='#182a43', font=('Segoe UI', 22, 'bold'))

    def _build_widgets(self):
        content = ttk.Frame(self.root, padding=(24, 18, 24, 14), style='Helpdesk.TFrame')
        self.content = content
        content.grid(row=0, column=0, sticky='nsew')
        content.columnconfigure(0, weight=1)
        content.rowconfigure(4, weight=1)

        header = ttk.Frame(content, style='Helpdesk.TFrame')
        header.grid(row=0, column=0, sticky='ew', pady=(0, 16))
        title = ttk.Label(header, text='IT HELP DESK TICKETING SYSTEM',
                          wraplength=540, style='Helpdesk.Title.TLabel')
        title.grid(row=0, column=0, sticky='w')
        ttk.Label(header, text='Ticket Management',
                  style='Helpdesk.Subtitle.TLabel').grid(row=1, column=0, sticky='w', pady=(4, 0))
        header.columnconfigure(0, weight=1)
        self.administration_button = None
        self.administration_menu = None
        self.account_button = None
        self.account_menu = None
        session = None
        if self.user is not None:
            name = ''.join(character if character.isprintable() else ' ' for character in self.user['full_name'])
            session = ttk.Frame(header, style='Helpdesk.TFrame')
            session.grid(row=0, column=1, rowspan=2, sticky='ne', padx=(20, 0))
            session.columnconfigure(0, weight=1)
            ttk.Label(session, text=f'Logged in as: {name} ({self.user["role"]})', wraplength=280,
                      justify='right', anchor='e', style='Helpdesk.Status.TLabel').grid(
                          row=0, column=0, sticky='e', pady=(0, 8))
            account_controls = ttk.Frame(session, style='Helpdesk.TFrame')
            account_controls.grid(row=1, column=0, sticky='e')
            administration_options = (
                ('Manage Users', 'manage_users', self.open_user_management),
                ('Manage Technicians', 'manage_technicians', self.open_technician_management),
                ('Reports', 'reports', self.open_reports),
            )
            allowed_options = [option for option in administration_options if self.permissions.allows(option[1])]
            if allowed_options:
                self.administration_button = ttk.Menubutton(account_controls, text='Administration', width=16,
                                                            style='Helpdesk.TMenubutton')
                self.administration_button.grid(row=0, column=0, padx=(0, 8))
                self.administration_menu = tk.Menu(self.administration_button, tearoff=False)
                for label, _, command in allowed_options:
                    self.administration_menu.add_command(label=label, command=command)
                self.administration_button.configure(menu=self.administration_menu)
            self.account_button = ttk.Menubutton(account_controls, text='Account', width=10,
                                                 style='Helpdesk.TMenubutton')
            self.account_button.grid(row=0, column=1)
            self.account_menu = tk.Menu(self.account_button, tearoff=False)
            if self.permissions.allows('change_password'):
                self.account_menu.add_command(label='Change Password', command=self.open_change_password)
                self.account_menu.add_separator()
            self.account_menu.add_command(label='Logout', command=self.logout)
            self.account_button.configure(menu=self.account_menu)

        def resize_title(event):
            available = max(1, event.width - (session.winfo_reqwidth() + 20 if session is not None else 0))
            if int(title.cget('wraplength')) != available:
                title.configure(wraplength=available)

        header.bind('<Configure>', resize_title)

        self.dashboard = DashboardPanel(content, self.filter_by_status)
        self.dashboard.frame.grid(row=1, column=0, sticky='ew', pady=(0, 14))

        toolbar = ttk.Frame(content, style='Helpdesk.TFrame')
        toolbar.grid(row=2, column=0, sticky='ew', pady=(0, 10))
        toolbar.columnconfigure(4, weight=1)
        ttk.Label(toolbar, text='Tickets', style='Helpdesk.Section.TLabel').grid(
            row=0, column=0, sticky='w', padx=(0, 16),
        )
        ticket_actions = ttk.Frame(toolbar, style='Helpdesk.TFrame')
        ticket_actions.grid(row=0, column=1, sticky='w')
        self.create_button = ttk.Button(ticket_actions, text='Create', command=self.open_create_ticket,
                                        width=8, style='Helpdesk.Toolbar.TButton')
        self.create_button.grid(row=0, column=0, padx=(0, 6))
        self.update_button = ttk.Button(ticket_actions, text='Update', command=self.open_update_ticket,
                                        width=8, style='Helpdesk.Toolbar.TButton')
        self.update_button.grid(row=0, column=1, padx=(0, 6))
        self.delete_button = ttk.Button(ticket_actions, text='Delete', command=self.open_delete_ticket,
                                        width=8, style='Helpdesk.Toolbar.TButton')
        self.delete_button.grid(row=0, column=2)
        if not self.permissions.allows('delete_ticket'):
            self.delete_button.state(['disabled'])
        ttk.Separator(toolbar, orient='vertical').grid(row=0, column=2, sticky='ns', padx=16)
        ticket_tools = ttk.Frame(toolbar, style='Helpdesk.TFrame')
        ticket_tools.grid(row=0, column=3, sticky='w')
        self.history_button = ttk.Button(ticket_tools, text='History', command=self.open_ticket_history,
                                         width=8, style='Helpdesk.Toolbar.TButton')
        self.history_button.grid(row=0, column=0, padx=(0, 6))
        self.notes_button = ttk.Button(ticket_tools, text='Notes', command=self.open_ticket_notes,
                                       width=8, style='Helpdesk.Toolbar.TButton')
        self.notes_button.grid(row=0, column=1, padx=(0, 6))
        self.refresh_button = ttk.Button(ticket_tools, text='Refresh', command=self.refresh_tickets,
                                         width=8, style='Helpdesk.Toolbar.TButton')
        self.refresh_button.grid(row=0, column=2)

        search_area = ttk.Frame(content, style='Helpdesk.TFrame')
        search_area.grid(row=3, column=0, sticky='ew', pady=(0, 8))
        search_area.columnconfigure(1, weight=1)
        ttk.Label(search_area, text='Search tickets:', style='Helpdesk.Status.TLabel').grid(
            row=0, column=0, sticky='w', padx=(0, 12),
        )
        self.search_term = tk.StringVar(master=self.root, value='')
        self.search_entry = ttk.Entry(search_area, textvariable=self.search_term, font=('Segoe UI', 10))
        self.search_entry.grid(row=0, column=1, sticky='ew', padx=(0, 10))
        self.search_entry.bind('<Return>', self.perform_search)
        self.search_button = ttk.Button(search_area, text='Search', command=self.perform_search,
                                        width=8, style='Helpdesk.Toolbar.TButton')
        self.search_button.grid(row=0, column=2, padx=(0, 8))
        self.clear_search_button = ttk.Button(search_area, text='Clear', command=self.clear_search,
                                              width=8, style='Helpdesk.Toolbar.TButton')
        self.clear_search_button.grid(row=0, column=3)

        if self.user is not None and self.user['role'] == 'Technician' and self.permissions.allows('view_tickets'):
            views = ttk.Frame(toolbar, style='Helpdesk.TFrame')
            views.grid(row=1, column=0, columnspan=5, sticky='ew', pady=(8, 0))
            self.all_tickets_button = ttk.Button(views, text='All Tickets', command=self.show_all_tickets,
                                                 style='Helpdesk.Toolbar.TButton')
            self.all_tickets_button.grid(row=0, column=0, padx=(0, 10))
            self.assigned_tickets_button = ttk.Button(views, text='My Assigned Tickets', command=self.show_assigned_tickets,
                                                      style='Helpdesk.Toolbar.TButton')
            self.assigned_tickets_button.grid(row=0, column=1, padx=(0, 16))
            self.view_status = tk.StringVar(master=self.root, value='View: All Tickets')
            ttk.Label(views, textvariable=self.view_status, style='Helpdesk.Status.TLabel').grid(row=0, column=2, sticky='w')

        table = ttk.Frame(content)
        table.grid(row=4, column=0, sticky='nsew')
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
                  wraplength=820, anchor='w').grid(row=5, column=0, sticky='ew', pady=(8, 0))

    def filter_by_status(self, status):
        if self._closed or (status and status not in STATUSES):
            return
        self._active_status = status
        self.dashboard.set_status_filter(status)
        self._request_refresh()

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
        self._active_status = ''
        self.dashboard.set_status_filter('')
        self._request_refresh()

    def show_all_tickets(self):
        if self._closed or not require_permission(self.permissions, 'view_tickets', self.root):
            return
        self._set_ticket_view(False)

    def show_assigned_tickets(self):
        if self._closed or self.user is None or self.user['role'] != 'Technician':
            return
        if not require_permission(self.permissions, 'view_tickets', self.root):
            return
        self._set_ticket_view(True)

    def _clear_ticket_rows(self):
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)

    def _set_ticket_view(self, assigned):
        if self._assigned_view != assigned:
            # Never show rows from the previous scope under the new view label.
            self._clear_ticket_rows()
        self._assigned_view = assigned
        if self.view_status is not None:
            self.view_status.set('View: My Assigned Tickets' if assigned else 'View: All Tickets')
        self.status.set('Loading assigned tickets...' if assigned else 'Loading tickets...')
        self._request_refresh()

    def refresh_tickets(self):
        if self._closed or self._loading:
            return
        self.dashboard.refresh()
        self._loading = True
        self._loading_search = self._active_search
        self._loading_assigned_view = self._assigned_view
        if self._assigned_view:
            self.status.set('Searching assigned tickets...' if self._active_search else 'Loading assigned tickets...')
        else:
            self.status.set('Searching tickets...' if self._active_search else 'Loading tickets...')
        self.refresh_button.state(['disabled'])
        # Capture view and session ID before starting work; no worker reads GUI state.
        args = ((self._loading_search, self.user.get('technician_id'), True)
                if self._assigned_view else (self._loading_search,))
        Thread(target=self._load_tickets, args=args, daemon=True).start()
        self._poll_id = self.root.after(100, self._check_refresh)

    def _load_tickets(self, search_term='', technician_id=None, assigned=False):
        """The worker reads data and queues results; it never calls Tkinter."""
        try:
            if assigned:
                tickets = get_assigned_tickets(technician_id, search_term)
            else:
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
        if (self._refresh_pending or self._loading_search != self._active_search
                or self._loading_assigned_view != self._assigned_view):
            # Discard an older result and load the most recently submitted search.
            self._refresh_pending = False
            self.refresh_tickets()
            return
        self.refresh_button.state(['!disabled'])
        if error is not None:
            if self._assigned_view and error == MISSING_TECHNICIAN_LINK:
                self._clear_ticket_rows()
                self.status.set(error)
                return
            # Leave the last successful table visible when a refresh fails.
            self.status.set(f'Unable to load tickets. {error}')
        else:
            self._display_tickets(tickets)

    def open_create_ticket(self):
        if self._closed or not require_permission(self.permissions, 'create_ticket', self.root):
            return
        if self._focus_notes_dialog():
            return
        for dialog in (self._update_dialog, self._delete_dialog, self._technician_window, self._user_window, self._password_dialog):
            if dialog is not None and dialog.is_open:
                dialog.focus()
                return
        if self._create_dialog is not None and self._create_dialog.is_open:
            self._create_dialog.focus()
            return
        self._create_dialog = CreateTicketDialog(self.root, self._refresh_after_creation,
                                                 permissions=self.permissions, user=self.user)

    def open_update_ticket(self):
        if self._closed or not require_permission(self.permissions, 'update_ticket', self.root):
            return
        if self._focus_notes_dialog():
            return
        for dialog in (self._create_dialog, self._update_dialog, self._delete_dialog, self._technician_window, self._user_window, self._password_dialog):
            if dialog is not None and dialog.is_open:
                dialog.focus()
                return
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo('Select a Ticket', 'Please select a ticket row before clicking Update Ticket.',
                                parent=self.root)
            return
        self._update_dialog = UpdateTicketDialog(self.root, int(selection[0]), self._request_refresh,
                                                 permissions=self.permissions, user=self.user)

    def open_delete_ticket(self):
        if self._closed or not require_permission(self.permissions, 'delete_ticket', self.root):
            return
        if self._focus_notes_dialog():
            return
        for dialog in (self._create_dialog, self._update_dialog, self._delete_dialog, self._technician_window, self._user_window, self._password_dialog):
            if dialog is not None and dialog.is_open:
                dialog.focus()
                return
        selection = self.tree.selection()
        if len(selection) != 1:
            messagebox.showinfo('Select a Ticket', 'Please select one ticket row before clicking Delete Ticket.',
                                parent=self.root)
            return
        try:
            ticket_id = int(selection[0])
            validate_ticket_id(ticket_id)
        except (TypeError, ValueError):
            messagebox.showinfo('Select a Ticket', 'Please Refresh and select a valid ticket row.', parent=self.root)
            return
        self._delete_dialog = DeleteTicketDialog(self.root, ticket_id, self._refresh_after_deletion,
                                                permissions=self.permissions)

    def open_technician_management(self):
        if self._closed or not require_permission(self.permissions, 'manage_technicians', self.root):
            return
        if self._focus_notes_dialog():
            return
        for dialog in (self._create_dialog, self._update_dialog, self._delete_dialog, self._technician_window, self._user_window, self._password_dialog):
            if dialog is not None and dialog.is_open:
                dialog.focus()
                return
        self._technician_window = TechnicianManagementWindow(self.root, on_change=self.dashboard.refresh,
                                                             permissions=self.permissions)

    def open_user_management(self):
        if self._closed or not require_permission(self.permissions, 'manage_users', self.root):
            return
        if self._focus_notes_dialog() or self._focus_ticket_dialog():
            return
        self._user_window = UserManagementWindow(self.root, self.user, permissions=self.permissions)

    def open_reports(self):
        if self._closed or not require_permission(self.permissions, 'reports', self.root):
            return
        if self._focus_notes_dialog() or self._focus_ticket_dialog():
            return
        if self._reports_window is not None and self._reports_window.is_open:
            self._reports_window.focus()
            return
        try:
            self._reports_window = ReportsWindow(self.root, self.user, permissions=self.permissions)
        except ReportPermissionError as error:
            messagebox.showinfo('Permission Denied', str(error), parent=self.root)

    def open_change_password(self):
        if self._closed or not require_permission(self.permissions, 'change_password', self.root):
            return
        if self._focus_notes_dialog() or self._focus_ticket_dialog():
            return
        self._password_dialog = ChangePasswordDialog(self.root, self.user['user_id'], permissions=self.permissions)

    def open_ticket_history(self):
        if self._closed or self._focus_notes_dialog():
            return
        for dialog in (self._create_dialog, self._update_dialog, self._delete_dialog, self._technician_window, self._user_window, self._password_dialog):
            if dialog is not None and dialog.is_open:
                dialog.focus()
                return
        selection = self.tree.selection()
        if len(selection) != 1:
            messagebox.showinfo('Select a Ticket', 'Please select one ticket row before clicking View History.',
                                parent=self.root)
            return
        try:
            ticket_id = int(selection[0])
            validate_ticket_id(ticket_id)
        except (TypeError, ValueError):
            messagebox.showinfo('Select a Ticket', 'Please Refresh and select a valid ticket row.', parent=self.root)
            return
        if self._history_window is not None and self._history_window.is_open:
            if self._history_window.ticket_id == ticket_id:
                self._history_window.focus()
                return
            self._history_window.close()
        self._history_window = TicketHistoryWindow(self.root, ticket_id)

    def _focus_notes_dialog(self):
        if (self._notes_window is not None and self._notes_window.is_open
                and self._notes_window.has_open_dialog):
            self._notes_window.focus()
            return True
        return False

    def _focus_ticket_dialog(self):
        for dialog in (self._create_dialog, self._update_dialog, self._delete_dialog, self._technician_window, self._user_window, self._password_dialog):
            if dialog is not None and dialog.is_open:
                dialog.focus()
                return True
        return False

    def open_ticket_notes(self):
        if self._closed or self._focus_notes_dialog() or self._focus_ticket_dialog():
            return
        selection = self.tree.selection()
        if len(selection) != 1:
            messagebox.showinfo('Select a Ticket', 'Please select one ticket row before clicking Ticket Notes.',
                                parent=self.root)
            return
        try:
            ticket_id = int(selection[0])
            validate_ticket_id(ticket_id)
        except (TypeError, ValueError):
            messagebox.showinfo('Select a Ticket', 'Please Refresh and select a valid ticket row.', parent=self.root)
            return
        if self._notes_window is not None and self._notes_window.is_open:
            if self._notes_window.ticket_id == ticket_id:
                self._notes_window.focus()
                return
            self._notes_window.close()
        self._notes_window = TicketNotesWindow(self.root, ticket_id, self._focus_ticket_dialog,
                                              permissions=self.permissions, user=self.user)

    def _refresh_after_deletion(self, ticket_id):
        if self._closed:
            return
        # Remove the confirmed missing row even if the subsequent refresh fails.
        row_id = str(ticket_id)
        if self.tree.exists(row_id):
            self.tree.delete(row_id)
        self._request_refresh()

    def _refresh_after_creation(self):
        self._request_refresh()

    def _request_refresh(self):
        if self._closed:
            return
        if self._loading:
            self._refresh_pending = True
            self.dashboard.refresh()
        else:
            self.refresh_tickets()

    def _display_tickets(self, tickets):
        # Apply an exact status to the complete results of the existing list/search.
        # Summary cards always use independent global database aggregates.
        if self._active_status:
            tickets = [ticket for ticket in tickets if ticket.get('status') == self._active_status]
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
        if self._assigned_view:
            scope = f'{self._active_status} assigned' if self._active_status else 'assigned'
            self.status.set(f'{count} {scope} ticket{"s" if count != 1 else ""} '
                            f'{"matched the search" if self._active_search else "loaded"}.'
                            if count else 'No matching assigned tickets found.' if self._active_search else
                            f'No {scope} tickets found.')
        elif self._active_status:
            self.status.set(f'{count} {self._active_status} ticket{"s" if count != 1 else ""} '
                            f'{"matched the search" if self._active_search else "loaded"}.'
                            if count else f'No {self._active_status} tickets '
                            f'{"matched the search" if self._active_search else "found"}.')
        elif self._active_search:
            self.status.set(f'{count} matching ticket{"s" if count != 1 else ""} found.'
                            if count else 'No matching tickets found.')
        else:
            self.status.set(f'{count} ticket{"s" if count != 1 else ""} loaded.'
                            if count else 'No tickets found.')

    def _stop_session(self):
        if self._closed:
            return False
        dialogs = (self._create_dialog, self._update_dialog, self._delete_dialog, self._technician_window,
                   self._user_window, self._password_dialog, self._notes_window, self._reports_window)
        for dialog in dialogs:
            if dialog is not None and dialog.is_open and dialog.is_saving:
                dialog.focus()
                return False
        self.permissions.revoke()
        for dialog in dialogs:
            if dialog is not None and dialog.is_open:
                dialog.cancel()
        if self._history_window is not None and self._history_window.is_open:
            self._history_window.close()
        self._closed = True
        self.dashboard.close()
        if self._poll_id is not None:
            self.root.after_cancel(self._poll_id)
            self._poll_id = None
        self.user = None
        return True

    def logout(self):
        if self._on_logout is not None and self._stop_session():
            self.content.destroy()
            self._on_logout()

    def close(self):
        if self._stop_session():
            self.root.destroy()


def main():
    root = tk.Tk()
    HelpDeskApplication(root, TicketViewer)
    root.mainloop()


if __name__ == '__main__':
    main()
