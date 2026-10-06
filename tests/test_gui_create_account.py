"""Guarded GUI account creation; ephemeral credentials, no live database or GUI."""
from contextlib import ExitStack, redirect_stdout
import io
from queue import Queue
import secrets
import unittest
from unittest.mock import MagicMock, patch

import mysql.connector
import gui_create_account as gui
import gui_login as login_gui
import password_security as security
import user_repository as repo
from test_gui_authentication import account, login_without_widgets


def authorized_admin():
    with patch.object(repo, 'authenticate_user', return_value=account()):
        return repo.authorize_account_creation('test_account', secrets.token_urlsafe(32))


def dialog_without_widgets(stage='create', first=False):
    dialog = gui.CreateAccountDialog.__new__(gui.CreateAccountDialog)
    dialog.parent = MagicMock()
    dialog.window = MagicMock()
    dialog.form = MagicMock()
    dialog.form.winfo_children.return_value = []
    dialog.explanation = MagicMock()
    dialog.feedback = MagicMock()
    dialog.action_button = MagicMock()
    dialog.cancel_button = MagicMock()
    dialog._results = Queue()
    dialog._closed = False
    dialog._busy = False
    dialog._saving = False
    dialog._poll_id = None
    dialog._authorization = None if first else authorized_admin()
    dialog._first_account = first
    dialog._stage = stage
    dialog._widgets = [(MagicMock(), 'normal'), (MagicMock(), 'readonly')]
    dialog._available_technicians = [dict(technician_id=12, full_name='Test Technician')]
    dialog._technician_rows = []
    dialog.technician_combo = MagicMock()
    dialog.technician_combo.current.return_value = 0
    credential = secrets.token_urlsafe(32)
    values = {'username': '  new_account  ', 'full_name': '  New Operator  ', 'role': 'Technician',
              'password': credential, 'confirmation': credential,
              'admin_username': '  test_account  ', 'admin_password': secrets.token_urlsafe(32)}
    dialog._fields = {key: MagicMock() for key in values}
    for key, value in values.items():
        dialog._fields[key].get.return_value = value
    return dialog


class AccountRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.credential = secrets.token_urlsafe(32)
        cls.stored = security.hash_password(cls.credential)

    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.lastrowid = 15
        self.connect = patch.object(repo, 'get_connection', return_value=self.connection)
        self.connect.start()
        self.addCleanup(self.connect.stop)

    def test_count_includes_inactive_users_and_queries_only_helpdesk(self):
        self.cursor.fetchone.return_value = (3,)
        self.assertEqual(repo.get_user_count(), 3)
        self.cursor.execute.assert_called_once_with('SELECT COUNT(*) FROM helpdesk.users')
        self.connection.commit.assert_not_called()

    def test_count_failure_never_returns_zero_or_leaks_details(self):
        for code in (1146, 2003):
            self.cursor.execute.side_effect = mysql.connector.Error(self.credential + self.stored, errno=code)
            with self.assertRaises(repo.UserReadError) as caught:
                repo.get_user_count()
            self.assertTrue(self.credential not in str(caught.exception) and self.stored not in str(caught.exception))

    def test_first_gui_account_is_admin_even_if_technician_role_is_submitted(self):
        self.cursor.fetchone.side_effect = [(1,), (0,)]
        with patch.object(repo, 'hash_password', return_value=self.stored):
            self.assertEqual(repo.create_account('  new_account  ', '  New Operator  ', 'Technician', self.credential), 15)
        calls = self.cursor.execute.call_args_list
        self.assertEqual(calls[0].args, ('SELECT GET_LOCK(%s, %s)', ('helpdesk.users.create', 5)))
        self.assertEqual(calls[1].args, ('SELECT COUNT(*) FROM helpdesk.users',))
        sql, params = calls[2].args
        self.assertEqual(params[0], 'new_account')
        self.assertEqual(params[2:], ('New Operator', 'Admin', None))
        self.assertTrue(params[1] == self.stored)
        self.assertTrue(self.credential not in params and self.credential not in sql)
        self.assertNotIn('status', sql)
        self.connection.commit.assert_called_once_with()
        self.connection.__exit__.assert_called_once()  # Closing releases the creation lock.

    def test_stale_bootstrap_attempt_requires_authorization_after_an_account_exists(self):
        self.cursor.fetchone.side_effect = [(1,), (0,), (1,), (1,)]
        with patch.object(repo, 'hash_password', return_value=self.stored):
            repo.create_account('first_account', 'First Operator', 'Admin', self.credential)
            with self.assertRaises(repo.UserAuthorizationError):
                repo.create_account('second_account', 'Second Operator', 'Admin', self.credential)
        inserts = [call for call in self.cursor.execute.call_args_list if call.args[0].startswith('INSERT')]
        self.assertEqual(len(inserts), 1)
        self.connection.commit.assert_called_once_with()
        self.connection.rollback.assert_called_once_with()

    def test_existing_accounts_require_a_verified_grant_not_an_id_or_public_user(self):
        for grant in (None, 7, account(), object()):
            self.cursor.fetchone.side_effect = [(1,), (2,)]
            with patch.object(repo, 'hash_password', return_value=self.stored), self.assertRaises(repo.UserAuthorizationError):
                repo.create_account('new_account', 'New Operator', 'Admin', self.credential, authorization=grant)
        self.assertFalse(any(call.args[0].startswith('INSERT') for call in self.cursor.execute.call_args_list))
        self.connection.commit.assert_not_called()

    def test_active_admin_can_create_both_supported_roles_using_bound_queries(self):
        for role in repo.USER_ROLES:
            with self.subTest(role=role):
                self.cursor.reset_mock()
                self.connection.reset_mock()
                technician_id = 12 if role == 'Technician' else None
                self.cursor.fetchone.side_effect = [(1,), (2,), (7,)] + ([(12,), None] if technician_id else [])
                with patch.object(repo, 'hash_password', return_value=self.stored):
                    self.assertEqual(repo.create_account("new'account", 'New Operator', role, self.credential,
                                                        authorization=authorized_admin(), technician_id=technician_id), 15)
                admin_sql, admin_params = self.cursor.execute.call_args_list[2].args
                self.assertIn('FOR UPDATE', admin_sql)
                self.assertEqual(admin_params, (7, 'Admin', 'Active'))
                insert_sql, params = self.cursor.execute.call_args.args
                self.assertEqual(params[0], "new'account")
                self.assertTrue(params[1] == self.stored)
                self.assertEqual(params[2:], ('New Operator', role, technician_id))
                self.assertNotIn("new'account", insert_sql)
                self.connection.commit.assert_called_once_with()

    def test_revoked_or_no_longer_active_admin_cannot_save(self):
        for revoked in (False, True):
            authorization = authorized_admin()
            if revoked:
                authorization.revoke()
            self.cursor.fetchone.side_effect = [(1,), (2,), None]
            with patch.object(repo, 'hash_password', return_value=self.stored), self.assertRaises(repo.UserAuthorizationError):
                repo.create_account('new_account', 'New Operator', 'Admin', self.credential, authorization=authorization)
        self.assertFalse(any(call.args[0].startswith('INSERT') for call in self.cursor.execute.call_args_list))
        self.connection.commit.assert_not_called()

    def test_non_admin_inactive_wrong_password_and_unknown_admin_authorization_fail(self):
        for case in ('technician', 'inactive', 'wrong_password', 'unknown'):
            user = {**account('Technician' if case == 'technician' else 'Admin'), 'password_hash': self.stored}
            if case == 'inactive':
                user['status'] = 'Inactive'
            self.cursor.fetchone.side_effect = None
            self.cursor.fetchone.return_value = None if case == 'unknown' else user
            credential = secrets.token_urlsafe(32) if case == 'wrong_password' else self.credential
            with self.subTest(case=case):
                self.assertIsNone(repo.authorize_account_creation('test_account', credential))
        self.connection.commit.assert_not_called()

    def test_valid_admin_authorization_contains_no_credentials(self):
        self.cursor.fetchone.return_value = {**account(), 'password_hash': self.stored}
        grant = repo.authorize_account_creation('test_account', self.credential)
        self.assertIsNotNone(grant)
        self.assertEqual(grant.user_id, 7)
        self.assertTrue(grant.active)
        self.assertTrue('password' not in vars(grant) and 'password_hash' not in vars(grant))

    def test_duplicate_username_is_friendly_and_rolls_back(self):
        self.cursor.fetchone.side_effect = [(1,), (2,), (7,)]
        def execute(sql, *args):
            if sql.startswith('INSERT'):
                raise mysql.connector.Error(self.credential + self.stored, errno=1062)
        self.cursor.execute.side_effect = execute
        with patch.object(repo, 'hash_password', return_value=self.stored), self.assertRaises(repo.UserCreateError) as caught:
            repo.create_account('existing_account', 'New Operator', 'Admin', self.credential,
                                authorization=authorized_admin())
        self.assertIn('username already exists', str(caught.exception))
        self.assertTrue(self.credential not in str(caught.exception) and self.stored not in str(caught.exception))
        self.connection.rollback.assert_called_once_with()
        self.connection.commit.assert_not_called()

    def test_lock_timeout_or_commit_failure_never_reports_success(self):
        for locked in (0, None):
            self.cursor.fetchone.side_effect = None
            self.cursor.fetchone.return_value = (locked,)
            with patch.object(repo, 'hash_password', return_value=self.stored), self.assertRaises(repo.UserCreateError):
                repo.create_account('new_account', 'New Operator', 'Admin', self.credential)
        self.assertFalse(any(call.args[0].startswith('INSERT') for call in self.cursor.execute.call_args_list))
        self.cursor.fetchone.side_effect = [(1,), (0,)]
        self.connection.commit.side_effect = mysql.connector.Error(self.credential, errno=2013)
        with patch.object(repo, 'hash_password', return_value=self.stored), self.assertRaises(repo.UserCreateError) as caught:
            repo.create_account('new_account', 'New Operator', 'Admin', self.credential)
        self.assertTrue(self.credential not in str(caught.exception))
        self.assertEqual(self.connection.rollback.call_count, 3)


