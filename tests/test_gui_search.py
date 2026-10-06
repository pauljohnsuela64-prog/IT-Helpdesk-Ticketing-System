"""GUI search checks without opening a window or connecting to MySQL."""
import unittest
from datetime import datetime
from queue import Queue
from unittest.mock import MagicMock, patch

import gui_app as gui
import ticket_repository as repository


def ticket(ticket_id=3):
    return dict(ticket_id=ticket_id, employee_name='Alice Reyes', department='IT',
                category='Hardware', subject='PC 3', priority='Medium', status='Open',
                assigned_to='Mark Santos', created_at=datetime(2026, 10, 5, 12, 45))


def viewer_without_window():
    viewer = gui.TicketViewer.__new__(gui.TicketViewer)
    viewer.root = MagicMock()
    viewer.tree = MagicMock()
    viewer.tree.selection.return_value = ()
    viewer.tree.get_children.return_value = ('previous-row',)
    viewer.tree.exists.return_value = False
    viewer.status = MagicMock()
    viewer.refresh_button = MagicMock()
    viewer.search_term = MagicMock()
    viewer.search_term.get.return_value = ''
    viewer._results = Queue()
    viewer._loading = False
    viewer._closed = False
    viewer._poll_id = None
    viewer._create_dialog = None
    viewer._update_dialog = None
    viewer._refresh_pending = False
    viewer._active_search = ''
    viewer._loading_search = ''
    return viewer


