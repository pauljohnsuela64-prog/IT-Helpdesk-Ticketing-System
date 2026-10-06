"""GUI note workflows without an interactive window or a live database."""
import unittest
from contextlib import ExitStack
from datetime import datetime
from queue import Queue
from unittest.mock import MagicMock, call, patch

import gui_app as gui
import gui_ticket_notes as notes_gui
import ticket_comment_repository as repository
from gui_permissions import SessionPermissions
from test_gui_app import ticket, viewer_without_window


def notes():
    # Repository chronology, rather than ID order, determines the display order.
    return [dict(comment_id=51, ticket_id=7, technician_id=12,
                 technician_name='Mark Santos', created_at=datetime(2026, 10, 6, 9, 0),
                 comment_text='Checked network cable.\nConnection is stable now.'),
            dict(comment_id=8, ticket_id=7, technician_id=35,
                 technician_name='Inactive Author', created_at=datetime(2026, 10, 6, 9, 15),
                 comment_text='Restarted router.')]


def window_without_widgets():
    window = notes_gui.TicketNotesWindow.__new__(notes_gui.TicketNotesWindow)
    window.parent = MagicMock()
    window.permissions = SessionPermissions({'role': 'Admin', 'status': 'Active'})
    window.window = MagicMock()
    window.ticket_id = 7
    window.tree = MagicMock()
    window.tree.selection.return_value = ()
    window.tree.get_children.return_value = ('old-row',)
    window.tree.exists.return_value = False
    window.tree.selection_set.side_effect = lambda ident: setattr(window.tree.selection, 'return_value', (ident,))
    window.details = MagicMock()
    window.summary = MagicMock()
    window.feedback = MagicMock()
    window.add_button = MagicMock()
    window.delete_button = MagicMock()
    window.refresh_button = MagicMock()
    window._results = Queue()
    window._loading = False
    window._closed = False
    window._poll_id = None
    window._refresh_pending = False
    window._focus_other_dialog = None
    window._child_dialog = None
    window._ticket = ticket()
    window._notes = {}
    return window


def dialog_without_widgets(deletion=False):
    kind = notes_gui.DeleteNoteDialog if deletion else notes_gui.AddNoteDialog
    dialog = kind.__new__(kind)
    dialog.owner = window_without_widgets()
    dialog.owner._child_dialog = dialog
    dialog.parent = dialog.owner.window
    dialog.window = MagicMock()
    dialog.ticket_id = 7
    dialog.summary = MagicMock()
    dialog.feedback = MagicMock()
    dialog.save_button = MagicMock()
    dialog.cancel_button = MagicMock()
    dialog.text = MagicMock()
    dialog.text.get.return_value = '  Checked network cable.\nConnection is stable now.  '
    dialog._results = Queue()
    dialog._loading = False
    dialog._saving = False
    dialog._closed = False
    dialog._ready = True
    dialog._poll_id = None
    if deletion:
        dialog.comment_id = 51
        dialog._note = notes()[0]
        dialog._widgets = [(dialog.text, 'disabled')]
    else:
        dialog.author = MagicMock()
        dialog.author.current.return_value = 0
        dialog._technicians = [dict(technician_id=12, full_name='Mark Santos', status='Active')]
        dialog._widgets = [(dialog.author, 'readonly'), (dialog.text, 'normal')]
    return dialog


