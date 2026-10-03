"""Delete feature checks using mocks; never connect to a live database."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, call, patch

import mysql.connector

import app
import ticket_repository as repo


def ticket():
    return dict(ticket_id=7, employee_name='Alice', department='IT', category='Hardware',
                subject='Printer issue', description='Paper jam', priority='Medium',
                status='Resolved', assigned_to='Sam', created_at='created time',
                updated_at='updated time', resolved_at='resolved time')


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = (7,)
        self.cursor.rowcount = 1
        self.connect = patch.object(repo, 'get_connection', return_value=self.connection).start()
        self.addCleanup(patch.stopall)

    def test_deletes_only_the_requested_id_with_parameters(self):
        self.assertTrue(repo.delete_ticket(7))
        self.assertEqual(self.cursor.execute.call_args_list, [
            call('SELECT ticket_id FROM helpdesk.tickets WHERE ticket_id = %s FOR UPDATE', (7,)),
            call('DELETE FROM helpdesk.tickets WHERE ticket_id = %s LIMIT 1', (7,)),
        ])
        self.connection.commit.assert_called_once_with()
        self.connection.rollback.assert_not_called()

    def test_missing_ticket_never_runs_delete(self):
        self.cursor.fetchone.return_value = None
        self.assertFalse(repo.delete_ticket(7))
        self.assertEqual(self.cursor.execute.call_count, 1)
        self.connection.commit.assert_not_called()
        self.connection.rollback.assert_called_once_with()

    def test_invalid_ids_are_rejected_before_connection(self):
        for value in (None, True, 0, -1, 1.5, 2147483648, 'Alice', 'Printer issue',
                      '7 OR 1=1', '7', [7, 8]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                repo.delete_ticket(value)
        self.connect.assert_not_called()

    def test_select_and_delete_errors_roll_back(self):
        for failures in ([mysql.connector.Error(errno=2003)],
                         [None, mysql.connector.Error(errno=1451)]):
            with self.subTest(failures=len(failures)):
                self.connection.reset_mock()
                self.cursor.execute.side_effect = failures
                with self.assertRaises(repo.TicketDeleteError):
                    repo.delete_ticket(7)
                self.connection.rollback.assert_called_once_with()
                self.connection.commit.assert_not_called()

    def test_commit_error_does_not_report_success(self):
        self.connection.commit.side_effect = mysql.connector.Error(errno=2013)
        with self.assertRaisesRegex(repo.TicketDeleteError, 'could not be confirmed'):
            repo.delete_ticket(7)
        self.connection.rollback.assert_called_once_with()

    def test_unexpected_affected_count_does_not_commit(self):
        for count in (0, 2):
            with self.subTest(count=count):
                self.connection.reset_mock()
                self.cursor.rowcount = count
                with self.assertRaises(repo.TicketDeleteError):
                    repo.delete_ticket(7)
                self.connection.rollback.assert_called_once_with()
                self.connection.commit.assert_not_called()

    def test_connection_and_configuration_errors_are_safe(self):
        for error in (ValueError('private configuration details'),
                      mysql.connector.Error('private connection details', errno=2003)):
            with self.subTest(error=type(error).__name__):
                self.connect.side_effect = error
                with self.assertRaises(repo.TicketDeleteError) as raised:
                    repo.delete_ticket(7)
                self.assertNotIn('private', str(raised.exception))

    def test_rollback_error_preserves_original_error(self):
        self.cursor.execute.side_effect = mysql.connector.Error(errno=1451)
        self.connection.rollback.side_effect = mysql.connector.Error(errno=2003)
        with self.assertRaisesRegex(repo.TicketDeleteError, '1451'):
            repo.delete_ticket(7)


class CliTests(unittest.TestCase):
    def run_delete(self, inputs, current=None, deleted=True, read_error=None, delete_error=None):
        output = io.StringIO()
        with patch('builtins.input', side_effect=inputs), \
             patch.object(app, 'get_ticket', return_value=current, side_effect=read_error) as lookup, \
             patch.object(app, 'delete_ticket', return_value=deleted, side_effect=delete_error) as delete, \
             redirect_stdout(output):
            app.delete_ticket_interactively()
        return output.getvalue(), lookup, delete

    def test_invalid_ids_never_reach_the_database(self):
        for raw in ('', 'abc', '7.0', '-1', '0', '2147483648', '7 OR 1=1', '7,8'):
            with self.subTest(raw=raw):
                output, lookup, delete = self.run_delete([raw])
                lookup.assert_not_called()
                delete.assert_not_called()
                self.assertIn('Ticket ID must be a positive number', output)

    def test_nonexistent_id_never_prompts_for_confirmation(self):
        output, lookup, delete = self.run_delete(['7'])
        lookup.assert_called_once_with(7)
        delete.assert_not_called()
        self.assertIn('No ticket found', output)

    def test_only_uppercase_y_or_yes_confirms(self):
        for answer in ('Y', 'YES'):
            with self.subTest(answer=answer):
                output, _, delete = self.run_delete(['7', answer], ticket())
                delete.assert_called_once_with(7)
                self.assertIn('Ticket 7 deleted successfully.', output)

    def test_every_other_response_cancels(self):
        for answer in ('', 'N', 'NO', 'y', 'yes', 'Yes', 'maybe', 'Y please', '1'):
            with self.subTest(answer=answer):
                output, _, delete = self.run_delete(['7', answer], ticket())
                delete.assert_not_called()
                self.assertIn('Deletion cancelled. No ticket was deleted.', output)

    def test_complete_information_is_displayed_before_confirmation(self):
        output = io.StringIO()

        def input_response(prompt):
            if prompt == 'Ticket ID: ':
                return '7'
            for value in ticket().values():
                self.assertIn(str(value), output.getvalue())
            self.assertIn('Resolved at', output.getvalue())
            return 'N'

        with patch('builtins.input', side_effect=input_response), \
             patch.object(app, 'get_ticket', return_value=ticket()), \
             patch.object(app, 'delete_ticket') as delete, redirect_stdout(output):
            app.delete_ticket_interactively()
        delete.assert_not_called()

    def test_confirmation_interruptions_cancel(self):
        for error in (EOFError(), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                output, _, delete = self.run_delete(['7', error], ticket())
                delete.assert_not_called()
                self.assertIn('Deletion cancelled', output)

    def test_disappearance_after_preview_is_friendly(self):
        output, _, delete = self.run_delete(['7', 'YES'], ticket(), deleted=False)
        delete.assert_called_once_with(7)
        self.assertIn('No ticket found', output)
        self.assertNotIn('successfully', output)

    def test_read_and_delete_errors_are_displayed(self):
        output, _, delete = self.run_delete(['7'], read_error=repo.TicketReadError('Unable to retrieve tickets'))
        delete.assert_not_called()
        self.assertIn('Unable to retrieve tickets', output)
        output, _, _ = self.run_delete(['7', 'Y'], ticket(), delete_error=repo.TicketDeleteError('Unable to delete ticket'))
        self.assertIn('Unable to delete ticket', output)
        self.assertNotIn('successfully', output)

    def test_menu_routes_all_features_and_exits_with_six(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=['1', '2', '3', '4', '5', '6']), \
             patch.object(app, 'view_tickets') as view, \
             patch.object(app, 'create_ticket_interactively') as create, \
             patch.object(app, 'search_tickets_interactively') as search, \
             patch.object(app, 'update_ticket_interactively') as update, \
             patch.object(app, 'delete_ticket_interactively') as delete, redirect_stdout(output):
            app.main()
        for action in (view, create, search, update, delete):
            action.assert_called_once_with()
        for option in ('1. View Tickets', '2. Create Ticket', '3. Search Tickets',
                       '4. Update Ticket', '5. Delete Ticket', '6. Exit'):
            self.assertIn(option, output.getvalue())

    def test_cancel_returns_to_menu(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=['5', '7', 'N', '6']), \
             patch.object(app, 'get_ticket', return_value=ticket()), \
             patch.object(app, 'delete_ticket') as delete, redirect_stdout(output):
            app.main()
        delete.assert_not_called()
        self.assertIn('Deletion cancelled', output.getvalue())
        self.assertIn('Goodbye!', output.getvalue())


if __name__ == '__main__':
    unittest.main()
