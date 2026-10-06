"""Self-service password validation and transactions without live database writes."""
import secrets
import unittest
from unittest.mock import MagicMock, patch

import mysql.connector
import password_security as security
import user_repository as repo


class PasswordChangeRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Generate all credentials at runtime; preserve meaningful whitespace.
        cls.current = ' ' + secrets.token_urlsafe(32) + ' '
        cls.new = ' ' + secrets.token_urlsafe(32) + ' '
        cls.stored = security.hash_password(cls.current)
        cls.replacement = security.hash_password(cls.new)

    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = {'password_hash': self.stored, 'role': 'Admin', 'status': 'Active'}
        self.cursor.rowcount = 1
        self.connect = patch.object(repo, 'get_connection', return_value=self.connection)
        self.connect_mock = self.connect.start()
        self.addCleanup(self.connect.stop)

    def change(self, user_id=7):
        return repo.change_password(user_id, self.current, self.new, self.new)

    def test_both_roles_update_only_own_hash_with_bound_sql_and_commit(self):
        for role in repo.USER_ROLES:
            with self.subTest(role=role):
                self.connection.reset_mock()
                self.cursor.reset_mock()
                self.cursor.fetchone.return_value = {'password_hash': self.stored, 'role': role, 'status': 'Active'}
                with patch.object(repo, 'verify_password', return_value=True) as verify, \
                     patch.object(repo, 'hash_password', return_value=self.replacement) as hash_function:
                    self.assertTrue(self.change())
                self.assertTrue(verify.call_args.args == (self.current, self.stored))
                self.assertTrue(hash_function.call_args.args == (self.new,))
                select, update = self.cursor.execute.call_args_list
                self.assertEqual(select.args, (
                    'SELECT password_hash, role, status FROM helpdesk.users WHERE user_id = %s FOR UPDATE', (7,)))
                self.assertEqual(update.args[0], 'UPDATE helpdesk.users SET password_hash = %s WHERE user_id = %s LIMIT 1')
                self.assertTrue(update.args[1] == (self.replacement, 7))
                for call in (select, update):
                    self.assertTrue(self.current not in call.args[0] and self.new not in call.args[0])
                    self.assertTrue(self.current not in call.args[1] and self.new not in call.args[1])
                self.connection.commit.assert_called_once_with()
                self.connection.rollback.assert_not_called()

    def test_invalid_password_inputs_fail_before_connecting(self):
        cases = (
            ('', self.new, self.new, 'Current password is incorrect.'),
            (' \t ', self.new, self.new, 'Current password is incorrect.'),
            (self.current, '', '', 'New password cannot be blank.'),
            (self.current, ' \t ', ' \t ', 'New password cannot be blank.'),
            (self.current, self.new, secrets.token_urlsafe(32), 'New passwords do not match.'),
            (self.current, self.current, self.current, 'New password must be different from the current password.'),
        )
        for current, new, confirmation, message in cases:
            with self.assertRaises(ValueError) as caught:
                repo.change_password(7, current, new, confirmation)
            self.assertEqual(str(caught.exception), message)
        self.connect_mock.assert_not_called()

    def test_existing_password_length_and_encoding_limits_are_reused(self):
        for candidate in (secrets.token_urlsafe(1025), '\ud800' + self.new):
            with self.assertRaises(ValueError):
                repo.change_password(7, self.current, candidate, candidate)
        self.connect_mock.assert_not_called()

    def test_invalid_user_ids_never_query_another_account(self):
        for user_id in (None, True, 0, -1, 2147483648, '7', "7 OR 1=1"):
            with self.assertRaises(ValueError):
                self.change(user_id)
        self.connect_mock.assert_not_called()

    def test_wrong_current_password_is_generic_and_never_hashes_or_updates(self):
        with patch.object(repo, 'hash_password') as hash_function, \
             self.assertRaises(repo.UserPasswordChangeError) as caught:
            repo.change_password(7, secrets.token_urlsafe(32), self.new, self.new)
        self.assertEqual(str(caught.exception), 'Current password is incorrect.')
        self.assertEqual(self.cursor.execute.call_count, 1)
        hash_function.assert_not_called()
        self.connection.rollback.assert_called_once_with()
        self.connection.commit.assert_not_called()

    def test_missing_inactive_or_unsupported_account_cannot_change_password(self):
        for row in (None, {'password_hash': self.stored, 'role': 'Admin', 'status': 'Inactive'},
                    {'password_hash': self.stored, 'role': 'Unknown', 'status': 'Active'}):
            self.cursor.reset_mock()
            self.cursor.fetchone.return_value = row
            with patch.object(repo, 'verify_password') as verify, patch.object(repo, 'hash_password') as hash_function, \
                 self.assertRaises(repo.UserPasswordChangeError):
                self.change()
            verify.assert_not_called()
            hash_function.assert_not_called()
            self.assertEqual(self.cursor.execute.call_count, 1)
        self.connection.commit.assert_not_called()

    def test_malformed_hash_is_rejected_without_a_write(self):
        self.cursor.fetchone.return_value = {'password_hash': None, 'role': 'Technician', 'status': 'Active'}
        with self.assertRaises(repo.UserPasswordChangeError) as caught:
            self.change()
        self.assertEqual(str(caught.exception), 'Current password is incorrect.')
        self.assertEqual(self.cursor.execute.call_count, 1)
        self.connection.commit.assert_not_called()

    def test_hashing_failures_roll_back_without_updating(self):
        for function in ('verify_password', 'hash_password'):
            self.connection.reset_mock()
            self.cursor.reset_mock()
            with patch.object(repo, 'verify_password', return_value=True), \
                 patch.object(repo, function, side_effect=security.PasswordHashError('Secure hashing unavailable.')), \
                 self.assertRaises(repo.UserPasswordChangeError) as caught:
                self.change()
            self.assertEqual(str(caught.exception), 'Secure hashing unavailable.')
            self.assertEqual(self.cursor.execute.call_count, 1)
            self.connection.rollback.assert_called_once_with()
            self.connection.commit.assert_not_called()

    def test_database_and_commit_errors_hide_secrets_and_never_report_success(self):
        for stage in ('read', 'write', 'commit'):
            self.connection.reset_mock()
            self.cursor.reset_mock()
            self.cursor.execute.side_effect = None
            self.connection.commit.side_effect = None
            failure = mysql.connector.Error(self.current + self.new + self.stored, errno=2013)
            if stage == 'read':
                self.cursor.execute.side_effect = failure
            elif stage == 'write':
                self.cursor.execute.side_effect = [None, failure]
            else:
                self.connection.commit.side_effect = failure
            with patch.object(repo, 'verify_password', return_value=True), \
                 patch.object(repo, 'hash_password', return_value=self.replacement), \
                 self.assertRaises(repo.UserPasswordChangeError) as caught:
                self.change()
            self.assertTrue(all(secret not in str(caught.exception) for secret in (self.current, self.new, self.stored)))
            self.connection.rollback.assert_called_once_with()
            if stage != 'commit':
                self.connection.commit.assert_not_called()

    def test_missing_table_and_connection_configuration_have_safe_messages(self):
        for error, fragment in ((mysql.connector.Error(errno=1146), 'setup_users.py'),
                                (ValueError(self.current), 'DB_NAME must be helpdesk')):
            self.connect_mock.side_effect = error
            with self.assertRaises(repo.UserPasswordChangeError) as caught:
                self.change()
            self.assertIn(fragment, str(caught.exception))
            self.assertTrue(self.current not in str(caught.exception))

    def test_unconfirmed_update_rolls_back(self):
        self.cursor.rowcount = 0
        with patch.object(repo, 'verify_password', return_value=True), \
             patch.object(repo, 'hash_password', return_value=self.replacement), \
             self.assertRaises(repo.UserPasswordChangeError):
            self.change()
        self.connection.rollback.assert_called_once_with()
        self.connection.commit.assert_not_called()

    def test_real_hash_change_invalidates_old_login_and_preserves_public_identity(self):
        for role in repo.USER_ROLES:
            row = dict(user_id=7, username='test_account', full_name='Test Operator', role=role,
                       status='Active', created_at=None, password_hash=self.stored)
            self.cursor.fetchone.return_value = row

            def execute(sql, params):
                if sql.startswith('UPDATE '):
                    row['password_hash'] = params[0]

            self.cursor.execute.side_effect = execute
            self.assertTrue(self.change())
            self.assertTrue(row['password_hash'] != self.stored)
            self.assertTrue(repo.authenticate_user('test_account', self.current) is None)
            self.assertTrue(repo.authenticate_user('test_account', self.new.strip()) is None)
            identity = repo.authenticate_user('test_account', self.new)
            self.assertEqual(identity, repo.public_user(row))
            self.assertNotIn('password_hash', identity)
            with self.assertRaises(repo.UserPasswordChangeError) as caught:
                self.change()
            self.assertEqual(str(caught.exception), 'Current password is incorrect.')
            self.assertTrue(repo.authenticate_user('test_account', self.new) is not None)


if __name__ == '__main__':
    unittest.main()
