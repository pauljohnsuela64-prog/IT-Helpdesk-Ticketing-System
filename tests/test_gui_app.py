"""GUI data and refresh checks without opening a window or connecting to MySQL."""
import unittest
from datetime import datetime
from queue import Queue
from unittest.mock import MagicMock, patch

import gui_app as gui
import ticket_repository as repository
from gui_permissions import SessionPermissions


def ticket(ticket_id=7):
    return dict(ticket_id=ticket_id, employee_name='Alice Reyes', department='IT',
                category='Hardware', subject='Printer issue', priority='Medium',
                status='Open', assigned_to=None, created_at=datetime(2026, 10, 5, 12, 45))


def viewer_without_window():
    viewer = gui.TicketViewer.__new__(gui.TicketViewer)
    viewer.root = MagicMock()
    viewer.permissions = SessionPermissions({'role': 'Admin', 'status': 'Active'})
    viewer.tree = MagicMock()
    viewer.tree.selection.return_value = ()
    viewer.tree.get_children.return_value = ()
    viewer.tree.exists.return_value = False
    viewer.status = MagicMock()
    viewer.dashboard = MagicMock()
    viewer.refresh_button = MagicMock()
    viewer._results = Queue()
    viewer._loading = False
    viewer._closed = False
    viewer._poll_id = None
    viewer._create_dialog = None
    viewer._update_dialog = None
    viewer._delete_dialog = None
    viewer._technician_window = None
    viewer._history_window = None
    viewer._notes_window = None
    viewer._refresh_pending = False
    viewer._active_search = ''
    viewer._active_status = ''
    viewer._loading_search = ''
    viewer.search_term = MagicMock()
    viewer.search_term.get.return_value = ''
    return viewer


