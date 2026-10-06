"""Masked form, session targeting, and async feedback without interactive Tk."""
from contextlib import ExitStack
from queue import Queue
import secrets
import unittest
from unittest.mock import MagicMock, patch

import gui_app as app_gui
import gui_change_password as gui
from gui_permissions import PERMISSION_DENIED, SessionPermissions
from test_gui_app import viewer_without_window
from test_gui_authentication import account
from test_gui_permissions import mock_viewer_widgets
from user_repository import UserPasswordChangeError


def dialog_without_widgets(role='Admin'):
    dialog = gui.ChangePasswordDialog.__new__(gui.ChangePasswordDialog)
    dialog.window = MagicMock()
    dialog._user_id = 7
    dialog.permissions = SessionPermissions(account(role))
    dialog._results = Queue()
    dialog._saving = False
    dialog._closed = False
    dialog._poll_id = None
    dialog.current_password = MagicMock()
    dialog.current_password.get.return_value = secrets.token_urlsafe(32)
    dialog.new_password = MagicMock()
    dialog.new_password.get.return_value = secrets.token_urlsafe(32)
    dialog.confirmation = MagicMock()
    dialog.confirmation.get.return_value = dialog.new_password.get.return_value
    dialog.entries = [MagicMock() for _ in range(3)]
    dialog.feedback = MagicMock()
    dialog.save_button = MagicMock()
    dialog.cancel_button = MagicMock()
    return dialog


class PasswordControlsTests(unittest.TestCase):
    def test_three_fields_are_masked_with_enter_bindings_and_no_account_selector(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(gui.tk, 'Toplevel'))
            stack.enter_context(patch.object(gui.tk, 'StringVar'))
            stack.enter_context(patch.object(gui.ttk, 'Frame'))
            labels = stack.enter_context(patch.object(gui.ttk, 'Label'))
            entries = stack.enter_context(patch.object(gui.ttk, 'Entry', side_effect=lambda *args, **kwargs: MagicMock()))
            buttons = stack.enter_context(patch.object(gui.ttk, 'Button'))
            dialog = gui.ChangePasswordDialog(MagicMock(), 7, SessionPermissions(account()))
        self.assertEqual(entries.call_count, 3)
        self.assertEqual([call.kwargs['show'] for call in entries.call_args_list], ['*'] * 3)
        for entry in dialog.entries:
            entry.bind.assert_called_once_with('<Return>', dialog.save)
        self.assertEqual([call.kwargs.get('text') for call in labels.call_args_list[:4]],
                         ['Change Password', 'Current Password', 'New Password', 'Confirm New Password'])
        self.assertEqual([call.kwargs['text'] for call in buttons.call_args_list], ['Cancel', 'Save'])

    def test_both_roles_have_account_button_and_handler_uses_session_id(self):
        for role in ('Admin', 'Technician'):
            with ExitStack() as stack:
                mock_viewer_widgets(stack)
                viewer = app_gui.TicketViewer(MagicMock(), account(role), MagicMock())
                viewer.password_button.state.assert_not_called()
                dialog = stack.enter_context(patch.object(app_gui, 'ChangePasswordDialog'))
                # The selected ticket ID must never decide whose password changes.
                viewer.tree.selection.return_value = ('999',)
                viewer.open_change_password()
            dialog.assert_called_once_with(viewer.root, 7, permissions=viewer.permissions)
            viewer.tree.selection.assert_not_called()

    def test_duplicate_dialog_and_other_modal_forms_are_focused(self):
        for field in ('_password_dialog', '_create_dialog', '_update_dialog', '_delete_dialog',
                      '_technician_window', '_user_window'):
            viewer = viewer_without_window()
            viewer.user = account()
            existing = MagicMock(is_open=True)
            setattr(viewer, field, existing)
            with patch.object(app_gui, 'ChangePasswordDialog') as dialog:
                viewer.open_change_password()
            dialog.assert_not_called()
            existing.focus.assert_called_once_with()

    def test_revoked_unknown_and_absent_sessions_cannot_open_form(self):
        for identity in (None, account('Unknown'), {**account(), 'status': 'Inactive'}):
            viewer = viewer_without_window()
            viewer.user = identity
            viewer.permissions = SessionPermissions(identity)
            with patch.object(app_gui, 'ChangePasswordDialog') as dialog, patch.object(gui.messagebox, 'showinfo') as message:
                viewer.open_change_password()
            dialog.assert_not_called()
            message.assert_called_once_with('Permission Denied', PERMISSION_DENIED, parent=viewer.root)
        viewer = viewer_without_window()
        viewer.permissions.revoke()
        with patch.object(app_gui, 'ChangePasswordDialog') as dialog, patch.object(gui.messagebox, 'showinfo'):
            viewer.open_change_password()
        dialog.assert_not_called()