class MainNotesTests(unittest.TestCase):
    def test_no_single_valid_ticket_selection_never_opens_notes(self):
        for selection in ((), ('7', '8'), ('0',), ('2147483648',), ('7 OR 1=1',)):
            with self.subTest(selection=selection):
                viewer = viewer_without_window()
                viewer.tree.selection.return_value = selection
                with patch.object(gui, 'TicketNotesWindow') as window, patch.object(gui.messagebox, 'showinfo') as message:
                    viewer.open_ticket_notes()
                window.assert_not_called()
                message.assert_called_once()

    def test_same_ticket_focuses_existing_window_different_ticket_replaces_it(self):
        viewer = viewer_without_window()
        viewer.tree.selection.return_value = ('7',)
        with patch.object(gui, 'TicketNotesWindow') as window:
            window.return_value.is_open = True
            window.return_value.has_open_dialog = False
            window.return_value.ticket_id = 7
            viewer.open_ticket_notes()
            viewer.open_ticket_notes()
            window.assert_called_once_with(viewer.root, 7, viewer._focus_ticket_dialog,
                                           permissions=viewer.permissions)
            window.return_value.focus.assert_called_once_with()
            viewer.tree.selection.return_value = ('8',)
            viewer.open_ticket_notes()
            window.return_value.close.assert_called_once_with()
            self.assertEqual(window.call_args.args[:2], (viewer.root, 8))
            window.return_value.is_open = False
            viewer.open_ticket_notes()
            self.assertEqual(window.call_count, 3)

    def test_notes_child_and_existing_ticket_dialogs_do_not_replace_each_others_grabs(self):
        viewer = viewer_without_window()
        viewer._notes_window = MagicMock(is_open=True, has_open_dialog=True)
        viewer.tree.selection.return_value = ('7',)
        with patch.object(gui, 'TicketNotesWindow') as window, patch.object(gui, 'CreateTicketDialog') as create, \
             patch.object(gui, 'UpdateTicketDialog') as update, patch.object(gui, 'DeleteTicketDialog') as delete, \
             patch.object(gui, 'TechnicianManagementWindow') as manager, patch.object(gui, 'TicketHistoryWindow') as history:
            for action in (viewer.open_ticket_notes, viewer.open_create_ticket, viewer.open_update_ticket,
                           viewer.open_delete_ticket, viewer.open_technician_management, viewer.open_ticket_history):
                action()
            for factory in (window, create, update, delete, manager, history):
                factory.assert_not_called()
        self.assertEqual(viewer._notes_window.focus.call_count, 6)
        for field in ('_create_dialog', '_update_dialog', '_delete_dialog', '_technician_window'):
            with self.subTest(field=field):
                viewer = viewer_without_window()
                existing = MagicMock(is_open=True)
                setattr(viewer, field, existing)
                with patch.object(gui, 'TicketNotesWindow') as window:
                    viewer.open_ticket_notes()
                window.assert_not_called()
                existing.focus.assert_called_once_with()

    def test_modeless_notes_leave_ticket_actions_history_and_search_available(self):
        viewer = viewer_without_window()
        viewer._active_search = 'Hardware'
        viewer._notes_window = MagicMock(is_open=True, has_open_dialog=False)
        viewer.tree.selection.return_value = ('7',)
        with patch.object(gui, 'UpdateTicketDialog') as update, patch.object(gui, 'TicketHistoryWindow') as history:
            viewer.open_update_ticket()
            update.assert_called_once_with(viewer.root, 7, viewer._request_refresh)
            viewer._update_dialog.is_open = False
            viewer.open_ticket_history()
            history.assert_called_once_with(viewer.root, 7)
        self.assertEqual(viewer._active_search, 'Hardware')
        viewer._notes_window.close.assert_not_called()

    def test_root_close_waits_for_note_write_then_closes_windows(self):
        for saving in (True, False):
            with self.subTest(saving=saving):
                viewer = viewer_without_window()
                viewer._notes_window = MagicMock(is_open=True, is_saving=saving)
                viewer._history_window = MagicMock(is_open=True)
                viewer.close()
                if saving:
                    viewer._notes_window.focus.assert_called_once_with()
                    viewer.root.destroy.assert_not_called()
                    viewer._history_window.close.assert_not_called()
                else:
                    viewer._notes_window.cancel.assert_called_once_with()
                    viewer._history_window.close.assert_called_once_with()
                    viewer.root.destroy.assert_called_once_with()


