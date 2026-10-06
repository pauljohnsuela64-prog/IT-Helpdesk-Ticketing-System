"""Login/session checks without interactive windows or stored test credentials."""
from contextlib import ExitStack
from queue import Queue
import secrets
import unittest
from unittest.mock import MagicMock, patch

import gui_app as gui
import gui_login as login_gui
import gui_session as sessions
from user_repository import public_user
from test_gui_app import ticket, viewer_without_window


def account(role='Admin'):
    return dict(user_id=7, username='test_account', full_name='Test Operator', role=role,
                status='Active', created_at=None)


def login_without_widgets():
    login = login_gui.LoginScreen.__new__(login_gui.LoginScreen)
    login.root = MagicMock()
    login.content = MagicMock()
    login.username = MagicMock()
    login.username.get.return_value = '  test_account  '
    login.password = MagicMock()
    login.password.get.return_value = secrets.token_urlsafe(32)
    login.username_entry = MagicMock()
    login.password_entry = MagicMock()
    login.login_button = MagicMock()
    login.feedback = MagicMock()
    login.on_authenticated = MagicMock()
    login._results = Queue()
    login._authenticating = False
    login._closed = False
    login._poll_id = None
    return login


class LoginScreenTests(unittest.TestCase):
    def test_masked_password_enter_binding_and_only_login_exit_controls(self):
        with ExitStack() as stack:
            for name in ('Style', 'Frame', 'Label'):
                stack.enter_context(patch.object(login_gui.ttk, name))
            entries = stack.enter_context(patch.object(login_gui.ttk, 'Entry', side_effect=[MagicMock(), MagicMock()]))
            buttons = stack.enter_context(patch.object(login_gui.ttk, 'Button'))
            stack.enter_context(patch.object(login_gui.tk, 'StringVar'))
            login = login_gui.LoginScreen(MagicMock(), MagicMock())
        self.assertEqual(entries.call_args.kwargs['show'], '*')
        login.password_entry.bind.assert_called_once_with('<Return>', login.attempt_login)
        self.assertEqual({item.kwargs['text'] for item in buttons.call_args_list}, {'Login', 'Exit'})

    def test_attempt_trims_username_preserves_password_clears_field_and_starts_only_one_worker(self):
        login = login_without_widgets()
        candidate = login.password.get.return_value
        with patch.object(login_gui, 'Thread') as worker:
            self.assertEqual(login.attempt_login(MagicMock()), 'break')
            login.attempt_login()
        self.assertTrue(worker.call_count == 1)
        self.assertTrue(worker.call_args.kwargs['args'] == ('test_account', candidate))
        self.assertEqual(worker.call_args.kwargs['target'], login._authenticate)
        login.password.set.assert_called_once_with('')
        login.username.set.assert_called_once_with('test_account')
        login.login_button.state.assert_called_once_with(['disabled'])
        self.assertTrue(login._authenticating)

    def test_invalid_input_is_generic_and_never_queries(self):
        for case in ('username', 'password', 'long_username'):
            login = login_without_widgets()
            if case == 'password':
                login.password.get.return_value = ' \t '
            else:
                login.username.get.return_value = '' if case == 'username' else 'x' * 51
            with patch.object(login_gui, 'Thread') as worker:
                login.attempt_login()
            worker.assert_not_called()
            login.feedback.set.assert_called_once_with('Invalid username or password.')

    def test_worker_reuses_authentication_returns_only_public_data_and_never_accesses_widgets(self):
        login = login_without_widgets()
        candidate = secrets.token_urlsafe(32)
        user = {**account(), 'password_hash': secrets.token_hex(64)}
        with patch.object(login_gui, 'authenticate_user', return_value=user) as authenticate:
            login._authenticate('test_account', candidate)
        self.assertTrue(authenticate.call_count == 1 and authenticate.call_args.args == ('test_account', candidate))
        result, error = login._results.get_nowait()
        self.assertTrue(result == account())
        self.assertIsNone(error)
        for widget in (login.root, login.username, login.password, login.feedback):
            self.assertEqual(widget.mock_calls, [])

    def test_failed_credentials_never_open_main_and_always_show_same_message(self):
        login = login_without_widgets()
        with patch.object(login_gui, 'authenticate_user', return_value=None):
            login._authenticate('test_account', secrets.token_urlsafe(32))
        login._check_login()
        login.on_authenticated.assert_not_called()
        login.feedback.set.assert_called_once_with('Invalid username or password.')
        self.assertFalse(login._authenticating)
        login.password.set.assert_called_once_with('')

    def test_success_uses_public_session_data_for_both_roles(self):
        for role in ('Admin', 'Technician'):
            login = login_without_widgets()
            login._results.put(({**account(role), 'password_hash': secrets.token_hex(64)}, None))
            login._check_login()
            self.assertTrue(login.on_authenticated.call_count == 1)
            self.assertTrue(login.on_authenticated.call_args.args[0] == account(role))
            login.password.set.assert_called_once_with('')

    def test_database_or_unexpected_error_stays_on_login_and_hides_exception_secrets(self):
        candidate = secrets.token_urlsafe(32)
        for error in (login_gui.UserAuthenticationError('Check the database connection.'), RuntimeError(candidate)):
            login = login_without_widgets()
            with patch.object(login_gui, 'authenticate_user', side_effect=error):
                login._authenticate('test_account', candidate)
            login._check_login()
            login.on_authenticated.assert_not_called()
            self.assertTrue(candidate not in login.feedback.set.call_args.args[0])
            self.assertFalse(login._authenticating)
            login.login_button.state.assert_called_once_with(['!disabled'])

    def test_pending_result_polls_and_exit_cancels_poll_clears_fields_ignores_late_login(self):
        login = login_without_widgets()
        login.root.after.return_value = 'pending'
        login._check_login()
        login.root.after.assert_called_once_with(100, login._check_login)
        login.close()
        login.close()
        login._results.put((account(), None))
        login._check_login()
        with patch.object(login_gui, 'Thread') as worker:
            login.attempt_login()
        worker.assert_not_called()
        login.on_authenticated.assert_not_called()
        login.root.destroy.assert_called_once_with()
        login.root.after_cancel.assert_called_once_with('pending')
        login.password.set.assert_called_once_with('')


