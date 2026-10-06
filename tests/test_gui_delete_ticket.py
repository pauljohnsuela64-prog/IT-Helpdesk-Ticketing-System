"""GUI deletion checks using mocks; never delete a ticket in a live database."""
import unittest
from queue import Queue
from unittest.mock import MagicMock, call, patch

import mysql.connector

import gui_app as gui
import gui_delete_ticket as delete_gui
import ticket_repository as repository
from gui_permissions import SessionPermissions
from test_gui_app import ticket, viewer_without_window


def dialog_without_window():
    dialog = delete_gui.DeleteTicketDialog.__new__(delete_gui.DeleteTicketDialog)
    dialog.parent = MagicMock()
    dialog.permissions = SessionPermissions({'role': 'Admin', 'status': 'Active'})
    dialog.window = MagicMock()
    dialog.ticket_id = 7
    dialog.on_deleted = MagicMock()
    dialog.summary = MagicMock()
    dialog.feedback = MagicMock()
    dialog.delete_button = MagicMock()
    dialog.cancel_button = MagicMock()
    dialog._results = Queue()
    dialog._loading = False
    dialog._deleting = False
    dialog._closed = False
    dialog._poll_id = None
    dialog._ticket = ticket()
    return dialog


class ViewerDeletionTests(unittest.TestCase):
    def test_no_selection_or_multiple_rows_never_open_a_dialog_or_attempt_deletion(self):
        for selection in ((), ('7', '8')):
            with self.subTest(selection=selection):
                viewer = viewer_without_window()
                viewer.tree.selection.return_value = selection
                with patch.object(gui, 'DeleteTicketDialog') as dialog, \
                     patch.object(gui.messagebox, 'showinfo') as message:
                    viewer.open_delete_ticket()
                dialog.assert_not_called()
                self.assertIn('select one ticket row', message.call_args.args[1])

    def test_invalid_row_id_never_reaches_the_repository(self):
        for row_id in ('0', '-1', '2147483648', '7 OR 1=1', 'employee name'):
            with self.subTest(row_id=row_id):
                viewer = viewer_without_window()
                viewer.tree.selection.return_value = (row_id,)
                with patch.object(gui, 'DeleteTicketDialog') as dialog, \
                     patch.object(gui.messagebox, 'showinfo') as message:
                    viewer.open_delete_ticket()
                dialog.assert_not_called()
                self.assertIn('Refresh', message.call_args.args[1])

    def test_selected_id_opens_one_dialog_and_repeated_requests_focus_it(self):
        viewer = viewer_without_window()
        viewer.tree.selection.return_value = ('7',)
        with patch.object(gui, 'DeleteTicketDialog') as dialog:
            dialog.return_value.is_open = True
            viewer.open_delete_ticket()
            viewer.tree.selection.return_value = ('8',)
            viewer.open_delete_ticket()
        dialog.assert_called_once_with(viewer.root, 7, viewer._refresh_after_deletion,
                                       permissions=viewer.permissions)
        dialog.return_value.focus.assert_called_once_with()

    def test_open_create_or_update_prevents_a_concurrent_deletion_dialog(self):
        for field in ('_create_dialog', '_update_dialog'):
            with self.subTest(field=field):
                viewer = viewer_without_window()
                existing = MagicMock(is_open=True)
                setattr(viewer, field, existing)
                with patch.object(gui, 'DeleteTicketDialog') as delete:
                    viewer.open_delete_ticket()
                delete.assert_not_called()
                existing.focus.assert_called_once_with()

    def test_open_deletion_prevents_new_create_and_update_dialogs(self):
        viewer = viewer_without_window()
        existing = MagicMock(is_open=True)
        viewer._delete_dialog = existing
        with patch.object(gui, 'CreateTicketDialog') as create, patch.object(gui, 'UpdateTicketDialog') as update:
            viewer.open_create_ticket()
            viewer.open_update_ticket()
        create.assert_not_called()
        update.assert_not_called()
        self.assertEqual(existing.focus.call_count, 2)

    def test_closed_confirmation_can_be_opened_again(self):
        viewer = viewer_without_window()
        viewer.tree.selection.return_value = ('7',)
        with patch.object(gui, 'DeleteTicketDialog') as dialog:
            viewer.open_delete_ticket()
            dialog.return_value.is_open = False
            viewer.open_delete_ticket()
        self.assertEqual(dialog.call_count, 2)

    def test_success_removes_only_selected_row_immediately_and_preserves_active_search(self):
        viewer = viewer_without_window()
        viewer._active_search = 'Hardware'
        viewer.tree.exists.return_value = True
        with patch.object(gui, 'Thread') as worker:
            viewer._refresh_after_deletion(7)
        viewer.tree.delete.assert_called_once_with('7')
        self.assertEqual(viewer._active_search, 'Hardware')
        worker.assert_called_once_with(target=viewer._load_tickets, args=('Hardware',), daemon=True)

    def test_older_inflight_refresh_cannot_restore_a_deleted_ticket(self):
        viewer = viewer_without_window()
        viewer._active_search = 'Hardware'
        viewer._loading_search = 'Hardware'
        viewer._loading = True
        viewer.tree.exists.return_value = True
        viewer._refresh_after_deletion(7)
        viewer._results.put(([ticket(), ticket(8)], None))
        with patch.object(gui, 'Thread') as worker:
            viewer._check_refresh()
        self.assertEqual(worker.call_args.kwargs['args'], ('Hardware',))
        viewer.tree.insert.assert_not_called()
        viewer.tree.delete.assert_called_once_with('7')
        # A failed follow-up read still must not restore the deleted row.
        viewer._results.put((None, 'Check MySQL.'))
        viewer._check_refresh()
        viewer.tree.insert.assert_not_called()
        viewer.tree.delete.assert_called_once_with('7')
        self.assertIn('Unable to load tickets', viewer.status.set.call_args.args[0])

    def test_main_close_cancels_preview_but_waits_for_pending_deletion(self):
        for deleting in (False, True):
            with self.subTest(deleting=deleting):
                viewer = viewer_without_window()
                dialog = MagicMock(is_open=True, is_saving=deleting)
                viewer._delete_dialog = dialog
                viewer.close()
                if deleting:
                    dialog.focus.assert_called_once_with()
                    dialog.cancel.assert_not_called()
                    viewer.root.destroy.assert_not_called()
                else:
                    dialog.cancel.assert_called_once_with()
                    viewer.root.destroy.assert_called_once_with()

    def test_closed_viewer_ignores_open_and_late_refresh(self):
        viewer = viewer_without_window()
        viewer._closed = True
        with patch.object(gui, 'DeleteTicketDialog') as dialog, \
             patch.object(viewer, '_request_refresh') as refresh:
            viewer.open_delete_ticket()
            viewer._refresh_after_deletion(7)
        dialog.assert_not_called()
        refresh.assert_not_called()
        viewer.tree.delete.assert_not_called()


