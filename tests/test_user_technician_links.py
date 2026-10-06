"""Technician account links, fresh note attribution, and guarded SQL transactions."""
from queue import Queue
import secrets
from threading import Barrier, Lock, Thread
import unittest
import ticket_access
from unittest.mock import MagicMock, patch

import mysql.connector
import ticket_comment_repository as comments
import user_repository as users
from test_gui_authentication import account


class LinkRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.rowcount = 1
        self.connect = patch.object(users, 'get_connection', return_value=self.connection)
        self.connect.start()
        self.addCleanup(self.connect.stop)

    def prepare_link(self, target=None, technician=True, owner=None):
        target = {'role': 'Technician', 'technician_id': None} if target is None else target
        self.cursor.fetchone.side_effect = [
            {'acquired': 1}, account(), target,
            {'technician_id': 12} if technician else None, owner,
        ]

    def assert_no_write(self):
        self.assertFalse(any(call.args[0].startswith(('INSERT', 'UPDATE')) for call in self.cursor.execute.call_args_list))
        self.connection.commit.assert_not_called()

    def test_admin_links_one_user_with_parameterized_sql_and_rechecks_active_unclaimed_record(self):
        self.prepare_link()
        self.assertTrue(users.link_user_technician(20, 12, 7))
        calls = self.cursor.execute.call_args_list
        self.assertEqual(calls[0].args[1], ('helpdesk.users.create', 5))
        self.assertEqual(calls[1].args[1], (7,))
        self.assertIn('FOR UPDATE', calls[1].args[0])
        self.assertEqual(calls[2].args[1], (20,))
        self.assertIn('FOR UPDATE', calls[2].args[0])
        self.assertEqual(calls[3].args[1], (12, 'Active'))
        self.assertIn('FOR UPDATE', calls[3].args[0])
        self.assertEqual(calls[4].args[1], (12,))
        self.assertEqual(calls[-1].args, ('UPDATE helpdesk.users SET technician_id = %s WHERE user_id = %s LIMIT 1', (12, 20)))
        self.connection.commit.assert_called_once_with()

    def test_non_admin_inactive_or_missing_actor_cannot_link(self):
        for actor in (account('Technician'), {**account(), 'status': 'Inactive'}, None):
            self.cursor.fetchone.side_effect = [{'acquired': 1}, actor]
            with self.assertRaises(users.UserManagementPermissionError):
                users.link_user_technician(20, 12, 7)
        self.assert_no_write()

    def test_admin_role_missing_or_already_linked_target_is_rejected(self):
        for target in ({'role': 'Admin', 'technician_id': None}, {'role': 'Technician', 'technician_id': 35}, None):
            self.cursor.fetchone.side_effect = [{'acquired': 1}, account(), target]
            with self.assertRaises(users.UserTechnicianLinkError):
                users.link_user_technician(20, 12, 7)
        self.assert_no_write()

    def test_inactive_missing_or_already_claimed_technician_is_rejected(self):
        for technician, owner in ((False, None), (True, {'user_id': 30})):
            self.prepare_link(technician=technician, owner=owner)
            with self.assertRaises(users.UserTechnicianLinkError):
                users.link_user_technician(20, 12, 7)
        self.assert_no_write()
        self.assertEqual(self.connection.rollback.call_count, 2)

    def test_invalid_user_actor_and_technician_ids_fail_before_connection(self):
        with patch.object(users, 'get_connection') as connect:
            for invalid in (None, True, '12', "12 OR 1=1", 0, -1, 2147483648):
                for ids in ((invalid, 12, 7), (20, invalid, 7), (20, 12, invalid)):
                    with self.assertRaises(ValueError):
                        users.link_user_technician(*ids)
        connect.assert_not_called()

    def test_unique_foreign_key_schema_and_other_sql_errors_are_safe_and_rollback(self):
        secret = secrets.token_urlsafe(32)
        for code in (1062, 1452, 1054, 1146, 2013):
            self.connection.reset_mock()
            self.prepare_link()
            self.cursor.execute.side_effect = mysql.connector.Error(secret, errno=code)
            with self.assertRaises(users.UserTechnicianLinkError) as caught:
                users.link_user_technician(20, 12, 7)
            self.assertTrue(secret not in str(caught.exception))
            self.connection.rollback.assert_called_once_with()
            self.connection.commit.assert_not_called()
            if code in (1054, 1146):
                self.assertIn('setup_user_technicians.py', str(caught.exception))

    def test_lock_timeout_unconfirmed_write_and_commit_failure_are_not_success(self):
        self.cursor.fetchone.side_effect = [{'acquired': 0}]
        with self.assertRaises(users.UserTechnicianLinkError):
            users.link_user_technician(20, 12, 7)
        self.assert_no_write()
        self.prepare_link()
        self.cursor.rowcount = 0
        with self.assertRaises(users.UserTechnicianLinkError):
            users.link_user_technician(20, 12, 7)
        self.prepare_link()
        self.cursor.rowcount = 1
        self.connection.commit.side_effect = mysql.connector.Error(errno=2013)
        with self.assertRaises(users.UserTechnicianLinkError):
            users.link_user_technician(20, 12, 7)
        self.assertEqual(self.connection.rollback.call_count, 3)

    def test_available_choices_exclude_links_owned_by_inactive_users_too(self):
        self.cursor.fetchone.return_value = account()
        available = [{'technician_id': 12, 'full_name': 'Test Technician', 'email': 'test@example.com'}]
        self.cursor.fetchall.return_value = available
        self.assertEqual(users.get_available_technicians(7), available)
        sql, params = self.cursor.execute.call_args.args
        self.assertEqual(params, ('Active',))
        self.assertIn('NOT EXISTS', sql)
        self.assertIn('u.technician_id = t.technician_id', sql)
        self.assertNotIn('u.status', sql)

    def test_creation_grant_is_rechecked_before_reading_choices(self):
        grant = users._AccountAuthorization(7)
        self.cursor.fetchone.return_value = account()
        self.cursor.fetchall.return_value = []
        self.assertEqual(users.get_available_technicians(authorization=grant), [])
        self.cursor.fetchone.return_value = account('Technician')
        with self.assertRaises(users.UserManagementPermissionError):
            users.get_available_technicians(authorization=grant)
        grant.revoke()
        with self.assertRaises(users.UserManagementPermissionError):
            users.get_available_technicians(authorization=grant)

    def test_choice_reads_have_safe_migration_and_connection_feedback(self):
        for code in (1054, 1146, 2003):
            self.cursor.execute.side_effect = mysql.connector.Error(errno=code)
            for read in (lambda: users.get_available_technicians(7), lambda: users.get_linked_active_technician(20)):
                with self.assertRaises(users.UserReadError) as caught:
                    read()
                if code in (1054, 1146):
                    self.assertIn('setup_user_technicians.py', str(caught.exception))

    def test_current_note_link_requires_active_technician_role_user_and_record(self):
        self.cursor.fetchone.return_value = {'technician_id': 12, 'full_name': 'Test Technician'}
        self.assertEqual(users.get_linked_active_technician(20)['technician_id'], 12)
        self.assertEqual(self.cursor.execute.call_args.args[1], (20, 'Technician', 'Active', 'Active'))
        self.assertIn('JOIN helpdesk.technicians', self.cursor.execute.call_args.args[0])
        self.cursor.fetchone.return_value = None
        self.assertIsNone(users.get_linked_active_technician(20))


