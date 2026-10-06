"""Role enforcement and session isolation without GUI interaction or database writes."""
from contextlib import ExitStack
import unittest
from unittest.mock import MagicMock, patch

import gui_app as gui
import gui_delete_ticket as delete_gui
import gui_permissions as permissions
import gui_session as sessions
import gui_technicians as technicians_gui
import gui_ticket_notes as notes_gui
from test_gui_app import ticket, viewer_without_window
from test_gui_authentication import account
from test_gui_delete_ticket import dialog_without_window as ticket_deletion
from test_gui_technicians import dialog_without_window as technician_dialog, manager_without_window
from test_gui_ticket_notes import dialog_without_widgets as note_dialog, notes, window_without_widgets


def policy(role):
    return permissions.SessionPermissions(account(role))


def mock_viewer_widgets(stack):
    """Keep distinct button mocks so one disabled button cannot mask another."""
    for name in ('Style', 'Frame', 'Entry', 'Treeview', 'Scrollbar'):
        stack.enter_context(patch.object(gui.ttk, name))
    labels = stack.enter_context(patch.object(gui.ttk, 'Label'))
    stack.enter_context(patch.object(gui.ttk, 'Button', side_effect=lambda *args, **kwargs: MagicMock()))
    stack.enter_context(patch.object(gui.tk, 'StringVar'))
    stack.enter_context(patch.object(gui, 'DashboardPanel'))
    stack.enter_context(patch.object(gui.TicketViewer, 'refresh_tickets'))
    return labels


class RolePolicyTests(unittest.TestCase):
    def test_complete_admin_and_technician_permission_matrix(self):
        allowed = ('dashboard', 'view_tickets', 'search_tickets', 'create_ticket', 'update_ticket',
                   'view_history', 'view_notes', 'add_note', 'refresh', 'change_password')
        restricted = ('delete_ticket', 'manage_technicians', 'delete_note', 'manage_users')
        for role in ('Admin', 'Technician'):
            session = policy(role)
            for action in allowed + restricted:
                with self.subTest(role=role, action=action):
                    self.assertEqual(session.allows(action), role == 'Admin' or action in allowed)
            self.assertFalse(session.allows('unknown_action'))

    def test_missing_unknown_or_inactive_identity_has_no_permissions(self):
        for user in (None, {}, account('Unknown'), account('admin'), {**account(), 'status': 'Inactive'}):
            with self.subTest(user=user):
                session = permissions.SessionPermissions(user)
                for action in permissions.ADMIN_PERMISSIONS:
                    self.assertFalse(session.allows(action))

    def test_role_is_a_snapshot_and_revoking_one_session_leaves_another_unchanged(self):
        user = account('Technician')
        technician = permissions.SessionPermissions(user)
        user['role'] = 'Admin'
        self.assertFalse(technician.allows('delete_ticket'))
        first_admin, next_admin = policy('Admin'), policy('Admin')
        first_admin.revoke()
        self.assertTrue(next_admin.allows('delete_ticket'))
        for action in permissions.ADMIN_PERMISSIONS:
            self.assertFalse(first_admin.allows(action))


