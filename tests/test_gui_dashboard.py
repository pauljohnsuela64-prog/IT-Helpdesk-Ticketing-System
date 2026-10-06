"""Dashboard loading and refresh integration without Tk windows or MySQL."""
from contextlib import ExitStack
from queue import Queue
import unittest
from unittest.mock import MagicMock, patch

import gui_app as gui
import gui_dashboard as dashboard_gui
import gui_technicians as tech_gui
from dashboard_repository import STATISTIC_FIELDS
from test_gui_app import ticket, viewer_without_window
from test_gui_create_ticket import dialog_without_window as create_dialog
from test_gui_update_ticket import dialog_without_window as update_dialog
from test_gui_delete_ticket import dialog_without_window as delete_dialog
from test_gui_technicians import dialog_without_window as technician_dialog


def statistics():
    return dict(total_tickets=25, open_tickets=6, assigned_tickets=4, in_progress_tickets=5,
                resolved_tickets=8, closed_tickets=2, critical_tickets=3, active_technicians=4)


def dashboard_without_widgets():
    panel = dashboard_gui.DashboardPanel.__new__(dashboard_gui.DashboardPanel)
    panel.frame = MagicMock()
    panel.counts = {field: MagicMock() for field in STATISTIC_FIELDS}
    panel.feedback = MagicMock()
    panel.filter_label = MagicMock()
    panel._on_status_filter = MagicMock()
    panel._results = Queue()
    panel._loading = False
    panel._closed = False
    panel._poll_id = None
    panel._refresh_pending = False
    return panel


class DashboardLoadingTests(unittest.TestCase):
    def test_worker_uses_aggregate_repository_without_accessing_widgets(self):
        panel = dashboard_without_widgets()
        with patch.object(dashboard_gui, 'get_dashboard_statistics', return_value=statistics()) as read:
            panel._load_statistics()
        read.assert_called_once_with()
        self.assertEqual(panel._results.get_nowait(), (statistics(), None))
        self.assertEqual(panel.frame.mock_calls, [])
        self.assertEqual(panel.feedback.mock_calls, [])
        for value in panel.counts.values():
            self.assertEqual(value.mock_calls, [])

    def test_success_and_empty_counts_update_every_card_including_zero(self):
        for counts in (statistics(), dict.fromkeys(STATISTIC_FIELDS, 0)):
            with self.subTest(counts=counts):
                panel = dashboard_without_widgets()
                panel._loading = True
                panel._results.put((counts, None))
                panel._check_refresh()
                for field, value in counts.items():
                    panel.counts[field].set.assert_called_once_with(str(value))
                self.assertFalse(panel._loading)
                panel.filter_label.set.assert_not_called()

    def test_refresh_starts_one_worker_and_multiple_requests_coalesce_one_followup(self):
        panel = dashboard_without_widgets()
        with patch.object(dashboard_gui, 'Thread') as worker:
            panel.refresh()
            panel.refresh()
            panel.refresh()
            worker.assert_called_once_with(target=panel._load_statistics, daemon=True)
            self.assertTrue(panel._refresh_pending)
            panel._results.put((statistics(), None))
            panel._check_refresh()
            self.assertEqual(worker.call_count, 2)
        self.assertFalse(panel._refresh_pending)
        self.assertTrue(panel._loading)
        for value in panel.counts.values():
            value.set.assert_not_called()
        panel._results.put(({**statistics(), 'total_tickets': 26}, None))
        panel._check_refresh()
        panel.counts['total_tickets'].set.assert_called_once_with('26')

    def test_stale_error_after_a_save_is_discarded_before_fresh_read(self):
        panel = dashboard_without_widgets()
        panel._loading = True
        panel.refresh()
        panel._results.put((None, 'Old read failed.'))
        with patch.object(dashboard_gui, 'Thread') as worker:
            panel._check_refresh()
        worker.assert_called_once_with(target=panel._load_statistics, daemon=True)
        self.assertEqual(panel.feedback.set.call_args.args[0], 'Refreshing dashboard...')

    def test_read_error_retains_counts_and_filter_allows_retry_and_hides_unexpected_details(self):
        for error in (dashboard_gui.DashboardReadError('Check MySQL.'), RuntimeError('private details')):
            with self.subTest(error=type(error).__name__):
                panel = dashboard_without_widgets()
                with patch.object(dashboard_gui, 'get_dashboard_statistics', side_effect=error):
                    panel._load_statistics()
                panel._check_refresh()
                message = panel.feedback.set.call_args.args[0]
                self.assertIn('Unable to load dashboard', message)
                self.assertNotIn('private details', message)
                for value in panel.counts.values():
                    value.set.assert_not_called()
                panel.filter_label.set.assert_not_called()
                self.assertFalse(panel._loading)
                with patch.object(dashboard_gui, 'Thread') as worker:
                    panel.refresh()
                worker.return_value.start.assert_called_once_with()

    def test_empty_queue_polls_without_changing_counts(self):
        panel = dashboard_without_widgets()
        panel._check_refresh()
        panel.frame.after.assert_called_once_with(100, panel._check_refresh)
        panel.feedback.set.assert_not_called()

    def test_close_cancels_poll_and_ignores_late_reads_refresh_and_selection(self):
        panel = dashboard_without_widgets()
        panel._poll_id = 'pending'
        panel.close()
        panel.close()
        panel._results.put((statistics(), None))
        with patch.object(dashboard_gui, 'Thread') as worker:
            panel._check_refresh()
            panel.refresh()
            panel.set_status_filter('Open')
            panel._select_status('Open', MagicMock())
        worker.assert_not_called()
        panel.frame.after_cancel.assert_called_once_with('pending')
        panel._on_status_filter.assert_not_called()
        panel.filter_label.set.assert_not_called()
        for value in panel.counts.values():
            value.set.assert_not_called()


