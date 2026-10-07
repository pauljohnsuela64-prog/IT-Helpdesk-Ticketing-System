"""History and transaction checks using mocks; do not change live data."""
import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from unittest.mock import MagicMock, patch

import mysql.connector

import app
import setup_ticket_history
import ticket_history_repository as history
import ticket_repository as tickets


def ticket(status='Assigned', assigned_to='Mark Santos'):
    return dict(ticket_id=7, employee_name='Alice', department='IT', category='Hardware',
                subject='Printer issue', description='Paper jam', priority='Medium',
                status=status, assigned_to=assigned_to, resolved_at=None,
                assigned_technician_id=12 if assigned_to == 'Mark Santos' else None)


class HistoryRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.connect = patch.object(history, 'get_connection', return_value=self.connection).start()
        self.addCleanup(patch.stopall)

    def test_schema_is_only_history_with_cascading_ticket_foreign_key(self):
        history.setup_ticket_history_table()
        self.cursor.execute.assert_called_once_with(history.CREATE_TICKET_HISTORY_SQL)
        query = ' '.join(self.cursor.execute.call_args.args[0].split())
        self.assertTrue(query.startswith('CREATE TABLE IF NOT EXISTS helpdesk.ticket_history ('))
        self.assertIn('history_id INT AUTO_INCREMENT PRIMARY KEY', query)
        self.assertIn('ticket_id INT NOT NULL', query)
        self.assertIn('action VARCHAR(100) NOT NULL', query)
        self.assertIn('details TEXT', query)
        self.assertIn('created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP', query)
        self.assertIn('REFERENCES helpdesk.tickets (ticket_id) ON DELETE CASCADE', query)
        self.assertNotIn('ALTER ', query)

    def test_read_is_parameterized_and_orders_by_timestamp_then_id(self):
        rows = [dict(history_id=1, ticket_id=7, action='Ticket Created', details='Priority: Medium, Status: Open',
                     created_at=datetime(2026, 10, 5, 10, 0))]
        self.cursor.fetchall.return_value = rows
        self.assertEqual(history.get_ticket_history(7), rows)
        self.cursor.execute.assert_called_once_with(
            'SELECT h.history_id, h.ticket_id, h.action, h.details, h.created_at, '
            'h.performed_by_user_id, u.full_name AS performed_by_full_name, '
            'u.role AS performed_by_role FROM helpdesk.ticket_history AS h '
            'LEFT JOIN helpdesk.users AS u ON u.user_id = h.performed_by_user_id '
            'WHERE h.ticket_id = %s ORDER BY h.created_at, h.history_id', (7,),
        )
        self.connection.commit.assert_not_called()

    def test_invalid_ids_fail_before_connection(self):
        for invalid in (True, 0, -1, '7', '7 OR 1=1', 7.5, 2147483648):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                history.get_ticket_history(invalid)
        self.connect.assert_not_called()

    def test_missing_table_points_to_setup(self):
        self.cursor.execute.side_effect = mysql.connector.Error(errno=1146)
        with self.assertRaisesRegex(history.TicketHistoryReadError, 'python setup_ticket_history.py'):
            history.get_ticket_history(7)

    def test_read_and_setup_errors_hide_private_details(self):
        for failure in (ValueError('private settings'), mysql.connector.Error('private details', errno=2003)):
            self.connect.side_effect = failure
            for action, error_type in ((lambda: history.get_ticket_history(7), history.TicketHistoryReadError),
                                       (history.setup_ticket_history_table, history.TicketHistorySetupError)):
                with self.assertRaises(error_type) as raised:
                    action()
                self.assertNotIn('private', str(raised.exception))


class TicketTransactionTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = ticket()
        self.cursor.lastrowid = 7
        self.connect = patch.object(tickets, 'get_connection', return_value=self.connection).start()
        self.addCleanup(patch.stopall)

    def activity(self):
        entries = [item.args[1] for item in self.cursor.execute.call_args_list
                if item.args[0].startswith('INSERT INTO helpdesk.ticket_history')]
        # These are CLI calls: every event must explicitly have no GUI actor.
        self.assertTrue(all(entry[-1] is None for entry in entries))
        return [entry[:3] for entry in entries]

    def test_creation_logs_actual_defaults_in_same_transaction_and_keeps_ticket_id(self):
        def execute(query, parameters):
            if query.startswith('INSERT INTO helpdesk.ticket_history'):
                self.cursor.lastrowid = 99

        self.cursor.execute.side_effect = execute
        created = tickets.create_ticket('Alice', 'IT', 'Hardware', 'PC 3', 'Error 404', 'Medium')
        self.assertEqual(created, 7)
        calls = self.cursor.execute.call_args_list
        self.assertTrue(calls[0].args[0].startswith('INSERT INTO helpdesk.tickets '))
        self.assertIn("CONCAT('Priority: ', priority, ', Status: ', status)", calls[1].args[0])
        self.assertEqual(calls[1].args[1], ('Ticket Created', None, 7))
        self.connect.assert_called_once_with()
        self.connection.commit.assert_called_once_with()

    def test_status_priority_and_important_information_changes_are_logged(self):
        tickets.update_ticket(7, {'status': 'In Progress', 'priority': 'High', 'subject': 'Error 404'})
        self.assertEqual(self.activity(), [
            (7, 'Status Changed', 'Assigned -> In Progress'),
            (7, 'Priority Changed', 'Medium -> High'),
            (7, 'Ticket Information Updated', 'Subject: Printer issue -> Error 404'),
        ])
        self.connection.commit.assert_called_once_with()
        self.connect.assert_called_once_with()

    def test_all_important_fields_have_old_and_new_values(self):
        tickets.update_ticket(7, dict(employee_name='Bob', department='HR', category='Software',
                                     subject='Error 404', description='Reinstall app'))
        entries = self.activity()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1], 'Ticket Information Updated')
        for detail in ('Employee name: Alice -> Bob', 'Department: IT -> HR',
                       'Category: Hardware -> Software', 'Subject: Printer issue -> Error 404',
                       'Description: Paper jam -> Reinstall app'):
            self.assertIn(detail, entries[0][2])

    def test_assignment_and_automatic_status_change_are_both_logged(self):
        self.cursor.fetchone.side_effect = [ticket('Open', None), {'technician_id': 35, 'full_name': 'Anna Reyes'}]
        tickets.update_ticket(7, {}, technician_id=35)
        self.assertEqual(self.activity(), [
            (7, 'Technician Assigned', 'Anna Reyes'),
            (7, 'Status Changed', 'Open -> Assigned'),
        ])

    def test_reassignment_and_unassignment_details(self):
        self.cursor.fetchone.side_effect = [ticket(), {'technician_id': 35, 'full_name': 'Anna Reyes'}]
        tickets.update_ticket(7, {}, technician_id=35)
        self.assertEqual(self.activity(), [(7, 'Technician Reassigned', 'Mark Santos -> Anna Reyes')])
        self.cursor.execute.reset_mock()
        self.cursor.fetchone.side_effect = None
        self.cursor.fetchone.return_value = ticket()
        tickets.update_ticket(7, {'assigned_to': None})
        self.assertEqual(self.activity(), [(7, 'Technician Unassigned', 'Mark Santos')])

    def test_no_changes_produce_no_activity(self):
        self.assertFalse(tickets.update_ticket(7, {'subject': 'Printer issue', 'priority': 'Medium'}))
        self.assertEqual(self.activity(), [])
        self.connection.commit.assert_not_called()

    def test_reselecting_same_name_does_not_create_activity(self):
        self.cursor.fetchone.side_effect = [ticket(), {'technician_id': 12, 'full_name': 'Mark Santos'}]
        self.assertFalse(tickets.update_ticket(7, {}, technician_id=12))
        self.assertEqual(self.activity(), [])

    def test_unchanged_fields_are_not_included_with_a_real_change(self):
        tickets.update_ticket(7, {'subject': 'Printer issue', 'priority': 'High'})
        self.assertEqual(self.activity(), [(7, 'Priority Changed', 'Medium -> High')])

    def test_history_uses_bound_values_for_stored_text(self):
        value = "Printer's fault'; --"
        tickets.update_ticket(7, {'subject': value})
        query, parameters = self.cursor.execute.call_args.args
        self.assertNotIn(value, query)
        self.assertEqual(parameters, (7, 'Ticket Information Updated', f'Subject: Printer issue -> {value}', None))

    def test_long_descriptions_are_not_truncated_on_the_ticket(self):
        self.cursor.fetchone.return_value = {**ticket(), 'description': '🙂' * 16000}
        new_description = '3' * 65000
        tickets.update_ticket(7, {'description': new_description})
        update = next(item.args for item in self.cursor.execute.call_args_list
                      if item.args[0].startswith('UPDATE helpdesk.tickets '))
        self.assertEqual(update[1][4], new_description)
        details = self.activity()[0][2]
        self.assertLess(len(details.encode('utf-8')), 65535)
        self.assertIn('...', details)

    def test_create_history_failure_rolls_back_ticket_creation(self):
        self.cursor.execute.side_effect = [None, mysql.connector.Error(errno=1146)]
        with self.assertRaisesRegex(tickets.TicketCreateError, 'python setup_ticket_history.py'):
            tickets.create_ticket('Alice', 'IT', 'Hardware', 'PC 3', 'Error 404', 'Medium')
        self.connection.rollback.assert_called_once_with()
        self.connection.commit.assert_not_called()

    def test_update_history_failure_rolls_back_ticket_and_previous_history_inserts(self):
        self.cursor.execute.side_effect = [None, None, None, mysql.connector.Error(errno=1142)]
        with self.assertRaises(tickets.TicketUpdateError):
            tickets.update_ticket(7, {'status': 'In Progress', 'priority': 'High'})
        self.assertEqual(len(self.activity()), 2)
        self.connection.rollback.assert_called_once_with()
        self.connection.commit.assert_not_called()

    def test_failed_ticket_update_does_not_insert_activity(self):
        self.cursor.execute.side_effect = [None, mysql.connector.Error(errno=2003)]
        with self.assertRaises(tickets.TicketUpdateError):
            tickets.update_ticket(7, {'priority': 'High'})
        self.assertEqual(self.activity(), [])


