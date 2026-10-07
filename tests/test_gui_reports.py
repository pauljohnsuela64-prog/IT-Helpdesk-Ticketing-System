"""Report UI, session boundaries, and snapshot exports without interactive Tk."""
from contextlib import ExitStack
from queue import Queue
import unittest
from unittest.mock import MagicMock, patch

import gui_app as app
import gui_permissions as permissions
import gui_reports as gui
from test_gui_app import viewer_without_window
from test_gui_authentication import account
from test_gui_permissions import mock_viewer_widgets
from test_reports import report_ticket


TECHNICIANS = [dict(technician_id=12, full_name='Same Name', status='Active'),
               dict(technician_id=35, full_name='Same Name', status='Inactive')]
ALL_FILTERS = dict(status=None, priority=None, category=None, assigned_technician_id=None)
ALL_LABELS = dict(Status='All', Priority='All', Category='All', **{'Assigned Technician': 'All'})


def report_without_widgets(role='Admin'):
    report = gui.ReportsWindow.__new__(gui.ReportsWindow)
    report.parent = MagicMock()
    report.window = MagicMock()
    report.user = account(role)
    report.permissions = permissions.SessionPermissions(report.user)
    report._results = Queue()
    report._closed = False
    report._loading = False
    report._exporting = False
    report._poll_id = None
    report._has_report = True
    report._rows = (report_ticket(),)
    report._applied_filters = dict(ALL_LABELS)
    report._technician_choices = [(None, 'All'), (12, 'Same Name (ID: 12)'), (35, 'Same Name (ID: 35, Inactive)')]
    report._filter_widgets = [MagicMock() for _ in range(4)]
    report.fields = {field: MagicMock() for field in ('status', 'priority', 'category')}
    for value in report.fields.values():
        value.get.return_value = 'All'
    report.technician_combo = report._filter_widgets[3]
    report.technician_combo.current.return_value = 0
    report.tree = MagicMock()
    report.tree.get_children.return_value = ('old',)
    report.feedback = MagicMock()
    report.applied = MagicMock()
    report.generate_button = MagicMock()
    report.export_button = MagicMock()
    report.close_button = MagicMock()
    return report


class ReportsMainWindowTests(unittest.TestCase):
    def test_button_is_visible_only_for_active_admin_and_every_login_recalculates_permissions(self):
        for role in ('Admin', 'Technician', 'Admin'):
            with self.subTest(role=role), ExitStack() as stack:
                mock_viewer_widgets(stack)
                buttons = stack.enter_context(patch.object(app.ttk, 'Button', side_effect=lambda *args, **kwargs: MagicMock()))
                viewer = app.TicketViewer(MagicMock(), user=account(role), on_logout=MagicMock())
            self.assertEqual('Reports' in {call.kwargs['text'] for call in buttons.call_args_list}, role == 'Admin')
            self.assertEqual(viewer.reports_button is not None, role == 'Admin')
            self.assertEqual(viewer.permissions.allows('export_reports'), role == 'Admin')

    def test_handler_denies_technician_and_revoked_admin_before_opening_window(self):
        for role in ('Technician', 'Admin'):
            viewer = viewer_without_window()
            viewer.permissions = permissions.SessionPermissions(account(role))
            if role == 'Admin':
                viewer.permissions.revoke()
            with patch.object(app, 'ReportsWindow') as create, patch.object(permissions.messagebox, 'showinfo') as message:
                viewer.open_reports()
            create.assert_not_called()
            message.assert_called_once_with('Permission Denied', permissions.PERMISSION_DENIED, parent=viewer.root)

    def test_admin_opens_one_window_with_session_and_preserves_main_filters(self):
        viewer = viewer_without_window()
        viewer._active_search, viewer._active_status, viewer._assigned_view = '3', 'Open', False
        with patch.object(app, 'ReportsWindow') as create:
            create.return_value.is_open = True
            viewer.open_reports()
            viewer.open_reports()
            create.assert_called_once_with(viewer.root, viewer.user, permissions=viewer.permissions)
            create.return_value.focus.assert_called_once_with()
            create.return_value.is_open = False
            viewer.open_reports()
        self.assertEqual(create.call_count, 2)
        self.assertEqual((viewer._active_search, viewer._active_status, viewer._assigned_view), ('3', 'Open', False))

    def test_reports_do_not_replace_modal_ticket_dialogs(self):
        viewer = viewer_without_window()
        viewer._update_dialog = MagicMock(is_open=True)
        with patch.object(app, 'ReportsWindow') as create:
            viewer.open_reports()
        viewer._update_dialog.focus.assert_called_once_with()
        create.assert_not_called()

    def test_logout_closes_report_and_revokes_its_shared_session(self):
        viewer = viewer_without_window()
        report = report_without_widgets()
        report.permissions = viewer.permissions
        viewer._reports_window = report
        self.assertTrue(viewer._stop_session())
        self.assertFalse(report.is_open)
        self.assertFalse(report.permissions.allows('reports'))
        report.window.destroy.assert_called_once_with()

    def test_logout_and_close_wait_for_an_export_to_finish(self):
        viewer = viewer_without_window()
        report = report_without_widgets()
        report.permissions = viewer.permissions
        report._exporting = True
        viewer._reports_window = report
        with patch.object(report, 'focus') as focus:
            self.assertFalse(viewer._stop_session())
            report.close()
        self.assertEqual(focus.call_count, 2)
        self.assertTrue(report.is_open)
        self.assertTrue(viewer.permissions.allows('reports'))
        report._exporting = False
        self.assertTrue(viewer._stop_session())


