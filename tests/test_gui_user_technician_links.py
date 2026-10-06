"""Account forms, Admin linking, and automatic note authors without interactive Tk."""
from contextlib import ExitStack
from queue import Queue
import unittest
from unittest.mock import MagicMock, patch

import gui_create_account as creation
import gui_link_user_technician as linking
import gui_permissions
import gui_ticket_notes as notes_gui
import gui_users
import user_repository as users
from test_gui_app import ticket
from test_gui_authentication import account
from test_gui_create_account import dialog_without_widgets as account_dialog
from test_gui_ticket_notes import dialog_without_widgets as note_dialog, window_without_widgets
from test_gui_users import manager_without_widgets


TECHNICIANS = [dict(technician_id=12, full_name='Test Technician', email='test@example.com'),
               dict(technician_id=35, full_name='Test Technician', email='second@example.com')]


def link_dialog():
    dialog = linking.LinkUserTechnicianDialog.__new__(linking.LinkUserTechnicianDialog)
    dialog.manager = manager_without_widgets()
    dialog.manager._request_refresh = MagicMock()
    dialog.manager._child_dialog = dialog
    dialog.window = MagicMock()
    dialog.user_id = 20
    dialog._results = Queue()
    dialog._loading = False
    dialog._saving = False
    dialog._closed = False
    dialog._poll_id = None
    dialog._current = {**account('Technician'), 'user_id': 20}
    dialog._technicians = TECHNICIANS.copy()
    dialog.technician_combo = MagicMock()
    dialog.technician_combo.current.return_value = 0
    dialog.feedback = MagicMock()
    dialog.summary = MagicMock()
    dialog.save_button = MagicMock()
    dialog.cancel_button = MagicMock()
    return dialog


def technician_note_dialog():
    dialog = note_dialog()
    dialog._ticket = {**dialog._ticket, 'assigned_technician_id': 12}
    dialog.owner._ticket = dialog._ticket
    dialog.owner.user = {**account('Technician'), 'technician_id': 12}
    dialog.owner.permissions = gui_permissions.SessionPermissions(dialog.owner.user)
    dialog.author = None
    dialog.author_name = MagicMock()
    dialog._widgets = [(dialog.text, 'normal')]
    return dialog