class LinkedCreationAndSessionTests(unittest.TestCase):
    assert_no_write = LinkRepositoryTests.assert_no_write

    def setUp(self):
        LinkRepositoryTests.setUp(self)
        self.credential = secrets.token_urlsafe(32)
        self.stored = secrets.token_hex(64)
        self.cursor.lastrowid = 20
        self.grant = users._AccountAuthorization(7)

    def create(self, role='Technician', technician_id=12):
        return users.create_account('new_account', 'New Operator', role, self.credential,
                                    authorization=self.grant, technician_id=technician_id)

    def test_gui_technician_creation_requires_link_even_if_ui_is_bypassed(self):
        self.cursor.fetchone.side_effect = [(1,), (2,), (7,)]
        with patch.object(users, 'hash_password', return_value=self.stored), self.assertRaises(users.UserTechnicianLinkError):
            self.create(technician_id=None)
        self.assert_no_write()

    def test_gui_technician_creation_saves_selected_id_and_hash_with_defaults(self):
        self.cursor.fetchone.side_effect = [(1,), (2,), (7,), (12,), None]
        with patch.object(users, 'hash_password', return_value=self.stored):
            self.assertEqual(self.create(), 20)
        sql, params = self.cursor.execute.call_args.args
        self.assertTrue(params == ('new_account', self.stored, 'New Operator', 'Technician', 12))
        self.assertIn('technician_id', sql)
        self.assertTrue(self.credential not in sql and self.credential not in params)

    def test_first_gui_account_still_becomes_admin_without_link(self):
        self.cursor.fetchone.side_effect = [(1,), (0,)]
        with patch.object(users, 'hash_password', return_value=self.stored):
            self.assertEqual(self.create(), 20)
        self.assertEqual(self.cursor.execute.call_args.args[1][2:], ('New Operator', 'Admin', None))

    def test_admin_cannot_receive_technician_link(self):
        with self.assertRaises(ValueError):
            self.create(role='Admin')
        self.assert_no_write()

    def test_admin_can_create_unlinked_admin_and_fallback_can_create_legacy_unlinked_technician(self):
        self.cursor.fetchone.side_effect = [(1,), (2,), (7,)]
        with patch.object(users, 'hash_password', return_value=self.stored):
            self.create(role='Admin', technician_id=None)
        self.assertIsNone(self.cursor.execute.call_args.args[1][-1])
        self.cursor.fetchone.side_effect = [(1,)]
        with patch.object(users, 'hash_password', return_value=self.stored):
            users.create_user('fallback_user', 'Fallback Operator', 'Technician', self.credential)
        self.assertIsNone(self.cursor.execute.call_args.args[1][-1])

    def test_inactive_or_claimed_link_blocks_gui_creation_before_insert(self):
        for results in ([(1,), (2,), (7,), None], [(1,), (2,), (7,), (12,), (30,)]):
            self.cursor.fetchone.side_effect = results
            with patch.object(users, 'hash_password', return_value=self.stored), self.assertRaises(users.UserTechnicianLinkError):
                self.create()
        self.assert_no_write()

    def test_login_and_public_session_include_link_and_never_credentials(self):
        self.cursor.fetchone.return_value = {**account('Technician'), 'technician_id': 12,
                                            'technician_name': 'Test Technician', 'password_hash': self.stored}
        with patch.object(users, 'verify_password', return_value=True):
            session = users.authenticate_user('test_account', self.credential)
        self.assertEqual(session['technician_id'], 12)
        self.assertEqual(session['user_id'], 7)
        self.assertNotIn('password_hash', session)
        self.assertNotIn('password', session)
        self.cursor.fetchone.return_value['technician_id'] = None
        with patch.object(users, 'verify_password', return_value=True):
            self.assertIsNotNone(users.authenticate_user('test_account', self.credential))


class LinkedNoteRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.lastrowid = 51
        self.connect = patch.object(comments, 'get_connection', return_value=self.connection)
        self.connect.start()
        self.addCleanup(self.connect.stop)

    def prepare(self, user):
        self.cursor.fetchone.side_effect = [{'ticket_id': 7, 'assigned_technician_id': 12}, user,
                                           {'technician_id': 12, 'full_name': 'Test Technician'}]

    def assert_no_insert(self):
        self.assertFalse(any(call.args[0].startswith('INSERT') for call in self.cursor.execute.call_args_list))
        self.connection.commit.assert_not_called()

    def test_technician_author_is_derived_from_locked_account_not_user_supplied_name(self):
        self.prepare({**account('Technician'), 'technician_id': 12})
        with patch.object(ticket_access, 'get_active_technician', return_value={'technician_id': 12}) as active:
            self.assertEqual(comments.add_ticket_comment_for_user(7, 20, '  Checked cable.  '), 51)
        active.assert_called_once_with(12, self.cursor)
        self.assertEqual(self.cursor.execute.call_args_list[1].args[1], (20,))
        self.assertIn('FOR UPDATE', self.cursor.execute.call_args_list[1].args[0])
        self.assertEqual(self.cursor.execute.call_args.args[1], (7, 12, 'Checked cable.'))
        self.assertTrue(all('ticket_history' not in call.args[0] and not call.args[0].startswith('UPDATE')
                            for call in self.cursor.execute.call_args_list))

    def test_technician_cannot_impersonate_another_author(self):
        self.prepare({**account('Technician'), 'technician_id': 12})
        with self.assertRaises(comments.TicketCommentCreateError) as caught:
            comments.add_ticket_comment_for_user(7, 20, 'Checked cable.', technician_id=35)
        self.assertIn('contact an administrator', str(caught.exception))
        self.assert_no_insert()

    def test_missing_link_or_inactive_technician_blocks_saving(self):
        for linked in (None, 12):
            self.prepare({**account('Technician'), 'technician_id': linked})
            with patch.object(ticket_access, 'get_active_technician', return_value=None), \
                 self.assertRaises(comments.TicketCommentCreateError) as caught:
                comments.add_ticket_comment_for_user(7, 20, 'Checked cable.')
            self.assertEqual(str(caught.exception), users.INVALID_TECHNICIAN_LINK)
        self.assert_no_insert()

    def test_inactive_missing_or_unknown_user_cannot_add_notes(self):
        for user in (None, {**account(), 'status': 'Inactive'}, account('Unknown')):
            self.prepare(user)
            with self.assertRaises(comments.TicketCommentCreateError):
                comments.add_ticket_comment_for_user(7, 20, 'Checked cable.', technician_id=12)
        self.assert_no_insert()

    def test_admin_keeps_active_author_selection_even_when_record_is_linked(self):
        self.prepare(account())
        with patch.object(comments, 'get_active_technician', return_value={'technician_id': 35}) as active:
            self.assertEqual(comments.add_ticket_comment_for_user(7, 7, 'Checked cable.', technician_id=35), 51)
        active.assert_called_once_with(35, self.cursor)
        self.assertEqual(self.cursor.execute.call_args.args[1], (7, 35, 'Checked cable.'))

    def test_admin_requires_author_and_rejects_inactive_choice(self):
        for choice in (None, 35):
            self.prepare(account())
            with patch.object(comments, 'get_active_technician', return_value=None), \
                 self.assertRaises(comments.TicketCommentCreateError):
                comments.add_ticket_comment_for_user(7, 7, 'Checked cable.', technician_id=choice)
        self.assert_no_insert()

    def test_missing_ticket_and_invalid_comment_never_insert(self):
        self.cursor.fetchone.return_value = None
        with self.assertRaises(comments.TicketCommentCreateError):
            comments.add_ticket_comment_for_user(7, 20, 'Checked cable.')
        with self.assertRaises(ValueError):
            comments.add_ticket_comment_for_user(7, 20, '!!!')
        self.assert_no_insert()

    def test_schema_errors_and_failed_commits_are_safe(self):
        self.cursor.execute.side_effect = mysql.connector.Error(errno=1054)
        with self.assertRaises(comments.TicketCommentCreateError) as caught:
            comments.add_ticket_comment_for_user(7, 20, 'Checked cable.')
        self.assertIn('setup_user_technicians.py', str(caught.exception))
        self.cursor.execute.side_effect = None
        self.prepare({**account('Technician'), 'technician_id': 12})
        self.connection.commit.side_effect = mysql.connector.Error(errno=2013)
        with patch.object(ticket_access, 'get_active_technician', return_value={'technician_id': 12}), \
             self.assertRaises(comments.TicketCommentCreateError):
            comments.add_ticket_comment_for_user(7, 20, 'Checked cable.')
        self.assertEqual(self.connection.rollback.call_count, 2)