class MainWindowPermissionTests(unittest.TestCase):
    def test_buttons_are_disabled_consistently_only_for_restricted_roles(self):
        for role in ('Admin', 'Technician'):
            with self.subTest(role=role), ExitStack() as stack:
                labels = mock_viewer_widgets(stack)
                viewer = gui.TicketViewer(MagicMock(), user=account(role), on_logout=MagicMock())
                for button in (viewer.delete_button, viewer.technician_button):
                    if role == 'Technician':
                        button.state.assert_called_once_with(['disabled'])
                    else:
                        button.state.assert_not_called()
                for button in (viewer.create_button, viewer.update_button, viewer.history_button,
                               viewer.notes_button, viewer.refresh_button, viewer.logout_button, viewer.password_button):
                    button.state.assert_not_called()
                self.assertIn(f'Logged in as: Test Operator ({role})',
                              [call.kwargs.get('text') for call in labels.call_args_list])

    def test_direct_restricted_handlers_are_blocked_before_selection_or_dialogs(self):
        for role in ('Technician', 'Unknown'):
            for handler in ('open_delete_ticket', 'open_technician_management'):
                with self.subTest(role=role, handler=handler):
                    viewer = viewer_without_window()
                    viewer.permissions = policy(role)
                    viewer.tree.selection.return_value = ('7',)
                    with patch.object(gui, 'DeleteTicketDialog') as delete, \
                         patch.object(gui, 'TechnicianManagementWindow') as manager, \
                         patch.object(permissions.messagebox, 'showinfo') as message:
                        getattr(viewer, handler)()
                    delete.assert_not_called()
                    manager.assert_not_called()
                    viewer.tree.selection.assert_not_called()
                    message.assert_called_once_with('Permission Denied', permissions.PERMISSION_DENIED,
                                                    parent=viewer.root)

    def test_admin_restricted_dialogs_receive_the_current_session_policy(self):
        for handler, factory in (('open_delete_ticket', 'DeleteTicketDialog'),
                                 ('open_technician_management', 'TechnicianManagementWindow')):
            viewer = viewer_without_window()
            viewer.tree.selection.return_value = ('7',)
            with patch.object(gui, factory) as dialog:
                getattr(viewer, handler)()
            self.assertIs(dialog.call_args.kwargs['permissions'], viewer.permissions)

    def test_technician_can_create_update_history_and_notes_for_any_ticket(self):
        for handler, factory in (('open_create_ticket', 'CreateTicketDialog'),
                                 ('open_update_ticket', 'UpdateTicketDialog'),
                                 ('open_ticket_history', 'TicketHistoryWindow'),
                                 ('open_ticket_notes', 'TicketNotesWindow')):
            with self.subTest(handler=handler):
                viewer = viewer_without_window()
                viewer.permissions = policy('Technician')
                viewer.tree.selection.return_value = ('987',)
                with patch.object(gui, factory) as dialog, patch.object(permissions.messagebox, 'showinfo') as message:
                    getattr(viewer, handler)()
                dialog.assert_called_once()
                message.assert_not_called()
                if handler != 'open_create_ticket':
                    self.assertEqual(dialog.call_args.args[1], 987)
                if handler == 'open_ticket_notes':
                    self.assertIs(dialog.call_args.kwargs['permissions'], viewer.permissions)

    def test_technician_numeric_search_refresh_and_dashboard_are_preserved(self):
        viewer = viewer_without_window()
        viewer.permissions = policy('Technician')
        viewer.search_term.get.return_value = ' 3 '
        with patch.object(gui, 'Thread'):
            viewer.perform_search()
        self.assertEqual(viewer._active_search, '3')
        viewer.dashboard.refresh.assert_called_once_with()
        viewer._loading = False
        with patch.object(gui, 'Thread') as worker:
            viewer.refresh_tickets()
        worker.assert_called_once_with(target=viewer._load_tickets, args=('3',), daemon=True)
        self.assertEqual(viewer.dashboard.refresh.call_count, 2)