class NotesReadTests(unittest.TestCase):
    def test_worker_verifies_ticket_then_reads_notes_without_accessing_widgets(self):
        window = window_without_widgets()
        with patch.object(notes_gui, 'get_ticket', return_value=ticket()) as read, \
             patch.object(notes_gui, 'get_ticket_comments', return_value=notes()) as comments:
            window._load_notes()
        read.assert_called_once_with(7)
        comments.assert_called_once_with(7)
        self.assertEqual(window._results.get_nowait(), (ticket(), notes(), None))
        for widget in (window.window, window.tree, window.details, window.summary, window.feedback):
            self.assertEqual(widget.mock_calls, [])

    def test_missing_ticket_skips_notes_and_clears_obsolete_rows_disabling_actions(self):
        window = window_without_widgets()
        with patch.object(notes_gui, 'get_ticket', return_value=None), patch.object(notes_gui, 'get_ticket_comments') as read:
            window._load_notes()
        read.assert_not_called()
        window._check_refresh()
        window.tree.delete.assert_called_once_with('old-row')
        self.assertIn('no longer exists', window.feedback.set.call_args.args[0])
        window.add_button.state.assert_called_once_with(['disabled'])
        window.delete_button.state.assert_called_once_with(['disabled'])

    def test_errors_preserve_rows_and_details_allow_retry_and_hide_unexpected_error_details(self):
        for function, error in (('get_ticket', notes_gui.TicketReadError('Check MySQL.')),
                                ('get_ticket_comments', notes_gui.TicketCommentReadError('Check notes.')),
                                ('get_ticket', RuntimeError('private details'))):
            with self.subTest(function=function):
                window = window_without_widgets()
                with patch.object(notes_gui, 'get_ticket', return_value=ticket()), \
                     patch.object(notes_gui, function, side_effect=error):
                    window._load_notes()
                window._check_refresh()
                window.tree.delete.assert_not_called()
                window.details.delete.assert_not_called()
                window.summary.set.assert_not_called()
                message = window.feedback.set.call_args.args[0]
                self.assertIn('Unable to load ticket notes', message)
                self.assertNotIn('private details', message)
                window.refresh_button.state.assert_called_once_with(['!disabled'])
                self.assertFalse(window._loading)

    def test_refresh_preserves_repository_order_inactive_author_and_selected_full_text(self):
        window = window_without_widgets()
        window.tree.selection.return_value = ('51',)
        fresh = {**ticket(), 'employee_name': 'Updated Employee', 'subject': 'Updated subject'}
        window._results.put((fresh, notes(), None))
        window._check_refresh()
        self.assertEqual([item.kwargs['iid'] for item in window.tree.insert.call_args_list], ['51', '8'])
        self.assertEqual(window.tree.insert.call_args.kwargs['values'][2], 'Inactive Author')
        self.assertIn('Updated Employee\nSubject: Updated subject', window.summary.set.call_args.args[0])
        window.tree.selection_set.assert_called_once_with('51')
        self.assertIn(notes()[0]['comment_text'], window.details.insert.call_args.args[1])
        self.assertEqual(window.details.configure.call_args_list[-1], call(state='disabled'))
        self.assertEqual(window.feedback.set.call_args.args[0], '2 notes loaded.')

    def test_empty_list_is_friendly_does_not_select_a_note(self):
        window = window_without_widgets()
        window._display_notes([])
        window.tree.selection_set.assert_not_called()
        self.assertEqual(window.feedback.set.call_args.args[0], 'No notes found for this ticket.')
        window.tree.insert.assert_not_called()

    def test_row_format_does_not_mutate_records_and_preserves_long_multiline_details(self):
        note = {**notes()[0], 'comment_text': 'Network\nchecked\tOK\x00' * 1000}
        original = note.copy()
        values = notes_gui.note_row_values(note)
        self.assertEqual(values[:3], ('51', '2026-10-06 09:00:00', 'Mark Santos'))
        self.assertEqual(values[3], 'Network\\nchecked\\tOK\\x00' * 1000)
        self.assertEqual(note, original)
        self.assertEqual(notes_gui.note_row_values({}), ('-', '-', 'Unknown technician', '-'))
        window = window_without_widgets()
        window._display_notes([note])
        window.tree.selection.return_value = ('51',)
        window._show_selected_note()
        self.assertTrue(window.details.insert.call_args.args[1].endswith('Network\nchecked\tOK\\x00' * 1000))

    def test_single_refresh_worker_and_save_during_read_discards_stale_result(self):
        window = window_without_widgets()
        with patch.object(notes_gui, 'Thread') as worker:
            window.refresh()
            window.refresh()
            self.assertEqual(worker.call_count, 1)
            window._request_refresh()
            window._results.put((ticket(), notes(), None))
            window._check_refresh()
            self.assertEqual(worker.call_count, 2)
        window.tree.insert.assert_not_called()
        self.assertFalse(window._refresh_pending)
        self.assertTrue(window._loading)

    def test_deleted_row_disappears_even_if_following_refresh_fails(self):
        window = window_without_widgets()
        window._notes = {'51': notes()[0], '8': notes()[1]}
        window.tree.exists.return_value = True
        with patch.object(window, 'refresh') as refresh:
            window._refresh_after_deletion(51)
        window.tree.delete.assert_called_once_with('51')
        self.assertEqual(list(window._notes), ['8'])
        refresh.assert_called_once_with()
        window._results.put((None, None, 'Check MySQL.'))
        window._check_refresh()
        window.tree.insert.assert_not_called()

    def test_closed_window_cancels_poll_and_ignores_late_reads_actions_and_refresh(self):
        window = window_without_widgets()
        window._poll_id = 'pending'
        window.close()
        window.close()
        window._results.put((ticket(), notes(), None))
        with patch.object(notes_gui, 'Thread') as worker, patch.object(notes_gui, 'AddNoteDialog') as add:
            window._check_refresh()
            window.refresh()
            window.open_add()
        window.window.after_cancel.assert_called_once_with('pending')
        window.window.destroy.assert_called_once_with()
        worker.assert_not_called()
        add.assert_not_called()
        window.tree.insert.assert_not_called()