class CliTests(unittest.TestCase):
    def run_history(self, raw_id, existing=None, entries=None, error=None):
        output = io.StringIO()
        with patch('builtins.input', return_value=raw_id), \
             patch.object(app, 'get_ticket', return_value=existing) as lookup, \
             patch.object(app, 'get_ticket_history', return_value=entries or [], side_effect=error) as read, \
             redirect_stdout(output):
            app.view_ticket_history()
        return output.getvalue(), lookup, read

    def test_invalid_ids_do_not_reach_database(self):
        for invalid in ('', 'abc', '-1', '0', '2147483648', '7 OR 1=1'):
            with self.subTest(invalid=invalid):
                output, lookup, read = self.run_history(invalid)
                lookup.assert_not_called()
                read.assert_not_called()
                self.assertIn('Ticket ID must be a positive number', output)

    def test_ticket_must_exist_before_reading_history(self):
        output, lookup, read = self.run_history('7')
        lookup.assert_called_once_with(7)
        read.assert_not_called()
        self.assertIn('No ticket found', output)

    def test_empty_history_is_friendly(self):
        output, _, read = self.run_history('7', ticket())
        read.assert_called_once_with(7)
        self.assertIn('No activity recorded for this ticket yet', output)

    def test_timestamp_and_entries_are_displayed_in_repository_order(self):
        entries = [
            dict(created_at=datetime(2026, 10, 5, 10, 0), action='Ticket Created', details='Priority: Medium, Status: Open'),
            dict(created_at=datetime(2026, 10, 5, 10, 15), action='Technician Assigned', details='Mark Santos'),
        ]
        output, _, _ = self.run_history('7', ticket(), entries)
        first = '2026-10-05 10:00 | Ticket Created | Priority: Medium, Status: Open'
        second = '2026-10-05 10:15 | Technician Assigned | Mark Santos'
        self.assertIn(first, output)
        self.assertIn(second, output)
        self.assertLess(output.index(first), output.index(second))

    def test_stored_control_characters_are_escaped(self):
        output, _, _ = self.run_history('7', ticket(), [
            dict(created_at=None, action='Ticket Information Updated', details='Subject: old -> new\nline\x1b'),
        ])
        self.assertIn('new\\nline\\x1b', output)

    def test_read_errors_are_friendly(self):
        output, _, _ = self.run_history('7', ticket(), error=history.TicketHistoryReadError('Unable to read history'))
        self.assertIn('Unable to read history', output)

    def test_cancelled_ticket_update_never_calls_repository(self):
        current = {**ticket(), 'created_at': None, 'updated_at': None}
        with patch('builtins.input', side_effect=['7', '', '', '', 'Changed subject', '', '', '', '', 'n']), \
             patch.object(app, 'get_ticket', return_value=current), \
             patch.object(app, 'get_active_technicians', return_value=[]), \
             patch.object(app, 'update_ticket') as update, redirect_stdout(io.StringIO()):
            app.update_ticket_interactively()
        update.assert_not_called()

    def test_main_menu_routes_history_and_exits_with_nine(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=['7', '9']), \
             patch.object(app, 'view_ticket_history') as view, redirect_stdout(output):
            app.main()
        view.assert_called_once_with()
        self.assertIn('7. View Ticket History', output.getvalue())
        self.assertIn('9. Exit', output.getvalue())


class SetupCommandTests(unittest.TestCase):
    def test_setup_reports_success_and_failure_exit_codes(self):
        output = io.StringIO()
        with patch.object(setup_ticket_history, 'setup_ticket_history_table'), redirect_stdout(output):
            self.assertEqual(setup_ticket_history.main(), 0)
        self.assertIn('helpdesk.ticket_history is available', output.getvalue())
        with patch.object(setup_ticket_history, 'setup_ticket_history_table',
                          side_effect=history.TicketHistorySetupError('Setup failed')), redirect_stdout(output):
            self.assertEqual(setup_ticket_history.main(), 1)
        self.assertIn('Setup failed', output.getvalue())


if __name__ == '__main__':
    unittest.main()
