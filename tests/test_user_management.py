"""Public user reads, safe status changes, and concurrent Admin protection."""
from datetime import datetime
import secrets
from queue import Queue
from threading import Barrier, Lock, Thread
import unittest
from unittest.mock import MagicMock, patch

import mysql.connector
import password_security as security
import user_repository as repo


def user(user_id=7, role='Admin', status='Active'):
    return dict(user_id=user_id, username=f'test_user_{user_id}', full_name='Test Operator',
                role=role, status=status, created_at=datetime(2026, 10, 6, 10, 30), technician_id=None, technician_name=None)


class UserManagementRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.rowcount = 1
        self.connect = patch.object(repo, 'get_connection', return_value=self.connection)
        self.connect.start()
        self.addCleanup(self.connect.stop)

    def prepare_update(self, current, actor=None, admins=2):
        actor = user() if actor is None else actor
        self.cursor.fetchone.side_effect = [{'acquired': 1}, actor, current, {'active_admins': admins}]

    def assert_no_update(self):
        self.assertFalse(any(call.args[0].startswith('UPDATE') for call in self.cursor.execute.call_args_list))
        self.connection.commit.assert_not_called()

    def test_admin_list_reads_only_public_fields_including_inactive_users(self):
        self.cursor.fetchone.return_value = user()
        rows = [user(), {**user(12, 'Technician', 'Inactive'), 'password_hash': secrets.token_hex(64)}]
        self.cursor.fetchall.return_value = rows
        self.assertEqual(repo.get_users(7), [user(), user(12, 'Technician', 'Inactive')])
        self.assertEqual(self.cursor.execute.call_args_list[0].args[1], (7,))
        query, params = self.cursor.execute.call_args.args
        self.assertIn('u.technician_id, t.full_name AS technician_name', query)
        self.assertIn('LEFT JOIN helpdesk.technicians', query)
        self.assertTrue(query.endswith('ORDER BY u.user_id'))
        self.assertEqual(params, ())
        for call in self.cursor.execute.call_args_list:
            self.assertNotIn('password', call.args[0])
        self.connection.commit.assert_not_called()

    def test_one_user_read_binds_id_and_returns_missing_friendly_value(self):
        self.cursor.fetchone.return_value = user()
        self.cursor.fetchall.return_value = [user(12, 'Technician')]
        self.assertEqual(repo.get_user(12, 7), user(12, 'Technician'))
        self.assertEqual(self.cursor.execute.call_args.args[1], (12,))
        self.cursor.fetchall.return_value = []
        self.assertIsNone(repo.get_user(99, 7))

    def test_technician_inactive_admin_or_missing_actor_cannot_read_users(self):
        for actor in (user(role='Technician'), user(status='Inactive'), None):
            self.cursor.reset_mock()
            self.cursor.fetchone.return_value = actor
            with self.subTest(actor=actor), self.assertRaises(repo.UserManagementPermissionError):
                repo.get_users(7)
            self.assertEqual(self.cursor.execute.call_count, 1)
            self.cursor.fetchall.assert_not_called()

    def test_technician_or_deactivated_actor_cannot_update_through_repository(self):
        for actor in (user(role='Technician'), user(status='Inactive'), None):
            self.prepare_update(user(12, 'Technician'), actor=user())
            self.cursor.fetchone.side_effect = [{'acquired': 1}, actor]
            with self.assertRaises(repo.UserManagementPermissionError):
                repo.update_user_status(12, 'Inactive', 7)
        self.assert_no_update()
        self.assertEqual(self.connection.rollback.call_count, 3)

    def test_status_update_changes_only_one_account_with_bound_values(self):
        self.prepare_update(user(12, 'Technician'))
        self.assertTrue(repo.update_user_status(12, ' Inactive ', 7))
        calls = self.cursor.execute.call_args_list
        self.assertEqual(calls[0].args, ('SELECT GET_LOCK(%s, %s) AS acquired', ('helpdesk.users.create', 5)))
        self.assertIn('FOR UPDATE', calls[1].args[0])
        self.assertEqual(calls[1].args[1], (7,))
        self.assertEqual(calls[2].args[1], (12,))
        self.assertEqual(calls[-1].args, ('UPDATE helpdesk.users SET status = %s WHERE user_id = %s LIMIT 1', ('Inactive', 12)))
        self.connection.commit.assert_called_once_with()
        self.connection.__exit__.assert_called_once()

    def test_inactive_account_can_be_reactivated(self):
        for role in ('Admin', 'Technician'):
            self.prepare_update(user(12, role, 'Inactive'))
            self.assertTrue(repo.update_user_status(12, 'Active', 7))
        self.assertEqual(self.connection.commit.call_count, 2)

    def test_another_admin_can_be_deactivated_when_an_active_admin_remains(self):
        self.prepare_update(user(12), admins=2)
        self.assertTrue(repo.update_user_status(12, 'Inactive', 7))
        count_query = self.cursor.execute.call_args_list[3]
        self.assertEqual(count_query.args[1], ('Admin', 'Active'))
        self.connection.commit.assert_called_once_with()

    def test_last_active_admin_deactivation_is_blocked_before_any_write(self):
        self.prepare_update(user(), admins=1)
        with self.assertRaises(repo.UserStatusChangeError) as caught:
            repo.update_user_status(7, 'Inactive', 7)
        self.assertIn('last Active Admin', str(caught.exception))
        self.assert_no_update()
        self.connection.rollback.assert_called_once_with()

    def test_self_deactivation_is_blocked_even_with_another_active_admin(self):
        self.prepare_update(user(), admins=2)
        with self.assertRaises(repo.UserStatusChangeError) as caught:
            repo.update_user_status(7, 'Inactive', 7)
        self.assertIn('your own account', str(caught.exception))
        self.assert_no_update()

    def test_missing_user_or_no_change_never_updates(self):
        self.prepare_update(None)
        with self.assertRaises(repo.UserUpdateError) as caught:
            repo.update_user_status(99, 'Inactive', 7)
        self.assertIn('No user found', str(caught.exception))
        self.prepare_update(user(12, 'Technician'))
        self.assertFalse(repo.update_user_status(12, 'Active', 7))
        self.assert_no_update()
        self.assertEqual(self.connection.rollback.call_count, 2)

    def test_invalid_ids_or_statuses_fail_before_connection(self):
        with patch.object(repo, 'get_connection') as connect:
            for invalid in (None, True, 0, -1, 2147483648, '12', '12 OR 1=1'):
                with self.assertRaises(ValueError):
                    repo.get_user(invalid, 7)
                with self.assertRaises(ValueError):
                    repo.get_users(invalid)
                with self.assertRaises(ValueError):
                    repo.update_user_status(invalid, 'Inactive', 7)
                with self.assertRaises(ValueError):
                    repo.update_user_status(12, 'Inactive', invalid)
            for status in ('', ' ', 'Disabled', 'Admin', 'Inactive; DELETE FROM users'):
                with self.assertRaises(ValueError):
                    repo.update_user_status(12, status, 7)
        connect.assert_not_called()

    def test_lock_timeout_rowcount_or_commit_failure_never_reports_success(self):
        for acquired in (0, None):
            self.cursor.fetchone.side_effect = [{'acquired': acquired}]
            with self.assertRaises(repo.UserUpdateError):
                repo.update_user_status(12, 'Inactive', 7)
        self.assert_no_update()
        self.prepare_update(user(12, 'Technician'))
        self.cursor.rowcount = 0
        with self.assertRaises(repo.UserUpdateError):
            repo.update_user_status(12, 'Inactive', 7)
        self.cursor.rowcount = 1
        self.prepare_update(user(12, 'Technician'))
        self.connection.commit.side_effect = mysql.connector.Error(errno=2013)
        with self.assertRaises(repo.UserUpdateError):
            repo.update_user_status(12, 'Inactive', 7)
        self.assertEqual(self.connection.rollback.call_count, 4)

    def test_read_and_write_database_errors_do_not_leak_private_details(self):
        private_detail = secrets.token_hex(64)
        for code in (1146, 2003):
            self.cursor.execute.side_effect = mysql.connector.Error(private_detail, errno=code)
            for action, error_type in ((lambda: repo.get_users(7), repo.UserReadError),
                                       (lambda: repo.update_user_status(12, 'Inactive', 7), repo.UserUpdateError)):
                with self.assertRaises(error_type) as caught:
                    action()
                self.assertTrue(private_detail not in str(caught.exception))
                if code == 1146:
                    self.assertIn('setup_users.py', str(caught.exception))

    def test_login_rejects_inactive_account_and_accepts_after_reactivation(self):
        credential = secrets.token_urlsafe(32)
        stored = security.hash_password(credential)
        row = {**user(12, 'Technician'), 'password_hash': stored}
        for target in ('Inactive', 'Active'):
            self.prepare_update(row)
            self.assertTrue(repo.update_user_status(12, target, 7))
            row['status'] = target
            self.cursor.fetchone.side_effect = None
            self.cursor.fetchone.return_value = row.copy()
            authenticated = repo.authenticate_user(row['username'], credential)
            if target == 'Inactive':
                self.assertIsNone(authenticated)
            else:
                self.assertEqual(authenticated, repo.public_user(row))