class ReportPreviewTests(unittest.TestCase):
    def test_generate_captures_selected_ids_and_shared_validated_filters_once(self):
        report = report_without_widgets()
        report.fields['status'].get.return_value = 'Resolved'
        report.fields['priority'].get.return_value = 'High'
        report.fields['category'].get.return_value = 'Network'
        report.technician_combo.current.return_value = 2
        with patch.object(gui, 'Thread') as worker:
            report.generate_report()
            report.generate_report()
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(worker.call_args.kwargs['args'],
                         (dict(status='Resolved', priority='High', category='Network', assigned_technician_id=35),
                          dict(Status='Resolved', Priority='High', Category='Network',
                               **{'Assigned Technician': 'Same Name (ID: 35, Inactive)'})))
        self.assertTrue(report._loading)
        report.export_button.state.assert_called_with(['disabled'])
        self.assertTrue(all(widget.configure.call_args.kwargs['state'] == 'disabled' for widget in report._filter_widgets))

    def test_invalid_filter_and_selection_never_start_database_worker(self):
        for invalid in ('status', 'technician'):
            report = report_without_widgets()
            if invalid == 'status':
                report.fields['status'].get.return_value = 'Unknown'
            else:
                report.technician_combo.current.return_value = -1
            with patch.object(gui, 'Thread') as worker:
                report.generate_report()
            worker.assert_not_called()
            self.assertFalse(report._loading)
            report.feedback.set.assert_called_once()

    def test_worker_reuses_authorized_repositories_and_never_touches_tk(self):
        report = report_without_widgets()
        with patch.object(gui, 'get_report_technicians', return_value=TECHNICIANS) as technicians, \
             patch.object(gui, 'get_ticket_report', return_value=[report_ticket()]) as tickets:
            report._load_report(ALL_FILTERS, ALL_LABELS)
        technicians.assert_called_once_with(report.user['user_id'])
        tickets.assert_called_once_with(report.user['user_id'], **ALL_FILTERS)
        self.assertEqual(report._results.get_nowait(), ('report', ([report_ticket()], TECHNICIANS, ALL_FILTERS, ALL_LABELS), None))
        self.assertEqual(report.window.mock_calls, [])
        self.assertEqual(report.feedback.mock_calls, [])

    def test_successful_preview_replaces_rows_counts_all_records_and_preserves_inactive_choices(self):
        report = report_without_widgets()
        rows = [report_ticket(), {**report_ticket(9), 'status': 'Closed'}]
        report._loading = True
        report._results.put(('report', (rows, TECHNICIANS, ALL_FILTERS, ALL_LABELS), None))
        report._check_results()
        report.tree.delete.assert_called_once_with('old')
        self.assertEqual([call.kwargs['iid'] for call in report.tree.insert.call_args_list], ['3', '9'])
        self.assertEqual(report.tree.insert.call_args_list[0].kwargs['values'], gui.report_row_values(rows[0]))
        report.feedback.set.assert_called_once_with('2 tickets found.')
        self.assertIn('(ID: 35, Inactive)', report.technician_combo.configure.call_args_list[0].kwargs['values'][2])
        self.assertFalse(report._loading)
        report.export_button.state.assert_called_with(['!disabled'])

    def test_empty_report_removes_previous_rows_and_can_export_zero_row_report(self):
        report = report_without_widgets()
        report._results.put(('report', ([], TECHNICIANS, ALL_FILTERS, ALL_LABELS), None))
        report._check_results()
        self.assertEqual(report._rows, ())
        self.assertTrue(report._has_report)
        report.tree.insert.assert_not_called()
        report.feedback.set.assert_called_once_with('0 tickets found. No matching tickets.')
        report.export_button.state.assert_called_with(['!disabled'])

    def test_read_failure_preserves_previous_preview_and_applied_filters(self):
        report = report_without_widgets()
        original = report._rows
        report._loading = True
        report._results.put(('report', None, 'Check the connection.'))
        report._check_results()
        self.assertEqual(report._rows, original)
        self.assertEqual(report._applied_filters, ALL_LABELS)
        report.tree.delete.assert_not_called()
        report.feedback.set.assert_called_once_with('Check the connection.')

    def test_worker_errors_hide_unexpected_private_details(self):
        report = report_without_widgets()
        for failure in (gui.ReportReadError('Check the connection.'), RuntimeError('private database details')):
            with patch.object(gui, 'get_report_technicians', side_effect=failure), patch.object(gui, 'get_ticket_report') as read:
                report._load_report(ALL_FILTERS, ALL_LABELS)
            kind, payload, error = report._results.get_nowait()
            self.assertEqual(kind, 'report')
            self.assertIsNone(payload)
            self.assertNotIn('private', error)
            read.assert_not_called()

    def test_revoked_result_does_not_display_previous_session_data(self):
        report = report_without_widgets()
        report._results.put(('report', ([report_ticket()], TECHNICIANS, ALL_FILTERS, ALL_LABELS), None))
        report.permissions.revoke()
        report._check_results()
        self.assertEqual(report._rows, ())
        self.assertFalse(report._has_report)
        report.tree.insert.assert_not_called()
        report.export_button.state.assert_called_with(['disabled'])

    def test_closing_read_only_window_cancels_poll_and_ignores_late_result(self):
        report = report_without_widgets()
        report._loading = True
        report._poll_id = 'pending-poll'
        report.close()
        report._results.put(('report', ([report_ticket()], TECHNICIANS, ALL_FILTERS, ALL_LABELS), None))
        report._check_results()
        report.window.after_cancel.assert_called_once_with('pending-poll')
        report.window.destroy.assert_called_once_with()
        report.tree.insert.assert_not_called()