class DashboardConstructionTests(unittest.TestCase):
    def test_eight_initially_unknown_cards_and_mouse_keyboard_filter_bindings(self):
        with ExitStack() as stack:
            labels = stack.enter_context(patch.object(dashboard_gui.ttk, 'Label'))
            frames = stack.enter_context(patch.object(dashboard_gui.ttk, 'Frame'))
            variables = stack.enter_context(patch.object(dashboard_gui.tk, 'StringVar'))
            worker = stack.enter_context(patch.object(dashboard_gui, 'Thread'))
            callback = MagicMock()
            panel = dashboard_gui.DashboardPanel(MagicMock(), callback)
        titles = [item.kwargs.get('text') for item in labels.call_args_list if 'text' in item.kwargs]
        self.assertEqual(titles[2:], ['Total Tickets', 'Open', 'Assigned', 'In Progress',
                                    'Resolved', 'Closed', 'Critical Priority', 'Active Technicians'])
        self.assertEqual(len(panel.counts), 8)
        self.assertEqual(sum(item.kwargs.get('value') == '—' for item in variables.call_args_list), 8)
        # Critical and technician cards have no misleading ticket status filter.
        mouse = [item.args[1] for item in frames.return_value.bind.call_args_list if item.args[0] == '<Button-1>']
        keyboard = [item.args[1] for item in frames.return_value.bind.call_args_list if item.args[0] == '<Return>']
        for action in (mouse, keyboard):
            for handler in action:
                handler(MagicMock())
            self.assertEqual([item.args[0] for item in callback.call_args_list],
                             ['', 'Open', 'Assigned', 'In Progress', 'Resolved', 'Closed'])
            callback.reset_mock()
        worker.assert_not_called()

    def test_selected_status_readout_identifies_exact_filter(self):
        panel = dashboard_without_widgets()
        panel.set_status_filter('In Progress')
        panel.filter_label.set.assert_called_once_with('Status filter: In Progress')
        panel.set_status_filter('')
        self.assertEqual(panel.filter_label.set.call_args.args[0], 'Status filter: All statuses')