class ConcurrentLinkTests(unittest.TestCase):
    def test_two_admin_requests_cannot_claim_the_same_technician(self):
        owner = None
        mutex = Lock()
        start = Barrier(3)
        results = Queue()

        class Connection:
            def __init__(self):
                self.locked = False
                self.pending = None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                if self.locked:
                    mutex.release()

            def cursor(self, dictionary=False):
                return Cursor(self)

            def commit(self):
                nonlocal owner
                owner = self.pending

            def rollback(self):
                self.pending = None

        class Cursor:
            rowcount = 1

            def __init__(self, connection):
                self.connection = connection
                self.result = None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def execute(self, sql, params):
                if 'GET_LOCK' in sql:
                    self.connection.locked = mutex.acquire(timeout=3)
                    self.result = {'acquired': int(self.connection.locked)}
                elif 'SELECT user_id, role, status' in sql:
                    self.result = account()
                elif 'SELECT role, technician_id' in sql:
                    self.result = {'role': 'Technician', 'technician_id': None}
                elif 'FROM helpdesk.technicians' in sql:
                    self.result = {'technician_id': 12}
                elif 'WHERE technician_id = %s' in sql:
                    self.result = None if owner is None else {'user_id': owner}
                elif sql.startswith('UPDATE'):
                    self.connection.pending = params[1]
                else:
                    raise AssertionError('Unexpected SQL.')

            def fetchone(self):
                return self.result

        def link(user_id):
            start.wait(timeout=3)
            try:
                users.link_user_technician(user_id, 12, 7)
            except users.UserTechnicianLinkError:
                results.put('blocked')
            else:
                results.put('linked')

        with patch.object(users, 'get_connection', side_effect=Connection):
            threads = [Thread(target=link, args=(ident,), daemon=True) for ident in (20, 30)]
            for thread in threads:
                thread.start()
            start.wait(timeout=3)
            for thread in threads:
                thread.join(timeout=4)
                self.assertFalse(thread.is_alive())
        self.assertEqual(sorted([results.get_nowait(), results.get_nowait()]), ['blocked', 'linked'])
        self.assertIn(owner, (20, 30))


if __name__ == '__main__':
    unittest.main()