class NotesActionTests(unittest.TestCase):
    def test_delete_requires_exactly_one_valid_comment_id(self):
        for selection in ((), ('51', '8'), ('0',), ('2147483648',), ('51 OR 1=1',)):
            with self.subTest(selection=selection):
                window = window_without_widgets()
                window.tree.selection.return_value = selection
                with patch.object(notes_gui, 'DeleteNoteDialog') as dialog, patch.object(notes_gui.messagebox, 'showinfo') as message:
                    window.open_delete()
                dialog.assert_not_called()
                message.assert_called_once()

    def test_add_and_delete_prevent_duplicate_forms_and_use_selected_id(self):
        window = window_without_widgets()
        window.tree.selection.return_value = ('51',)
        with patch.object(notes_gui, 'DeleteNoteDialog') as delete, patch.object(notes_gui, 'AddNoteDialog') as add:
            delete.return_value.is_open = True
            window.open_delete()
            window.open_delete()
            window.open_add()
            delete.assert_called_once_with(window, 51)
            self.assertEqual(delete.return_value.focus.call_count, 2)
            add.assert_not_called()
            delete.return_value.is_open = False
            window.open_add()
            add.assert_called_once_with(window)

    def test_existing_main_modal_or_unloaded_ticket_blocks_note_actions(self):
        for existing_dialog in (False, True):
            with self.subTest(existing_dialog=existing_dialog):
                window = window_without_widgets()
                window._focus_other_dialog = MagicMock(return_value=existing_dialog)
                if not existing_dialog:
                    window._ticket = None
                with patch.object(notes_gui, 'AddNoteDialog') as add, patch.object(notes_gui, 'DeleteNoteDialog') as delete:
                    window.open_add()
                    window.open_delete()
                add.assert_not_called()
                delete.assert_not_called()