class SessionControllerTests(unittest.TestCase):
    def test_initial_screen_is_login_and_main_does_not_start_before_authentication(self):
        viewer = MagicMock()
        root = MagicMock()
        with patch.object(sessions, 'LoginScreen') as login:
            app = sessions.HelpDeskApplication(root, viewer)
        viewer.assert_not_called()
        login.assert_called_once_with(root, app._open_helpdesk)
        self.assertIsNone(app.user)

    def test_login_logout_login_reuses_root_and_resets_user_without_saving_credentials(self):
        viewer = MagicMock()
        root = MagicMock()
        with patch.object(sessions, 'LoginScreen') as login:
            app = sessions.HelpDeskApplication(root, viewer)
            app._open_helpdesk({**account(), 'password_hash': secrets.token_hex(64)})
            self.assertTrue(app.user == account())
            login.return_value.dispose.assert_called_once_with()
            self.assertIsNone(app._login)
            self.assertTrue(viewer.call_count == 1)
            self.assertTrue(viewer.call_args.args == (root,) and viewer.call_args.kwargs['user'] == account())
            self.assertEqual(viewer.call_args.kwargs['on_logout'], app._return_to_login)
            app._return_to_login()
            self.assertIsNone(app.user)
            self.assertIsNone(app._viewer)
            self.assertEqual(login.call_count, 2)
            app._open_helpdesk(account('Technician'))
            self.assertEqual(app.user['role'], 'Technician')
            self.assertEqual(viewer.call_count, 2)
        root.destroy.assert_not_called()

    def test_gui_entry_point_starts_session_controller_then_event_loop(self):
        with patch.object(gui.tk, 'Tk') as root, patch.object(gui, 'HelpDeskApplication') as session, \
             patch.object(gui, 'TicketViewer') as viewer:
            gui.main()
        session.assert_called_once_with(root.return_value, viewer)
        viewer.assert_not_called()
        root.return_value.mainloop.assert_called_once_with()


