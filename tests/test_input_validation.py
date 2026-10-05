"""Form validation checks; database operations use mocked connections only."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import app
import technician_repository as technicians
import ticket_repository as tickets


INVALID_TICKET_INPUTS = {
    'employee_name': ('', '  ', '3', '12345', '!@#', '12-34', '１２３'),
    'department': ('', '  ', '3', '12345', '!@#', '12-34'),
    'subject': ('', '  ', '3', 'ab', ' 12 '),
    'description': ('', '  ', '3', 'four', ' 1234 '),
}


def current_ticket():
    return dict(ticket_id=7, employee_name='Alice', department='IT', category='Hardware',
                subject='Printer issue', description='Paper jam', priority='Medium',
                status='Open', assigned_to=None, created_at='created time',
                updated_at='updated time', resolved_at=None)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.lastrowid = 7
        self.cursor.fetchone.return_value = current_ticket()
        self.ticket_connect = patch.object(tickets, 'get_connection', return_value=self.connection).start()
        self.technician_connect = patch.object(technicians, 'get_connection', return_value=self.connection).start()
        patch.object(tickets, 'record_ticket_created').start()
        patch.object(tickets, 'record_ticket_updated').start()
        self.addCleanup(patch.stopall)

    def test_invalid_ticket_values_fail_for_create_and_update_before_connection(self):
        for field, invalid_values in INVALID_TICKET_INPUTS.items():
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    inputs = dict(employee_name='Alice', department='IT', category='Hardware',
                                  subject='PC 3', description='Error 404', priority='Medium')
                    inputs[field] = value
                    with self.assertRaises(ValueError):
                        tickets.create_ticket(**inputs)
                    with self.assertRaises(ValueError):
                        tickets.update_ticket(7, {field: value})
        self.ticket_connect.assert_not_called()

    def test_invalid_technician_names_fail_before_connection(self):
        for value in ('', ' ', '3', '12345', '!@#', '12-34', '１２３'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                technicians.add_technician(value, 'test@example.com')
        self.technician_connect.assert_not_called()

    def test_create_trims_text_and_allows_letters_mixed_with_numbers(self):
        self.assertEqual(tickets.create_ticket(
            '  José 3  ', '  IT 2  ', 'Hardware', '  PC 3  ', '  Error 404  ', 'Medium'), 7)
        self.assertEqual(self.cursor.execute.call_args.args[1],
                         ('José 3', 'IT 2', 'Hardware', 'PC 3', 'Error 404', 'Medium'))
        self.connection.commit.assert_called_once_with()

    def test_numeric_subjects_and_descriptions_at_minimum_length_are_valid(self):
        for subject, description in (('123', '12345'), ('PC 3', 'Error 404')):
            with self.subTest(subject=subject, description=description):
                tickets.create_ticket('A', 'IT', 'Hardware', subject, description, 'Medium')
                params = self.cursor.execute.call_args.args[1]
                self.assertEqual(params[3:5], (subject, description))
                tickets.update_ticket(7, {'subject': subject, 'description': description})
                params = self.cursor.execute.call_args.args[1]
                self.assertEqual(params[3:5], (subject, description))

    def test_update_trims_all_changed_fields(self):
        self.assertTrue(tickets.update_ticket(7, {
            'employee_name': '  李 3  ', 'department': '  IT 2  ',
            'subject': '  PC 3  ', 'description': '  12345  ',
        }))
        params = self.cursor.execute.call_args.args[1]
        self.assertEqual(params[0:2], ('李 3', 'IT 2'))
        self.assertEqual(params[3:5], ('PC 3', '12345'))

    def test_technician_name_with_unicode_letter_is_accepted_and_trimmed(self):
        self.assertEqual(technicians.add_technician('  李 3  ', ' test@example.com '), 7)
        self.assertEqual(self.cursor.execute.call_args.args[1], ('李 3', 'test@example.com'))

    def test_unedited_legacy_values_are_preserved_when_updating_other_fields(self):
        legacy = {**current_ticket(), 'employee_name': '3', 'department': '123',
                  'subject': '3', 'description': '1234'}
        self.cursor.fetchone.return_value = legacy
        self.assertTrue(tickets.update_ticket(7, {'priority': 'High'}))
        params = self.cursor.execute.call_args.args[1]
        self.assertEqual(params[:2], ('3', '123'))
        self.assertEqual(params[3:5], ('3', '1234'))

    def test_numeric_repository_search_remains_allowed(self):
        self.cursor.fetchall.return_value = [current_ticket()]
        self.assertEqual(tickets.search_tickets(' 3 '), [current_ticket()])
        query, params = self.cursor.execute.call_args.args
        self.assertTrue(query.startswith('SELECT '))
        self.assertEqual(params, ('%3%',) * 8)
        self.connection.commit.assert_not_called()


class CliTests(unittest.TestCase):
    def test_create_reprompts_invalid_values_and_sends_trimmed_valid_text(self):
        inputs = ['3', '!@#', ' Alice ', '3', ' IT ', 'Hardware',
                  '3', 'ab', ' PC 3 ', '1234', ' 12345 ', 'Medium']
        output = io.StringIO()
        with patch('builtins.input', side_effect=inputs), \
             patch.object(app, 'create_ticket', return_value=7) as create, redirect_stdout(output):
            app.create_ticket_interactively()
        create.assert_called_once_with('Alice', 'IT', 'Hardware', 'PC 3', '12345', 'Medium')
        self.assertEqual(output.getvalue().count('must contain at least one alphabetic letter'), 3)
        self.assertEqual(output.getvalue().count('Subject must be at least 3 characters'), 2)
        self.assertIn('Description must be at least 5 characters', output.getvalue())
        self.assertIn('Ticket created successfully', output.getvalue())

    def test_update_reprompts_invalid_values_using_the_same_rules(self):
        inputs = ['7', '3', ' Bob ', '!@#', ' HR ', '', '12', ' 123 ',
                  '1234', ' 12345 ', '', '', '', 'yes']
        output = io.StringIO()
        with patch('builtins.input', side_effect=inputs), \
             patch.object(app, 'get_ticket', return_value=current_ticket()), \
             patch.object(app, 'get_active_technicians', return_value=[]), \
             patch.object(app, 'update_ticket', return_value=True) as update, redirect_stdout(output):
            app.update_ticket_interactively()
        update.assert_called_once_with(7, {'employee_name': 'Bob', 'department': 'HR',
                                          'subject': '123', 'description': '12345'})
        self.assertIn('Employee name must contain at least one alphabetic letter', output.getvalue())
        self.assertIn('Department must contain at least one alphabetic letter', output.getvalue())
        self.assertIn('Subject must be at least 3 characters', output.getvalue())
        self.assertIn('Description must be at least 5 characters', output.getvalue())

    def test_enter_or_whitespace_keeps_existing_values_without_revalidating_them(self):
        for label, field, limit, value in (
            ('Employee name', 'employee_name', 100, '3'), ('Department', 'department', 100, '123'),
            ('Subject', 'subject', 150, '3'), ('Description', 'description', None, '1234'),
        ):
            for blank in ('', '   '):
                with self.subTest(field=field, blank=blank), \
                     patch('builtins.input', return_value=blank), redirect_stdout(io.StringIO()):
                    self.assertEqual(app.prompt_edit(label, field, value, limit, None), value)

    def test_add_technician_reprompts_numeric_or_symbol_only_names(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=['3', '!@#', ' José 3 ', ' test@example.com ']), \
             patch.object(app, 'add_technician', return_value=12) as add, redirect_stdout(output):
            app.add_technician_interactively()
        add.assert_called_once_with('José 3', 'test@example.com')
        self.assertEqual(output.getvalue().count('Full name must contain at least one alphabetic letter'), 2)

    def test_search_accepts_single_digit(self):
        output = io.StringIO()
        with patch('builtins.input', return_value=' 3 '), \
             patch.object(app, 'search_tickets', return_value=[]) as search, redirect_stdout(output):
            app.search_tickets_interactively()
        search.assert_called_once_with('3')
        self.assertNotIn('must contain', output.getvalue())
        self.assertNotIn('must be at least', output.getvalue())


if __name__ == '__main__':
    unittest.main()