class AddNoteTests(unittest.TestCase):
    def test_author_loader_verifies_ticket_and_uses_only_active_repository_without_widgets(self):
        dialog = dialog_without_widgets()
        with patch.object(notes_gui, 'get_ticket', return_value=ticket()) as read, \
             patch.object(notes_gui, 'get_active_technicians', return_value=dialog._technicians) as authors:
            dialog._load_data()
        read.assert_called_once_with(7)
        authors.assert_called_once_with()
        self.assertEqual(dialog._results.get_nowait(), ((ticket(), dialog._technicians), None))
        self.assertEqual(dialog.window.mock_calls, [])
        self.assertEqual(dialog.author.mock_calls, [])

    def test_no_active_authors_disables_save_and_missing_ticket_closes_without_saving(self):
        dialog = dialog_without_widgets()
        dialog._ready = False
        dialog._results.put(((ticket(), []), None))
        dialog._check_load()
        self.assertFalse(dialog._ready)
        self.assertIn('No active technicians', dialog.feedback.set.call_args.args[0])
        dialog.author.configure.assert_called_once_with(values=(), state='disabled')
        with patch.object(notes_gui, 'add_ticket_comment') as add:
            dialog.save()
        add.assert_not_called()
        dialog = dialog_without_widgets()
        with patch.object(notes_gui, 'get_ticket', return_value=None), patch.object(notes_gui, 'get_active_technicians') as authors, \
             patch.object(dialog.owner, '_request_refresh') as refresh, patch.object(notes_gui.messagebox, 'showinfo') as message:
            dialog._load_data()
            dialog._check_load()
        authors.assert_not_called()
        self.assertFalse(dialog.is_open)
        refresh.assert_called_once_with()
        self.assertIn('No note was saved', message.call_args.args[1])

    def test_duplicate_names_are_disambiguated_with_id_and_combo_is_readonly(self):
        dialog = dialog_without_widgets()
        authors = [dict(technician_id=12, full_name='Mark Santos'), dict(technician_id=35, full_name='Mark Santos')]
        dialog._results.put(((ticket(), authors), None))
        dialog._check_load()
        dialog.author.configure.assert_called_once_with(values=('Mark Santos (ID: 12)', 'Mark Santos (ID: 35)'), state='readonly')
        self.assertTrue(dialog._ready)

    def test_invalid_author_or_note_never_starts_save_and_keeps_form_open(self):
        for author, note in ((-1, 'Valid note'), (1, 'Valid note'), (0, ''), (0, ' \t\n '),
                             (0, 'ab'), (0, 'a b'), (0, '!!!'), (0, '😀😀😀'), (0, 'é' * 32768)):
            with self.subTest(author=author, note=note[:30]):
                dialog = dialog_without_widgets()
                dialog.author.current.return_value = author
                dialog.text.get.return_value = note
                with patch.object(notes_gui, 'Thread') as worker, patch.object(notes_gui, 'add_ticket_comment') as add:
                    dialog.save()
                worker.assert_not_called()
                add.assert_not_called()
                self.assertTrue(dialog.is_open)
                self.assertFalse(dialog.is_saving)
                dialog.feedback.set.assert_called_once()

    def test_valid_notes_are_trimmed_and_snapshotted_once_with_author_id(self):
        for note in ('  Checked cable.\nRestarted router.  ', '  404  ', '  PC 3  '):
            with self.subTest(note=note):
                dialog = dialog_without_widgets()
                dialog.text.get.return_value = note
                with patch.object(notes_gui, 'Thread') as worker:
                    dialog.save()
                    dialog.save()
                worker.assert_called_once_with(target=dialog._add_note, args=(12, note.strip()), daemon=True)
                self.assertTrue(dialog.is_saving)
                dialog.cancel_button.state.assert_called_once_with(['disabled'])

    def test_insert_worker_reuses_repository_and_does_not_access_widgets(self):
        dialog = dialog_without_widgets()
        with patch.object(notes_gui, 'add_ticket_comment', return_value=90) as add:
            dialog._add_note(12, 'Checked cable.')
        add.assert_called_once_with(7, 12, 'Checked cable.')
        self.assertEqual(dialog._results.get_nowait(), (90, None))
        self.assertEqual(dialog.window.mock_calls, [])
        self.assertEqual(dialog.text.mock_calls, [])
        self.assertEqual(dialog.feedback.mock_calls, [])

    def test_save_success_closes_form_refreshes_notes_and_shows_new_id(self):
        dialog = dialog_without_widgets()
        dialog._saving = True
        dialog._results.put((90, None))
        with patch.object(dialog.owner, '_request_refresh') as refresh, patch.object(notes_gui.messagebox, 'showinfo') as message:
            dialog._check_save()
        self.assertFalse(dialog.is_open)
        refresh.assert_called_once_with()
        self.assertEqual(message.call_args.args, ('Note Added', 'Note #90 added successfully to ticket #7.'))
        dialog.window.grab_release.assert_called_once_with()
        dialog.owner.window.grab_set.assert_not_called()

    def test_insert_errors_keep_text_open_restore_readonly_author_and_allow_cancel(self):
        for error in (notes_gui.TicketCommentCreateError('Technician is no longer Active.'), RuntimeError('private details')):
            with self.subTest(error=type(error).__name__):
                dialog = dialog_without_widgets()
                dialog._saving = True
                with patch.object(notes_gui, 'add_ticket_comment', side_effect=error), \
                     patch.object(dialog.owner, '_request_refresh') as refresh, patch.object(notes_gui.messagebox, 'showinfo') as message:
                    dialog._add_note(12, 'Checked cable.')
                    dialog._check_save()
                refresh.assert_not_called()
                message.assert_not_called()
                self.assertTrue(dialog.is_open)
                self.assertFalse(dialog.is_saving)
                self.assertNotIn('private details', dialog.feedback.set.call_args.args[0])
                dialog.author.configure.assert_called_once_with(state='readonly')
                dialog.text.delete.assert_not_called()
                dialog.cancel_button.state.assert_called_once_with(['!disabled'])