class PasswordFormTests(unittest.TestCase):
    def test_invalid_input_keeps_form_open_without_starting_save(self):
        for case, message in (('current', 'Current password is incorrect.'), ('blank', 'New password cannot be blank.'),
                              ('mismatch', 'New passwords do not match.'),
                              ('same', 'New password must be different from the current password.')):
            dialog = dialog_without_widgets()
            if case == 'current':
                dialog.current_password.get.return_value = ''
            elif case == 'blank':
                dialog.new_password.get.return_value = ' \t '
            elif case == 'mismatch':
                dialog.confirmation.get.return_value = secrets.token_urlsafe(32)
            else:
                dialog.new_password.get.return_value = dialog.current_password.get.return_value
                dialog.confirmation.get.return_value = dialog.current_password.get.return_value
            with patch.object(gui, 'Thread') as worker:
                self.assertEqual(dialog.save(), 'break')
            dialog.feedback.set.assert_called_once_with(message)
            worker.assert_not_called()
            self.assertTrue(dialog.is_open)
            self.assertFalse(dialog.is_saving)

    def test_save_uses_shared_validation_and_background_worker_once_for_both_roles(self):
        for role in ('Admin', 'Technician'):
            dialog = dialog_without_widgets(role)
            values = tuple(variable.get() for variable in (dialog.current_password, dialog.new_password, dialog.confirmation))
            with patch.object(gui, 'validate_password_change', wraps=gui.validate_password_change) as validate, \
                 patch.object(gui, 'Thread') as worker:
                dialog.save()
                dialog.save()
            self.assertTrue(validate.call_count == 1 and validate.call_args.args == values)
            self.assertTrue(worker.call_count == 1 and worker.call_args.kwargs['args'] == values)
            self.assertEqual(worker.call_args.kwargs['target'], dialog._change_password)
            worker.return_value.start.assert_called_once_with()
            dialog.window.after.assert_called_once_with(100, dialog._check_save)
            for variable in (dialog.current_password, dialog.new_password, dialog.confirmation):
                variable.set.assert_called_once_with('')
            self.assertTrue(dialog.is_saving)

    def test_worker_passes_fixed_account_id_and_never_touches_tkinter(self):
        dialog = dialog_without_widgets('Technician')
        values = tuple(variable.get() for variable in (dialog.current_password, dialog.new_password, dialog.confirmation))
        with patch.object(gui, 'change_password', return_value=True) as change:
            dialog._change_password(*values)
        self.assertTrue(change.call_args.args == (7, *values))
        self.assertIsNone(dialog._results.get_nowait())
        for widget in (dialog.window, dialog.feedback, dialog.save_button, *dialog.entries):
            self.assertEqual(widget.mock_calls, [])

    def test_safe_errors_keep_form_open_and_enable_retry(self):
        for error in (UserPasswordChangeError('Current password is incorrect.'), ValueError('New passwords do not match.')):
            dialog = dialog_without_widgets()
            dialog._saving = True
            with patch.object(gui, 'change_password', side_effect=error):
                dialog._change_password(secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(32))
            with patch.object(gui.messagebox, 'showinfo') as message:
                dialog._check_save()
            dialog.feedback.set.assert_called_once_with(str(error))
            message.assert_not_called()
            dialog.save_button.state.assert_called_once_with(['!disabled'])
            self.assertTrue(dialog.is_open)
            self.assertFalse(dialog.is_saving)

    def test_unexpected_errors_never_expose_secret_details(self):
        dialog = dialog_without_widgets()
        secret = secrets.token_urlsafe(32)
        with patch.object(gui, 'change_password', side_effect=RuntimeError(secret)):
            dialog._change_password(secret, secrets.token_urlsafe(32), secrets.token_urlsafe(32))
        self.assertEqual(dialog._results.get_nowait(), 'Unable to change password. Please try again.')

    def test_revoked_session_blocks_handler_and_worker(self):
        dialog = dialog_without_widgets()
        dialog.permissions.revoke()
        with patch.object(gui, 'Thread') as worker, patch.object(gui.messagebox, 'showinfo'):
            dialog.save()
        worker.assert_not_called()
        with patch.object(gui, 'change_password') as change:
            dialog._change_password(secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(32))
        change.assert_not_called()
        self.assertEqual(dialog._results.get_nowait(), PERMISSION_DENIED)

    def test_success_closes_only_dialog_and_keeps_session_permissions(self):
        dialog = dialog_without_widgets()
        dialog._saving = True
        dialog._results.put(None)
        with patch.object(gui.messagebox, 'showinfo') as message:
            dialog._check_save()
        message.assert_called_once_with('Password Changed', 'Password changed successfully.', parent=dialog.window)
        self.assertFalse(dialog.is_open)
        self.assertTrue(dialog.permissions.allows('change_password'))
        self.assertTrue(dialog.permissions.allows('view_tickets'))
        dialog.window.destroy.assert_called_once_with()

    def test_cancel_clears_fields_and_never_calls_repository(self):
        dialog = dialog_without_widgets()
        dialog._poll_id = 'pending-save'
        with patch.object(gui, 'change_password') as change:
            dialog.cancel()
            dialog.cancel()
            dialog.save()
        change.assert_not_called()
        for variable in (dialog.current_password, dialog.new_password, dialog.confirmation):
            variable.set.assert_called_once_with('')
        dialog.window.after_cancel.assert_called_once_with('pending-save')
        dialog.window.destroy.assert_called_once_with()

    def test_cancel_waits_for_pending_write_and_empty_queue_keeps_polling(self):
        dialog = dialog_without_widgets()
        dialog._saving = True
        dialog.cancel()
        self.assertTrue(dialog.is_open)
        dialog.window.destroy.assert_not_called()
        dialog._check_save()
        self.assertTrue(dialog.is_saving)
        dialog.window.after.assert_called_once_with(100, dialog._check_save)

    def test_closed_dialog_ignores_late_results(self):
        dialog = dialog_without_widgets()
        dialog.cancel()
        dialog._results.put(None)
        with patch.object(gui.messagebox, 'showinfo') as message:
            dialog._check_save()
        message.assert_not_called()


if __name__ == '__main__':
    unittest.main()
