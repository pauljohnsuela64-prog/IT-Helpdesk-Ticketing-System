"""Admin user management UI contracts without interactive Tk or live writes."""
from contextlib import ExitStack
from queue import Queue
import secrets
import unittest
from unittest.mock import MagicMock, patch

import gui_app as app
import gui_permissions as permissions
import gui_session as sessions
import gui_users as gui
import user_repository as repo
from test_gui_app import viewer_without_window
from test_gui_permissions import mock_viewer_widgets, policy
from test_user_management import user


def manager_without_widgets(role='Admin'):
    manager = gui.UserManagementWindow.__new__(gui.UserManagementWindow)
    manager.parent = MagicMock()
    manager.window = MagicMock()
    manager.user = user(role=role)
    manager.permissions = policy(role)
    manager.tree = MagicMock()
    manager.tree.selection.return_value = ()
    manager.tree.get_children.return_value = ('old',)
    manager.tree.exists.return_value = False
    manager.feedback = MagicMock()
    manager.refresh_button = MagicMock()
    manager.change_button = MagicMock()
    manager.link_button = MagicMock()
    manager._results = Queue()
    manager._loading = False
    manager._closed = False
    manager._poll_id = None
    manager._refresh_pending = False
    manager._child_dialog = None
    return manager


def status_dialog_without_widgets():
    dialog = gui.ChangeUserStatusDialog.__new__(gui.ChangeUserStatusDialog)
    dialog.manager = manager_without_widgets()
    dialog.manager._child_dialog = dialog
    dialog.parent = dialog.manager.window
    dialog.window = MagicMock()
    dialog.user_id = 12
    dialog._results = Queue()
    dialog._loading = False
    dialog._saving = False
    dialog._closed = False
    dialog._poll_id = None
    dialog._current = user(12, 'Technician')
    dialog.status = MagicMock()
    dialog.status.get.return_value = 'Inactive'
    dialog.status_combo = MagicMock()
    dialog.save_button = MagicMock()
    dialog.cancel_button = MagicMock()
    dialog.feedback = MagicMock()
    dialog.summary = MagicMock()
    return dialog


class MainUserManagementTests(unittest.TestCase):
    def test_manage_users_is_created_for_admin_and_hidden_for_technician(self):
        for role in ('Admin', 'Technician'):
            with self.subTest(role=role), ExitStack() as stack:
                mock_viewer_widgets(stack)
                buttons = stack.enter_context(patch.object(app.ttk, 'Button', side_effect=lambda *args, **kwargs: MagicMock()))
                viewer = app.TicketViewer(MagicMock(), user=user(role=role), on_logout=MagicMock())
            labels = {call.kwargs['text'] for call in buttons.call_args_list}
            self.assertEqual('Manage Users' in labels, role == 'Admin')
            if role == 'Admin':
                self.assertIsNotNone(viewer.user_button)
                viewer.user_button.state.assert_not_called()
            else:
                self.assertIsNone(viewer.user_button)

    def test_direct_handler_denies_technician_and_revoked_admin(self):
        for revoked in (False, True):
            viewer = viewer_without_window()
            viewer.permissions = policy('Admin' if revoked else 'Technician')
            if revoked:
                viewer.permissions.revoke()
            with patch.object(app, 'UserManagementWindow') as window, \
                 patch.object(permissions.messagebox, 'showinfo') as message:
                viewer.open_user_management()
            window.assert_not_called()
            message.assert_called_once_with('Permission Denied', permissions.PERMISSION_DENIED, parent=viewer.root)

    def test_admin_opens_one_manager_with_session_identity_and_shared_permissions(self):
        viewer = viewer_without_window()
        viewer.user = user()
        with patch.object(app, 'UserManagementWindow') as window:
            window.return_value.is_open = True
            viewer.open_user_management()
            viewer.open_user_management()
            window.assert_called_once_with(viewer.root, viewer.user, permissions=viewer.permissions)
            window.return_value.focus.assert_called_once_with()
            window.return_value.is_open = False
            viewer.open_user_management()
            self.assertEqual(window.call_count, 2)

    def test_user_manager_and_existing_forms_do_not_replace_modal_grabs(self):
        viewer = viewer_without_window()
        viewer._user_window = MagicMock(is_open=True)
        for action, factory in ((viewer.open_create_ticket, 'CreateTicketDialog'),
                                (viewer.open_update_ticket, 'UpdateTicketDialog'),
                                (viewer.open_delete_ticket, 'DeleteTicketDialog'),
                                (viewer.open_technician_management, 'TechnicianManagementWindow'),
                                (viewer.open_ticket_history, 'TicketHistoryWindow'),
                                (viewer.open_ticket_notes, 'TicketNotesWindow')):
            with patch.object(app, factory) as window:
                action()
            window.assert_not_called()
        self.assertEqual(viewer._user_window.focus.call_count, 6)
        for attribute in ('_create_dialog', '_update_dialog', '_delete_dialog', '_technician_window'):
            viewer = viewer_without_window()
            existing = MagicMock(is_open=True)
            setattr(viewer, attribute, existing)
            with patch.object(app, 'UserManagementWindow') as window:
                viewer.open_user_management()
            window.assert_not_called()
            existing.focus.assert_called_once_with()

    def test_logout_waits_for_user_status_save_then_closes_and_revokes(self):
        viewer = viewer_without_window()
        viewer.user = user()
        viewer.content = MagicMock()
        viewer._on_logout = MagicMock()
        viewer._user_window = MagicMock(is_open=True, is_saving=True)
        viewer.logout()
        viewer._on_logout.assert_not_called()
        self.assertTrue(viewer.permissions.allows('manage_users'))
        viewer._user_window.is_saving = False
        viewer.logout()
        viewer._user_window.cancel.assert_called_once_with()
        viewer._on_logout.assert_called_once_with()
        self.assertFalse(viewer.permissions.allows('manage_users'))

    def test_admin_technician_admin_sessions_recalculate_manage_users_visibility(self):
        with ExitStack() as stack:
            mock_viewer_widgets(stack)
            stack.enter_context(patch.object(sessions, 'LoginScreen', side_effect=lambda *args: MagicMock()))
            application = sessions.HelpDeskApplication(MagicMock(), app.TicketViewer)
            for role in ('Admin', 'Technician', 'Admin'):
                application._open_helpdesk(user(role=role))
                viewer = application._viewer
                self.assertEqual(viewer.user_button is not None, role == 'Admin')
                self.assertEqual(viewer.permissions.allows('manage_users'), role == 'Admin')
                viewer.logout()
                self.assertFalse(viewer.permissions.allows('manage_users'))