class DeleteNoteTests(unittest.TestCase):
    def test_preview_reloads_selected_ticket_notes_and_checks_both_ids_before_confirmation(self):
        for preview in (notes(), [{**notes()[0], 'ticket_id': 8}], []):
            with self.subTest(preview=preview):
                dialog = dialog_without_widgets(deletion=True)
                with patch.object(notes_gui, 'get_ticket_comments', return_value=preview) as read, \
                     patch.object(notes_gui, 'delete_ticket_comment') as delete, \
                     patch.object(dialog.owner, '_refresh_after_deletion') as refresh, patch.object(notes_gui.messagebox, 'showinfo'):
                    dialog._load_data()
                    dialog._check_load()
                read.assert_called_once_with(7)
                delete.assert_not_called()
                if preview == notes():
                    self.assertTrue(dialog.is_open)
                    summary = dialog.summary.set.call_args.args[0]
                    for value in ('Ticket ID: 7', 'Comment ID: 51', 'Mark Santos', '2026-10-06 09:00:00'):
                        self.assertIn(value, summary)
                    self.assertEqual(dialog.text.insert.call_args.args[1], notes()[0]['comment_text'])
                    self.assertEqual(dialog.text.configure.call_args_list[-1], call(state='disabled'))
                else:
                    self.assertFalse(dialog.is_open)
                    refresh.assert_called_once_with(51)

    def test_cancel_never_calls_deletion_and_loading_closed_or_unready_dialog_cannot_delete(self):
        with patch.object(notes_gui, 'delete_ticket_comment') as delete, patch.object(notes_gui, 'Thread') as worker:
            dialog = dialog_without_widgets(deletion=True)
            dialog.cancel()
            dialog.save()
            for field in ('_loading', '_closed', '_saving', '_ready'):
                dialog = dialog_without_widgets(deletion=True)
                setattr(dialog, field, field != '_ready')
                dialog.save()
        delete.assert_not_called()
        worker.assert_not_called()

    def test_explicit_confirmation_starts_one_delete_worker(self):
        dialog = dialog_without_widgets(deletion=True)
        with patch.object(notes_gui, 'Thread') as worker:
            dialog.save()
            dialog.save()
        worker.assert_called_once_with(target=dialog._delete_note, args=(), daemon=True)
        self.assertTrue(dialog.is_saving)
        dialog.cancel()
        self.assertTrue(dialog.is_open)

    def test_delete_calls_repository_with_both_ids_and_handles_concurrent_disappearance(self):
        for deleted in (True, False):
            with self.subTest(deleted=deleted):
                dialog = dialog_without_widgets(deletion=True)
                with patch.object(notes_gui, 'delete_ticket_comment', return_value=deleted) as delete, \
                     patch.object(dialog.owner, '_refresh_after_deletion') as refresh, patch.object(notes_gui.messagebox, 'showinfo') as message:
                    dialog._delete_note()
                    delete.assert_called_once_with(7, 51)
                    self.assertEqual(dialog.window.mock_calls, [])
                    dialog._check_save()
                refresh.assert_called_once_with(51)
                self.assertFalse(dialog.is_open)
                self.assertEqual(message.call_args.args[0], 'Note Deleted' if deleted else 'Note Not Found')

    def test_delete_error_keeps_confirmation_open_without_removing_row_or_success_message(self):
        for error in (notes_gui.TicketCommentDeleteError('Check MySQL.'), RuntimeError('private details')):
            with self.subTest(error=type(error).__name__):
                dialog = dialog_without_widgets(deletion=True)
                with patch.object(notes_gui, 'delete_ticket_comment', side_effect=error), \
                     patch.object(dialog.owner, '_refresh_after_deletion') as refresh, patch.object(notes_gui.messagebox, 'showinfo') as message:
                    dialog._delete_note()
                    dialog._check_save()
                refresh.assert_not_called()
                message.assert_not_called()
                self.assertTrue(dialog.is_open)
                self.assertNotIn('private details', dialog.feedback.set.call_args.args[0])
                dialog.text.configure.assert_called_once_with(state='disabled')


