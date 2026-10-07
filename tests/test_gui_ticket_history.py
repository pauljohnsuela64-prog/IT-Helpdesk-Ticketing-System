"""History GUI checks with mocks; no interactive window or live database required."""
import unittest
from datetime import datetime
from queue import Queue
from unittest.mock import MagicMock, call, patch

import gui_app as gui
import gui_ticket_history as history_gui
import ticket_history_repository as history_repository
import ticket_repository as ticket_repository
from test_gui_app import ticket, viewer_without_window


def activities():
    # Chronological IDs need not be numerically ascending.
    return [dict(history_id=42, ticket_id=7, created_at=datetime(2026, 10, 6, 9, 0),
                 action='Ticket Created', details='Priority: Medium, Status: Open'),
            dict(history_id=7, ticket_id=7, created_at=datetime(2026, 10, 6, 9, 15),
                 action='Technician Assigned', details='Mark Santos'),
            dict(history_id=50, ticket_id=7, created_at=datetime(2026, 10, 6, 9, 30),
                 action='Status Changed', details='Open -> In Progress')]


def history_without_window():
    window = history_gui.TicketHistoryWindow.__new__(history_gui.TicketHistoryWindow)
    window.parent = MagicMock()
    window.window = MagicMock()
    window.ticket_id = 7
    window.tree = MagicMock()
    window.tree.selection.return_value = ()
    window.tree.get_children.return_value = ('old-row',)
    window.tree.selection_set.side_effect = lambda row_id: setattr(window.tree.selection, 'return_value', (row_id,))
    window.details = MagicMock()
    window.summary = MagicMock()
    window.feedback = MagicMock()
    window.refresh_button = MagicMock()
    window._results = Queue()
    window._loading = False
    window._closed = False
    window._poll_id = None
    window._entries = {}
    return window


class MainHistoryTests(unittest.TestCase):
    def test_no_selection_or_multiple_selection_does_not_open_or_query_history(self):
        for selection in ((), ('7', '8')):
            with self.subTest(selection=selection):
                viewer = viewer_without_window()
                viewer.tree.selection.return_value = selection
                with patch.object(gui, 'TicketHistoryWindow') as history, \
                     patch.object(gui.messagebox, 'showinfo') as message:
                    viewer.open_ticket_history()
                history.assert_not_called()
                self.assertIn('select one ticket row', message.call_args.args[1])

    def test_invalid_row_id_does_not_open_history(self):
        for row_id in ('0', '-1', '2147483648', '7 OR 1=1', 'employee name'):
            with self.subTest(row_id=row_id):
                viewer = viewer_without_window()
                viewer.tree.selection.return_value = (row_id,)
                with patch.object(gui, 'TicketHistoryWindow') as history, \
                     patch.object(gui.messagebox, 'showinfo') as message:
                    viewer.open_ticket_history()
                history.assert_not_called()
                message.assert_called_once()

    def test_same_ticket_focuses_one_window_and_different_ticket_replaces_it(self):
        viewer = viewer_without_window()
        viewer.tree.selection.return_value = ('7',)
        with patch.object(gui, 'TicketHistoryWindow') as history:
            history.return_value.is_open = True
            history.return_value.ticket_id = 7
            viewer.open_ticket_history()
            viewer.open_ticket_history()
            history.assert_called_once_with(viewer.root, 7)
            history.return_value.focus.assert_called_once_with()
            viewer.tree.selection.return_value = ('8',)
            viewer.open_ticket_history()
        history.return_value.close.assert_called_once_with()
        self.assertEqual(history.call_args.args, (viewer.root, 8))

    def test_closed_history_can_reopen_and_closed_main_ignores_request(self):
        viewer = viewer_without_window()
        viewer.tree.selection.return_value = ('7',)
        with patch.object(gui, 'TicketHistoryWindow') as history:
            viewer.open_ticket_history()
            history.return_value.is_open = False
            viewer.open_ticket_history()
            viewer._closed = True
            viewer.open_ticket_history()
        self.assertEqual(history.call_count, 2)

    def test_history_is_modeless_and_keeps_search_state_while_ticket_actions_remain_available(self):
        viewer = viewer_without_window()
        viewer._active_search = 'Hardware'
        viewer._history_window = MagicMock(is_open=True, ticket_id=7)
        viewer.tree.selection.return_value = ('7',)
        with patch.object(gui, 'UpdateTicketDialog') as update:
            viewer.open_update_ticket()
        update.assert_called_once_with(viewer.root, 7, viewer._request_refresh,
                                       permissions=viewer.permissions, user=viewer.user)
        self.assertEqual(viewer._active_search, 'Hardware')
        viewer._history_window.close.assert_not_called()

    def test_existing_modal_dialog_is_focused_before_opening_history(self):
        for field in ('_create_dialog', '_update_dialog', '_delete_dialog', '_technician_window'):
            with self.subTest(field=field):
                viewer = viewer_without_window()
                existing = MagicMock(is_open=True)
                setattr(viewer, field, existing)
                with patch.object(gui, 'TicketHistoryWindow') as history:
                    viewer.open_ticket_history()
                history.assert_not_called()
                existing.focus.assert_called_once_with()

    def test_main_close_closes_history_but_still_waits_for_pending_writes(self):
        for saving in (True, False):
            with self.subTest(saving=saving):
                viewer = viewer_without_window()
                viewer._history_window = MagicMock(is_open=True)
                viewer._update_dialog = MagicMock(is_open=True, is_saving=saving)
                viewer.close()
                if saving:
                    viewer._history_window.close.assert_not_called()
                    viewer.root.destroy.assert_not_called()
                else:
                    viewer._history_window.close.assert_called_once_with()
                    viewer.root.destroy.assert_called_once_with()


