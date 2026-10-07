"""Admin-only report preview and Excel export; database work stays in repositories."""
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from gui_styles import ALTERNATE_ROW, BODY_FONT, BUTTON_GAP, WINDOW_PADDING

from gui_permissions import PERMISSION_DENIED, SessionPermissions, require_permission
from report_export import (
    REPORT_COLUMNS, ReportExportError, export_ticket_report, report_generated_at, report_row_values,
)
from report_repository import (
    ReportPermissionError, ReportReadError, authorize_report_access, get_report_technicians,
    get_ticket_report, validate_report_filters,
)
from ticket_repository import CATEGORIES, PRIORITIES, STATUSES
from user_repository import public_user


class ReportsWindow:
    """A modeless preview with independent filters and an exportable data snapshot."""

    def __init__(self, parent, user, permissions=None):
        self.parent = parent
        self.user = public_user(user) if user is not None else None
        self.permissions = permissions if permissions is not None else SessionPermissions(self.user)
        if (not self.permissions.allows('reports') or self.user is None
                or self.user.get('role') != 'Admin' or self.user.get('status') != 'Active'):
            raise ReportPermissionError(PERMISSION_DENIED)
        self._results = Queue()
        self._closed = False
        self._loading = False
        self._exporting = False
        self._poll_id = None
        self._has_report = False
        self._rows = ()
        self._applied_filters = {}
        self._technician_choices = [(None, 'All')]
        self._filter_widgets = []
        self.window = tk.Toplevel(parent)
        self.window.title('Ticket Reports')
        self.window.geometry('1250x720')
        self.window.minsize(900, 540)
        self.window.resizable(True, True)
        self.window.transient(parent)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.bind('<Escape>', lambda event: self.close())
        self.fields = {field: tk.StringVar(master=self.window, value='All')
                       for field in ('status', 'priority', 'category')}
        self.applied = tk.StringVar(master=self.window, value='Generate a report to apply filters.')
        self.feedback = tk.StringVar(master=self.window, value='Ready.')
        self._build_widgets()
        self.generate_report()

    @property
    def is_open(self):
        return not self._closed

    @property
    def is_saving(self):
        return self._exporting

    def _build_widgets(self):
        content = ttk.Frame(self.window, padding=WINDOW_PADDING, style='Helpdesk.TFrame')
        content.grid(row=0, column=0, sticky='nsew')
        content.columnconfigure(0, weight=1)
        content.rowconfigure(4, weight=1)
        ttk.Label(content, text='Ticket Reports', style='Helpdesk.Section.TLabel').grid(
            row=0, column=0, sticky='w', pady=(0, 16))
        filters = ttk.Frame(content, style='Helpdesk.TFrame')
        filters.grid(row=1, column=0, sticky='ew', pady=(0, 16))
        for column, (field, label, values) in enumerate((
            ('status', 'Status', STATUSES), ('priority', 'Priority', PRIORITIES),
            ('category', 'Category', CATEGORIES), ('technician', 'Assigned Technician', ()),
        )):
            filters.columnconfigure(column, weight=1)
            ttk.Label(filters, text=label, style='Helpdesk.Status.TLabel').grid(
                row=0, column=column, sticky='w', padx=(0, 12), pady=(0, 6))
            combo = ttk.Combobox(filters, values=('All',) + values, state='readonly',
                                 width=30 if field == 'technician' else 18, font=BODY_FONT,
                                 **({'textvariable': self.fields[field]} if field != 'technician' else {}))
            combo.grid(row=1, column=column, sticky='ew', padx=(0, 12))
            self._filter_widgets.append(combo)
            if field == 'technician':
                self.technician_combo = combo
                combo.current(0)
        controls = ttk.Frame(content, style='Helpdesk.TFrame')
        controls.grid(row=2, column=0, sticky='ew', pady=(0, 12))
        controls.columnconfigure(2, weight=1)
        self.generate_button = ttk.Button(controls, text='Generate Report', command=self.generate_report,
                                          style='Helpdesk.TButton')
        self.generate_button.grid(row=0, column=0, padx=(0, BUTTON_GAP))
        self.export_button = ttk.Button(controls, text='Export to Excel', command=self.export_report,
                                        style='Helpdesk.TButton')
        self.export_button.grid(row=0, column=1, padx=(0, BUTTON_GAP))
        self.export_button.state(['disabled'])
        self.close_button = ttk.Button(controls, text='Close', command=self.close, style='Helpdesk.TButton')
        self.close_button.grid(row=0, column=3)
        ttk.Label(content, textvariable=self.applied, wraplength=1100, style='Helpdesk.Status.TLabel').grid(
            row=3, column=0, sticky='ew', pady=(0, 12))
        table = ttk.Frame(content)
        table.grid(row=4, column=0, sticky='nsew')
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=tuple(field for field, _, _ in REPORT_COLUMNS),
                                 show='headings', selectmode='browse', style='Helpdesk.Treeview')
        for field, heading, width in REPORT_COLUMNS:
            self.tree.heading(field, text=heading)
            self.tree.column(field, width=int(width * 8), minwidth=80, stretch=field == 'subject', anchor='w')
        self.tree.tag_configure('alternate', background=ALTERNATE_ROW)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        ttk.Label(content, textvariable=self.feedback, wraplength=1100, style='Helpdesk.Status.TLabel').grid(
            row=5, column=0, sticky='ew', pady=(12, 0))

    def _selected_filters(self):
        selection = self.technician_combo.current()
        if not 0 <= selection < len(self._technician_choices):
            raise ValueError('Choose All or one of the listed technicians.')
        technician_id, label = self._technician_choices[selection]
        filters = validate_report_filters(*(self.fields[field].get() for field in ('status', 'priority', 'category')),
                                          assigned_technician_id=technician_id)
        labels = {field.title(): filters[field] or 'All' for field in ('status', 'priority', 'category')}
        labels['Assigned Technician'] = label
        return filters, labels

    def _set_busy(self):
        busy = self._loading or self._exporting
        allowed = self.permissions.allows('reports')
        for widget in self._filter_widgets:
            widget.configure(state='disabled' if busy or not allowed else 'readonly')
        self.generate_button.state(['disabled'] if busy or not allowed else ['!disabled'])
        self.export_button.state(['!disabled'] if not busy and self._has_report
                                 and self.permissions.allows('export_reports') else ['disabled'])
        self.close_button.state(['disabled'] if self._exporting else ['!disabled'])

    def generate_report(self):
        if self._closed or self._loading or self._exporting:
            return
        if not require_permission(self.permissions, 'reports', self.window):
            return
        try:
            filters, labels = self._selected_filters()
        except ValueError as error:
            self.feedback.set(str(error))
            return
        self._loading = True
        self._set_busy()
        self.feedback.set('Loading ticket report...')
        Thread(target=self._load_report, args=(filters, labels), daemon=True).start()
        self._poll_id = self.window.after(100, self._check_results)

    def _load_report(self, filters, labels):
        try:
            if not self.permissions.allows('reports'):
                raise ReportPermissionError(PERMISSION_DENIED)
            technicians = get_report_technicians(self.user['user_id'])
            if not self.permissions.allows('reports'):
                raise ReportPermissionError(PERMISSION_DENIED)
            rows = get_ticket_report(self.user['user_id'], **filters)
        except (ReportReadError, ValueError) as error:
            self._results.put(('report', None, str(error)))
        except Exception:
            self._results.put(('report', None, 'Unable to load reports. Please try Generate Report again.'))
        else:
            self._results.put(('report', (rows, technicians, filters, labels), None))

    def _display_report(self, rows, technicians, filters, labels):
        self._technician_choices = [(None, 'All')] + [
            (technician['technician_id'], f'{technician["full_name"]} (ID: {technician["technician_id"]}'
             + (', Inactive)' if technician['status'] == 'Inactive' else ')')) for technician in technicians]
        self.technician_combo.configure(values=tuple(label for _, label in self._technician_choices))
        self.technician_combo.current(next((index for index, (tech_id, _) in enumerate(self._technician_choices)
                                           if tech_id == filters['assigned_technician_id']), 0))
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self._rows = tuple({field: row.get(field) for field, _, _ in REPORT_COLUMNS} for row in rows)
        self._applied_filters = dict(labels)
        self._has_report = True
        for index, row in enumerate(self._rows):
            self.tree.insert('', 'end', iid=str(row['ticket_id']), values=report_row_values(row),
                             tags=('alternate',) if index % 2 else ())
        self.applied.set('Applied filters: ' + ' | '.join(f'{label}: {value}' for label, value in labels.items()))
        count = len(self._rows)
        self.feedback.set(f'{count} ticket{"s" if count != 1 else ""} found.' if count else '0 tickets found. No matching tickets.')

    def export_report(self):
        if self._closed or self._loading or self._exporting:
            return
        if not require_permission(self.permissions, 'export_reports', self.window):
            return
        if not self._has_report:
            messagebox.showinfo('Generate a Report', 'Please generate a report before exporting.', parent=self.window)
            return
        path = filedialog.asksaveasfilename(parent=self.window, title='Export Ticket Report',
                                           defaultextension='.xlsx', filetypes=[('Excel Workbook', '*.xlsx')],
                                           initialfile=f'helpdesk_ticket_report_{report_generated_at():%Y-%m-%d}.xlsx',
                                           confirmoverwrite=True)
        if not path or self._closed:
            return
        if not require_permission(self.permissions, 'export_reports', self.window):
            return
        rows, labels = tuple(dict(row) for row in self._rows), dict(self._applied_filters)
        self._exporting = True
        self._set_busy()
        self.feedback.set('Exporting ticket report...')
        Thread(target=self._export_report, args=(path, rows, labels), daemon=True).start()
        self._poll_id = self.window.after(100, self._check_results)

    def _export_report(self, path, rows, labels):
        try:
            if not self.permissions.allows('export_reports'):
                raise ReportPermissionError(PERMISSION_DENIED)
            authorize_report_access(self.user['user_id'])
            if not self.permissions.allows('export_reports'):
                raise ReportPermissionError(PERMISSION_DENIED)
            saved_path = export_ticket_report(path, rows, labels)
        except (ReportReadError, ReportExportError, ValueError) as error:
            self._results.put(('export', None, str(error)))
        except Exception:
            self._results.put(('export', None, 'Unable to export the report. Please choose another location and try again.'))
        else:
            self._results.put(('export', saved_path, None))

    def _check_results(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            kind, payload, error = self._results.get_nowait()
        except Empty:
            self._poll_id = self.window.after(100, self._check_results)
            return
        if kind == 'report':
            self._loading = False
        else:
            self._exporting = False
        if not self.permissions.allows('reports'):
            children = self.tree.get_children()
            if children:
                self.tree.delete(*children)
            self._rows = ()
            self._has_report = False
            self.feedback.set(PERMISSION_DENIED)
        elif error is not None:
            # Keep the previous complete preview and its applied filters on failure.
            self.feedback.set(error)
        elif kind == 'report':
            self._display_report(*payload)
        else:
            self.feedback.set(f'Report exported successfully: {payload}')
            messagebox.showinfo('Report Exported', f'Report exported successfully.\nSaved to: {payload}', parent=self.window)
        self._set_busy()

    def focus(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_set()

    def cancel(self):
        self.close()

    def close(self):
        if self._closed:
            return
        if self._exporting:
            self.focus()
            return
        self._closed = True
        self._rows = ()
        if self._poll_id is not None:
            self.window.after_cancel(self._poll_id)
            self._poll_id = None
        self.window.destroy()