class ConfirmationTests(unittest.TestCase):
    def test_summary_contains_required_details_and_handles_inactive_or_missing_assignment(self):
        for assignment in ('Inactive technician', None, ''):
            with self.subTest(assignment=assignment):
                current = {**ticket(), 'assigned_to': assignment}
                summary = delete_gui.deletion_summary(current)
                for text in ('Ticket ID: 7', 'Employee Name: Alice Reyes', 'Subject: Printer issue', 'Status: Open'):
                    self.assertIn(text, summary)
                self.assertIn(f'Assigned Technician: {assignment or "Unassigned"}', summary)
                self.assertEqual(current, {**ticket(), 'assigned_to': assignment})

    def test_control_characters_cannot_hide_ticket_identifiers(self):
        summary = delete_gui.deletion_summary({**ticket(), 'subject': 'Printer\nissue\t\x00'})
        self.assertIn('Subject: Printer\\nissue\\t\\x00', summary)
        self.assertEqual(len(summary.splitlines()), 5)

    def test_dialog_warns_permanent_deletion_and_defaults_to_cancel_without_writing(self):
        with patch.object(delete_gui.tk, 'Toplevel') as window, \
             patch.object(delete_gui.tk, 'StringVar'), patch.object(delete_gui.ttk, 'Frame'), \
             patch.object(delete_gui.ttk, 'Label') as label, patch.object(delete_gui.ttk, 'Button') as button, \
             patch.object(delete_gui, 'Thread') as worker, patch.object(delete_gui, 'delete_ticket') as delete:
            dialog = delete_gui.DeleteTicketDialog(MagicMock(), 7, MagicMock(),
                                                   permissions=SessionPermissions({'role': 'Admin', 'status': 'Active'}))
        self.assertEqual([item.kwargs['text'] for item in button.call_args_list], ['Cancel', 'Permanently Delete'])
        self.assertEqual(button.call_args_list[1].kwargs['command'], dialog.confirm_delete)
        texts = ' '.join(item.kwargs.get('text', '') for item in label.call_args_list)
        self.assertIn('permanent', texts)
        self.assertIn('cannot be undone', texts)
        self.assertIn('activity history and notes', texts)
        dialog.delete_button.state.assert_called_once_with(['disabled'])
        dialog.cancel_button.focus_set.assert_called_once_with()
        window.return_value.protocol.assert_called_once_with('WM_DELETE_WINDOW', dialog.cancel)
        self.assertEqual(window.return_value.bind.call_args.args[0], '<Escape>')
        worker.assert_called_once_with(target=dialog._load_ticket, daemon=True)
        delete.assert_not_called()

    def test_cancel_and_close_before_confirmation_never_delete(self):
        dialog = dialog_without_window()
        dialog._poll_id = 'pending-read'
        with patch.object(delete_gui, 'delete_ticket') as delete, patch.object(delete_gui, 'Thread') as worker:
            dialog.cancel()
            dialog.cancel()
            dialog.confirm_delete()
        delete.assert_not_called()
        worker.assert_not_called()
        dialog.window.after_cancel.assert_called_once_with('pending-read')
        dialog.window.destroy.assert_called_once_with()
        dialog.on_deleted.assert_not_called()

    def test_loading_and_failed_preview_never_allow_confirmation(self):
        for loading, current in ((True, ticket()), (False, None)):
            with self.subTest(loading=loading):
                dialog = dialog_without_window()
                dialog._loading, dialog._ticket = loading, current
                with patch.object(delete_gui, 'Thread') as worker:
                    dialog.confirm_delete()
                worker.assert_not_called()


