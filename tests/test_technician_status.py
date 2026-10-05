"""Technician status checks using mocks; never modify a live database."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, call, patch

import mysql.connector

import app
import technician_repository as repo
import ticket_repository as tickets


def technician(status='Active'):
    return dict(technician_id=12, full_name='Alex Reyes', email='alex@example.com', status=status)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = technician()
        self.connect = patch.object(repo, 'get_connection', return_value=self.connection).start()
        self.addCleanup(patch.stopall)

    def test_status_updates_are_parameterized_and_touch_only_status(self):
        for old, new in (('Active', 'Inactive'), ('Inactive', 'Active')):
            with self.subTest(old=old, new=new):
                self.connection.reset_mock()
                self.cursor.fetchone.return_value = technician(old)
                self.assertTrue(repo.update_technician_status(12, new))
                self.assertEqual(self.cursor.execute.call_args_list, [
                    call('SELECT technician_id, status FROM helpdesk.technicians '
                         'WHERE technician_id = %s FOR UPDATE', (12,)),
                    call('UPDATE helpdesk.technicians SET status = %s WHERE technician_id = %s', (new, 12)),
                ])
                self.connection.commit.assert_called_once_with()
                self.connection.rollback.assert_not_called()

    def test_exact_lookup_includes_inactive_technicians(self):
        self.cursor.fetchall.return_value = [technician('Inactive')]
        self.assertEqual(repo.get_technician(12), technician('Inactive'))
        self.cursor.execute.assert_called_once_with(
            'SELECT technician_id, full_name, email, status '
            'FROM helpdesk.technicians WHERE technician_id = %s', (12,),
        )
        self.cursor.fetchall.return_value = []
        self.assertIsNone(repo.get_technician(999))

    def test_invalid_ids_and_statuses_fail_before_connection(self):
        for invalid in (True, 0, -1, '12', '12 OR 1=1', 1.5, 2147483648):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    repo.get_technician(invalid)
                with self.assertRaises(ValueError):
                    repo.update_technician_status(invalid, 'Inactive')
        for invalid in ('', ' ', 'Pending', 'active', 'Inactive; DELETE', None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                repo.update_technician_status(12, invalid)
        self.connect.assert_not_called()

    def test_nonexistent_technician_rolls_back_without_updating(self):
        self.cursor.fetchone.return_value = None
        with self.assertRaisesRegex(repo.TechnicianUpdateError, 'No technician found'):
            repo.update_technician_status(12, 'Inactive')
        self.assertEqual(self.cursor.execute.call_count, 1)
        self.connection.rollback.assert_called_once_with()
        self.connection.commit.assert_not_called()

    def test_same_status_does_not_update(self):
        self.assertFalse(repo.update_technician_status(12, 'Active'))
        self.assertEqual(self.cursor.execute.call_count, 1)
        self.connection.rollback.assert_called_once_with()
        self.connection.commit.assert_not_called()

    def test_select_and_update_errors_roll_back(self):
        for failures in ([mysql.connector.Error(errno=2003)],
                         [None, mysql.connector.Error(errno=1142)]):
            with self.subTest(stage=len(failures)):
                self.connection.reset_mock()
                self.cursor.execute.side_effect = failures
                with self.assertRaises(repo.TechnicianUpdateError):
                    repo.update_technician_status(12, 'Inactive')
                self.connection.rollback.assert_called_once_with()
                self.connection.commit.assert_not_called()

    def test_commit_error_does_not_report_success(self):
        self.connection.commit.side_effect = mysql.connector.Error(errno=2013)
        with self.assertRaisesRegex(repo.TechnicianUpdateError, 'could not be confirmed'):
            repo.update_technician_status(12, 'Inactive')
        self.connection.rollback.assert_called_once_with()

    def test_rollback_error_preserves_original_error(self):
        self.cursor.execute.side_effect = mysql.connector.Error(errno=1142)
        self.connection.rollback.side_effect = mysql.connector.Error(errno=2003)
        with self.assertRaisesRegex(repo.TechnicianUpdateError, '1142'):
            repo.update_technician_status(12, 'Inactive')

    def test_configuration_and_connection_errors_hide_private_details(self):
        for error in (ValueError('private settings'),
                      mysql.connector.Error('private connection details', errno=2003)):
            self.connect.side_effect = error
            with self.assertRaises(repo.TechnicianUpdateError) as raised:
                repo.update_technician_status(12, 'Inactive')
            self.assertNotIn('private', str(raised.exception))


class CliTests(unittest.TestCase):
    def run_change(self, inputs, current=None, listed=None, read_error=None, save_error=None, saved=True):
        output = io.StringIO()
        with patch('builtins.input', side_effect=inputs) as prompt, \
             patch.object(app, 'get_technicians', return_value=[technician()] if listed is None else listed), \
             patch.object(app, 'get_technician', return_value=current, side_effect=read_error) as lookup, \
             patch.object(app, 'update_technician_status', return_value=saved, side_effect=save_error) as save, \
             redirect_stdout(output):
            app.change_technician_status_interactively()
        return output.getvalue(), prompt, lookup, save

    def test_displays_existing_technicians_before_prompting_for_id(self):
        output = io.StringIO()

        def input_response(prompt):
            for value in technician().values():
                self.assertIn(str(value), output.getvalue())
            for label in ('Technician ID', 'Full Name', 'Email', 'Status'):
                self.assertIn(label, output.getvalue())
            return 'abc'

        with patch('builtins.input', side_effect=input_response), \
             patch.object(app, 'get_technicians', return_value=[technician()]), redirect_stdout(output):
            app.change_technician_status_interactively()

    def test_confirmation_saves_both_status_transitions(self):
        for old, new in (('Active', 'Inactive'), ('Inactive', 'Active')):
            with self.subTest(old=old, new=new):
                output, _, lookup, save = self.run_change(['12', new.lower(), 'YES'], technician(old))
                lookup.assert_called_once_with(12)
                save.assert_called_once_with(12, new)
                self.assertIn(f'{old} -> {new}', output)
                self.assertIn(f'status changed to {new} successfully', output)

    def test_invalid_ids_never_reach_lookup_or_update(self):
        for value in ('', ' ', 'abc', '0', '-1', '12.5', '2147483648', '12 OR 1=1'):
            with self.subTest(value=value):
                output, _, lookup, save = self.run_change([value])
                lookup.assert_not_called()
                save.assert_not_called()
                self.assertIn('Technician ID must be a positive number', output)

    def test_nonexistent_id_returns_friendly_message(self):
        output, _, _, save = self.run_change(['999'])
        save.assert_not_called()
        self.assertIn('No technician found', output)

    def test_empty_list_does_not_prompt(self):
        output, prompt, lookup, save = self.run_change([], listed=[])
        prompt.assert_not_called()
        lookup.assert_not_called()
        save.assert_not_called()
        self.assertIn('No technicians found yet', output)

    def test_invalid_status_reprompts(self):
        output, _, _, save = self.run_change(['12', '', 'Pending', '3', ' inactive ', 'y'], technician())
        save.assert_called_once_with(12, 'Inactive')
        self.assertEqual(output.count('Enter one of: Active, Inactive'), 3)

    def test_same_status_skips_confirmation_and_save(self):
        output, prompt, _, save = self.run_change(['12', 'Active'], technician())
        self.assertEqual(prompt.call_count, 2)
        save.assert_not_called()
        self.assertIn('already Active', output)

    def test_cancellation_never_saves(self):
        for confirmation in ('', 'n', 'NO', 'maybe'):
            with self.subTest(confirmation=confirmation):
                output, _, _, save = self.run_change(['12', 'Inactive', confirmation], technician())
                save.assert_not_called()
                self.assertIn('Status change cancelled', output)

    def test_confirmation_interruptions_cancel(self):
        for error in (EOFError(), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                output, _, _, save = self.run_change(['12', 'Inactive', error], technician())
                save.assert_not_called()
                self.assertIn('Status change cancelled', output)

    def test_database_errors_are_friendly(self):
        output, _, _, save = self.run_change(['12'], read_error=repo.TechnicianReadError('Unable to retrieve technician'))
        save.assert_not_called()
        self.assertIn('Unable to retrieve technician', output)
        output, _, _, _ = self.run_change(['12', 'Inactive', 'y'], technician(),
                                         save_error=repo.TechnicianUpdateError('Unable to change status'))
        self.assertIn('Unable to change status', output)
        self.assertNotIn('successfully', output)

    def test_cancel_returns_to_submenu_and_back_returns_to_main_menu(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=['6', '3', '12', 'Inactive', 'n', '4', '7']), \
             patch.object(app, 'get_technicians', return_value=[technician()]), \
             patch.object(app, 'get_technician', return_value=technician()), \
             patch.object(app, 'update_technician_status') as save, redirect_stdout(output):
            app.main()
        save.assert_not_called()
        self.assertIn('3. Change Technician Status', output.getvalue())
        self.assertIn('4. Back', output.getvalue())
        self.assertIn('Status change cancelled', output.getvalue())
        self.assertIn('Goodbye!', output.getvalue())


class StatusLifecycleTests(unittest.TestCase):
    def test_inactive_rows_and_ticket_names_are_retained_and_reactivation_restores_selection(self):
        # Model persisted rows so the real repositories share the same changing state.
        stored = technician()
        assigned_ticket = dict(ticket_id=7, employee_name='Alice', department='IT', category='Hardware',
                               subject='PC 3', description='Error 404', priority='Medium', status='Assigned',
                               assigned_to=stored['full_name'], resolved_at=None)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value

        def execute(query, parameters=None):
            if query.startswith('UPDATE helpdesk.technicians SET status'):
                self.assertEqual(parameters[1], stored['technician_id'])
                stored['status'] = parameters[0]
                return
            if 'FROM helpdesk.technicians' in query:
                is_active_query = 'WHERE status = %s' in query or 'AND status = %s' in query
                rows = [] if is_active_query and stored['status'] != 'Active' else [stored.copy()]
            elif 'FROM helpdesk.tickets' in query and query.startswith('SELECT '):
                rows = [assigned_ticket.copy()]
            else:
                self.fail(f'Unexpected database operation: {query}')
            cursor.fetchall.return_value = rows
            cursor.fetchone.return_value = rows[0] if rows else None

        cursor.execute.side_effect = execute
        with patch.object(repo, 'get_connection', return_value=connection), \
             patch.object(tickets, 'get_connection', return_value=connection):
            self.assertEqual(len(repo.get_active_technicians()), 1)
            repo.update_technician_status(12, 'Inactive')
            self.assertEqual(repo.get_technician(12), technician('Inactive'))
            self.assertEqual(repo.get_technicians(), [technician('Inactive')])
            self.assertEqual(repo.get_active_technicians(), [])
            self.assertEqual(tickets.get_tickets()[0]['assigned_to'], 'Alex Reyes')
            output = io.StringIO()
            with redirect_stdout(output):
                app.view_tickets()
            self.assertIn('Alex Reyes', output.getvalue())
            with self.assertRaises(tickets.TicketUpdateError):
                tickets.update_ticket(7, {}, technician_id=12)
            with patch('builtins.input', return_value=''), redirect_stdout(output):
                self.assertEqual(app.prompt_assigned_technician('Alex Reyes'), ('Alex Reyes', None))
            self.assertIn('No active technicians are available', output.getvalue())
            repo.update_technician_status(12, 'Active')
            self.assertEqual(repo.get_active_technicians(), [technician()])
            with patch('builtins.input', return_value='1'), redirect_stdout(output):
                self.assertEqual(app.prompt_assigned_technician('Alex Reyes'), ('Alex Reyes', 12))
            self.assertEqual(tickets.get_tickets()[0]['assigned_to'], 'Alex Reyes')
        updates = [call.args[0] for call in cursor.execute.call_args_list if call.args[0].startswith('UPDATE ')]
        self.assertEqual(updates, [
            'UPDATE helpdesk.technicians SET status = %s WHERE technician_id = %s',
            'UPDATE helpdesk.technicians SET status = %s WHERE technician_id = %s',
        ])


if __name__ == '__main__':
    unittest.main()