class ExportActionTests(unittest.TestCase):
    def test_cancel_save_as_is_silent_and_performs_no_export_or_refresh(self):
        report = report_without_widgets()
        with patch.object(gui.filedialog, 'asksaveasfilename', return_value='') as save_as, \
             patch.object(gui, 'Thread') as worker, patch.object(gui.messagebox, 'showinfo') as message:
            report.export_report()
        self.assertEqual(save_as.call_args.kwargs['defaultextension'], '.xlsx')
        self.assertTrue(save_as.call_args.kwargs['confirmoverwrite'])
        worker.assert_not_called()
        message.assert_not_called()
        report.feedback.set.assert_not_called()
        self.assertFalse(report._exporting)

    def test_export_captures_displayed_snapshot_and_applied_filters_not_changed_dropdowns(self):
        report = report_without_widgets()
        report.fields['status'].get.return_value = 'Closed'
        report._applied_filters = {**ALL_LABELS, 'Status': 'Open'}
        with patch.object(gui.filedialog, 'asksaveasfilename', return_value='chosen report.xlsx'), \
             patch.object(gui, 'Thread') as worker:
            report.export_report()
            report.export_report()
        self.assertEqual(worker.call_count, 1)
        path, rows, labels = worker.call_args.kwargs['args']
        self.assertEqual(path, 'chosen report.xlsx')
        self.assertEqual(rows, report._rows)
        self.assertIsNot(rows[0], report._rows[0])
        self.assertEqual(labels['Status'], 'Open')
        report.fields['status'].get.assert_not_called()
        self.assertTrue(report.is_saving)

    def test_export_requires_current_admin_and_calls_exporter_with_snapshot_only(self):
        report = report_without_widgets()
        with patch.object(gui, 'authorize_report_access') as authorize, \
             patch.object(gui, 'export_ticket_report', return_value='C:/reports/report.xlsx') as save:
            report._export_report('report.xlsx', report._rows, ALL_LABELS)
        authorize.assert_called_once_with(report.user['user_id'])
        save.assert_called_once_with('report.xlsx', report._rows, ALL_LABELS)
        self.assertEqual(report._results.get_nowait(), ('export', 'C:/reports/report.xlsx', None))
        self.assertEqual(report.window.mock_calls, [])

    def test_deactivated_admin_or_revoked_session_cannot_write_file(self):
        report = report_without_widgets()
        with patch.object(gui, 'authorize_report_access', side_effect=gui.ReportPermissionError(permissions.PERMISSION_DENIED)), \
             patch.object(gui, 'export_ticket_report') as save:
            report._export_report('report.xlsx', report._rows, ALL_LABELS)
        save.assert_not_called()
        self.assertEqual(report._results.get_nowait(), ('export', None, permissions.PERMISSION_DENIED))
        report.permissions.revoke()
        with patch.object(gui, 'authorize_report_access') as authorize, patch.object(gui, 'export_ticket_report') as save:
            report._export_report('report.xlsx', report._rows, ALL_LABELS)
        authorize.assert_not_called()
        save.assert_not_called()

    def test_permission_is_rechecked_after_save_dialog_and_before_write(self):
        report = report_without_widgets()

        def save_as(**kwargs):
            report.permissions.revoke()
            return 'report.xlsx'

        with patch.object(gui.filedialog, 'asksaveasfilename', side_effect=save_as), patch.object(gui, 'Thread') as worker, \
             patch.object(permissions.messagebox, 'showinfo') as message:
            report.export_report()
        worker.assert_not_called()
        message.assert_called_once_with('Permission Denied', permissions.PERMISSION_DENIED, parent=report.window)

    def test_handler_and_worker_deny_technician_even_when_called_directly(self):
        report = report_without_widgets('Technician')
        with patch.object(permissions.messagebox, 'showinfo') as message, patch.object(gui, 'Thread') as worker, \
             patch.object(gui.filedialog, 'asksaveasfilename') as save_as, \
             patch.object(gui, 'get_report_technicians') as technicians, patch.object(gui, 'export_ticket_report') as save:
            report.generate_report()
            report.export_report()
            report._load_report(ALL_FILTERS, ALL_LABELS)
            report._export_report('report.xlsx', report._rows, ALL_LABELS)
        self.assertEqual(message.call_count, 2)
        for operation in (worker, save_as, technicians, save):
            operation.assert_not_called()

    def test_export_during_load_and_before_first_preview_does_not_open_save_as(self):
        report = report_without_widgets()
        report._loading = True
        with patch.object(gui.filedialog, 'asksaveasfilename') as save_as:
            report.export_report()
        save_as.assert_not_called()
        report._loading, report._has_report = False, False
        with patch.object(gui.filedialog, 'asksaveasfilename') as save_as, patch.object(gui.messagebox, 'showinfo') as message:
            report.export_report()
        save_as.assert_not_called()
        message.assert_called_once()

    def test_export_success_shows_path_and_retains_preview_for_another_export(self):
        report = report_without_widgets()
        report._exporting = True
        report._results.put(('export', 'C:/reports/my report.xlsx', None))
        with patch.object(gui.messagebox, 'showinfo') as message:
            report._check_results()
        self.assertFalse(report.is_saving)
        self.assertTrue(report.is_open)
        self.assertEqual(report._rows, (report_ticket(),))
        self.assertIn('C:/reports/my report.xlsx', message.call_args.args[1])
        report.export_button.state.assert_called_with(['!disabled'])

    def test_export_failure_keeps_preview_and_does_not_claim_success(self):
        report = report_without_widgets()
        report._exporting = True
        report._results.put(('export', None, 'Unable to save.'))
        with patch.object(gui.messagebox, 'showinfo') as message:
            report._check_results()
        self.assertFalse(report.is_saving)
        self.assertEqual(report._rows, (report_ticket(),))
        report.feedback.set.assert_called_once_with('Unable to save.')
        message.assert_not_called()