class PreviewTests(unittest.TestCase):
    def test_preview_worker_reads_fresh_data_by_id_without_accessing_widgets_or_deleting(self):
        dialog = dialog_without_window()
        fresh = {**ticket(), 'subject': 'Fresh subject', 'status': 'Resolved'}
        with patch.object(delete_gui, 'get_ticket', return_value=fresh) as read, \
             patch.object(delete_gui, 'delete_ticket') as delete:
            dialog._load_ticket()
        read.assert_called_once_with(7)
        delete.assert_not_called()
        self.assertEqual(dialog.window.mock_calls, [])
        self.assertEqual(dialog.summary.mock_calls, [])
        dialog._check_load()
        self.assertIn('Subject: Fresh subject', dialog.summary.set.call_args.args[0])
        self.assertIn('Status: Resolved', dialog.summary.set.call_args.args[0])
        dialog.delete_button.state.assert_called_once_with(['!disabled'])

    def test_read_errors_preserve_cancel_and_disable_deletion(self):
        for error in (delete_gui.TicketReadError('Check MySQL.'), ValueError('Invalid ticket ID'),
                      RuntimeError('private details')):
            with self.subTest(error=type(error).__name__):
                dialog = dialog_without_window()
                dialog._ticket = None
                dialog._loading = True
                with patch.object(delete_gui, 'get_ticket', side_effect=error):
                    dialog._load_ticket()
                dialog._check_load()
                self.assertTrue(dialog.is_open)
                self.assertFalse(dialog._loading)
                self.assertNotIn('private details', dialog.feedback.set.call_args.args[0])
                dialog.delete_button.state.assert_not_called()
                dialog.cancel_button.state.assert_not_called()

    def test_missing_preview_closes_and_refreshes_without_attempting_delete(self):
        dialog = dialog_without_window()
        with patch.object(delete_gui, 'get_ticket', return_value=None), \
             patch.object(delete_gui, 'delete_ticket') as delete, \
             patch.object(delete_gui.messagebox, 'showinfo') as message:
            dialog._load_ticket()
            dialog._check_load()
        delete.assert_not_called()
        self.assertFalse(dialog.is_open)
        dialog.on_deleted.assert_called_once_with(7)
        self.assertEqual(message.call_args.args[0], 'Ticket Not Found')

    def test_pending_preview_and_cancel_ignore_late_result(self):
        dialog = dialog_without_window()
        dialog._check_load()
        dialog.window.after.assert_called_once_with(100, dialog._check_load)
        dialog.cancel()
        dialog._results.put((ticket(), None))
        dialog._check_load()
        dialog.summary.set.assert_not_called()
        dialog.delete_button.state.assert_not_called()