class LoginAccountIntegrationTests(unittest.TestCase):
    def test_create_button_opens_only_one_dialog_and_never_logs_in(self):
        login = login_without_widgets()
        with patch.object(login_gui, 'CreateAccountDialog') as dialog:
            dialog.return_value.is_open = True
            login.open_create_account()
            login.open_create_account()
            dialog.assert_called_once_with(login.root)
            dialog.return_value.focus.assert_called_once_with()
            dialog.return_value.is_open = False
            login.open_create_account()
            self.assertEqual(dialog.call_count, 2)
        login.on_authenticated.assert_not_called()

    def test_login_and_account_creation_cannot_start_concurrently(self):
        login = login_without_widgets()
        login._account_dialog = MagicMock(is_open=True)
        with patch.object(login_gui, 'Thread') as worker:
            self.assertEqual(login.attempt_login(), 'break')
        worker.assert_not_called()
        login._account_dialog.focus.assert_called_once_with()
        for flag in ('_authenticating', '_closed'):
            login = login_without_widgets()
            setattr(login, flag, True)
            with patch.object(login_gui, 'CreateAccountDialog') as dialog:
                login.open_create_account()
            dialog.assert_not_called()

    def test_exit_closes_unsaved_form_but_waits_for_pending_creation(self):
        login = login_without_widgets()
        login._account_dialog = MagicMock(is_open=True, is_saving=True)
        login.close()
        login.root.destroy.assert_not_called()
        login._account_dialog.focus.assert_called_once_with()
        login._account_dialog.is_saving = False
        login.close()
        login._account_dialog.cancel.assert_called_once_with()
        login.root.destroy.assert_called_once_with()