class UserTableTests(unittest.TestCase):
    def test_row_formatter_contains_seven_public_fields_only(self):
        current = {**user(12, 'Technician', 'Inactive'), 'password': secrets.token_urlsafe(32),
                   'password_hash': secrets.token_hex(64)}
        values = gui.user_row_values(current)
        self.assertEqual(values, ('12', 'test_user_12', 'Test Operator', 'Technician', '-', 'Inactive', '2026-10-06 10:30'))
        self.assertEqual(gui.user_row_values({**current, 'full_name': 'José\nReyes', 'created_at': None})[2], 'José\\nReyes')

    def test_construction_uses_single_selection_seven_columns_scrollbars_and_link_action(self):
        with ExitStack() as stack:
            for name in ('Toplevel', 'StringVar'):
                stack.enter_context(patch.object(gui.tk, name))
            for name in ('Frame', 'Label'):
                stack.enter_context(patch.object(gui.ttk, name))
            tree = stack.enter_context(patch.object(gui.ttk, 'Treeview'))
            scrollbars = stack.enter_context(patch.object(gui.ttk, 'Scrollbar'))
            buttons = stack.enter_context(patch.object(gui.ttk, 'Button'))
            refresh = stack.enter_context(patch.object(gui.UserManagementWindow, 'refresh'))
            manager = gui.UserManagementWindow(MagicMock(), user(), permissions=policy('Admin'))
        self.assertEqual(tree.call_args.kwargs['columns'], ('user_id', 'username', 'full_name', 'role', 'technician_name', 'status', 'created_at'))
        self.assertEqual(tree.call_args.kwargs['selectmode'], 'browse')
        self.assertEqual({call.kwargs['text'] for call in buttons.call_args_list}, {'Change User Status', 'Link Technician', 'Refresh', 'Close'})
        self.assertEqual([call.kwargs['orient'] for call in scrollbars.call_args_list], ['vertical', 'horizontal'])
        refresh.assert_called_once_with()
        self.assertTrue(manager.is_open)

    def test_refresh_worker_strips_private_fields_and_never_accesses_widgets(self):
        manager = manager_without_widgets()
        with patch.object(gui, 'get_users', return_value=[{**user(), 'password_hash': secrets.token_hex(64)}]) as read:
            manager._load_users()
        read.assert_called_once_with(7)
        self.assertEqual(manager._results.get_nowait(), ([user()], None))
        for widget in (manager.window, manager.tree, manager.feedback, manager.refresh_button):
            self.assertEqual(widget.mock_calls, [])

    def test_success_empty_error_and_repeated_refresh_are_handled(self):
        manager = manager_without_widgets()
        with patch.object(gui, 'Thread') as worker:
            manager.refresh()
            manager.refresh()
        worker.assert_called_once_with(target=manager._load_users, daemon=True)
        manager._results.put(([user(), user(12, 'Technician', 'Inactive')], None))
        manager._check_refresh()
        self.assertEqual(manager.tree.insert.call_count, 2)
        self.assertIn('Inactive', manager.tree.insert.call_args.args + manager.tree.insert.call_args.kwargs['values'])
        manager.tree.reset_mock()
        manager._results.put((None, 'Check the connection.'))
        manager._check_refresh()
        manager.tree.delete.assert_not_called()
        manager.change_button.state.assert_called_with(['disabled'])
        manager.feedback.set.assert_called_with('Check the connection.')
        manager._results.put(([], None))
        manager._check_refresh()
        manager.feedback.set.assert_called_with('No application users found.')

    def test_unauthorized_refresh_status_handlers_and_worker_never_reach_repositories(self):
        for revoked in (False, True):
            manager = manager_without_widgets('Admin' if revoked else 'Technician')
            if revoked:
                manager.permissions.revoke()
            with patch.object(gui, 'get_users') as read, patch.object(gui, 'Thread') as worker, \
                 patch.object(gui, 'ChangeUserStatusDialog') as dialog, \
                 patch.object(permissions.messagebox, 'showinfo') as message:
                manager.refresh()
                manager.open_status()
                manager._load_users()
            for target in (read, worker, dialog):
                target.assert_not_called()
            self.assertEqual(message.call_count, 2)
            self.assertEqual(manager._results.get_nowait(), (None, permissions.PERMISSION_DENIED))

    def test_select_exactly_one_valid_user_and_repeated_click_focuses_existing_dialog(self):
        for selection in ((), ('7', '12'), ('0',), ('7 OR 1=1',), ('2147483648',)):
            manager = manager_without_widgets()
            manager.tree.selection.return_value = selection
            with patch.object(gui, 'ChangeUserStatusDialog') as dialog, patch.object(gui.messagebox, 'showinfo') as message:
                manager.open_status()
            dialog.assert_not_called()
            message.assert_called_once()
        manager = manager_without_widgets()
        manager.tree.selection.return_value = ('12',)
        with patch.object(gui, 'ChangeUserStatusDialog') as dialog:
            dialog.return_value.is_open = True
            manager.open_status()
            manager.open_status()
        dialog.assert_called_once_with(manager, 12)
        dialog.return_value.focus.assert_called_once_with()

    def test_close_waits_for_save_cancels_pending_reads_and_ignores_late_results(self):
        manager = manager_without_widgets()
        manager._child_dialog = MagicMock(is_open=True, is_saving=True)
        manager.close()
        manager.window.destroy.assert_not_called()
        manager._child_dialog.is_saving = False
        manager._poll_id = 'pending'
        manager.close()
        manager._results.put(([user()], None))
        manager._check_refresh()
        manager._child_dialog.cancel.assert_called_once_with()
        manager.window.after_cancel.assert_called_once_with('pending')
        manager.tree.insert.assert_not_called()


