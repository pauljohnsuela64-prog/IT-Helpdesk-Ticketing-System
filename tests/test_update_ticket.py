"""Update feature checks using mocks; never connect to or modify a database."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import mysql.connector
import app
import ticket_repository as repo


def ticket(status='Open', resolved_at=None):
    return dict(ticket_id=7, employee_name='Alice', department='IT', category='Hardware',
                subject='Printer issue', description='Paper jam', priority='Medium',
                status=status, assigned_to=None, created_at='created', updated_at='updated',
                resolved_at=resolved_at)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = ticket()
        self.mock_connection = patch.object(repo, 'get_connection', return_value=self.connection).start()
        patch.object(repo, 'record_ticket_updated').start()
        self.addCleanup(patch.stopall)

    def test_parameterized_update_and_system_fields(self):
        value = "Printer's fault'; DROP TABLE tickets; --"
        self.assertTrue(repo.update_ticket(7, {'subject': value}))
        query, params = self.cursor.execute.call_args.args
        self.assertTrue(query.startswith('UPDATE helpdesk.tickets SET '))
        self.assertNotIn(value, query)
        self.assertEqual(query.count('%s'), len(params))
        self.assertEqual(params[3], value)
        self.assertEqual(params[-1], 7)
        self.assertNotIn('created_at', query)
        self.assertNotIn('updated_at', query)
        self.assertNotIn('ticket_id =', query.split('WHERE')[0])
        self.connection.commit.assert_called_once()

    def test_resolution_transitions(self):
        for old, new, timestamp, expected_flag, expected_time in (
            ('Open', 'Resolved', None, True, None),
            ('Resolved', 'Resolved', 'old time', False, 'old time'),
            ('Resolved', 'Open', 'old time', False, None),
            ('Resolved', 'Closed', 'old time', False, None),
        ):
            with self.subTest(old=old, new=new):
                self.cursor.fetchone.return_value = ticket(old, timestamp)
                repo.update_ticket(7, {'status': new, 'subject': 'Changed'})
                params = self.cursor.execute.call_args.args[1]
                self.assertEqual(params[-3:-1], (expected_flag, expected_time))

    def test_missing_and_unchanged_do_not_update(self):
        self.cursor.fetchone.return_value = None
        with self.assertRaises(repo.TicketUpdateError):
            repo.update_ticket(7, {'subject': 'New'})
        self.assertEqual(self.cursor.execute.call_count, 1)
        self.connection.commit.assert_not_called()
        self.cursor.reset_mock()
        self.cursor.fetchone.return_value = ticket()
        self.assertFalse(repo.update_ticket(7, {'subject': 'Printer issue'}))
        self.assertEqual(self.cursor.execute.call_count, 1)
        self.connection.commit.assert_not_called()

    def test_validation_before_connection(self):
        for changes in ({'category': 'Invalid'}, {'priority': 'Urgent'}, {'status': 'Done'},
                        {'employee_name': ' '}, {'assigned_to': 'a' * 101},
                        {'description': 'é' * 32768}, {'subject': 'x' * 151},
                        {'ticket_id': 9}, {'created_at': 'now'}, {'resolved_at': 'now'}):
            with self.subTest(changes=list(changes)):
                with self.assertRaises(ValueError):
                    repo.update_ticket(7, changes)
        self.mock_connection.assert_not_called()

    def test_database_error_rolls_back(self):
        self.cursor.execute.side_effect = [None, mysql.connector.Error(errno=2003)]
        with self.assertRaises(repo.TicketUpdateError):
            repo.update_ticket(7, {'subject': 'New'})
        self.connection.rollback.assert_called_once()
        self.connection.commit.assert_not_called()

    def test_exact_id_lookup(self):
        self.cursor.fetchall.return_value = [ticket()]
        self.assertEqual(repo.get_ticket(7), ticket())
        query, params = self.cursor.execute.call_args.args
        self.assertIn('WHERE ticket_id = %s', query)
        self.assertEqual(params, (7,))
        self.cursor.fetchall.return_value = []
        self.assertIsNone(repo.get_ticket(7))
        for invalid in (0, -1, '7', True, 2147483648):
            with self.assertRaises(ValueError):
                repo.get_ticket(invalid)


class CliTests(unittest.TestCase):
    def run_edit(self, inputs, current=None, error=None):
        output = io.StringIO()
        with patch('builtins.input', side_effect=inputs), \
             patch.object(app, 'get_active_technicians', return_value=[{'technician_id': 12, 'full_name': 'Sam'}]), \
             patch.object(app, 'get_ticket', return_value=current), \
             patch.object(app, 'update_ticket', return_value=True, side_effect=error) as save, \
             redirect_stdout(output):
            app.update_ticket_interactively()
        return output.getvalue(), save

    def test_blank_values_keep_existing(self):
        output, save = self.run_edit(['7'] + [''] * 8, ticket())
        save.assert_not_called()
        self.assertIn('No changes made', output)
        self.assertIn('Paper jam', output)

    def test_confirmation_and_cancel(self):
        for answer in ('n', '', 'yes'):
            output, save = self.run_edit(['7', 'Bob'] + [''] * 7 + [answer], ticket())
            if answer == 'yes':
                save.assert_called_once_with(7, {'employee_name': 'Bob'})
                self.assertIn('successfully', output)
            else:
                save.assert_not_called()
                self.assertIn('cancelled', output)

    def test_validation_reprompts_and_normalizes(self):
        output, save = self.run_edit(
            ['7', '', '', 'bad', 'software', '', '', 'urgent', 'HIGH', 'done', 'in progress', '1', 'yes'], ticket())
        save.assert_called_once_with(7, {'category': 'Software', 'priority': 'High',
                                       'status': 'In Progress'}, technician_id=12)
        self.assertIn('Enter one of:', output)

    def test_invalid_missing_and_database_errors(self):
        for value in ('abc', '0', '-1', "7 OR 1=1"):
            output, save = self.run_edit([value], None)
            save.assert_not_called()
        output, save = self.run_edit(['7'], None)
        self.assertIn('No ticket found', output)
        output, save = self.run_edit(['7', 'Bob'] + [''] * 7 + ['yes'], ticket(), repo.TicketUpdateError('Unable to save'))
        self.assertIn('Unable to save', output)

    def test_existing_menu_routes_and_new_exit(self):
        with patch('builtins.input', side_effect=['1', '2', '3', '4', '8']), \
             patch.object(app, 'view_tickets') as view, \
             patch.object(app, 'create_ticket_interactively') as create, \
             patch.object(app, 'search_tickets_interactively') as search, \
             patch.object(app, 'update_ticket_interactively') as update, \
             redirect_stdout(io.StringIO()):
            app.main()
        for action in (view, create, search, update):
            action.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