class AccountDialogTests(unittest.TestCase):
    def test_count_result_selects_bootstrap_or_authorization_and_error_never_opens_form(self):
        for count in (0, 1, 5):
            dialog = dialog_without_widgets(stage='checking')
            dialog._results.put((count, None))
            with patch.object(dialog, '_show_account_form') as form, patch.object(dialog, '_show_authorization') as authorize:
                dialog._check_count()
            if count == 0:
                form.assert_called_once_with(first_account=True)
                authorize.assert_not_called()
            else:
                authorize.assert_called_once_with()
                form.assert_not_called()
        dialog = dialog_without_widgets(stage='checking')
        dialog._results.put((None, 'Check the connection.'))
        with patch.object(dialog, '_show_account_form') as form, patch.object(dialog, '_show_authorization') as authorize:
            dialog._check_count()
        form.assert_not_called()
        authorize.assert_not_called()
        dialog.feedback.set.assert_called_once_with('Check the connection.')
        self.assertEqual(dialog.action_button.configure.call_args.kwargs['text'], 'Retry')

    def test_authorization_uses_existing_repository_preserves_password_and_clears_field(self):
        dialog = dialog_without_widgets(stage='authorize')
        credential = dialog._fields['admin_password'].get.return_value
        with patch.object(gui, 'Thread') as worker:
            dialog.authorize()
            dialog.authorize()
        self.assertEqual(worker.call_count, 1)
        self.assertTrue(worker.call_args.kwargs['args'] == ('test_account', credential))
        dialog._fields['admin_password'].set.assert_called_once_with('')
        self.assertFalse(dialog.is_saving)
        grant = authorized_admin()
        with patch.object(gui, 'authorize_account_creation', return_value=grant) as authorize:
            dialog._authorize('test_account', credential)
        self.assertTrue(authorize.call_args.args == ('test_account', credential))
        with patch.object(dialog, '_show_account_form') as form:
            dialog._check_authorization()
        form.assert_called_once_with(first_account=False)
        self.assertIs(dialog._authorization, grant)

    def test_failed_authorization_is_generic_and_keeps_creation_form_closed(self):
        dialog = dialog_without_widgets(stage='authorize')
        with patch.object(gui, 'authorize_account_creation', return_value=None):
            dialog._authorize('test_account', secrets.token_urlsafe(32))
        with patch.object(dialog, '_show_account_form') as form:
            dialog._check_authorization()
        form.assert_not_called()
        dialog.feedback.set.assert_called_once_with(gui.AUTHORIZATION_FAILED)
        for field in ('admin_username', 'admin_password'):
            dialog = dialog_without_widgets(stage='authorize')
            dialog._fields[field].get.return_value = ' \t '
            with patch.object(gui, 'Thread') as worker:
                dialog.authorize()
            worker.assert_not_called()
            dialog.feedback.set.assert_called_once_with(gui.AUTHORIZATION_FAILED)

    def test_first_account_role_cannot_be_changed_to_technician(self):
        dialog = dialog_without_widgets(first=True)
        dialog._fields['role'].get.return_value = 'Technician'
        with patch.object(gui, 'Thread') as worker:
            dialog.save()
        self.assertEqual(worker.call_args.kwargs['args'][:3], ('new_account', 'New Operator', 'Admin'))

    def test_blank_invalid_name_role_password_and_mismatched_confirmation_do_not_write(self):
        for field, value in (('username', ' \t '), ('full_name', ''), ('full_name', '333'),
                             ('full_name', '!!!'), ('role', 'Other'), ('password', ' \t '),
                             ('confirmation', secrets.token_urlsafe(32))):
            dialog = dialog_without_widgets()
            dialog._fields[field].get.return_value = value
            with self.subTest(field=field), patch.object(gui, 'Thread') as worker:
                dialog.save()
            worker.assert_not_called()
            dialog.feedback.set.assert_called_once()
            self.assertFalse(dialog._closed)
            dialog._fields['password'].set.assert_called_once_with('')
            dialog._fields['confirmation'].set.assert_called_once_with('')

    def test_worker_saves_via_guarded_repository_and_success_closes_without_login(self):
        dialog = dialog_without_widgets()
        credential = secrets.token_urlsafe(32)
        with patch.object(gui, 'create_account', return_value=15) as create:
            dialog._create('new_account', 'New Operator', 'Technician', credential, 12)
        self.assertTrue(create.call_count == 1 and create.call_args.args == ('new_account', 'New Operator', 'Technician', credential))
        self.assertIs(create.call_args.kwargs['authorization'], dialog._authorization)
        self.assertEqual(create.call_args.kwargs['technician_id'], 12)
        grant = dialog._authorization
        with patch.object(gui.messagebox, 'showinfo') as message:
            dialog._check_creation()
        message.assert_called_once_with('Account Created', 'Account created successfully.', parent=dialog.parent)
        self.assertTrue(dialog._closed)
        self.assertFalse(grant.active)
        dialog.window.destroy.assert_called_once_with()

    def test_duplicate_and_database_errors_keep_form_open_and_unexpected_errors_hide_secrets(self):
        credential = secrets.token_urlsafe(32)
        for error in (repo.UserCreateError('A user with that username already exists.'), RuntimeError(credential)):
            dialog = dialog_without_widgets()
            with patch.object(gui, 'create_account', side_effect=error), patch.object(gui.messagebox, 'showinfo') as message:
                dialog._create('new_account', 'New Operator', 'Admin', credential)
                dialog._check_creation()
            self.assertFalse(dialog._closed)
            self.assertTrue(credential not in dialog.feedback.set.call_args.args[0])
            message.assert_not_called()

    def test_cancel_releases_authorization_clears_credentials_and_ignores_late_results(self):
        dialog = dialog_without_widgets()
        grant = dialog._authorization
        dialog._poll_id = 'pending'
        dialog.cancel()
        dialog._results.put((15, None))
        with patch.object(gui, 'create_account') as create, patch.object(gui.messagebox, 'showinfo') as message:
            dialog._check_creation()
            dialog._create('new_account', 'New Operator', 'Admin', secrets.token_urlsafe(32))
        create.assert_not_called()
        message.assert_not_called()
        self.assertFalse(grant.active)
        for value in dialog._fields.values():
            value.set.assert_called_once_with('')
        dialog.window.after_cancel.assert_called_once_with('pending')

    def test_cancel_during_authorization_revokes_late_grant_and_save_cannot_be_cancelled(self):
        dialog = dialog_without_widgets(stage='authorize')
        dialog.cancel()
        grant = authorized_admin()
        with patch.object(gui, 'authorize_account_creation', return_value=grant):
            dialog._authorize('test_account', secrets.token_urlsafe(32))
        self.assertFalse(grant.active)
        dialog = dialog_without_widgets()
        dialog._saving = True
        dialog.cancel()
        dialog.window.destroy.assert_not_called()

    def test_form_masks_passwords_and_limits_bootstrap_role_options(self):
        for first in (True, False):
            dialog = dialog_without_widgets()
            with ExitStack() as stack:
                stack.enter_context(patch.object(gui.tk, 'StringVar', side_effect=lambda *args, **kwargs: MagicMock()))
                stack.enter_context(patch.object(gui.ttk, 'Label'))
                entries = stack.enter_context(patch.object(gui.ttk, 'Entry'))
                combo = stack.enter_context(patch.object(gui.ttk, 'Combobox'))
                stack.enter_context(patch.object(gui, 'Thread'))
                dialog._show_account_form(first_account=first)
            self.assertEqual([call.kwargs.get('show') for call in entries.call_args_list], [None, None, '*', '*'])
            self.assertEqual(combo.call_args.kwargs['state'], 'readonly')
            self.assertEqual(combo.call_args_list[0].kwargs['values'], ('Admin',) if first else ('Admin', 'Technician'))

    def test_workers_never_print_credentials_or_access_widgets(self):
        dialog = dialog_without_widgets(stage='checking')
        credential = secrets.token_urlsafe(32)
        output = io.StringIO()
        with redirect_stdout(output), patch.object(gui, 'get_user_count', return_value=1), \
             patch.object(gui, 'authorize_account_creation', return_value=None), \
             patch.object(gui, 'create_account', side_effect=RuntimeError(credential)):
            dialog._load_count()
            dialog._authorize('test_account', credential)
            dialog._create('new_account', 'New Operator', 'Admin', credential)
        self.assertEqual(output.getvalue(), '')
        for widget in (dialog.window, dialog.feedback, dialog.explanation, dialog.action_button):
            self.assertEqual(widget.mock_calls, [])


if __name__ == '__main__':
    unittest.main()