class DeletionTests(unittest.TestCase):
    def test_explicit_confirmation_starts_one_worker_and_blocks_repeat_or_cancel(self):
        dialog = dialog_without_window()
        with patch.object(delete_gui, 'Thread') as worker:
            dialog.confirm_delete()
            dialog.confirm_delete()
            dialog.cancel()
        worker.assert_called_once_with(target=dialog._delete_ticket, daemon=True)
        self.assertTrue(dialog.is_saving)
        dialog.delete_button.state.assert_called_once_with(['disabled'])
        dialog.cancel_button.state.assert_called_once_with(['disabled'])
        dialog.window.destroy.assert_not_called()

    def test_worker_deletes_only_original_selected_id_without_using_widgets_or_preview_fields(self):
        dialog = dialog_without_window()
        dialog._ticket = {**ticket(8), 'subject': 'Changed preview text'}
        with patch.object(delete_gui, 'delete_ticket', return_value=True) as delete:
            dialog._delete_ticket()
        delete.assert_called_once_with(7)
        self.assertEqual(dialog._results.get_nowait(), (True, None))
        self.assertEqual(dialog.window.mock_calls, [])
        self.assertEqual(dialog.feedback.mock_calls, [])

    def test_success_closes_refreshes_and_shows_success_once(self):
        dialog = dialog_without_window()
        dialog._deleting = True
        dialog._results.put((True, None))
        with patch.object(delete_gui.messagebox, 'showinfo') as message:
            dialog._check_delete()
            dialog._check_delete()
        self.assertFalse(dialog.is_open)
        self.assertFalse(dialog.is_saving)
        dialog.window.destroy.assert_called_once_with()
        dialog.on_deleted.assert_called_once_with(7)
        message.assert_called_once_with('Ticket Deleted', 'Ticket #7 deleted successfully.', parent=dialog.parent)

    def test_missing_at_save_is_friendly_without_reporting_success(self):
        dialog = dialog_without_window()
        with patch.object(delete_gui, 'delete_ticket', return_value=False), \
             patch.object(delete_gui.messagebox, 'showinfo') as message:
            dialog._delete_ticket()
            dialog._check_delete()
        self.assertFalse(dialog.is_open)
        dialog.on_deleted.assert_called_once_with(7)
        self.assertEqual(message.call_args.args[0], 'Ticket Not Found')
        self.assertNotIn('successfully', message.call_args.args[1])

    def test_errors_keep_preview_and_restore_buttons_without_success_or_refresh(self):
        for error in (delete_gui.TicketDeleteError('Deletion could not be confirmed.'),
                      ValueError('Invalid ticket ID'), RuntimeError('private details')):
            with self.subTest(error=type(error).__name__):
                dialog = dialog_without_window()
                dialog._deleting = True
                with patch.object(delete_gui, 'delete_ticket', side_effect=error), \
                     patch.object(delete_gui.messagebox, 'showinfo') as message:
                    dialog._delete_ticket()
                    dialog._check_delete()
                self.assertNotIn('private details', dialog.feedback.set.call_args.args[0])
                self.assertTrue(dialog.is_open)
                self.assertFalse(dialog.is_saving)
                dialog.delete_button.state.assert_called_once_with(['!disabled'])
                dialog.cancel_button.state.assert_called_once_with(['!disabled'])
                dialog.on_deleted.assert_not_called()
                message.assert_not_called()
                self.assertEqual(dialog._ticket, ticket())

    def test_pending_delete_schedules_poll_and_closed_dialog_ignores_late_results(self):
        dialog = dialog_without_window()
        dialog._deleting = True
        dialog._check_delete()
        dialog.window.after.assert_called_once_with(100, dialog._check_delete)
        dialog._closed = True
        dialog._results.put((True, None))
        with patch.object(delete_gui.messagebox, 'showinfo') as message:
            dialog._check_delete()
        dialog.on_deleted.assert_not_called()
        message.assert_not_called()


class RepositoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = (7,)
        self.cursor.rowcount = 1

    def test_gui_uses_existing_parameterized_single_ticket_transaction_and_cascades(self):
        dialog = dialog_without_window()
        with patch.object(repository, 'get_connection', return_value=self.connection):
            dialog._delete_ticket()
        self.assertEqual(dialog._results.get_nowait(), (True, None))
        self.assertEqual(self.cursor.execute.call_args_list, [
            call('SELECT ticket_id FROM helpdesk.tickets WHERE ticket_id = %s FOR UPDATE', (7,)),
            call('DELETE FROM helpdesk.tickets WHERE ticket_id = %s LIMIT 1', (7,)),
        ])
        self.connection.commit.assert_called_once_with()
        self.connection.rollback.assert_not_called()
        # No manual deletion of notes/history, status update, or schema operation.
        self.assertEqual(self.cursor.execute.call_count, 2)

    def test_repository_failure_reaches_gui_feedback_and_rolls_back(self):
        dialog = dialog_without_window()
        dialog._deleting = True
        self.cursor.execute.side_effect = [None, mysql.connector.Error(errno=1451)]
        with patch.object(repository, 'get_connection', return_value=self.connection):
            dialog._delete_ticket()
        dialog._check_delete()
        self.assertIn('1451', dialog.feedback.set.call_args.args[0])
        self.connection.rollback.assert_called_once_with()
        self.connection.commit.assert_not_called()
        dialog.on_deleted.assert_not_called()


if __name__ == '__main__':
    unittest.main()