class ConcurrentAdminSafetyTests(unittest.TestCase):
    def test_two_admin_sessions_cannot_deactivate_each_other_and_leave_no_active_admin(self):
        rows = {7: user(7), 12: user(12)}
        write_lock = Lock()
        start = Barrier(3)
        results = Queue()

        class Connection:
            def __init__(self):
                self.acquired = False
                self.pending = None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                if self.acquired:
                    write_lock.release()

            def cursor(self, dictionary=False):
                return Cursor(self)

            def commit(self):
                ident, status = self.pending
                rows[ident]['status'] = status
                self.pending = None

            def rollback(self):
                self.pending = None

        class Cursor:
            def __init__(self, connection):
                self.connection = connection
                self.result = None
                self.rowcount = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def execute(self, sql, params=()):
                if sql.startswith('SELECT GET_LOCK'):
                    if params != ('helpdesk.users.create', 5):
                        raise AssertionError('Status changes must share the creation lock.')
                    self.connection.acquired = write_lock.acquire(timeout=2)
                    self.result = {'acquired': int(self.connection.acquired)}
                elif sql.startswith('SELECT COUNT'):
                    self.result = {'active_admins': sum(row['role'] == 'Admin' and row['status'] == 'Active'
                                                        for row in rows.values())}
                elif sql.startswith('SELECT user_id'):
                    row = rows.get(params[0])
                    self.result = row.copy() if row else None
                elif sql.startswith('UPDATE'):
                    status, ident = params
                    self.connection.pending = ident, status
                    self.rowcount = 1
                else:
                    raise AssertionError('Unexpected SQL in status transaction.')

            def fetchone(self):
                return self.result

        def deactivate(actor, target):
            start.wait(timeout=5)
            try:
                results.put(repo.update_user_status(target, 'Inactive', actor))
            except Exception as error:
                results.put(error)

        with patch.object(repo, 'get_connection', side_effect=Connection):
            workers = [Thread(target=deactivate, args=(7, 12)), Thread(target=deactivate, args=(12, 7))]
            for worker in workers:
                worker.start()
            start.wait(timeout=5)
            for worker in workers:
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
        outcomes = [results.get_nowait(), results.get_nowait()]
        self.assertEqual(sum(result is True for result in outcomes), 1)
        self.assertEqual(sum(isinstance(result, repo.UserManagementPermissionError) for result in outcomes), 1)
        self.assertEqual(sum(row['status'] == 'Active' for row in rows.values()), 1)


if __name__ == '__main__':
    unittest.main()