class TicketRowTests(unittest.TestCase):
    def test_column_order_timestamp_and_unassigned_label(self):
        self.assertEqual(gui.ticket_row_values(ticket()), (
            '7', 'Alice Reyes', 'IT', 'Hardware', 'Printer issue', 'Medium',
            'Open', 'Unassigned', '2026-10-05 12:45',
        ))

    def test_null_values_and_unicode_are_readable_without_changing_source_data(self):
        row = {**ticket(), 'employee_name': 'José Reyes', 'created_at': None,
               'department': '', 'assigned_to': 'Anna Reyes'}
        original = row.copy()
        values = gui.ticket_row_values(row)
        self.assertEqual(values[1], 'José Reyes')
        self.assertEqual(values[2], '-')
        self.assertEqual(values[7], 'Anna Reyes')
        self.assertEqual(values[8], '-')
        self.assertEqual(row, original)

    def test_multiline_and_control_characters_stay_in_one_table_row(self):
        row = {**ticket(), 'subject': 'Printer\nissue\t\x00\x1b'}
        subject = gui.ticket_row_values(row)[4]
        self.assertEqual(subject, 'Printer\\nissue\\t\\x00\\x1b')
        self.assertNotIn('\n', subject)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.viewer = viewer_without_window()

    def test_refresh_starts_one_background_read_and_temporarily_disables_button(self):
        self.viewer.root.after.return_value = 'poll-1'
        with patch.object(gui, 'Thread') as worker:
            self.viewer.refresh_tickets()
            self.viewer.refresh_tickets()
        worker.assert_called_once_with(target=self.viewer._load_tickets, args=('',), daemon=True)
        worker.return_value.start.assert_called_once_with()
        self.viewer.status.set.assert_called_once_with('Loading tickets...')
        self.viewer.refresh_button.state.assert_called_once_with(['disabled'])
        self.viewer.root.after.assert_called_once_with(100, self.viewer._check_refresh)
        self.assertEqual(self.viewer._poll_id, 'poll-1')
        self.assertTrue(self.viewer._loading)

    def test_worker_uses_existing_repository_and_never_calls_tkinter(self):
        rows = [ticket()]
        with patch.object(gui, 'get_tickets', return_value=rows) as read:
            self.viewer._load_tickets()
        read.assert_called_once_with()
        self.assertEqual(self.viewer._results.get_nowait(), (rows, None))
        for widget in (self.viewer.root, self.viewer.tree, self.viewer.status, self.viewer.refresh_button):
            self.assertEqual(widget.mock_calls, [])

    def test_worker_queues_repository_error_without_touching_widgets(self):
        with patch.object(gui, 'get_tickets', side_effect=repository.TicketReadError('Check MySQL.')):
            self.viewer._load_tickets()
        self.assertEqual(self.viewer._results.get_nowait(), (None, 'Check MySQL.'))
        self.assertEqual(self.viewer.root.mock_calls, [])
        self.assertEqual(self.viewer.status.mock_calls, [])

    def test_unexpected_reader_error_is_queued_without_exposing_private_details(self):
        with patch.object(gui, 'get_tickets', side_effect=RuntimeError('private details')):
            self.viewer._load_tickets()
        self.assertEqual(self.viewer._results.get_nowait(), (None, 'Please try Refresh again.'))

    def test_waiting_for_result_schedules_another_poll_without_clearing_rows(self):
        self.viewer._loading = True
        self.viewer.root.after.return_value = 'poll-2'
        self.viewer._check_refresh()
        self.viewer.root.after.assert_called_once_with(100, self.viewer._check_refresh)
        self.assertEqual(self.viewer._poll_id, 'poll-2')
        self.assertTrue(self.viewer._loading)
        self.viewer.tree.delete.assert_not_called()
        self.viewer.refresh_button.state.assert_not_called()

    def test_success_replaces_rows_and_reenables_refresh(self):
        self.viewer._loading = True
        self.viewer.tree.get_children.return_value = ('old-1', 'old-2')
        self.viewer._results.put(([ticket()], None))
        self.viewer._check_refresh()
        self.viewer.tree.delete.assert_called_once_with('old-1', 'old-2')
        self.viewer.tree.insert.assert_called_once_with(
            '', 'end', iid='7', values=gui.ticket_row_values(ticket()), tags=(),
        )
        self.viewer.status.set.assert_called_once_with('1 ticket loaded.')
        self.viewer.refresh_button.state.assert_called_once_with(['!disabled'])
        self.assertFalse(self.viewer._loading)
        self.assertIsNone(self.viewer._poll_id)

    def test_many_rows_are_loaded_without_truncation(self):
        rows = [ticket(number) for number in range(1, 251)]
        self.viewer._results.put((rows, None))
        self.viewer._check_refresh()
        self.assertEqual(self.viewer.tree.insert.call_count, 250)
        self.assertEqual(self.viewer.tree.insert.call_args.kwargs['iid'], '250')
        self.viewer.status.set.assert_called_once_with('250 tickets loaded.')

    def test_empty_success_clears_old_rows_and_shows_friendly_message(self):
        self.viewer.tree.get_children.return_value = ('7',)
        self.viewer._results.put(([], None))
        self.viewer._check_refresh()
        self.viewer.tree.delete.assert_called_once_with('7')
        self.viewer.tree.insert.assert_not_called()
        self.viewer.status.set.assert_called_once_with('No tickets found.')
        self.viewer.refresh_button.state.assert_called_once_with(['!disabled'])

    def test_failed_refresh_preserves_existing_rows_and_allows_retry(self):
        self.viewer._loading = True
        self.viewer._results.put((None, 'Check MySQL.'))
        self.viewer._check_refresh()
        self.viewer.tree.delete.assert_not_called()
        self.viewer.tree.insert.assert_not_called()
        self.viewer.status.set.assert_called_once_with('Unable to load tickets. Check MySQL.')
        self.viewer.refresh_button.state.assert_called_once_with(['!disabled'])
        self.assertFalse(self.viewer._loading)
        with patch.object(gui, 'Thread') as worker:
            self.viewer.refresh_tickets()
        worker.return_value.start.assert_called_once_with()

    def test_refresh_keeps_selection_if_ticket_still_exists(self):
        self.viewer.tree.selection.return_value = ('7',)
        self.viewer.tree.exists.return_value = True
        self.viewer._display_tickets([ticket(), ticket(8)])
        self.viewer.tree.selection_set.assert_called_once_with('7')
        self.viewer.tree.focus.assert_called_once_with('7')

    def test_missing_previous_selection_is_not_restored(self):
        self.viewer.tree.selection.return_value = ('7',)
        self.viewer._display_tickets([ticket(8)])
        self.viewer.tree.selection_set.assert_not_called()
        self.viewer.tree.focus.assert_not_called()

    def test_close_cancels_poll_and_prevents_late_widget_updates_or_new_reads(self):
        self.viewer._poll_id = 'pending-poll'
        self.viewer.close()
        self.viewer.root.after_cancel.assert_called_once_with('pending-poll')
        self.viewer.root.destroy.assert_called_once_with()
        self.assertTrue(self.viewer._closed)
        self.assertIsNone(self.viewer._poll_id)
        self.viewer._results.put(([ticket()], None))
        with patch.object(gui, 'Thread') as worker:
            self.viewer._check_refresh()
            self.viewer.refresh_tickets()
        worker.assert_not_called()
        self.viewer.tree.insert.assert_not_called()
        self.viewer.status.set.assert_not_called()