class HistoryReadTests(unittest.TestCase):
    def test_refresh_starts_one_worker_and_temporarily_disables_refresh(self):
        window = history_without_window()
        with patch.object(history_gui, 'Thread') as worker:
            window.refresh()
            window.refresh()
        worker.assert_called_once_with(target=window._load_history, daemon=True)
        worker.return_value.start.assert_called_once_with()
        window.refresh_button.state.assert_called_once_with(['disabled'])
        window.window.after.assert_called_once_with(100, window._check_refresh)
        self.assertTrue(window._loading)

    def test_worker_verifies_ticket_then_reads_same_id_without_accessing_widgets(self):
        window = history_without_window()
        calls = []
        with patch.object(history_gui, 'get_ticket', side_effect=lambda ident: calls.append(('ticket', ident)) or ticket()) as read, \
             patch.object(history_gui, 'get_ticket_history', side_effect=lambda ident: calls.append(('history', ident)) or activities()) as history:
            window._load_history()
        read.assert_called_once_with(7)
        history.assert_called_once_with(7)
        self.assertEqual(calls, [('ticket', 7), ('history', 7)])
        self.assertEqual(window._results.get_nowait(), (ticket(), activities(), None))
        for widget in (window.window, window.tree, window.details, window.summary, window.feedback):
            self.assertEqual(widget.mock_calls, [])

    def test_missing_ticket_does_not_query_history_and_clears_obsolete_rows(self):
        window = history_without_window()
        with patch.object(history_gui, 'get_ticket', return_value=None), \
             patch.object(history_gui, 'get_ticket_history') as history:
            window._load_history()
        history.assert_not_called()
        window._check_refresh()
        window.tree.delete.assert_called_once_with('old-row')
        window.tree.insert.assert_not_called()
        self.assertIn('Ticket not found', window.summary.set.call_args.args[0])
        self.assertIn('no longer exists', window.feedback.set.call_args.args[0])
        window.refresh_button.state.assert_called_once_with(['!disabled'])
        self.assertTrue(window.is_open)

    def test_read_errors_keep_previous_rows_details_summary_and_allow_retry(self):
        for function, error in (('get_ticket', history_gui.TicketReadError('Check MySQL.')),
                                ('get_ticket_history', history_gui.TicketHistoryReadError('Check history.')),
                                ('get_ticket', RuntimeError('private details'))):
            with self.subTest(function=function, error=type(error).__name__):
                window = history_without_window()
                with patch.object(history_gui, 'get_ticket', return_value=ticket()), \
                     patch.object(history_gui, function, side_effect=error):
                    window._load_history()
                window._check_refresh()
                window.tree.delete.assert_not_called()
                window.details.delete.assert_not_called()
                window.summary.set.assert_not_called()
                self.assertNotIn('private details', window.feedback.set.call_args.args[0])
                self.assertIn('Unable to load ticket history', window.feedback.set.call_args.args[0])
                self.assertFalse(window._loading)
                window.refresh_button.state.assert_called_once_with(['!disabled'])

    def test_pending_refresh_reschedules_without_clearing_data(self):
        window = history_without_window()
        window._loading = True
        window._check_refresh()
        window.window.after.assert_called_once_with(100, window._check_refresh)
        self.assertTrue(window._loading)
        window.tree.delete.assert_not_called()

    def test_refresh_updates_summary_and_history_from_current_repository_values(self):
        window = history_without_window()
        fresh = {**ticket(), 'employee_name': 'Bob Reyes', 'subject': 'Updated subject'}
        window._results.put((fresh, activities(), None))
        window._check_refresh()
        self.assertEqual(window.summary.set.call_args.args[0], 'Ticket ID: 7\nEmployee: Bob Reyes\nSubject: Updated subject')
        self.assertEqual(window.feedback.set.call_args.args[0], '3 history entries loaded.')
        self.assertFalse(window._loading)

    def test_close_cancels_poll_and_ignores_late_result_and_refresh(self):
        window = history_without_window()
        window._poll_id = 'pending-poll'
        window.close()
        window.close()
        window._results.put((ticket(), activities(), None))
        with patch.object(history_gui, 'Thread') as worker:
            window._check_refresh()
            window.refresh()
        window.window.after_cancel.assert_called_once_with('pending-poll')
        window.window.destroy.assert_called_once_with()
        worker.assert_not_called()
        window.tree.insert.assert_not_called()


