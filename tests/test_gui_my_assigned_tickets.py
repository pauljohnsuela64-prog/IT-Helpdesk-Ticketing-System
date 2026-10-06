"""GUI view switches, search/refresh state, and session isolation without interactive Tk."""
from contextlib import ExitStack
import unittest
from unittest.mock import MagicMock, patch

import gui_app as gui
from gui_permissions import SessionPermissions
from test_gui_app import ticket, viewer_without_window
from test_gui_authentication import account
from test_gui_permissions import mock_viewer_widgets
from ticket_repository import AssignedTicketsLinkError, MISSING_TECHNICIAN_LINK, TicketReadError


def technician_viewer(link=12):
    viewer = viewer_without_window()
    viewer.user = {**account('Technician'), 'technician_id': link}
    viewer.permissions = SessionPermissions(viewer.user)
    viewer.view_status = MagicMock()
    return viewer


class AssignedViewControlsTests(unittest.TestCase):
    def test_controls_only_appear_for_technicians_and_share_existing_table(self):
        for role in ('Admin', 'Technician'):
            with ExitStack() as stack:
                mock_viewer_widgets(stack)
                trees = stack.enter_context(patch.object(gui.ttk, 'Treeview'))
                buttons = stack.enter_context(patch.object(gui.ttk, 'Button', side_effect=lambda *args, **kwargs: MagicMock()))
                viewer = gui.TicketViewer(MagicMock(), user={**account(role), 'technician_id': 12}, on_logout=MagicMock())
            labels = {call.kwargs['text'] for call in buttons.call_args_list}
            self.assertEqual('My Assigned Tickets' in labels, role == 'Technician')
            self.assertEqual('All Tickets' in labels, role == 'Technician')
            self.assertFalse(viewer._assigned_view)
            trees.assert_called_once()

    def test_switch_uses_linked_technician_id_and_preserves_search_and_status(self):
        viewer = technician_viewer()
        viewer.tree.get_children.return_value = ('other-ticket',)
        viewer._active_search = '3'
        viewer._active_status = 'In Progress'
        with patch.object(gui, 'Thread') as worker:
            viewer.show_assigned_tickets()
        self.assertTrue(viewer._assigned_view)
        self.assertEqual(viewer._active_status, 'In Progress')
        self.assertEqual(viewer._active_search, '3')
        worker.assert_called_once_with(target=viewer._load_tickets, args=('3', 12, True), daemon=True)
        viewer.tree.delete.assert_called_once_with('other-ticket')
        viewer.view_status.set.assert_called_once_with('View: My Assigned Tickets')

    def test_switch_back_all_keeps_filters_and_uses_existing_repository_path(self):
        viewer = technician_viewer()
        viewer._assigned_view = True
        viewer._active_search = 'Hardware'
        with patch.object(gui, 'Thread') as worker:
            viewer.show_all_tickets()
        self.assertFalse(viewer._assigned_view)
        self.assertEqual(viewer._active_search, 'Hardware')
        worker.assert_called_once_with(target=viewer._load_tickets, args=('Hardware',), daemon=True)
        viewer.view_status.set.assert_called_once_with('View: All Tickets')

    def test_admin_unknown_and_revoked_sessions_do_not_load_my_view(self):
        for role in ('Admin', 'Unknown', 'Technician'):
            viewer = technician_viewer()
            viewer.user = {**account(role), 'technician_id': 12}
            viewer.permissions = SessionPermissions(viewer.user)
            if role == 'Technician':
                viewer.permissions.revoke()
            with patch.object(gui, 'Thread') as worker, patch.object(gui.messagebox, 'showinfo'):
                viewer.show_assigned_tickets()
            worker.assert_not_called()
            self.assertFalse(viewer._assigned_view)

    def test_missing_link_still_allows_all_view_and_update_permissions_are_unchanged(self):
        viewer = technician_viewer(None)
        self.assertTrue(viewer.permissions.allows('update_ticket'))
        self.assertFalse(viewer.permissions.allows('delete_ticket'))
        self.assertFalse(viewer.permissions.allows('manage_users'))
        with patch.object(gui, 'Thread') as worker:
            viewer.show_assigned_tickets()
        self.assertEqual(worker.call_args.kwargs['args'], ('', None, True))
        viewer._loading = False
        with patch.object(gui, 'Thread') as worker:
            viewer.show_all_tickets()
        worker.assert_called_once_with(target=viewer._load_tickets, args=('',), daemon=True)

    def test_update_handler_passes_session_to_dialog_for_ownership_checks_in_all_view(self):
        viewer = technician_viewer()
        viewer.tree.selection.return_value = ('987',)
        with patch.object(gui, 'UpdateTicketDialog') as form:
            viewer.open_update_ticket()
        form.assert_called_once_with(viewer.root, 987, viewer._request_refresh,
                                    permissions=viewer.permissions, user=viewer.user)