class GuiConstructionTests(unittest.TestCase):
    def test_headings_single_selection_scrollbars_refresh_and_initial_loading(self):
        root = MagicMock()
        vertical, horizontal = MagicMock(), MagicMock()
        with patch.object(gui, 'DashboardPanel'), patch.object(gui.ttk, 'Style'), patch.object(gui.ttk, 'Frame'), \
             patch.object(gui.ttk, 'Label'), patch.object(gui.ttk, 'Entry') as entry, \
             patch.object(gui.ttk, 'Button') as button, \
             patch.object(gui.ttk, 'Treeview') as tree, \
             patch.object(gui.ttk, 'Scrollbar', side_effect=[vertical, horizontal]) as scrollbar, \
             patch.object(gui.tk, 'StringVar'), \
             patch.object(gui.TicketViewer, 'refresh_tickets') as refresh:
            viewer = gui.TicketViewer(root)
        root.title.assert_called_once_with('IT Help Desk Ticketing System')
        root.resizable.assert_called_once_with(True, True)
        self.assertEqual(tree.call_args.kwargs['selectmode'], 'browse')
        self.assertEqual(tree.call_args.kwargs['show'], 'headings')
        self.assertEqual(tree.call_args.kwargs['columns'], (
            'ticket_id', 'employee_name', 'department', 'category', 'subject',
            'priority', 'status', 'assigned_to', 'created_at',
        ))
        headings = [item.kwargs['text'] for item in viewer.tree.heading.call_args_list]
        self.assertEqual(headings, ['Ticket ID', 'Employee', 'Department', 'Category',
                                    'Subject', 'Priority', 'Status', 'Assigned To', 'Created At'])
        buttons = {item.kwargs['text']: item.kwargs['command'] for item in button.call_args_list}
        self.assertEqual(buttons, {'Manage Technicians': viewer.open_technician_management,
                                   'Create Ticket': viewer.open_create_ticket,
                                   'Update Ticket': viewer.open_update_ticket,
                                   'Delete Ticket': viewer.open_delete_ticket,
                                   'View History': viewer.open_ticket_history,
                                   'Ticket Notes': viewer.open_ticket_notes, 'Refresh': refresh,
                                   'Search': viewer.perform_search, 'Clear Search': viewer.clear_search})
        entry.return_value.bind.assert_called_once_with('<Return>', viewer.perform_search)
        tree.assert_called_once()
        self.assertEqual([item.kwargs['orient'] for item in scrollbar.call_args_list],
                         ['vertical', 'horizontal'])
        viewer.tree.configure.assert_called_once_with(yscrollcommand=vertical.set,
                                                       xscrollcommand=horizontal.set)
        refresh.assert_called_once_with()


class TicketListRepositoryTests(unittest.TestCase):
    def test_list_includes_created_at_in_existing_read_only_query(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [ticket()]
        with patch.object(repository, 'get_connection', return_value=connection):
            self.assertEqual(repository.get_tickets(), [ticket()])
        cursor.execute.assert_called_once()
        query, parameters = cursor.execute.call_args.args
        self.assertTrue(query.startswith('SELECT '))
        self.assertIn('assigned_to, created_at FROM helpdesk.tickets ORDER BY ticket_id', query)
        self.assertIsNone(parameters)
        connection.commit.assert_not_called()


class CreateDialogIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.viewer = viewer_without_window()

    def test_repeated_open_focuses_same_dialog(self):
        with patch.object(gui, 'CreateTicketDialog') as create:
            create.return_value.is_open = True
            self.viewer.open_create_ticket()
            self.viewer.open_create_ticket()
        create.assert_called_once_with(self.viewer.root, self.viewer._refresh_after_creation)
        create.return_value.focus.assert_called_once_with()

    def test_closed_form_can_be_opened_again(self):
        with patch.object(gui, 'CreateTicketDialog') as create:
            self.viewer.open_create_ticket()
            create.return_value.is_open = False
            self.viewer.open_create_ticket()
        self.assertEqual(create.call_count, 2)

    def test_success_requests_immediate_refresh_when_idle(self):
        with patch.object(self.viewer, 'refresh_tickets') as refresh:
            self.viewer._refresh_after_creation()
        refresh.assert_called_once_with()

    def test_creation_during_load_requests_followup_refresh_even_after_read_error(self):
        for result in (([ticket()], None), (None, 'Check MySQL.')):
            with self.subTest(error=result[1]):
                viewer = viewer_without_window()
                viewer._loading = True
                viewer._refresh_after_creation()
                self.assertTrue(viewer._refresh_pending)
                viewer._results.put(result)
                with patch.object(gui, 'Thread') as worker:
                    viewer._check_refresh()
                worker.assert_called_once_with(target=viewer._load_tickets, args=('',), daemon=True)
                worker.return_value.start.assert_called_once_with()
                self.assertTrue(viewer._loading)
                self.assertFalse(viewer._refresh_pending)

    def test_main_close_cancels_unsaved_form(self):
        dialog = MagicMock(is_open=True, is_saving=False)
        self.viewer._create_dialog = dialog
        self.viewer.close()
        dialog.cancel.assert_called_once_with()
        self.viewer.root.destroy.assert_called_once_with()

    def test_main_close_waits_for_save_result(self):
        dialog = MagicMock(is_open=True, is_saving=True)
        self.viewer._create_dialog = dialog
        self.viewer.close()
        dialog.focus.assert_called_once_with()
        dialog.cancel.assert_not_called()
        self.viewer.root.destroy.assert_not_called()
        self.assertFalse(self.viewer._closed)

    def test_closed_viewer_ignores_open_and_creation_callback(self):
        self.viewer._closed = True
        with patch.object(gui, 'CreateTicketDialog') as create, \
             patch.object(self.viewer, 'refresh_tickets') as refresh:
            self.viewer.open_create_ticket()
            self.viewer._refresh_after_creation()
        create.assert_not_called()
        refresh.assert_not_called()


if __name__ == '__main__':
    unittest.main()