class NotesPermissionTests(unittest.TestCase):
    def test_technician_refresh_never_enables_delete_but_keeps_add_available(self):
        window = window_without_widgets()
        window.permissions = policy('Technician')
        for _ in range(3):
            window._results.put((ticket(), notes(), None))
            window._check_refresh()
        self.assertEqual(window.delete_button.state.call_count, 3)
        for call in window.delete_button.state.call_args_list:
            self.assertEqual(call.args, (['disabled'],))
        window.add_button.state.assert_called_with(['!disabled'])
        self.assertEqual(set(window._notes), {'51', '8'})

    def test_admin_refresh_enables_add_and_delete(self):
        window = window_without_widgets()
        window._enable_actions(True)
        window.add_button.state.assert_called_once_with(['!disabled'])
        window.delete_button.state.assert_called_once_with(['!disabled'])

    def test_technician_can_view_old_notes_from_inactive_authors(self):
        window = window_without_widgets()
        window.permissions = policy('Technician')
        with patch.object(notes_gui, 'get_ticket', return_value=ticket()), \
             patch.object(notes_gui, 'get_ticket_comments', return_value=notes()) as read:
            window._load_notes()
        window._check_refresh()
        read.assert_called_once_with(7)
        self.assertIn('Inactive Author', window.tree.insert.call_args_list[1].kwargs['values'])

    def test_technician_can_open_add_note(self):
        window = window_without_widgets()
        window.permissions = policy('Technician')
        with patch.object(notes_gui, 'AddNoteDialog') as dialog:
            window.open_add()
        dialog.assert_called_once_with(window)

    def test_technician_can_save_note_through_existing_validation_and_repository(self):
        dialog = note_dialog()
        dialog.owner.permissions = policy('Technician')
        with patch.object(notes_gui, 'Thread') as worker:
            dialog.save()
        text = 'Checked network cable.\nConnection is stable now.'
        worker.assert_called_once_with(target=dialog._add_note, args=(12, text), daemon=True)
        with patch.object(notes_gui, 'add_ticket_comment', return_value=90) as add:
            dialog._add_note(12, text)
        add.assert_called_once_with(7, 12, text)
        self.assertEqual(dialog._results.get_nowait(), (90, None))

    def test_direct_delete_note_handler_is_blocked_even_with_selected_note(self):
        window = window_without_widgets()
        window.permissions = policy('Technician')
        window.tree.selection.return_value = ('51',)
        with patch.object(notes_gui, 'DeleteNoteDialog') as dialog, \
             patch.object(permissions.messagebox, 'showinfo') as message:
            window.open_delete()
        dialog.assert_not_called()
        window.tree.selection.assert_not_called()
        message.assert_called_once_with('Permission Denied', permissions.PERMISSION_DENIED, parent=window.window)


class RestrictedDialogPermissionTests(unittest.TestCase):
    def test_technician_cannot_confirm_ticket_or_note_deletion(self):
        for dialog, action, module in ((ticket_deletion(), 'confirm_delete', delete_gui),
                                       (note_dialog(deletion=True), 'save', notes_gui)):
            with self.subTest(action=action):
                context = dialog if action == 'confirm_delete' else dialog.owner
                context.permissions = policy('Technician')
                with patch.object(module, 'Thread') as worker, \
                     patch.object(permissions.messagebox, 'showinfo') as message:
                    getattr(dialog, action)()
                worker.assert_not_called()
                message.assert_called_once_with('Permission Denied', permissions.PERMISSION_DENIED, parent=dialog.window)
                self.assertFalse(dialog.is_saving)

    def test_technician_cannot_open_or_refresh_technician_management_through_other_paths(self):
        for action in ('open_add', 'open_status', 'refresh'):
            manager = manager_without_window()
            manager.permissions = policy('Technician')
            manager.tree.selection.return_value = ('12',)
            with patch.object(technicians_gui, 'Thread') as worker, \
                 patch.object(technicians_gui, 'AddTechnicianDialog') as add, \
                 patch.object(technicians_gui, 'ChangeTechnicianStatusDialog') as change, \
                 patch.object(permissions.messagebox, 'showinfo') as message:
                getattr(manager, action)()
            for target in (worker, add, change):
                target.assert_not_called()
            message.assert_called_once_with('Permission Denied', permissions.PERMISSION_DENIED, parent=manager.window)

    def test_technician_cannot_save_technician_forms_directly(self):
        for status_dialog in (False, True):
            dialog = technician_dialog(status_dialog=status_dialog)
            dialog.manager.permissions = policy('Technician')
            with patch.object(technicians_gui, 'Thread') as worker, \
                 patch.object(permissions.messagebox, 'showinfo') as message, \
                 patch.object(technicians_gui.messagebox, 'askyesno') as confirm:
                dialog.save()
            worker.assert_not_called()
            confirm.assert_not_called()
            message.assert_called_once_with('Permission Denied', permissions.PERMISSION_DENIED, parent=dialog.window)
            self.assertFalse(dialog.is_saving)

    def test_restricted_workers_check_permission_before_repository_access(self):
        for revoked in (False, True):
            context = policy('Admin' if revoked else 'Technician')
            if revoked:
                context.revoke()
            delete = ticket_deletion()
            delete.permissions = context
            note = note_dialog(deletion=True)
            note.owner.permissions = context
            manager = manager_without_window()
            manager.permissions = context
            add = technician_dialog()
            add.manager.permissions = context
            change = technician_dialog(status_dialog=True)
            change.manager.permissions = context
            operations = (
                (delete_gui, delete, '_load_ticket', 'get_ticket', ()),
                (delete_gui, delete, '_delete_ticket', 'delete_ticket', ()),
                (notes_gui, note, '_load_data', 'get_ticket_comments', ()),
                (notes_gui, note, '_delete_note', 'delete_ticket_comment', ()),
                (technicians_gui, manager, '_load_technicians', 'get_technicians', ()),
                (technicians_gui, add, '_add_technician', 'add_technician', ('Test Operator', 'test@example.com')),
                (technicians_gui, change, '_load_technician', 'get_technician', ()),
                (technicians_gui, change, '_change_status', 'update_technician_status', ('Inactive',)),
            )
            for module, target, action, repository, args in operations:
                with self.subTest(revoked=revoked, action=action), \
                     patch.object(module, repository) as write, \
                     patch.object(permissions.messagebox, 'showinfo') as message:
                    getattr(target, action)(*args)
                    write.assert_not_called()
                    message.assert_not_called()  # Workers must never access Tkinter.
                    self.assertEqual(target._results.get_nowait(), (None, permissions.PERMISSION_DENIED))

    def test_admin_can_still_delete_one_ticket_and_one_note(self):
        delete = ticket_deletion()
        note = note_dialog(deletion=True)
        with patch.object(delete_gui, 'delete_ticket', return_value=True) as delete_ticket, \
             patch.object(notes_gui, 'delete_ticket_comment', return_value=True) as delete_note:
            delete._delete_ticket()
            note._delete_note()
        delete_ticket.assert_called_once_with(7)
        delete_note.assert_called_once_with(7, 51)
        self.assertEqual(delete._results.get_nowait(), (True, None))
        self.assertEqual(note._results.get_nowait(), (True, None))