class AssignedSearchRefreshTests(unittest.TestCase):
    def setUp(self):
        self.viewer = technician_viewer()
        self.viewer._assigned_view = True

    def test_search_and_enter_trim_numeric_term_in_my_view(self):
        self.viewer.search_term.get.return_value = ' 3 '
        with patch.object(gui, 'Thread') as worker:
            self.assertEqual(self.viewer.perform_search(MagicMock()), 'break')
        worker.assert_called_once_with(target=self.viewer._load_tickets, args=('3', 12, True), daemon=True)
        self.assertTrue(self.viewer._assigned_view)

    def test_clear_search_restores_full_my_list_and_resets_only_search_status_filters(self):
        self.viewer._active_search = 'PC 3'
        self.viewer._active_status = 'Open'
        with patch.object(gui, 'Thread') as worker:
            self.viewer.clear_search()
        self.assertEqual(self.viewer._active_search, '')
        self.assertEqual(self.viewer._active_status, '')
        self.assertTrue(self.viewer._assigned_view)
        worker.assert_called_once_with(target=self.viewer._load_tickets, args=('', 12, True), daemon=True)

    def test_refresh_captures_session_id_view_and_submitted_search(self):
        self.viewer._active_search = 'Hardware'
        self.viewer.search_term.get.return_value = 'unsubmitted term'
        with patch.object(gui, 'Thread') as worker:
            self.viewer.refresh_tickets()
        worker.assert_called_once_with(target=self.viewer._load_tickets, args=('Hardware', 12, True), daemon=True)
        self.viewer.search_term.get.assert_not_called()
        self.assertTrue(self.viewer._loading_assigned_view)
        self.viewer.dashboard.refresh.assert_called_once_with()

    def test_worker_uses_captured_parameters_without_reading_live_gui_or_session_state(self):
        self.viewer._assigned_view = False
        self.viewer.user['technician_id'] = 35
        with patch.object(gui, 'get_assigned_tickets', return_value=[ticket()]) as read, \
             patch.object(gui, 'get_tickets') as all_tickets, patch.object(gui, 'search_tickets') as all_search:
            self.viewer._load_tickets('3', 12, True)
        read.assert_called_once_with(12, '3')
        all_tickets.assert_not_called()
        all_search.assert_not_called()
        self.assertEqual(self.viewer._results.get_nowait(), ([ticket()], None))
        for widget in (self.viewer.root, self.viewer.tree, self.viewer.search_term, self.viewer.view_status):
            self.assertEqual(widget.mock_calls, [])

    def test_old_all_result_is_discarded_after_switching_to_my_view(self):
        viewer = technician_viewer()
        viewer._loading = True
        viewer._loading_assigned_view = False
        viewer.show_assigned_tickets()
        viewer._results.put(([ticket(999)], None))
        with patch.object(gui, 'Thread') as worker:
            viewer._check_refresh()
        viewer.tree.insert.assert_not_called()
        worker.assert_called_once_with(target=viewer._load_tickets, args=('', 12, True), daemon=True)

    def test_old_my_result_is_discarded_after_switching_to_all_view(self):
        self.viewer._loading = True
        self.viewer._loading_assigned_view = True
        self.viewer.show_all_tickets()
        self.viewer._results.put(([ticket(7)], None))
        with patch.object(gui, 'Thread') as worker:
            self.viewer._check_refresh()
        self.viewer.tree.insert.assert_not_called()
        worker.assert_called_once_with(target=self.viewer._load_tickets, args=('',), daemon=True)

    def test_rapid_view_switches_and_search_apply_only_the_latest_request(self):
        viewer = technician_viewer()
        viewer._loading = True
        viewer._loading_assigned_view = False
        viewer.show_assigned_tickets()
        viewer.show_all_tickets()
        viewer.show_assigned_tickets()
        viewer.search_term.get.return_value = ' 3 '
        viewer.perform_search()
        viewer._results.put((None, 'Stale read failed.'))
        with patch.object(gui, 'Thread') as worker:
            viewer._check_refresh()
        worker.assert_called_once_with(target=viewer._load_tickets, args=('3', 12, True), daemon=True)
        self.assertNotIn('Stale read', viewer.status.set.call_args.args[0])

    def test_save_callbacks_keep_current_scope_and_reassigned_rows_disappear(self):
        for callback in ('_refresh_after_creation', '_request_refresh', '_refresh_after_deletion'):
            viewer = technician_viewer()
            viewer._assigned_view = True
            args = (7,) if callback == '_refresh_after_deletion' else ()
            with patch.object(gui, 'Thread') as worker:
                getattr(viewer, callback)(*args)
            worker.assert_called_once_with(target=viewer._load_tickets, args=('', 12, True), daemon=True)
            viewer._results.put(([], None))
            viewer._check_refresh()
            self.assertEqual(viewer.status.set.call_args.args[0], 'No assigned tickets found.')

    def test_dashboard_status_filter_combines_with_assignment_scope_and_global_counts(self):
        with patch.object(gui, 'Thread') as worker:
            self.viewer.filter_by_status('Resolved')
        worker.assert_called_once_with(target=self.viewer._load_tickets, args=('', 12, True), daemon=True)
        self.viewer._display_tickets([{**ticket(3), 'status': 'Open'}, {**ticket(8), 'status': 'Resolved'}])
        self.assertEqual(self.viewer.tree.insert.call_args.kwargs['iid'], '8')
        self.assertEqual(self.viewer.status.set.call_args.args[0], '1 Resolved assigned ticket loaded.')
        self.viewer.dashboard.refresh.assert_called_once_with()