class ViewerSessionTests(unittest.TestCase):
    def viewer(self):
        viewer = viewer_without_window()
        viewer.content = MagicMock()
        viewer.user = account()
        viewer._on_logout = MagicMock()
        return viewer

    def test_logged_in_information_logout_and_existing_controls_available_to_both_roles(self):
        for role in ('Admin', 'Technician'):
            with ExitStack() as stack:
                for name in ('Style', 'Frame', 'Entry', 'Treeview', 'Scrollbar'):
                    stack.enter_context(patch.object(gui.ttk, name))
                labels = stack.enter_context(patch.object(gui.ttk, 'Label'))
                buttons = stack.enter_context(patch.object(gui.ttk, 'Button'))
                stack.enter_context(patch.object(gui.tk, 'StringVar'))
                stack.enter_context(patch.object(gui, 'DashboardPanel'))
                stack.enter_context(patch.object(gui.TicketViewer, 'refresh_tickets'))
                viewer = gui.TicketViewer(MagicMock(), user=account(role), on_logout=MagicMock())
            texts = [item.kwargs.get('text') for item in labels.call_args_list]
            self.assertIn(f'Logged in as: Test Operator ({role})', texts)
            self.assertTrue('password_hash' not in viewer.user)
            self.assertTrue({'Logout', 'Create Ticket', 'Update Ticket', 'Delete Ticket', 'Manage Technicians',
                             'Ticket Notes', 'View History'}.issubset({item.kwargs['text'] for item in buttons.call_args_list}))

    def test_logout_closes_all_children_and_cancels_table_and_dashboard_reads_without_exiting(self):
        viewer = self.viewer()
        viewer._poll_id = 'pending-table'
        for field in ('_create_dialog', '_update_dialog', '_delete_dialog', '_technician_window', '_notes_window'):
            setattr(viewer, field, MagicMock(is_open=True, is_saving=False))
        viewer._history_window = MagicMock(is_open=True)
        viewer.logout()
        self.assertTrue(viewer._closed)
        self.assertIsNone(viewer.user)
        viewer.root.after_cancel.assert_called_once_with('pending-table')
        viewer.dashboard.close.assert_called_once_with()
        viewer._history_window.close.assert_called_once_with()
        viewer.content.destroy.assert_called_once_with()
        viewer._on_logout.assert_called_once_with()
        viewer.root.destroy.assert_not_called()
        viewer.logout()
        viewer.close()
        viewer._on_logout.assert_called_once_with()
        viewer.root.destroy.assert_not_called()

    def test_pending_write_keeps_session_logged_in_until_save_finishes(self):
        for field in ('_create_dialog', '_update_dialog', '_delete_dialog', '_technician_window', '_notes_window'):
            viewer = self.viewer()
            active = MagicMock(is_open=True, is_saving=True)
            setattr(viewer, field, active)
            viewer.logout()
            self.assertFalse(viewer._closed)
            self.assertEqual(viewer.user, account())
            active.focus.assert_called_once_with()
            viewer._on_logout.assert_not_called()
            viewer.dashboard.close.assert_not_called()
            active.is_saving = False
            viewer.logout()
            viewer._on_logout.assert_called_once_with()

    def test_late_old_session_result_cannot_repopulate_new_login_screen(self):
        viewer = self.viewer()
        viewer.logout()
        viewer._results.put(([ticket()], None))
        with patch.object(gui, 'Thread') as worker:
            viewer._check_refresh()
            viewer.refresh_tickets()
            viewer._request_refresh()
        worker.assert_not_called()
        viewer.tree.insert.assert_not_called()


if __name__ == '__main__':
    unittest.main()
