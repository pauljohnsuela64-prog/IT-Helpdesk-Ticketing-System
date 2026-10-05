"""Assignment checks with mocked connections; never modify live tickets."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, call, patch

import mysql.connector

import app
import technician_repository as technicians
import ticket_repository as tickets


ACTIVE_TECHNICIANS = [
    dict(technician_id=12, full_name='Mark Santos', email='mark@example.com', status='Active'),
    dict(technician_id=35, full_name='Anna Reyes', email='anna@example.com', status='Active'),
]


def ticket(status='Open', assigned_to=None):
    return dict(ticket_id=7, employee_name='Alice', department='IT', category='Hardware',
                subject='Printer issue', description='Paper jam', priority='Medium',
                status=status, assigned_to=assigned_to, created_at='created time',
                updated_at='updated time', resolved_at='resolved time' if status == 'Resolved' else None)


class TechnicianRepositoryTests(unittest.TestCase):
    def test_active_list_uses_parameterized_status_filter(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = ACTIVE_TECHNICIANS
        with patch.object(technicians, 'get_connection', return_value=connection):
            self.assertEqual(technicians.get_active_technicians(), ACTIVE_TECHNICIANS)
        cursor.execute.assert_called_once_with(
            'SELECT technician_id, full_name, email, status '
            'FROM helpdesk.technicians WHERE status = %s ORDER BY technician_id', ('Active',),
        )
        connection.commit.assert_not_called()

    def test_active_lookup_uses_id_and_status_in_update_transaction(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = ACTIVE_TECHNICIANS[1]
        self.assertEqual(technicians.get_active_technician(35, cursor), ACTIVE_TECHNICIANS[1])
        cursor.execute.assert_called_once_with(
            'SELECT technician_id, full_name FROM helpdesk.technicians '
            'WHERE technician_id = %s AND status = %s', (35, 'Active'),
        )
        cursor.fetchone.return_value = None
        self.assertIsNone(technicians.get_active_technician(35, cursor))

    def test_active_list_errors_are_friendly(self):
        for error in (ValueError('private settings'), mysql.connector.Error(errno=2003)):
            with patch.object(technicians, 'get_connection', side_effect=error):
                with self.assertRaises(technicians.TechnicianReadError):
                    technicians.get_active_technicians()


class TicketRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.connect = patch.object(tickets, 'get_connection', return_value=self.connection).start()
        self.addCleanup(patch.stopall)

    def select_for_save(self, current, technician=ACTIVE_TECHNICIANS[1]):
        self.cursor.fetchone.side_effect = [current, technician]

    def saved_values(self):
        query, params = self.cursor.execute.call_args.args
        self.assertTrue(query.startswith('UPDATE helpdesk.tickets SET '))
        return dict(zip(tickets.EDITABLE_FIELDS, params)), params

    def test_assign_saves_database_name_and_changes_open_to_assigned(self):
        self.select_for_save(ticket())
        self.assertTrue(tickets.update_ticket(7, {}, technician_id=35))
        values, params = self.saved_values()
        self.assertEqual(values['assigned_to'], 'Anna Reyes')
        self.assertEqual(values['status'], 'Assigned')
        self.assertEqual(params[-1], 7)
        self.connection.commit.assert_called_once_with()

    def test_other_statuses_are_preserved(self):
        for status in ('Assigned', 'In Progress', 'Resolved', 'Closed'):
            with self.subTest(status=status):
                self.select_for_save(ticket(status))
                tickets.update_ticket(7, {}, technician_id=35)
                values, params = self.saved_values()
                self.assertEqual(values['status'], status)
                self.assertEqual(params[-3], False)
                self.assertEqual(params[-2], 'resolved time' if status == 'Resolved' else None)

    def test_explicit_status_edits_are_preserved(self):
        self.select_for_save(ticket())
        tickets.update_ticket(7, {'status': 'In Progress'}, technician_id=35)
        self.assertEqual(self.saved_values()[0]['status'], 'In Progress')
        self.select_for_save(ticket('Resolved'))
        tickets.update_ticket(7, {'status': 'Open'}, technician_id=35)
        self.assertEqual(self.saved_values()[0]['status'], 'Open')

    def test_inactive_or_missing_technician_cannot_be_saved(self):
        self.select_for_save(ticket(), None)
        with self.assertRaisesRegex(tickets.TicketUpdateError, 'no longer active'):
            tickets.update_ticket(7, {}, technician_id=35)
        self.assertEqual(self.cursor.execute.call_count, 2)
        self.connection.commit.assert_not_called()
        self.connection.rollback.assert_called_once_with()

    def test_names_and_invalid_ids_are_rejected_before_connection(self):
        for changes in ({'assigned_to': 'Anna Reyes'}, {'assigned_to': 'arbitrary name'}):
            with self.assertRaisesRegex(ValueError, 'Select an active technician'):
                tickets.update_ticket(7, changes)
        for technician_id in (True, 0, -1, 1.5, '35', '35 OR 1=1', 2147483648):
            with self.subTest(technician_id=technician_id), self.assertRaises(ValueError):
                tickets.update_ticket(7, {}, technician_id=technician_id)
        with self.assertRaises(ValueError):
            tickets.update_ticket(7, {'assigned_to': None}, technician_id=35)
        self.connect.assert_not_called()

    def test_unassign_clears_name_and_preserves_status(self):
        for status in ('Open', 'Assigned', 'In Progress', 'Resolved', 'Closed'):
            with self.subTest(status=status):
                self.cursor.fetchone.side_effect = None
                self.cursor.fetchone.return_value = ticket(status, 'Mark Santos')
                tickets.update_ticket(7, {'assigned_to': None})
                values, params = self.saved_values()
                self.assertIsNone(values['assigned_to'])
                self.assertEqual(values['status'], status)
                self.assertEqual(params[-2], 'resolved time' if status == 'Resolved' else None)

    def test_keep_existing_assignment_does_not_lookup_technician(self):
        self.cursor.fetchone.return_value = ticket(assigned_to='Legacy name')
        tickets.update_ticket(7, {'subject': 'Changed'})
        values, _ = self.saved_values()
        self.assertEqual(values['assigned_to'], 'Legacy name')
        self.assertEqual(values['status'], 'Open')
        self.assertEqual(self.cursor.execute.call_count, 2)

    def test_reselect_same_name_on_open_ticket_changes_status(self):
        self.select_for_save(ticket(assigned_to='Anna Reyes'))
        self.assertTrue(tickets.update_ticket(7, {}, technician_id=35))
        self.assertEqual(self.saved_values()[0]['status'], 'Assigned')

    def test_reselect_unchanged_assignment_and_status_is_no_op(self):
        self.select_for_save(ticket('Assigned', 'Anna Reyes'))
        self.assertFalse(tickets.update_ticket(7, {}, technician_id=35))
        self.assertEqual(self.cursor.execute.call_count, 2)
        self.connection.commit.assert_not_called()

    def test_technician_read_error_rolls_back_ticket_update(self):
        self.cursor.fetchone.return_value = ticket()
        self.cursor.execute.side_effect = [None, mysql.connector.Error(errno=2003)]
        with self.assertRaises(tickets.TicketUpdateError):
            tickets.update_ticket(7, {}, technician_id=35)
        self.connection.commit.assert_not_called()
        self.connection.rollback.assert_called_once_with()

    def test_technician_name_is_bound_as_data(self):
        name = "Anna's name'; --"
        self.select_for_save(ticket(), dict(technician_id=35, full_name=name))
        tickets.update_ticket(7, {}, technician_id=35)
        query, _ = self.cursor.execute.call_args.args
        self.assertNotIn(name, query)
        self.assertEqual(self.saved_values()[0]['assigned_to'], name)


class CliTests(unittest.TestCase):
    def select(self, inputs, current='Legacy name', active=ACTIVE_TECHNICIANS):
        output = io.StringIO()
        with patch('builtins.input', side_effect=inputs), \
             patch.object(app, 'get_active_technicians', return_value=active), redirect_stdout(output):
            result = app.prompt_assigned_technician(current)
        return result, output.getvalue()

    def edit(self, current, selection, confirmation='yes', active=ACTIVE_TECHNICIANS, status=''):
        output = io.StringIO()
        # The first six editable fields are unchanged, followed by Status and Assigned To.
        inputs = ['7'] + [''] * 6 + [status, selection, confirmation]
        with patch('builtins.input', side_effect=inputs), \
             patch.object(app, 'get_ticket', return_value=current), \
             patch.object(app, 'get_active_technicians', return_value=active), \
             patch.object(app, 'update_ticket', return_value=True) as save, redirect_stdout(output):
            app.update_ticket_interactively()
        return output.getvalue(), save

    def test_list_numbers_map_to_actual_ids(self):
        result, output = self.select(['2'])
        self.assertEqual(result, ('Anna Reyes', 35))
        self.assertIn('1. Mark Santos', output)
        self.assertIn('2. Anna Reyes', output)
        self.assertIn('0. Unassign technician', output)

    def test_invalid_numbers_text_and_names_are_rejected(self):
        result, output = self.select(['abc', 'Mark Santos', '3', '-1', '1.5', '9' * 5000, '1'])
        self.assertEqual(result, ('Mark Santos', 12))
        self.assertEqual(output.count('Invalid selection.'), 6)

    def test_keep_and_unassign(self):
        self.assertEqual(self.select([''])[0], ('Legacy name', None))
        self.assertEqual(self.select(['0'])[0], (None, None))

    def test_no_active_technicians_still_allows_keep_or_unassign(self):
        result, output = self.select(['1', ''], active=[])
        self.assertEqual(result, ('Legacy name', None))
        self.assertIn('No active technicians are available', output)
        self.assertIn('Invalid selection.', output)
        self.assertEqual(self.select(['0'], active=[])[0], (None, None))

    def test_assignment_preview_and_save_use_selected_id(self):
        output, save = self.edit(ticket(), '2')
        save.assert_called_once_with(7, {}, technician_id=35)
        self.assertIn('Open -> Assigned', output)
        self.assertIn('Unassigned -> Anna Reyes', output)

    def test_other_status_previews_do_not_automatically_change(self):
        for status in ('In Progress', 'Resolved', 'Closed'):
            with self.subTest(status=status):
                output, save = self.edit(ticket(status), '1')
                save.assert_called_once_with(7, {}, technician_id=12)
                self.assertNotIn('changes an Open ticket', output)
                self.assertNotIn(f'{status} -> Assigned', output)

    def test_assignment_respects_explicit_status_selection(self):
        output, save = self.edit(ticket(), '1', status='In Progress')
        save.assert_called_once_with(7, {'status': 'In Progress'}, technician_id=12)
        self.assertIn('Open -> In Progress', output)
        self.assertNotIn('Open -> Assigned', output)

    def test_enter_keeps_existing_assignment_without_saving(self):
        output, save = self.edit(ticket(assigned_to='Legacy name'), '')
        save.assert_not_called()
        self.assertIn('No changes made.', output)

    def test_unassign_passes_null_and_leaves_status_alone(self):
        output, save = self.edit(ticket('In Progress', 'Mark Santos'), '0')
        save.assert_called_once_with(7, {'assigned_to': None})
        self.assertIn('Mark Santos -> Unassigned', output)

    def test_cancelled_assignment_is_not_saved(self):
        output, save = self.edit(ticket(), '1', 'n')
        save.assert_not_called()
        self.assertIn('Update cancelled', output)

    def test_no_active_technicians_does_not_block_other_edits(self):
        output, save = self.edit(ticket(assigned_to='Legacy name'), '', active=[], status='In Progress')
        save.assert_called_once_with(7, {'status': 'In Progress'})
        self.assertIn('No active technicians are available', output)

    def test_technician_loading_failure_aborts_without_saving(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=['7'] + [''] * 7), \
             patch.object(app, 'get_ticket', return_value=ticket()), \
             patch.object(app, 'get_active_technicians', side_effect=technicians.TechnicianReadError('Unable to read technicians')), \
             patch.object(app, 'update_ticket') as save, redirect_stdout(output):
            app.update_ticket_interactively()
        save.assert_not_called()
        self.assertIn('Unable to read technicians', output.getvalue())

    def test_enter_keeps_assignment_when_editing_other_fields(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=['7', 'Bob'] + [''] * 7 + ['yes']), \
             patch.object(app, 'get_ticket', return_value=ticket(assigned_to='Legacy name')), \
             patch.object(app, 'get_active_technicians', return_value=ACTIVE_TECHNICIANS), \
             patch.object(app, 'update_ticket', return_value=True) as save, redirect_stdout(output):
            app.update_ticket_interactively()
        save.assert_called_once_with(7, {'employee_name': 'Bob'})


if __name__ == '__main__':
    unittest.main()
