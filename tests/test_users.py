"""Accounts and bound SQL with generated credentials and mocked connections."""
from datetime import datetime
import io
import os
import secrets
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import mysql.connector
import database
import password_security as security
import setup_users
import user_repository as repo


class UserRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.credential = secrets.token_urlsafe(32)
        cls.stored = security.hash_password(cls.credential)

    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.lastrowid = 7
        self.row = dict(user_id=7, username='test_account', full_name='Test Operator', role='Admin',
                        status='Active', created_at=datetime(2026, 10, 6, 9), password_hash=self.stored)
        self.cursor.fetchone.return_value = self.row.copy()
        self.connect = patch.object(repo, 'get_connection', return_value=self.connection)
        self.connect.start()
        self.addCleanup(self.connect.stop)

    def test_creation_trims_fields_saves_only_hash_and_uses_mysql_defaults(self):
        self.cursor.fetchone.return_value = (1,)
        with patch.object(repo, 'hash_password', return_value=self.stored) as hash_function:
            ident = repo.create_user("  Test'Account  ", '  Test Operator  ', 'Admin', self.credential)
        self.assertEqual(ident, 7)
        self.assertTrue(hash_function.call_count == 1)
        self.assertTrue(hash_function.call_args.args[0] == self.credential)
        self.assertTrue(self.cursor.execute.call_count == 2)
        self.assertEqual(self.cursor.execute.call_args_list[0].args,
                         ('SELECT GET_LOCK(%s, %s)', ('helpdesk.users.create', 5)))
        sql, params = self.cursor.execute.call_args.args
        self.assertTrue(sql == 'INSERT INTO helpdesk.users (username, password_hash, full_name, role, technician_id) VALUES (%s, %s, %s, %s, %s)')
        self.assertTrue(params == ("Test'Account", self.stored, 'Test Operator', 'Admin', None))
        self.assertTrue(self.credential not in sql and self.credential not in params)
        self.connection.commit.assert_called_once_with()

    def test_invalid_fields_or_password_fail_before_connection(self):
        cases = [('', 'Test Name', 'Admin'), (' \t ', 'Test Name', 'Admin'), ('x' * 51, 'Test Name', 'Admin'),
                 ('user', '', 'Admin'), ('user', '333', 'Admin'), ('user', '!!!', 'Admin'),
                 ('user', 'Name', 'Other'), ('user', 'Name' * 30, 'Technician')]
        with patch.object(repo, 'get_connection') as connect:
            for username, name, role in cases:
                with self.assertRaises(ValueError):
                    repo.create_user(username, name, role, self.credential)
            with self.assertRaises(ValueError):
                repo.create_user('user', 'Test Name', 'Admin', '')
        connect.assert_not_called()

    def test_duplicate_database_and_commit_errors_never_report_success_or_leak_secrets(self):
        for code in (1062, 1146, 2003):
            with self.subTest(code=code):
                self.cursor.execute.side_effect = mysql.connector.Error(self.credential + self.stored, errno=code)
                with patch.object(repo, 'hash_password', return_value=self.stored), self.assertRaises(repo.UserCreateError) as caught:
                    repo.create_user('test_account', 'Test Operator', 'Technician', self.credential)
                self.assertTrue(self.credential not in str(caught.exception) and self.stored not in str(caught.exception))
                if code == 1062:
                    self.assertIn('username already exists', str(caught.exception))
                self.connection.commit.assert_not_called()
        self.cursor.execute.side_effect = None
        self.cursor.fetchone.return_value = (1,)
        self.connection.commit.side_effect = mysql.connector.Error(errno=2013)
        with patch.object(repo, 'hash_password', return_value=self.stored), self.assertRaises(repo.UserCreateError):
            repo.create_user('test_account', 'Test Operator', 'Admin', self.credential)
        self.assertTrue(self.connection.rollback.call_count == 4)

    def test_hardware_hashing_failure_does_not_open_connection(self):
        with patch.object(repo, 'get_connection') as connect, \
             patch.object(repo, 'hash_password', side_effect=security.PasswordHashError('Secure hashing unavailable.')), \
             self.assertRaises(repo.UserCreateError):
            repo.create_user('test_account', 'Test Operator', 'Admin', self.credential)
        connect.assert_not_called()

    def test_both_roles_can_authenticate_public_data_excludes_credentials(self):
        for role in repo.USER_ROLES:
            with self.subTest(role=role):
                self.cursor.fetchone.return_value = {**self.row, 'role': role}
                user = repo.authenticate_user('  test_account  ', self.credential)
                self.assertEqual(user['role'], role)
                self.assertTrue(set(user) == set(repo.PUBLIC_USER_FIELDS))
                self.assertTrue('password_hash' not in user and 'password' not in user)
        query, params = self.cursor.execute.call_args.args
        self.assertTrue(query.startswith('SELECT ') and query.endswith('WHERE u.username = %s LIMIT 1'))
        self.assertTrue(params == ('test_account',))
        self.connection.commit.assert_not_called()

    def test_unknown_incorrect_and_inactive_credentials_all_return_none(self):
        for case in ('unknown', 'incorrect', 'inactive'):
            with self.subTest(case=case):
                self.cursor.fetchone.return_value = None if case == 'unknown' else {**self.row, 'status': 'Inactive' if case == 'inactive' else 'Active'}
                candidate = secrets.token_urlsafe(32) if case == 'incorrect' else self.credential
                self.assertTrue(repo.authenticate_user('test_account', candidate) is None)

    def test_unknown_username_still_verifies_with_bounded_dummy_hash_even_if_comparison_matches(self):
        self.cursor.fetchone.return_value = None
        with patch.object(repo, 'verify_password', return_value=True) as verify:
            self.assertTrue(repo.authenticate_user('missing_user', self.credential) is None)
        self.assertTrue(verify.call_count == 1)
        self.assertTrue(verify.call_args.args[1] == security.DUMMY_PASSWORD_HASH)

    def test_username_sql_text_is_bound_and_password_is_not_in_read_query(self):
        username = "missing' OR 1=1 --"
        self.cursor.fetchone.return_value = None
        with patch.object(repo, 'verify_password', return_value=False):
            self.assertTrue(repo.authenticate_user(username, self.credential) is None)
        query, params = self.cursor.execute.call_args.args
        self.assertTrue(params == (username,))
        self.assertTrue(username not in query and self.credential not in query)

    def test_blank_and_excessive_login_input_is_generic_and_never_queries(self):
        with patch.object(repo, 'get_connection') as connect:
            for username, candidate in (('', self.credential), ('x' * 51, self.credential), ('user', ''), ('user', ' \t ')):
                self.assertTrue(repo.authenticate_user(username, candidate) is None)
        connect.assert_not_called()

    def test_corrupted_hash_cannot_create_session(self):
        self.cursor.fetchone.return_value = {**self.row, 'password_hash': 'invalid'}
        self.assertTrue(repo.authenticate_user('user', self.credential) is None)

    def test_authentication_errors_hide_credentials_and_return_setup_guidance_when_needed(self):
        for code in (1146, 2003):
            with self.subTest(code=code):
                self.cursor.execute.side_effect = mysql.connector.Error(self.credential + self.stored, errno=code)
                with self.assertRaises(repo.UserAuthenticationError) as caught:
                    repo.authenticate_user('user', self.credential)
                self.assertTrue(self.credential not in str(caught.exception) and self.stored not in str(caught.exception))
                if code == 1146:
                    self.assertIn('setup_users.py', str(caught.exception))

    def test_setup_is_idempotent_and_creates_only_users_with_required_fields(self):
        repo.setup_users_table()
        query = self.cursor.execute.call_args.args[0]
        self.assertTrue(query.startswith('CREATE TABLE IF NOT EXISTS helpdesk.users ('))
        for field in ('user_id INT AUTO_INCREMENT PRIMARY KEY', 'username VARCHAR(50) NOT NULL UNIQUE',
                      'password_hash VARCHAR(255) NOT NULL', 'full_name VARCHAR(100) NOT NULL', 'role VARCHAR(20) NOT NULL',
                      "status VARCHAR(20) NOT NULL DEFAULT 'Active'", 'created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP'):
            self.assertIn(field, query)
        self.assertNotIn('tickets', query)
        self.assertNotIn('technicians', query)
        self.connection.commit.assert_not_called()

    def test_shared_connection_refuses_a_different_database_before_connecting(self):
        with patch.object(repo, 'get_connection', wraps=database.get_connection), \
             patch.dict(os.environ, {'DB_NAME': 'not_the_application_database'}), \
             patch('database.load_dotenv'), patch('database.mysql.connector.connect') as connect:
            for action, error_type in ((repo.setup_users_table, repo.UserSetupError),
                                       (lambda: repo.authenticate_user('user', self.credential), repo.UserAuthenticationError)):
                with self.assertRaises(error_type):
                    action()
        connect.assert_not_called()

    def test_setup_permission_error_gives_admin_guidance_without_private_details(self):
        self.cursor.execute.side_effect = mysql.connector.Error(self.credential, errno=1142)
        with self.assertRaises(repo.UserSetupError) as caught:
            repo.setup_users_table()
        self.assertIn('MySQL root administrator', str(caught.exception))
        self.assertTrue(self.credential not in str(caught.exception))


class SetupScriptTests(unittest.TestCase):
    def test_success_and_error_exit_codes(self):
        output = io.StringIO()
        with patch.object(setup_users, 'setup_users_table'), redirect_stdout(output):
            self.assertEqual(setup_users.main(), 0)
        self.assertIn('helpdesk.users', output.getvalue())
        with patch.object(setup_users, 'setup_users_table', side_effect=repo.UserSetupError('Run as administrator.')), redirect_stdout(output):
            self.assertEqual(setup_users.main(), 1)


if __name__ == '__main__':
    unittest.main()