class WindowLifecycleTests(unittest.TestCase):
    def test_notes_close_cancels_unsaved_child_but_waits_for_write(self):
        for saving in (True, False):
            with self.subTest(saving=saving):
                window = window_without_widgets()
                window._child_dialog = MagicMock(is_open=True, is_saving=saving)
                window.close()
                if saving:
                    window._child_dialog.focus.assert_called_once_with()
                    window.window.destroy.assert_not_called()
                else:
                    window._child_dialog.cancel.assert_called_once_with()
                    window.window.destroy.assert_called_once_with()

    def test_closed_dialog_ignores_late_load_and_save_results(self):
        dialog = dialog_without_widgets()
        dialog._poll_id = 'pending'
        dialog.cancel()
        dialog._results.put((90, None))
        with patch.object(notes_gui.messagebox, 'showinfo') as message:
            dialog._check_load()
            dialog._check_save()
            dialog.cancel()
        dialog.window.after_cancel.assert_called_once_with('pending')
        dialog.window.destroy.assert_called_once_with()
        message.assert_not_called()
        dialog.text.delete.assert_not_called()

    def test_modeless_viewer_has_single_selection_four_columns_readonly_details_and_controls(self):
        with ExitStack() as stack:
            mocks = {name: stack.enter_context(patch.object(notes_gui.tk, name)) for name in ('Toplevel', 'StringVar', 'Text')}
            mocks.update({name: stack.enter_context(patch.object(notes_gui.ttk, name))
                          for name in ('Frame', 'Label', 'Treeview', 'Scrollbar', 'Button')})
            refresh = stack.enter_context(patch.object(notes_gui.TicketNotesWindow, 'refresh'))
            window = notes_gui.TicketNotesWindow(MagicMock(), 7,
                                                 permissions=SessionPermissions({'role': 'Admin', 'status': 'Active'}))
        self.assertEqual(mocks['Treeview'].call_args.kwargs['columns'], ('comment_id', 'created_at', 'technician_name', 'comment_text'))
        self.assertEqual(mocks['Treeview'].call_args.kwargs['selectmode'], 'browse')
        self.assertEqual({item.kwargs['text'] for item in mocks['Button'].call_args_list}, {'Add Note', 'Delete Note', 'Refresh', 'Close'})
        self.assertEqual([item.kwargs['orient'] for item in mocks['Scrollbar'].call_args_list], ['vertical', 'horizontal', 'vertical'])
        mocks['Toplevel'].return_value.grab_set.assert_not_called()
        self.assertEqual(mocks['Text'].return_value.configure.call_args_list[-1], call(state='disabled'))
        refresh.assert_called_once_with()
        self.assertTrue(window.is_open)

    def test_confirmation_is_modal_starts_disabled_defaults_to_cancel_and_has_no_enter_binding(self):
        with ExitStack() as stack:
            mocks = {name: stack.enter_context(patch.object(notes_gui.tk, name)) for name in ('Toplevel', 'StringVar', 'Text')}
            mocks.update({name: stack.enter_context(patch.object(notes_gui.ttk, name))
                          for name in ('Frame', 'Label', 'Scrollbar', 'Button')})
            worker = stack.enter_context(patch.object(notes_gui, 'Thread'))
            dialog = notes_gui.DeleteNoteDialog(window_without_widgets(), 51)
        self.assertEqual([item.kwargs['text'] for item in mocks['Button'].call_args_list], ['Cancel', 'Permanently Delete'])
        dialog.save_button.state.assert_called_once_with(['disabled'])
        dialog.cancel_button.focus_set.assert_called_once_with()
        mocks['Toplevel'].return_value.grab_set.assert_called_once_with()
        self.assertEqual(mocks['Toplevel'].return_value.bind.call_args.args[0], '<Escape>')
        worker.assert_called_once_with(target=dialog._load_data, daemon=True)
        self.assertFalse(dialog._ready)