class AccountLinkFormTests(unittest.TestCase):
    def test_technician_selection_is_required_and_no_available_records_is_friendly(self):
        for available, selected in (([], 0), (TECHNICIANS, -1), (TECHNICIANS, 10)):
            dialog = account_dialog()
            dialog._available_technicians = available
            dialog.technician_combo.current.return_value = selected
            with patch.object(creation, 'Thread') as worker:
                dialog.save()
            worker.assert_not_called()
            self.assertTrue(dialog.is_open)
            if not available:
                self.assertEqual(dialog.feedback.set.call_args.args[0], users.NO_AVAILABLE_TECHNICIANS)

    def test_selected_id_is_saved_and_admin_does_not_require_a_link(self):
        for role in ('Admin', 'Technician'):
            dialog = account_dialog()
            dialog._fields['role'].get.return_value = role
            dialog._available_technicians = TECHNICIANS
            dialog.technician_combo.current.return_value = 1
            with patch.object(creation, 'Thread') as worker:
                dialog.save()
            self.assertEqual(worker.call_args.kwargs['args'][:3], ('new_account', 'New Operator', role))
            self.assertEqual(worker.call_args.kwargs['args'][-1], 35 if role == 'Technician' else None)

    def test_role_switch_hides_admin_selection_and_displays_technician_selection(self):
        dialog = account_dialog()
        dialog._technician_rows = [MagicMock(), MagicMock()]
        dialog._fields['role'].get.return_value = 'Admin'
        dialog._role_changed()
        for widget in dialog._technician_rows:
            widget.grid_remove.assert_called_once_with()
        dialog._fields['role'].get.return_value = 'Technician'
        dialog._role_changed()
        for widget in dialog._technician_rows:
            widget.grid.assert_called_once_with()

    def test_available_choices_require_admin_authorization_and_never_touch_tk_in_worker(self):
        dialog = account_dialog()
        with patch.object(creation, 'get_available_technicians', return_value=TECHNICIANS) as read:
            dialog._load_technicians()
        read.assert_called_once_with(authorization=dialog._authorization)
        self.assertEqual(dialog._results.get_nowait(), (TECHNICIANS, None))
        self.assertEqual(dialog.feedback.mock_calls, [])
        dialog._results.put((TECHNICIANS, None))
        dialog._check_technicians()
        dialog.technician_combo.configure.assert_called_once_with(
            values=('Test Technician (ID: 12)', 'Test Technician (ID: 35)'))

    def test_read_failure_keeps_choices_empty_and_does_not_crash(self):
        dialog = account_dialog()
        dialog._available_technicians = []
        with patch.object(creation, 'get_available_technicians', side_effect=users.UserReadError('Run migration.')):
            dialog._load_technicians()
        dialog._check_technicians()
        dialog.feedback.set.assert_called_once_with('Run migration.')
        self.assertTrue(dialog.is_open)
        self.assertEqual(dialog._available_technicians, [])

    def test_new_selection_is_readonly_and_bootstrap_does_not_load_technicians(self):
        for first in (True, False):
            dialog = account_dialog(first=first)
            with ExitStack() as stack:
                def variable(*args, **kwargs):
                    result = MagicMock()
                    result.get.return_value = kwargs.get('value', '')
                    return result

                stack.enter_context(patch.object(creation.tk, 'StringVar', side_effect=variable))
                stack.enter_context(patch.object(creation.ttk, 'Label'))
                stack.enter_context(patch.object(creation.ttk, 'Entry'))
                combos = stack.enter_context(patch.object(creation.ttk, 'Combobox', side_effect=lambda *args, **kwargs: MagicMock()))
                worker = stack.enter_context(patch.object(creation, 'Thread'))
                dialog._show_account_form(first)
            self.assertEqual(combos.call_args_list[1].kwargs['state'], 'readonly')
            if first:
                worker.assert_not_called()
                dialog.technician_combo.grid_remove.assert_called_once_with()
            else:
                self.assertEqual(worker.call_args.kwargs['target'], dialog._load_technicians)