class HistoryDisplayTests(unittest.TestCase):
    def test_datetime_action_and_details_format_without_mutating_records(self):
        entry = activities()[0]
        self.assertEqual(history_gui.history_row_values(entry),
                         ('2026-10-06 09:00:00', 'Ticket Created', 'Priority: Medium, Status: Open', 'System / Legacy'))
        self.assertEqual(entry, activities()[0])
        self.assertEqual(history_gui.history_row_values({}), ('-', '-', '-', 'System / Legacy'))

    def test_multiline_details_have_single_table_row_and_complete_readonly_pane(self):
        window = history_without_window()
        entry = {**activities()[0], 'details': 'First line\nSecond line\tvalue\x00'}
        window._display_history([entry])
        self.assertEqual(window.tree.insert.call_args.kwargs['values'][2], 'First line\\nSecond line\\tvalue\\x00')
        self.assertEqual(window.details.insert.call_args.args[1],
                         '2026-10-06 09:00:00 | Ticket Created\nPerformed By: System / Legacy\n\n'
                         'First line\nSecond line\tvalue\\x00')
        self.assertEqual(window.details.configure.call_args_list[-1], call(state='disabled'))

    def test_repository_order_is_preserved_instead_of_sorting_by_id(self):
        window = history_without_window()
        window._display_history(activities())
        self.assertEqual([item.kwargs['iid'] for item in window.tree.insert.call_args_list], ['42', '7', '50'])
        self.assertEqual(window.tree.selection_set.call_args.args[0], '42')

    def test_empty_history_clears_table_and_has_friendly_feedback(self):
        window = history_without_window()
        window._display_history([])
        window.tree.delete.assert_called_once_with('old-row')
        window.tree.insert.assert_not_called()
        window.feedback.set.assert_called_once_with('No history found for this ticket.')
        self.assertIn('No history found', window.details.insert.call_args.args[1])
        self.assertTrue(window.is_open)

    def test_refresh_preserves_activity_selection_and_its_details(self):
        window = history_without_window()
        window.tree.selection.return_value = ('7',)
        window._display_history(activities())
        window.tree.selection_set.assert_called_once_with('7')
        self.assertIn('Mark Santos', window.details.insert.call_args.args[1])

    def test_selecting_other_activity_updates_details_and_closed_window_ignores_event(self):
        window = history_without_window()
        window._display_history(activities())
        window.tree.selection.return_value = ('50',)
        window._show_selected_details()
        self.assertIn('Open -> In Progress', window.details.insert.call_args.args[1])
        window._closed = True
        window.details.reset_mock()
        window._show_selected_details()
        window.details.insert.assert_not_called()

    def test_long_details_are_not_truncated(self):
        window = history_without_window()
        details = 'Long description.\n' * 1000
        window._display_history([{**activities()[0], 'details': details}])
        self.assertTrue(window.details.insert.call_args.args[1].endswith(details))

    def test_construction_is_modeless_has_only_view_refresh_close_and_both_scrollbars(self):
        with patch.object(history_gui.tk, 'Toplevel') as top, patch.object(history_gui.tk, 'StringVar'), \
             patch.object(history_gui.tk, 'Text') as text, patch.object(history_gui.ttk, 'Frame'), \
             patch.object(history_gui.ttk, 'Label'), patch.object(history_gui.ttk, 'Treeview') as tree, \
             patch.object(history_gui.ttk, 'Scrollbar') as scrollbar, patch.object(history_gui.ttk, 'Button') as button, \
             patch.object(history_gui.TicketHistoryWindow, 'refresh') as refresh:
            window = history_gui.TicketHistoryWindow(MagicMock(), 7)
        self.assertEqual(tree.call_args.kwargs['columns'], ('created_at', 'action', 'details', 'performed_by'))
        self.assertEqual([item.kwargs['text'] for item in window.tree.heading.call_args_list],
                         ['Date/Time', 'Action', 'Details', 'Performed By'])
        tree.return_value.bind.assert_called_once_with('<<TreeviewSelect>>', window._show_selected_details)
        self.assertEqual({item.kwargs['text']: item.kwargs['command'] for item in button.call_args_list},
                         {'Refresh': refresh, 'Close': window.close})
        self.assertEqual([item.kwargs['orient'] for item in scrollbar.call_args_list], ['vertical', 'horizontal', 'vertical'])
        top.return_value.grab_set.assert_not_called()
        self.assertEqual(text.call_args.kwargs['wrap'], 'word')
        self.assertEqual(text.return_value.configure.call_args_list[-1], call(state='disabled'))
        top.return_value.protocol.assert_called_once_with('WM_DELETE_WINDOW', window.close)
        self.assertEqual(top.return_value.bind.call_args.args[0], '<Escape>')
        refresh.assert_called_once_with()


class ReadOnlyRepositoryIntegrationTests(unittest.TestCase):
    def test_viewing_and_refreshing_use_only_bound_selects_and_never_commit_or_log_activity(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [ [ticket()], activities(), [ticket()], activities() ]
        window = history_without_window()
        with patch.object(ticket_repository, 'get_connection', return_value=connection), \
             patch.object(history_repository, 'get_connection', return_value=connection):
            window._load_history()
            window._load_history()
        calls = cursor.execute.call_args_list
        self.assertEqual(len(calls), 4)
        for item in calls:
            query, parameters = item.args
            self.assertTrue(query.startswith('SELECT '))
            self.assertEqual(parameters, (7,))
            self.assertIn('helpdesk.', query)
            self.assertNotIn('FOR UPDATE', query)
        for item in (calls[1], calls[3]):
            self.assertIn('ORDER BY h.created_at, h.history_id', item.args[0])
        connection.commit.assert_not_called()
        self.assertEqual(window._results.get_nowait(), (ticket(), activities(), None))


if __name__ == '__main__':
    unittest.main()