class RoleSessionTests(unittest.TestCase):
    def test_admin_technician_admin_logins_recalculate_permissions_and_revoke_old_sessions(self):
        with ExitStack() as stack:
            mock_viewer_widgets(stack)
            stack.enter_context(patch.object(sessions, 'LoginScreen', side_effect=lambda *args: MagicMock()))
            application = sessions.HelpDeskApplication(MagicMock(), gui.TicketViewer)
            previous = []
            for role in ('Admin', 'Technician', 'Admin'):
                application._open_helpdesk(account(role))
                viewer = application._viewer
                self.assertEqual(viewer.user['role'], role)
                for action in ('delete_ticket', 'manage_technicians', 'delete_note'):
                    self.assertEqual(viewer.permissions.allows(action), role == 'Admin')
                self.assertTrue(viewer.permissions.allows('update_ticket'))
                self.assertTrue(viewer.permissions.allows('add_note'))
                for old in previous:
                    self.assertIsNot(viewer.permissions, old)
                    self.assertFalse(old.allows('delete_ticket'))
                previous.append(viewer.permissions)
                viewer.logout()
                self.assertIsNone(application.user)
                self.assertIsNone(application._viewer)
                self.assertFalse(viewer.permissions.allows('delete_ticket'))
                self.assertFalse(viewer.permissions.allows('add_note'))

    def test_logout_with_pending_write_keeps_permissions_until_write_finishes(self):
        viewer = viewer_without_window()
        viewer.content = MagicMock()
        viewer._on_logout = MagicMock()
        viewer._delete_dialog = MagicMock(is_open=True, is_saving=True)
        viewer.logout()
        self.assertTrue(viewer.permissions.allows('delete_ticket'))
        viewer._on_logout.assert_not_called()
        viewer._delete_dialog.is_saving = False
        viewer.logout()
        self.assertFalse(viewer.permissions.allows('delete_ticket'))
        viewer._on_logout.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