class ManageUserLinkTests(unittest.TestCase):
    def test_selection_is_required_and_invalid_ids_are_rejected(self):
        for selected in ((), ('20', '30'), ('abc',), ('0',)):
            manager = manager_without_widgets()
            manager.tree.selection.return_value = selected
            with patch.object(gui_users, 'LinkUserTechnicianDialog') as form, patch.object(gui_users.messagebox, 'showinfo') as message:
                manager.open_link()
            form.assert_not_called()
            message.assert_called_once()

    def test_admin_opens_one_link_dialog_and_technician_cannot_open_it(self):
        manager = manager_without_widgets()
        manager.tree.selection.return_value = ('20',)
        with patch.object(gui_users, 'LinkUserTechnicianDialog') as form:
            manager.open_link()
            manager.open_link()
        form.assert_called_once_with(manager, 20)
        form.return_value.focus.assert_called_once_with()
        manager = manager_without_widgets('Technician')
        with patch.object(gui_users, 'LinkUserTechnicianDialog') as form, patch.object(gui_users.messagebox, 'showinfo'):
            manager.open_link()
        form.assert_not_called()

    def test_table_displays_linked_name_without_password_data(self):
        row = {**account('Technician'), 'technician_id': 12, 'technician_name': 'Test Technician'}
        values = gui_users.user_row_values(row)
        self.assertIn('Test Technician', values)
        self.assertEqual(len(values), 7)

    def test_link_dialog_loads_fresh_account_and_only_available_records(self):
        dialog = link_dialog()
        with patch.object(linking, 'get_user', return_value=dialog._current) as read, \
             patch.object(linking, 'get_available_technicians', return_value=TECHNICIANS) as available:
            dialog._load_data()
        read.assert_called_once_with(20, 7)
        available.assert_called_once_with(7)
        self.assertEqual(dialog.feedback.mock_calls, [])
        dialog._check_load()
        self.assertEqual(dialog.technician_combo.configure.call_args.kwargs['state'], 'readonly')
        self.assertEqual(dialog.technician_combo.configure.call_args.kwargs['values'],
                         ('Test Technician (ID: 12)', 'Test Technician (ID: 35)'))

    def test_missing_admin_and_already_linked_target_do_not_load_choices(self):
        for user in (None, account(), {**account('Technician'), 'technician_id': 12}):
            dialog = link_dialog()
            dialog._current = None
            with patch.object(linking, 'get_user', return_value=user), patch.object(linking, 'get_available_technicians') as choices:
                dialog._load_data()
            choices.assert_not_called()
            dialog._check_load()
            self.assertIsNone(dialog._current)
            self.assertTrue(dialog.is_open)
            dialog.save_button.state.assert_not_called()

    def test_no_available_technicians_keeps_save_disabled(self):
        dialog = link_dialog()
        dialog._results.put((dialog._current, [], None))
        dialog._check_load()
        dialog.feedback.set.assert_called_once_with(users.NO_AVAILABLE_TECHNICIANS)
        dialog.save_button.state.assert_called_once_with(['disabled'])

    def test_cancel_confirmation_does_not_start_write_and_valid_selection_uses_id(self):
        for confirm in (False, True):
            dialog = link_dialog()
            dialog.technician_combo.current.return_value = 1
            with patch.object(linking.messagebox, 'askyesno', return_value=confirm) as confirmation, \
                 patch.object(linking, 'Thread') as worker:
                dialog.save()
            self.assertEqual(confirmation.call_args.kwargs['default'], linking.messagebox.NO)
            if confirm:
                worker.assert_called_once_with(target=dialog._link, args=(35,), daemon=True)
            else:
                worker.assert_not_called()
                self.assertFalse(dialog.is_saving)

    def test_revoked_session_blocks_handlers_and_workers(self):
        dialog = link_dialog()
        dialog.manager.permissions.revoke()
        with patch.object(linking, 'Thread') as worker, patch.object(linking.messagebox, 'showinfo'), \
             patch.object(linking, 'get_user') as read, patch.object(linking, 'link_user_technician') as save:
            dialog.save()
            dialog._load_data()
            dialog._link(12)
        worker.assert_not_called()
        read.assert_not_called()
        save.assert_not_called()

    def test_success_refreshes_manager_and_errors_keep_form_open(self):
        dialog = link_dialog()
        with patch.object(linking, 'link_user_technician', return_value=True) as link, \
             patch.object(linking.messagebox, 'showinfo'):
            dialog._link(12)
            dialog._check_save()
        link.assert_called_once_with(20, 12, 7)
        self.assertFalse(dialog.is_open)
        dialog.manager._request_refresh.assert_called_once_with()
        dialog = link_dialog()
        with patch.object(linking, 'link_user_technician', side_effect=users.UserTechnicianLinkError('Already linked.')):
            dialog._link(12)
        dialog._check_save()
        self.assertTrue(dialog.is_open)
        dialog.feedback.set.assert_called_once_with('Already linked.')

    def test_close_waits_for_write_and_cancel_restores_manager_grab(self):
        dialog = link_dialog()
        dialog._saving = True
        dialog.cancel()
        dialog.window.destroy.assert_not_called()
        dialog._saving = False
        dialog._poll_id = 'pending'
        dialog.cancel()
        dialog.window.after_cancel.assert_called_once_with('pending')
        dialog.manager.window.grab_set.assert_called_once_with()