class AssignedFeedbackSessionTests(unittest.TestCase):
    def test_no_assigned_tickets_and_no_search_matches_have_friendly_messages(self):
        viewer = technician_viewer()
        viewer._assigned_view = True
        viewer._display_tickets([])
        self.assertEqual(viewer.status.set.call_args.args[0], 'No assigned tickets found.')
        viewer._active_search = 'missing'
        viewer._display_tickets([])
        self.assertEqual(viewer.status.set.call_args.args[0], 'No matching assigned tickets found.')

    def test_missing_link_clears_old_rows_without_falling_back_to_all_tickets(self):
        viewer = technician_viewer(None)
        viewer._assigned_view = viewer._loading_assigned_view = True
        viewer.tree.get_children.return_value = ('old-ticket',)
        with patch.object(gui, 'get_assigned_tickets', side_effect=AssignedTicketsLinkError(MISSING_TECHNICIAN_LINK)), \
             patch.object(gui, 'get_tickets') as all_tickets:
            viewer._load_tickets('', None, True)
        viewer._check_refresh()
        self.assertTrue(viewer._assigned_view)
        viewer.status.set.assert_called_once_with(MISSING_TECHNICIAN_LINK)
        viewer.tree.delete.assert_called_once_with('old-ticket')
        viewer.refresh_button.state.assert_called_once_with(['!disabled'])
        all_tickets.assert_not_called()

    def test_assigned_database_error_keeps_same_scope_and_allows_retry(self):
        for error in (TicketReadError('Check MySQL.'), RuntimeError('private database details')):
            viewer = technician_viewer()
            viewer._assigned_view = viewer._loading_assigned_view = True
            with patch.object(gui, 'get_assigned_tickets', side_effect=error):
                viewer._load_tickets('', 12, True)
            viewer._check_refresh()
            self.assertIn('Unable to load tickets.', viewer.status.set.call_args.args[0])
            self.assertNotIn('private database details', viewer.status.set.call_args.args[0])
            self.assertTrue(viewer._assigned_view)
            viewer.refresh_button.state.assert_called_once_with(['!disabled'])
            viewer.tree.delete.assert_not_called()

    def test_logout_ignores_late_assigned_results_and_next_login_starts_in_all_view(self):
        viewer = technician_viewer()
        viewer._assigned_view = viewer._loading_assigned_view = True
        viewer.content = MagicMock()
        viewer._on_logout = MagicMock()
        viewer.logout()
        viewer._results.put(([ticket()], None))
        viewer._check_refresh()
        viewer.tree.insert.assert_not_called()
        with ExitStack() as stack:
            mock_viewer_widgets(stack)
            next_viewer = gui.TicketViewer(MagicMock(), user={**account('Technician'), 'technician_id': 35})
        self.assertFalse(next_viewer._assigned_view)
        self.assertEqual(next_viewer.user['technician_id'], 35)


if __name__ == '__main__':
    unittest.main()
