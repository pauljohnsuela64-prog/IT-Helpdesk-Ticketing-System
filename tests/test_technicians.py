"""Technician checks with mocked connections; do not change a live database."""
import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import mysql.connector

import app
import setup_technicians
import technician_repository as repo


TECHNICIAN = dict(technician_id=12, full_name='Alex Reyes',
                  email='alex.reyes@example.com', status='Active')


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.lastrowid = 12
        self.connect = patch.object(repo, 'get_connection', return_value=self.connection).start()
        self.addCleanup(patch.stopall)

    def test_add_uses_parameters_and_database_defaults(self):
        name = "Alex O'Brien'; --"
        email = "alex'@example.com"
        self.assertEqual(repo.add_technician(' ' + name + ' ', ' ' + email + ' '), 12)
        query, params = self.cursor.execute.call_args.args
        self.assertEqual(query, 'INSERT INTO helpdesk.technicians (full_name, email) VALUES (%s, %s)')
        self.assertEqual(params, (name, email))
        self.assertNotIn(name, query)
        self.assertNotIn(email, query)
        self.connection.commit.assert_called_once_with()
        self.connection.rollback.assert_not_called()

    def test_blank_or_oversized_fields_fail_before_connection(self):
        for name, email in (('', 'a@example.com'), ('  ', 'a@example.com'),
                            ('Alex', ''), ('Alex', '  '),
                            ('x' * 101, 'a@example.com'), ('Alex', 'x' * 151),
                            (None, 'a@example.com'), ('Alex', None)):
            with self.subTest(name=name, email=email), self.assertRaises(ValueError):
                repo.add_technician(name, email)
        self.connect.assert_not_called()

    def test_exact_length_limits_are_accepted(self):
        self.assertEqual(repo.add_technician('x' * 100, 'x' * 150), 12)

    def test_duplicate_email_rolls_back_with_friendly_message(self):
        self.cursor.execute.side_effect = mysql.connector.Error('private details', errno=1062)
        with self.assertRaisesRegex(repo.TechnicianCreateError, 'email already exists') as raised:
            repo.add_technician('Alex', 'alex@example.com')
        self.assertNotIn('private', str(raised.exception))
        self.connection.rollback.assert_called_once_with()
        self.connection.commit.assert_not_called()

    def test_commit_failure_does_not_report_success(self):
        self.connection.commit.side_effect = mysql.connector.Error(errno=2013)
        with self.assertRaisesRegex(repo.TechnicianCreateError, 'could not be confirmed'):
            repo.add_technician('Alex', 'alex@example.com')
        self.connection.rollback.assert_called_once_with()

    def test_rollback_failure_preserves_original_error(self):
        self.cursor.execute.side_effect = mysql.connector.Error(errno=1062)
        self.connection.rollback.side_effect = mysql.connector.Error(errno=2003)
        with self.assertRaisesRegex(repo.TechnicianCreateError, 'email already exists'):
            repo.add_technician('Alex', 'alex@example.com')

    def test_view_is_read_only_and_does_not_auto_create_table(self):
        self.cursor.fetchall.return_value = [TECHNICIAN]
        self.assertEqual(repo.get_technicians(), [TECHNICIAN])
        self.connection.cursor.assert_called_once_with(dictionary=True)
        self.cursor.execute.assert_called_once_with(
            'SELECT technician_id, full_name, email, status, created_at FROM helpdesk.technicians ORDER BY technician_id'
        )
        self.connection.commit.assert_not_called()
        self.cursor.fetchall.return_value = []
        self.assertEqual(repo.get_technicians(), [])

    def test_missing_table_reports_setup_command(self):
        self.cursor.execute.side_effect = mysql.connector.Error(errno=1146)
        for action, error_type in ((repo.get_technicians, repo.TechnicianReadError),
                                   (lambda: repo.add_technician('Alex', 'alex@example.com'),
                                    repo.TechnicianCreateError)):
            with self.assertRaisesRegex(error_type, 'python setup_technicians.py'):
                action()

    def test_configuration_and_connection_errors_are_safe(self):
        for failure in (ValueError('private settings'),
                        mysql.connector.Error('private connection details', errno=2003)):
            self.connect.side_effect = failure
            for action, error_type in ((repo.get_technicians, repo.TechnicianReadError),
                                       (repo.setup_technicians_table, repo.TechnicianSetupError),
                                       (lambda: repo.add_technician('Alex', 'alex@example.com'),
                                        repo.TechnicianCreateError)):
                with self.subTest(action=action, failure=type(failure).__name__):
                    with self.assertRaises(error_type) as raised:
                        action()
                    self.assertNotIn('private', str(raised.exception))

    def test_setup_creates_only_technician_table(self):
        repo.setup_technicians_table()
        self.cursor.execute.assert_called_once_with(repo.CREATE_TECHNICIANS_SQL)
        query = self.cursor.execute.call_args.args[0]
        self.assertTrue(query.startswith('CREATE TABLE IF NOT EXISTS helpdesk.technicians ('))
        self.assertIn('technician_id INT AUTO_INCREMENT PRIMARY KEY', query)
        self.assertIn('full_name VARCHAR(100) NOT NULL', query)
        self.assertIn('email VARCHAR(150) NOT NULL UNIQUE', query)
        self.assertIn("status VARCHAR(20) NOT NULL DEFAULT 'Active'", query)
        self.assertIn('created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP', query)
        self.assertIn('COLLATE=utf8mb4_unicode_ci', query)
        self.assertNotIn('tickets', query)