class AutomaticNoteAuthorTests(unittest.TestCase):
    def test_technician_form_has_no_author_combobox_admin_keeps_it(self):
        for role in ('Admin', 'Technician'):
            owner = window_without_widgets()
            owner.user = {**account(role), 'technician_id': 12 if role == 'Technician' else None}
            with ExitStack() as stack:
                for name in ('Toplevel', 'StringVar', 'Text'):
                    stack.enter_context(patch.object(notes_gui.tk, name))
                for name in ('Frame', 'Label', 'Scrollbar', 'Button'):
                    stack.enter_context(patch.object(notes_gui.ttk, name))
                combo = stack.enter_context(patch.object(notes_gui.ttk, 'Combobox'))
                stack.enter_context(patch.object(notes_gui, 'Thread'))
                dialog = notes_gui.AddNoteDialog(owner)
            self.assertEqual(combo.call_count, int(role == 'Admin'))
            if role == 'Technician':
                self.assertIsNone(dialog.author)

    def test_technician_loader_reads_current_link_and_does_not_load_author_choices(self):
        dialog = technician_note_dialog()
        with patch.object(notes_gui, 'get_ticket', return_value={**ticket(), 'assigned_technician_id': 12}), \
             patch.object(notes_gui, 'get_linked_active_technician', return_value=TECHNICIANS[0]) as linked, \
             patch.object(notes_gui, 'get_active_technicians') as choices:
            dialog._load_data()
        linked.assert_called_once_with(7)
        choices.assert_not_called()
        dialog._check_load()
        dialog.author_name.set.assert_called_once_with('Test Technician (ID: 12)')

    def test_missing_or_inactive_link_blocks_note_with_contact_admin_message(self):
        dialog = technician_note_dialog()
        dialog._ready = False
        with patch.object(notes_gui, 'get_ticket', return_value={**ticket(), 'assigned_technician_id': 12}), \
             patch.object(notes_gui, 'get_linked_active_technician', return_value=None):
            dialog._load_data()
        dialog._check_load()
        self.assertFalse(dialog._ready)
        dialog.feedback.set.assert_called_once_with(users.INVALID_TECHNICIAN_LINK)
        with patch.object(notes_gui, 'Thread') as worker:
            dialog.save()
        worker.assert_not_called()

    def test_technician_save_has_no_author_selection_and_repository_receives_session_user_id(self):
        dialog = technician_note_dialog()
        with patch.object(notes_gui, 'Thread') as worker:
            dialog.save()
        text = 'Checked network cable.\nConnection is stable now.'
        worker.assert_called_once_with(target=dialog._add_note, args=(None, text), daemon=True)
        with patch.object(notes_gui, 'add_ticket_comment_for_user', return_value=51) as save:
            dialog._add_note(None, text)
        save.assert_called_once_with(7, 7, text, technician_id=None, session_technician_id=12)

    def test_note_worker_and_handler_do_not_execute_after_logout(self):
        dialog = technician_note_dialog()
        dialog.owner.permissions.revoke()
        with patch.object(notes_gui, 'Thread') as worker, patch.object(notes_gui.messagebox, 'showinfo'), \
             patch.object(notes_gui, 'add_ticket_comment_for_user') as save:
            dialog.save()
            dialog._add_note(None, 'Checked cable.')
        worker.assert_not_called()
        save.assert_not_called()

    def test_invalid_tech_note_still_uses_shared_validation(self):
        for text in ('', '  ', 'ab', '!!!'):
            dialog = technician_note_dialog()
            dialog.text.get.return_value = text
            with patch.object(notes_gui, 'Thread') as worker:
                dialog.save()
            worker.assert_not_called()
            self.assertTrue(dialog.is_open)

    def test_stale_link_failure_is_friendly_and_preserves_note_text(self):
        dialog = technician_note_dialog()
        with patch.object(notes_gui, 'add_ticket_comment_for_user',
                          side_effect=notes_gui.TicketCommentCreateError(users.INVALID_TECHNICIAN_LINK)):
            dialog._add_note(None, 'Checked cable.')
        dialog._check_save()
        self.assertTrue(dialog.is_open)
        dialog.text.delete.assert_not_called()
        dialog.feedback.set.assert_called_once_with(users.INVALID_TECHNICIAN_LINK)


if __name__ == '__main__':
    unittest.main()