class UserStatusDialogTests(unittest.TestCase):
    def test_loads_fresh_public_information_and_handles_missing_user(self):
        dialog = status_dialog_without_widgets()
        with patch.object(gui, 'get_user', return_value={**user(12, 'Technician'), 'password_hash': secrets.token_hex(64)}) as read:
            dialog._load_user()
        read.assert_called_once_with(12, 7)
        dialog._check_load()
        self.assertEqual(dialog._current, user(12, 'Technician'))
        self.assertNotIn('password', dialog.summary.set.call_args.args[0])
        dialog.status_combo.configure.assert_called_with(state='readonly')
        dialog = status_dialog_without_widgets()
        dialog._results.put((None, None))
        with patch.object(gui.messagebox, 'showinfo') as message, patch.object(dialog.manager, '_request_refresh'):
            dialog._check_load()
        message.assert_called_once_with('User Not Found', 'No user found with that ID.', parent=dialog.parent)
        self.assertFalse(dialog.is_open)

    def test_cancelled_confirmation_or_unchanged_invalid_status_never_starts_save(self):
        for status in ('Other', 'Active', 'Inactive'):
            dialog = status_dialog_without_widgets()
            dialog.status.get.return_value = status
            with patch.object(gui.messagebox, 'askyesno', return_value=False) as confirm, patch.object(gui, 'Thread') as worker:
                dialog.save()
            worker.assert_not_called()
            self.assertEqual(confirm.call_count, 1 if status == 'Inactive' else 0)
        self.assertIn('cancelled', dialog.feedback.set.call_args.args[0])

    def test_confirmed_save_updates_only_selected_user_then_refreshes(self):
        dialog = status_dialog_without_widgets()
        with patch.object(gui.messagebox, 'askyesno', return_value=True) as confirm, patch.object(gui, 'Thread') as worker:
            dialog.save()
            dialog.save()
        self.assertEqual(confirm.call_args.kwargs['default'], gui.messagebox.NO)
        self.assertIn('User ID: 12', confirm.call_args.args[1])
        worker.assert_called_once_with(target=dialog._change_status, args=('Inactive',), daemon=True)
        self.assertTrue(dialog.is_saving)
        dialog.cancel()
        dialog.window.destroy.assert_not_called()
        with patch.object(gui, 'update_user_status', return_value=True) as update:
            dialog._change_status('Inactive')
        update.assert_called_once_with(12, 'Inactive', 7)
        with patch.object(gui.messagebox, 'showinfo') as message, patch.object(dialog.manager, '_request_refresh') as refresh:
            dialog._check_save()
        refresh.assert_called_once_with()
        message.assert_called_once_with('User Status Changed', 'User #12 status changed successfully.', parent=dialog.parent)

    def test_self_and_last_admin_rejections_keep_form_open_with_friendly_reason(self):
        for reason in ('Cannot deactivate the last Active Admin account.', 'You cannot deactivate your own account while logged in.'):
            dialog = status_dialog_without_widgets()
            with patch.object(gui, 'update_user_status', side_effect=repo.UserStatusChangeError(reason)), \
                 patch.object(gui.messagebox, 'showinfo') as message:
                dialog._change_status('Inactive')
                dialog._check_save()
            self.assertTrue(dialog.is_open)
            dialog.feedback.set.assert_called_with(reason)
            message.assert_not_called()

    def test_technician_or_revoked_session_cannot_load_confirm_or_write_directly(self):
        for revoked in (False, True):
            dialog = status_dialog_without_widgets()
            dialog.manager.permissions = policy('Admin' if revoked else 'Technician')
            if revoked:
                dialog.manager.permissions.revoke()
            with patch.object(gui, 'get_user') as read, patch.object(gui, 'update_user_status') as update, \
                 patch.object(gui.messagebox, 'askyesno') as confirm, patch.object(gui, 'Thread') as worker, \
                 patch.object(permissions.messagebox, 'showinfo') as message:
                dialog.save()
                dialog._load_user()
                dialog._change_status('Inactive')
            for target in (read, update, confirm, worker):
                target.assert_not_called()
            message.assert_called_once_with('Permission Denied', permissions.PERMISSION_DENIED, parent=dialog.window)
            self.assertEqual(dialog._results.get_nowait(), (None, permissions.PERMISSION_DENIED))
            self.assertEqual(dialog._results.get_nowait(), (None, permissions.PERMISSION_DENIED))

    def test_database_or_unexpected_failures_do_not_crash_or_expose_private_details(self):
        private_detail = secrets.token_hex(64)
        for error in (repo.UserUpdateError('Check the connection.'), RuntimeError(private_detail)):
            dialog = status_dialog_without_widgets()
            with patch.object(gui, 'update_user_status', side_effect=error), patch.object(gui.messagebox, 'showinfo') as message:
                dialog._change_status('Inactive')
                dialog._check_save()
            self.assertTrue(private_detail not in dialog.feedback.set.call_args.args[0])
            self.assertFalse(dialog.is_saving)
            message.assert_not_called()

    def test_noop_result_and_late_cancelled_results_do_not_report_a_status_change(self):
        dialog = status_dialog_without_widgets()
        dialog._results.put((False, None))
        with patch.object(gui.messagebox, 'showinfo') as message, patch.object(dialog.manager, '_request_refresh'):
            dialog._check_save()
        self.assertEqual(message.call_args.args[0], 'No Changes')
        dialog = status_dialog_without_widgets()
        dialog._poll_id = 'pending'
        dialog.cancel()
        dialog._results.put((True, None))
        with patch.object(gui.messagebox, 'showinfo') as message:
            dialog._check_save()
        message.assert_not_called()
        dialog.window.after_cancel.assert_called_once_with('pending')


if __name__ == '__main__':
    unittest.main()