class DatabaseGuardTests(unittest.TestCase):
    def test_non_helpdesk_configuration_cannot_connect(self):
        # Exercise the actual shared connection guard, without a network connection.
        with patch.dict(os.environ, {'DB_NAME': 'other_database'}), \
             patch('database.load_dotenv'), patch('database.mysql.connector.connect') as connect:
            for action, error_type in ((repo.get_technicians, repo.TechnicianReadError),
                                       (repo.setup_technicians_table, repo.TechnicianSetupError),
                                       (lambda: repo.add_technician('Alex', 'alex@example.com'),
                                        repo.TechnicianCreateError)):
                with self.assertRaises(error_type):
                    action()
            connect.assert_not_called()


class CliTests(unittest.TestCase):
    def test_view_displays_all_required_fields(self):
        output = io.StringIO()
        with patch.object(app, 'get_technicians', return_value=[TECHNICIAN]), redirect_stdout(output):
            app.view_technicians()
        for value in TECHNICIAN.values():
            self.assertIn(str(value), output.getvalue())
        for label in ('Technician ID', 'Full Name', 'Email', 'Status'):
            self.assertIn(label, output.getvalue())

    def test_empty_view_is_friendly(self):
        output = io.StringIO()
        with patch.object(app, 'get_technicians', return_value=[]), redirect_stdout(output):
            app.view_technicians()
        self.assertIn('No technicians found yet', output.getvalue())

    def test_blank_inputs_reprompt_and_success_displays_generated_id(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=[' ', ' Alex Reyes ', '', ' alex.reyes@example.com ']), \
             patch.object(app, 'add_technician', return_value=12) as add, redirect_stdout(output):
            app.add_technician_interactively()
        add.assert_called_once_with('Alex Reyes', 'alex.reyes@example.com')
        self.assertIn('Full name is required.', output.getvalue())
        self.assertIn('Email is required.', output.getvalue())
        self.assertIn('New technician ID: 12', output.getvalue())

    def test_oversized_inputs_reprompt(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=['x' * 101, 'Alex', 'x' * 151, 'alex@example.com']), \
             patch.object(app, 'add_technician', return_value=12) as add, redirect_stdout(output):
            app.add_technician_interactively()
        add.assert_called_once_with('Alex', 'alex@example.com')
        self.assertIn('at most 100 characters', output.getvalue())
        self.assertIn('at most 150 characters', output.getvalue())

    def test_read_and_create_errors_do_not_crash(self):
        output = io.StringIO()
        with patch.object(app, 'get_technicians', side_effect=repo.TechnicianReadError('Unable to read')), \
             redirect_stdout(output):
            app.view_technicians()
        with patch('builtins.input', side_effect=['Alex', 'alex@example.com']), \
             patch.object(app, 'add_technician', side_effect=repo.TechnicianCreateError('Email already exists')), \
             redirect_stdout(output):
            app.add_technician_interactively()
        self.assertIn('Unable to read', output.getvalue())
        self.assertIn('Email already exists', output.getvalue())
        self.assertNotIn('successfully', output.getvalue())

    def test_submenu_routes_view_add_and_back(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=['abc', '5', '1', '2', '3', '4']), \
             patch.object(app, 'view_technicians') as view, \
             patch.object(app, 'change_technician_status_interactively') as change, \
             patch.object(app, 'add_technician_interactively') as add, redirect_stdout(output):
            app.manage_technicians()
        view.assert_called_once_with()
        add.assert_called_once_with()
        change.assert_called_once_with()
        self.assertEqual(output.getvalue().count('Invalid option.'), 2)
        for label in ('1. View Technicians', '2. Add Technician', '3. Change Technician Status', '4. Back'):
            self.assertIn(label, output.getvalue())

    def test_main_menu_and_submenu_back_then_exit(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=['abc', '10', '6', '4', '9']), redirect_stdout(output):
            app.main()
        self.assertIn('6. Manage Technicians', output.getvalue())
        self.assertIn('9. Exit', output.getvalue())
        self.assertEqual(output.getvalue().count('Invalid option. Please choose a number from 1 to 9.'), 2)
        self.assertIn('MANAGE TECHNICIANS', output.getvalue())
        self.assertIn('Goodbye!', output.getvalue())

    def test_submenu_eof_exits_main_gracefully(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=['6', EOFError()]), redirect_stdout(output):
            app.main()
        self.assertIn('Goodbye!', output.getvalue())


class SetupCommandTests(unittest.TestCase):
    def test_success_and_failure_exit_codes(self):
        output = io.StringIO()
        with patch.object(setup_technicians, 'setup_technicians_table'), redirect_stdout(output):
            self.assertEqual(setup_technicians.main(), 0)
        self.assertIn('helpdesk.technicians is available', output.getvalue())
        with patch.object(setup_technicians, 'setup_technicians_table',
                          side_effect=repo.TechnicianSetupError('Setup failed')), redirect_stdout(output):
            self.assertEqual(setup_technicians.main(), 1)
        self.assertIn('Setup failed', output.getvalue())


if __name__ == '__main__':
    unittest.main()