class RepositoryIntegrationTests(unittest.TestCase):
    def test_gui_writes_only_comments_and_deletes_with_bound_ticket_and_comment_ids(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (51,)
        cursor.lastrowid = 90
        cursor.rowcount = 1
        add = dialog_without_widgets()
        delete = dialog_without_widgets(deletion=True)
        with patch.object(repository, 'get_connection', return_value=connection), \
             patch.object(repository, 'get_active_technician', return_value={'technician_id': 12}) as active:
            add._add_note(12, "  Checked 'network'; restarted router.  ")
            delete._delete_note()
        active.assert_called_once_with(12, cursor)
        statements = [item.args for item in cursor.execute.call_args_list]
        writes = [(sql, params) for sql, params in statements if not sql.startswith('SELECT ')]
        self.assertEqual(len(writes), 2)
        self.assertTrue(writes[0][0].startswith('INSERT INTO helpdesk.ticket_comments '))
        self.assertEqual(writes[0][1], (7, 12, "Checked 'network'; restarted router."))
        self.assertTrue(writes[1][0].startswith('DELETE FROM helpdesk.ticket_comments '))
        self.assertIn('WHERE ticket_id = %s AND comment_id = %s LIMIT 1', writes[1][0])
        self.assertEqual(writes[1][1], (7, 51))
        self.assertTrue(all('ticket_history' not in sql for sql, _ in statements))
        self.assertEqual(connection.commit.call_count, 2)
        self.assertEqual(add._results.get_nowait(), (90, None))
        self.assertEqual(delete._results.get_nowait(), (True, None))


if __name__ == '__main__':
    unittest.main()
