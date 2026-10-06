"""GUI ownership checks and stale-dialog saves without interactive widgets."""
import unittest
from unittest.mock import MagicMock, patch

import gui_app as gui
import gui_permissions
import gui_ticket_notes as notes_gui
import gui_update_ticket as update_gui
import ticket_comment_repository as comments
import ticket_repository as tickets
from test_assigned_ticket_restrictions import assigned_ticket, technician_user
from test_gui_app import viewer_without_window
from test_gui_authentication import account
from test_gui_ticket_notes import dialog_without_widgets, window_without_widgets
from test_gui_update_ticket import dialog_without_window
from ticket_access import ADMIN_ASSIGNMENT_ONLY, NOTE_ASSIGNED_ONLY, UPDATE_ASSIGNED_ONLY
from user_repository import INVALID_TECHNICIAN_LINK


def update_dialog(link=12, session_link=12):
    dialog = dialog_without_window(assigned_ticket(link))
    dialog.user = technician_user(session_link)
    dialog.permissions = gui_permissions.SessionPermissions(dialog.user)
    return dialog


def notes_window(link=12, session_link=12):
    window = window_without_widgets()
    window.user = technician_user(session_link)
    window.permissions = gui_permissions.SessionPermissions(window.user)
    window._ticket = assigned_ticket(link)
    return window


def note_dialog(link=12, session_link=12):
    dialog = dialog_without_widgets()
    dialog.owner = notes_window(link, session_link)
    dialog._ticket = dialog.owner._ticket
    dialog._technicians = [{'technician_id': 12, 'full_name': 'Same Technician Name'}]
    dialog.author = None
    dialog.author_name = MagicMock()
    dialog._widgets = [(dialog.text, 'normal')]
    return dialog


class UpdateOwnershipTests(unittest.TestCase):
    def test_loader_allows_owned_id_and_does_not_load_assignment_choices(self):
        dialog = update_dialog()
        with patch.object(update_gui, 'get_ticket', return_value=assigned_ticket()), \
             patch.object(update_gui, 'get_linked_active_technician', return_value={'technician_id': 12}) as linked, \
             patch.object(update_gui, 'get_active_technicians') as choices:
            dialog._load_ticket()
        linked.assert_called_once_with(7)
        choices.assert_not_called()
        self.assertEqual(dialog._results.get_nowait(), ((assigned_ticket(), []), None))

    def test_other_and_unassigned_updates_have_exact_message_and_no_editable_form(self):
        for linked_id in (35, None):
            dialog = update_dialog(linked_id)
            with patch.object(update_gui, 'get_ticket', return_value=dialog._ticket), \
                 patch.object(update_gui, 'get_active_technicians') as choices, patch.object(dialog, '_build_fields') as build:
                dialog._load_ticket()
                dialog._check_load()
            self.assertEqual(dialog.feedback.set.call_args.args[0], UPDATE_ASSIGNED_ONLY)
            build.assert_not_called()
            choices.assert_not_called()
            dialog.save_button.state.assert_not_called()

    def test_missing_invalid_inactive_and_stale_link_have_contact_message(self):
        for session_link, record in ((None, None), (12, None), (12, {'technician_id': 35})):
            dialog = update_dialog(session_link=session_link)
            with patch.object(update_gui, 'get_ticket', return_value=assigned_ticket()), \
                 patch.object(update_gui, 'get_linked_active_technician', return_value=record):
                dialog._load_ticket()
            self.assertEqual(dialog._results.get_nowait(), (None, INVALID_TECHNICIAN_LINK))

    def test_technician_assignment_field_is_readonly_without_assignment_combobox(self):
        dialog = update_dialog()
        with patch.object(update_gui.tk, 'StringVar'), patch.object(update_gui.tk, 'Text'), \
             patch.object(update_gui.ttk, 'Frame'), patch.object(update_gui.ttk, 'Label'), \
             patch.object(update_gui.ttk, 'Scrollbar'), patch.object(update_gui.ttk, 'Entry') as entry, \
             patch.object(update_gui.ttk, 'Combobox') as combo:
            dialog._build_fields()
        self.assertEqual(combo.call_count, 3)  # Category, Priority and Status only.
        self.assertEqual(entry.call_args.kwargs['state'], 'readonly')
        self.assertEqual(dialog._widgets[-1][1], 'readonly')

    def test_owned_status_edit_keeps_assignment_and_uses_existing_confirmation(self):
        dialog = update_dialog()
        dialog.fields['status'].get.return_value = 'Resolved'
        self.assertEqual(dialog._validated_changes(), ({'status': 'Resolved'}, None))
        with patch.object(update_gui.messagebox, 'askyesno', return_value=True) as confirm, \
             patch.object(update_gui, 'Thread') as worker:
            dialog.save()
        confirm.assert_called_once()
        worker.assert_called_once_with(target=dialog._save_ticket, args=({'status': 'Resolved'}, None), daemon=True)
        dialog.assignee.current.assert_not_called()

    def test_save_handler_rejects_other_ticket_and_tampered_assignment(self):
        for current_id, tampered, message in ((35, False, UPDATE_ASSIGNED_ONLY),
                                              (None, False, UPDATE_ASSIGNED_ONLY),
                                              (12, True, ADMIN_ASSIGNMENT_ONLY)):
            dialog = update_dialog(current_id)
            dialog.fields['priority'].get.return_value = 'High'
            if tampered:
                dialog.fields['assigned_to'].get.return_value = 'Other Technician'
            with patch.object(update_gui.messagebox, 'askyesno') as confirm, patch.object(update_gui, 'Thread') as worker:
                dialog.save()
            confirm.assert_not_called()
            worker.assert_not_called()
            self.assertEqual(dialog.feedback.set.call_args.args[0], message)

    def test_revoked_session_cannot_open_update_load_save_or_invoke_worker(self):
        viewer = viewer_without_window()
        viewer.permissions.revoke()
        with patch.object(gui, 'UpdateTicketDialog') as form, patch.object(gui_permissions.messagebox, 'showinfo'):
            viewer.open_update_ticket()
        form.assert_not_called()
        dialog = update_dialog()
        dialog.permissions.revoke()
        with patch.object(update_gui, 'get_ticket') as read, \
             patch.object(update_gui, 'update_ticket_for_user') as write, \
             patch.object(gui_permissions.messagebox, 'showinfo'):
            dialog._load_ticket()
            dialog.save()
            dialog._save_ticket({'priority': 'High'}, None)
        read.assert_not_called()
        write.assert_not_called()

    def test_worker_always_passes_authenticated_user_and_link_to_repository(self):
        dialog = update_dialog()
        with patch.object(update_gui, 'update_ticket_for_user', return_value=True) as write:
            dialog._save_ticket({'priority': 'High'}, None)
        write.assert_called_once_with(7, 7, {'priority': 'High'}, session_technician_id=12, technician_id=None)