class ConstructionTests(unittest.TestCase):
    def test_constructor_rejects_technician_even_with_mismatched_admin_policy(self):
        with patch.object(gui.tk, 'Toplevel') as top:
            for user in (account('Technician'), {**account(), 'status': 'Inactive'}, None):
                with self.subTest(user=user), self.assertRaises(gui.ReportPermissionError):
                    gui.ReportsWindow(MagicMock(), user, permissions=permissions.SessionPermissions(account()))
        top.assert_not_called()

    def test_window_has_readonly_all_filters_full_columns_scrollbars_and_only_report_controls(self):
        with ExitStack() as stack:
            top = stack.enter_context(patch.object(gui.tk, 'Toplevel'))
            for widget in ('Frame', 'Label'):
                stack.enter_context(patch.object(gui.ttk, widget))
            stack.enter_context(patch.object(gui.tk, 'StringVar'))
            combo = stack.enter_context(patch.object(gui.ttk, 'Combobox', side_effect=lambda *args, **kwargs: MagicMock()))
            tree = stack.enter_context(patch.object(gui.ttk, 'Treeview'))
            scrollbar = stack.enter_context(patch.object(gui.ttk, 'Scrollbar'))
            button = stack.enter_context(patch.object(gui.ttk, 'Button', side_effect=lambda *args, **kwargs: MagicMock()))
            generate = stack.enter_context(patch.object(gui.ReportsWindow, 'generate_report'))
            report = gui.ReportsWindow(MagicMock(), account())
        self.assertEqual([call.kwargs['values'] for call in combo.call_args_list],
                         [('All',) + gui.STATUSES, ('All',) + gui.PRIORITIES, ('All',) + gui.CATEGORIES, ('All',)])
        self.assertTrue(all(call.kwargs['state'] == 'readonly' for call in combo.call_args_list))
        self.assertEqual(tree.call_args.kwargs['columns'], tuple(field for field, _, _ in gui.REPORT_COLUMNS))
        self.assertEqual([call.kwargs['orient'] for call in scrollbar.call_args_list], ['vertical', 'horizontal'])
        self.assertEqual([call.kwargs['text'] for call in button.call_args_list], ['Generate Report', 'Export to Excel', 'Close'])
        top.return_value.protocol.assert_called_once_with('WM_DELETE_WINDOW', report.close)
        top.return_value.grab_set.assert_not_called()
        generate.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