class ViewerDashboardIntegrationTests(unittest.TestCase):
    def test_manual_refresh_loads_dashboard_and_current_search_independently(self):
        viewer = viewer_without_window()
        viewer._active_search = '3'
        with patch.object(gui, 'Thread') as worker:
            viewer.refresh_tickets()
        viewer.dashboard.refresh.assert_called_once_with()
        worker.assert_called_once_with(target=viewer._load_tickets, args=('3',), daemon=True)

    def test_successful_create_update_delete_callbacks_refresh_cards_and_preserve_both_filters(self):
        for kind in ('create', 'update', 'delete'):
            with self.subTest(kind=kind):
                viewer = viewer_without_window()
                viewer._active_search = 'Printer'
                viewer._active_status = 'Open'
                if kind == 'create':
                    dialog = create_dialog()
                    dialog.on_created = viewer._refresh_after_creation
                    result = 9
                elif kind == 'update':
                    dialog = update_dialog()
                    dialog.on_updated = viewer._request_refresh
                    result = True
                else:
                    dialog = delete_dialog()
                    dialog.on_deleted = viewer._refresh_after_deletion
                    result = True
                dialog._results.put((result, None))
                with patch.object(gui, 'Thread') as worker, patch.object(gui.messagebox, 'showinfo'):
                    if kind == 'delete':
                        dialog._check_delete()
                    else:
                        dialog._check_save()
                viewer.dashboard.refresh.assert_called_once_with()
                worker.assert_called_once_with(target=viewer._load_tickets, args=('Printer',), daemon=True)
                self.assertEqual((viewer._active_search, viewer._active_status), ('Printer', 'Open'))

    def test_save_during_table_read_refreshes_dashboard_immediately_and_queues_table_refresh(self):
        viewer = viewer_without_window()
        viewer._loading = True
        viewer._refresh_after_creation()
        viewer.dashboard.refresh.assert_called_once_with()
        self.assertTrue(viewer._refresh_pending)

    def test_dashboard_failure_does_not_prevent_ticket_table_success_and_reverse(self):
        viewer = viewer_without_window()
        panel = dashboard_without_widgets()
        viewer.dashboard = panel
        panel._results.put((None, 'Check MySQL.'))
        panel._check_refresh()
        viewer._results.put(([ticket()], None))
        viewer._check_refresh()
        viewer.tree.insert.assert_called_once()
        viewer._results.put((None, 'Ticket read failed.'))
        viewer._check_refresh()
        panel._results.put((statistics(), None))
        panel._check_refresh()
        panel.counts['total_tickets'].set.assert_called_once_with('25')

    def test_main_close_closes_panel_but_waiting_for_save_does_not(self):
        for saving in (False, True):
            with self.subTest(saving=saving):
                viewer = viewer_without_window()
                viewer._update_dialog = MagicMock(is_open=True, is_saving=saving)
                viewer.close()
                if saving:
                    viewer.dashboard.close.assert_not_called()
                else:
                    viewer.dashboard.close.assert_called_once_with()