class NoteOwnershipTests(unittest.TestCase):
    def test_add_handler_rejects_other_unassigned_or_unlinked_before_opening_dialog(self):
        for current_id, session_id, message in ((35, 12, NOTE_ASSIGNED_ONLY),
                                                (None, 12, NOTE_ASSIGNED_ONLY),
                                                (12, None, INVALID_TECHNICIAN_LINK)):
            window = notes_window(current_id, session_id)
            with patch.object(notes_gui, 'AddNoteDialog') as form, patch.object(notes_gui.messagebox, 'showinfo') as feedback:
                window.open_add()
            form.assert_not_called()
            self.assertEqual(feedback.call_args.args[1], message)

    def test_owned_note_handler_still_opens_automatic_author_form(self):
        window = notes_window()
        with patch.object(notes_gui, 'AddNoteDialog') as form:
            window.open_add()
        form.assert_called_once_with(window)

    def test_admin_add_handler_keeps_any_ticket_and_author_selection(self):
        for link in (None, 12, 35):
            window = window_without_widgets()
            window._ticket = assigned_ticket(link)
            with patch.object(notes_gui, 'AddNoteDialog') as form:
                window.open_add()
            form.assert_called_once_with(window)

    def test_note_loader_and_save_handler_reject_other_ticket_even_if_called_directly(self):
        for link in (35, None):
            dialog = note_dialog(link)
            with patch.object(notes_gui, 'get_ticket', return_value=dialog._ticket), \
                 patch.object(notes_gui, 'get_active_technicians') as choices:
                dialog._load_data()
            self.assertEqual(dialog._results.get_nowait(), (None, NOTE_ASSIGNED_ONLY))
            choices.assert_not_called()
            with patch.object(notes_gui, 'Thread') as worker:
                dialog.save()
            worker.assert_not_called()
            self.assertEqual(dialog.feedback.set.call_args.args[0], NOTE_ASSIGNED_ONLY)

    def test_note_worker_passes_session_link_and_no_selected_author(self):
        dialog = note_dialog()
        with patch.object(notes_gui, 'add_ticket_comment_for_user', return_value=51) as write:
            dialog._add_note(None, 'Checked cable.')
        write.assert_called_once_with(7, 7, 'Checked cable.', technician_id=None, session_technician_id=12)

    def test_notes_and_history_view_handlers_remain_available_for_any_ticket(self):
        viewer = viewer_without_window()
        viewer.user = technician_user()
        viewer.permissions = gui_permissions.SessionPermissions(viewer.user)
        viewer.tree.selection.return_value = ('35',)
        with patch.object(gui, 'TicketNotesWindow') as notes, patch.object(gui, 'TicketHistoryWindow') as history:
            viewer.open_ticket_notes()
            viewer._notes_window = None
            viewer.open_ticket_history()
        self.assertEqual(notes.call_args.args[1], 35)
        history.assert_called_once_with(viewer.root, 35)


class StaleDialogTransactionTests(unittest.TestCase):
    def test_reassignment_after_loading_blocks_both_real_repository_save_paths(self):
        for kind, repository in (('update', tickets), ('note', comments)):
            with self.subTest(kind=kind):
                connection = MagicMock()
                connection.__enter__.return_value = connection
                cursor = connection.cursor.return_value.__enter__.return_value
                # Both dialogs originally loaded the user's own ID 12. The row
                # locked at save time now belongs to 35, even with the same name.
                cursor.fetchone.side_effect = [assigned_ticket(35), technician_user(), {'technician_id': 12}]
                with patch.object(repository, 'get_connection', return_value=connection):
                    if kind == 'update':
                        dialog = update_dialog()
                        dialog._save_ticket({'priority': 'High'}, None)
                        expected = UPDATE_ASSIGNED_ONLY
                    else:
                        dialog = note_dialog()
                        dialog._add_note(None, 'Checked cable.')
                        expected = NOTE_ASSIGNED_ONLY
                self.assertEqual(dialog._results.get_nowait(), (None, expected))
                self.assertTrue(all(call.args[0].startswith('SELECT ') for call in cursor.execute.call_args_list))
                connection.commit.assert_not_called()
                connection.rollback.assert_called_once()


if __name__ == '__main__':
    unittest.main()