class SearchInteractionTests(unittest.TestCase):
    def setUp(self):
        self.viewer = viewer_without_window()

    def test_search_trims_term_and_starts_background_search(self):
        self.viewer.search_term.get.return_value = '  Hardware  '
        with patch.object(gui, 'Thread') as worker:
            self.viewer.perform_search()
        self.assertEqual(self.viewer._active_search, 'Hardware')
        self.assertEqual(self.viewer._loading_search, 'Hardware')
        self.viewer.search_term.set.assert_called_once_with('Hardware')
        worker.assert_called_once_with(target=self.viewer._load_tickets, args=('Hardware',), daemon=True)
        worker.return_value.start.assert_called_once_with()
        self.viewer.status.set.assert_called_once_with('Searching tickets...')

    def test_numeric_single_character_and_symbol_searches_are_not_form_validated(self):
        for term in ('3', 'A', '%', '_', '!'):
            with self.subTest(term=term):
                viewer = viewer_without_window()
                viewer.search_term.get.return_value = term
                with patch.object(gui, 'Thread') as worker:
                    viewer.perform_search()
                worker.assert_called_once_with(target=viewer._load_tickets, args=(term,), daemon=True)
                self.assertEqual(viewer._active_search, term)

    def test_enter_performs_same_search_and_stops_event_propagation(self):
        self.viewer.search_term.get.return_value = 'Printer'
        with patch.object(gui, 'Thread') as worker:
            result = self.viewer.perform_search(MagicMock())
        self.assertEqual(result, 'break')
        worker.assert_called_once_with(target=self.viewer._load_tickets, args=('Printer',), daemon=True)

    def test_blank_or_whitespace_search_restores_full_list(self):
        for term in ('', ' \t '):
            with self.subTest(term=term):
                viewer = viewer_without_window()
                viewer._active_search = 'Hardware'
                viewer.search_term.get.return_value = term
                with patch.object(gui, 'Thread') as worker:
                    viewer.perform_search()
                self.assertEqual(viewer._active_search, '')
                viewer.search_term.set.assert_called_once_with('')
                worker.assert_called_once_with(target=viewer._load_tickets, args=('',), daemon=True)

    def test_clear_search_resets_box_and_filter_and_starts_full_reload(self):
        self.viewer._active_search = 'Hardware'
        with patch.object(gui, 'Thread') as worker:
            self.viewer.clear_search()
        self.viewer.search_term.set.assert_called_once_with('')
        self.assertEqual(self.viewer._active_search, '')
        worker.assert_called_once_with(target=self.viewer._load_tickets, args=('',), daemon=True)

    def test_refresh_uses_submitted_filter_instead_of_unsubmitted_box_text(self):
        self.viewer._active_search = 'Hardware'
        self.viewer.search_term.get.return_value = 'Software'
        with patch.object(gui, 'Thread') as worker:
            self.viewer.refresh_tickets()
        worker.assert_called_once_with(target=self.viewer._load_tickets, args=('Hardware',), daemon=True)
        self.viewer.search_term.get.assert_not_called()

    def test_worker_reuses_search_repository_with_snapshot_and_never_calls_tkinter(self):
        # Changing GUI state must not change the already requested worker query.
        self.viewer._active_search = 'Software'
        with patch.object(gui, 'search_tickets', return_value=[ticket()]) as search, \
             patch.object(gui, 'get_tickets') as read_all:
            self.viewer._load_tickets('3')
        search.assert_called_once_with('3')
        read_all.assert_not_called()
        self.assertEqual(self.viewer._results.get_nowait(), ([ticket()], None))
        for widget in (self.viewer.root, self.viewer.tree, self.viewer.status, self.viewer.search_term):
            self.assertEqual(widget.mock_calls, [])

    def test_unfiltered_worker_uses_existing_view_repository(self):
        with patch.object(gui, 'get_tickets', return_value=[ticket()]) as read_all, \
             patch.object(gui, 'search_tickets') as search:
            self.viewer._load_tickets('')
        read_all.assert_called_once_with()
        search.assert_not_called()

    def test_matches_replace_rows_in_existing_table_with_created_at(self):
        self.viewer._active_search = self.viewer._loading_search = 'Hardware'
        self.viewer._results.put(([ticket()], None))
        self.viewer._check_refresh()
        self.viewer.tree.delete.assert_called_once_with('previous-row')
        self.viewer.tree.insert.assert_called_once_with(
            '', 'end', iid='3', values=gui.ticket_row_values(ticket()), tags=(),
        )
        self.assertEqual(self.viewer.tree.insert.call_args.kwargs['values'][-1], '2026-10-05 12:45')
        self.viewer.status.set.assert_called_once_with('1 matching ticket found.')

    def test_no_matches_clears_previous_rows_and_shows_friendly_message(self):
        self.viewer._active_search = self.viewer._loading_search = 'Not present'
        self.viewer._results.put(([], None))
        self.viewer._check_refresh()
        self.viewer.tree.delete.assert_called_once_with('previous-row')
        self.viewer.tree.insert.assert_not_called()
        self.viewer.status.set.assert_called_once_with('No matching tickets found.')
        self.viewer.refresh_button.state.assert_called_once_with(['!disabled'])

    def test_latest_pending_search_discards_old_rows_and_queries_only_latest_term(self):
        self.viewer._loading = True
        self.viewer._loading_search = 'Old search'
        self.viewer.search_term.get.side_effect = ['Hardware', '3', 'IT']
        with patch.object(gui, 'Thread') as worker:
            for _ in range(3):
                self.viewer.perform_search()
            worker.assert_not_called()
            self.viewer._results.put(([ticket(99)], None))
            self.viewer._check_refresh()
        worker.assert_called_once_with(target=self.viewer._load_tickets, args=('IT',), daemon=True)
        self.viewer.tree.delete.assert_not_called()
        self.viewer.tree.insert.assert_not_called()
        self.assertEqual(self.viewer._active_search, 'IT')
        self.assertFalse(self.viewer._refresh_pending)
        self.viewer._results.put(([ticket()], None))
        self.viewer._check_refresh()
        self.assertEqual(self.viewer.tree.insert.call_args.kwargs['iid'], '3')

    def test_clear_during_search_discards_filtered_rows_and_restores_all_results(self):
        self.viewer._active_search = self.viewer._loading_search = 'Hardware'
        self.viewer._loading = True
        with patch.object(gui, 'Thread') as worker:
            self.viewer.clear_search()
            worker.assert_not_called()
            self.viewer._results.put(([ticket()], None))
            self.viewer._check_refresh()
        worker.assert_called_once_with(target=self.viewer._load_tickets, args=('',), daemon=True)
        self.viewer.tree.delete.assert_not_called()
        self.viewer._results.put(([ticket(), ticket(4)], None))
        self.viewer._check_refresh()
        self.assertEqual(self.viewer.tree.insert.call_count, 2)
        self.viewer.status.set.assert_called_with('2 tickets loaded.')

    def test_obsolete_error_does_not_replace_latest_search_status(self):
        self.viewer._loading = True
        self.viewer._loading_search = 'Hardware'
        self.viewer.search_term.get.return_value = '3'
        self.viewer.perform_search()
        self.viewer._results.put((None, 'Old query failed.'))
        with patch.object(gui, 'Thread') as worker:
            self.viewer._check_refresh()
        worker.assert_called_once_with(target=self.viewer._load_tickets, args=('3',), daemon=True)
        self.viewer.status.set.assert_called_once_with('Searching tickets...')
        self.viewer.tree.delete.assert_not_called()

    def test_search_errors_preserve_rows_and_allow_refresh_with_same_filter(self):
        self.viewer._active_search = self.viewer._loading_search = '3'
        self.viewer._loading = True
        with patch.object(gui, 'search_tickets', side_effect=repository.TicketReadError('Check MySQL.')):
            self.viewer._load_tickets('3')
        self.viewer._check_refresh()
        self.viewer.tree.delete.assert_not_called()
        self.viewer.tree.insert.assert_not_called()
        self.viewer.status.set.assert_called_once_with('Unable to load tickets. Check MySQL.')
        self.viewer.refresh_button.state.assert_called_once_with(['!disabled'])
        with patch.object(gui, 'Thread') as worker:
            self.viewer.refresh_tickets()
        worker.assert_called_once_with(target=self.viewer._load_tickets, args=('3',), daemon=True)

    def test_successful_creation_refreshes_current_filter(self):
        self.viewer._active_search = 'Hardware'
        with patch.object(gui, 'Thread') as worker:
            self.viewer._refresh_after_creation()
        worker.assert_called_once_with(target=self.viewer._load_tickets, args=('Hardware',), daemon=True)
        self.assertEqual(self.viewer._active_search, 'Hardware')

    def test_closed_window_ignores_search_clear_and_late_results(self):
        self.viewer._closed = True
        self.viewer._results.put(([ticket()], None))
        with patch.object(gui, 'Thread') as worker:
            self.viewer.perform_search()
            self.viewer.clear_search()
            self.viewer._check_refresh()
        worker.assert_not_called()
        self.viewer.search_term.get.assert_not_called()
        self.viewer.search_term.set.assert_not_called()
        self.viewer.tree.insert.assert_not_called()


class SearchRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchall.return_value = [ticket()]
        self.connect = patch.object(repository, 'get_connection', return_value=self.connection).start()
        self.addCleanup(patch.stopall)

    def test_search_is_read_only_case_insensitive_and_covers_all_eight_fields(self):
        self.assertEqual(repository.search_tickets('  Hardware  '), [ticket()])
        query, parameters = self.cursor.execute.call_args.args
        self.assertTrue(query.startswith('SELECT '))
        self.assertIn('assigned_to, created_at FROM helpdesk.tickets', query)
        self.assertEqual(parameters, ('%Hardware%',) * 8)
        self.assertEqual(query.count('LIKE LOWER(%s)'), 8)
        for expression in ('CAST(ticket_id AS CHAR)', 'employee_name', 'department', 'category',
                           'subject', 'priority', 'status', 'assigned_to'):
            self.assertIn(f'LOWER({expression}) LIKE LOWER(%s)', query)
        self.connection.commit.assert_not_called()
        self.cursor.execute.assert_called_once()

    def test_numeric_search_is_allowed_and_parameterized(self):
        repository.search_tickets(' 3 ')
        query, parameters = self.cursor.execute.call_args.args
        self.assertEqual(parameters, ('%3%',) * 8)
        self.assertIn('CAST(ticket_id AS CHAR)', query)
        self.connection.commit.assert_not_called()

    def test_wildcards_are_literals_and_sql_like_text_stays_in_parameters(self):
        for term, expected in (('  50%_!  ', '%50!%!_!!%'), ("' OR 1=1 --", "%' OR 1=1 --%")):
            with self.subTest(term=term):
                repository.search_tickets(term)
                query, parameters = self.cursor.execute.call_args.args
                self.assertEqual(parameters, (expected,) * 8)
                self.assertNotIn(term.strip(), query)
                self.assertEqual(query.count("ESCAPE '!'"), 8)

    def test_existing_blank_repository_search_still_does_not_connect(self):
        self.assertEqual(repository.search_tickets(' \t '), [])
        self.connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