class StatusFilteringTests(unittest.TestCase):
    def test_status_card_preserves_search_and_filters_exact_status_not_subject_text(self):
        viewer = viewer_without_window()
        viewer._active_search = 'Printer'
        with patch.object(gui, 'Thread'):
            viewer.filter_by_status('Open')
        viewer.dashboard.set_status_filter.assert_called_once_with('Open')
        rows = [ticket(), {**ticket(8), 'status': 'Resolved', 'subject': 'Open application'},
                {**ticket(9), 'status': 'Reopened'}]
        viewer._results.put((rows, None))
        viewer._check_refresh()
        self.assertEqual([item.kwargs['iid'] for item in viewer.tree.insert.call_args_list], ['7'])
        self.assertEqual(viewer._active_search, 'Printer')
        self.assertEqual(viewer.status.set.call_args.args[0], '1 Open ticket matched the search.')

    def test_every_status_filter_and_friendly_empty_result(self):
        rows = [{**ticket(index), 'status': status} for index, status in enumerate(gui.STATUSES, 1)]
        for status in gui.STATUSES:
            with self.subTest(status=status):
                viewer = viewer_without_window()
                viewer._active_status = status
                viewer._display_tickets(rows)
                self.assertEqual(viewer.tree.insert.call_count, 1)
                self.assertEqual(viewer.tree.insert.call_args.kwargs['values'][6], status)
                viewer.tree.reset_mock()
                viewer._display_tickets([])
                viewer.tree.insert.assert_not_called()
                self.assertEqual(viewer.status.set.call_args.args[0], f'No {status} tickets found.')

    def test_total_card_clears_only_status_and_clear_search_restores_all_tickets(self):
        viewer = viewer_without_window()
        viewer._active_search = 'Hardware'
        viewer._active_status = 'Resolved'
        with patch.object(gui, 'Thread'):
            viewer.filter_by_status('')
        self.assertEqual((viewer._active_search, viewer._active_status), ('Hardware', ''))
        viewer._active_status = 'Closed'
        viewer.clear_search()
        self.assertEqual((viewer._active_search, viewer._active_status), ('', ''))
        viewer.search_term.set.assert_called_once_with('')

    def test_filter_change_during_read_uses_latest_status_without_showing_old_view(self):
        viewer = viewer_without_window()
        viewer._loading = True
        viewer.filter_by_status('Open')
        viewer.filter_by_status('Resolved')
        viewer._results.put(([ticket()], None))
        with patch.object(gui, 'Thread') as worker:
            viewer._check_refresh()
        viewer.tree.insert.assert_not_called()
        worker.assert_called_once()
        viewer._results.put(([ticket(), {**ticket(8), 'status': 'Resolved'}], None))
        viewer._check_refresh()
        self.assertEqual(viewer.tree.insert.call_args.kwargs['iid'], '8')

    def test_invalid_status_and_closed_viewer_cannot_start_refresh(self):
        viewer = viewer_without_window()
        with patch.object(gui, 'Thread') as worker:
            viewer.filter_by_status('Open OR 1=1')
            viewer._closed = True
            viewer.filter_by_status('Open')
        worker.assert_not_called()
        viewer.dashboard.set_status_filter.assert_not_called()


class TechnicianCallbackTests(unittest.TestCase):
    def test_manager_is_connected_to_dashboard_refresh(self):
        viewer = viewer_without_window()
        with patch.object(gui, 'TechnicianManagementWindow') as manager:
            viewer.open_technician_management()
        manager.assert_called_once_with(viewer.root, on_change=viewer.dashboard.refresh,
                                        permissions=viewer.permissions)

    def test_add_and_status_save_notify_dashboard_after_success_including_resync_after_noop(self):
        for status_dialog, result in ((False, 42), (True, True), (True, False)):
            with self.subTest(status_dialog=status_dialog, result=result):
                dialog = technician_dialog(status_dialog)
                notify = MagicMock()
                dialog.manager._on_change = notify
                dialog._results.put((result, None))
                with patch.object(dialog.manager, '_request_refresh'), patch.object(tech_gui.messagebox, 'showinfo'):
                    dialog._check_save()
                    dialog._check_save()
                notify.assert_called_once_with()

    def test_error_cancel_or_unchanged_unsaved_status_does_not_notify_dashboard(self):
        for action in ('error', 'cancel', 'unchanged'):
            with self.subTest(action=action):
                dialog = technician_dialog(True)
                notify = MagicMock()
                dialog.manager._on_change = notify
                if action == 'error':
                    dialog._results.put((None, 'Check MySQL.'))
                    dialog._check_save()
                elif action == 'cancel':
                    dialog.cancel()
                else:
                    dialog.status.get.return_value = 'Active'
                    dialog.save()
                notify.assert_not_called()

    def test_closed_manager_does_not_notify_dashboard(self):
        dialog = technician_dialog()
        dialog.manager._on_change = MagicMock()
        dialog.manager._closed = True
        dialog.manager._notify_change()
        dialog.manager._on_change.assert_not_called()


if __name__ == '__main__':
    unittest.main()
