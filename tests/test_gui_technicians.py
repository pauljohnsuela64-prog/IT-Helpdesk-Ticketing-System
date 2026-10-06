"""Technician GUI checks with mocks; no interactive window or live database required."""
import unittest
from datetime import datetime
from queue import Queue
from unittest.mock import MagicMock, call, patch

import mysql.connector

import gui_app as gui
import gui_technicians as tech_gui
import gui_update_ticket as update_gui
import technician_repository as repository
import ticket_repository as tickets
from gui_permissions import SessionPermissions
from test_gui_app import viewer_without_window


def technician(status='Active', technician_id=12):
    return dict(technician_id=technician_id, full_name='Alex Reyes', email='alex@example.com',
                status=status, created_at=datetime(2026, 10, 6, 10, 30))


def manager_without_window():
    manager = tech_gui.TechnicianManagementWindow.__new__(tech_gui.TechnicianManagementWindow)
    manager.parent = MagicMock()
    manager.permissions = SessionPermissions({'role': 'Admin', 'status': 'Active'})
    manager.window = MagicMock()
    manager.tree = MagicMock()
    manager.tree.selection.return_value = ()
    manager.tree.get_children.return_value = ('old-row',)
    manager.tree.exists.return_value = False
    manager.feedback = MagicMock()
    manager.refresh_button = MagicMock()
    manager._results = Queue()
    manager._loading = False
    manager._closed = False
    manager._poll_id = None
    manager._refresh_pending = False
    manager._child_dialog = None
    manager._on_change = None
    return manager


def dialog_without_window(status_dialog=False, current=None):
    kind = tech_gui.ChangeTechnicianStatusDialog if status_dialog else tech_gui.AddTechnicianDialog
    dialog = kind.__new__(kind)
    dialog.manager = manager_without_window()
    dialog.manager._child_dialog = dialog
    dialog.parent = dialog.manager.window
    dialog.window = MagicMock()
    dialog.feedback = MagicMock()
    dialog.save_button = MagicMock()
    dialog.cancel_button = MagicMock()
    dialog._results = Queue()
    dialog._loading = False
    dialog._saving = False
    dialog._closed = False
    dialog._poll_id = None
    if status_dialog:
        dialog.technician_id = 12
        dialog._current = technician() if current is None else current
        dialog._target_status = 'Inactive'
        dialog.status = MagicMock()
        dialog.status.get.return_value = 'Inactive'
        dialog.status_combo = MagicMock()
        dialog.summary = MagicMock()
        dialog._widgets = [(dialog.status_combo, 'readonly')]
    else:
        dialog.fields = {'full_name': MagicMock(), 'email': MagicMock()}
        dialog.fields['full_name'].get.return_value = '  Alex Reyes  '
        dialog.fields['email'].get.return_value = '  alex@example.com  '
        dialog._widgets = [(MagicMock(), 'normal'), (MagicMock(), 'normal')]
    return dialog


class MainWindowTests(unittest.TestCase):
    def test_repeated_requests_focus_one_manager_and_closed_window_can_reopen(self):
        viewer = viewer_without_window()
        with patch.object(gui, 'TechnicianManagementWindow') as manager:
            manager.return_value.is_open = True
            viewer.open_technician_management()
            viewer.open_technician_management()
            manager.return_value.focus.assert_called_once_with()
            manager.assert_called_once_with(viewer.root, on_change=viewer.dashboard.refresh,
                                            permissions=viewer.permissions)
            manager.return_value.is_open = False
            viewer.open_technician_management()
        self.assertEqual(manager.call_count, 2)

    def test_modal_manager_and_ticket_dialogs_do_not_replace_each_others_grabs(self):
        viewer = viewer_without_window()
        manager = MagicMock(is_open=True)
        viewer._technician_window = manager
        with patch.object(gui, 'CreateTicketDialog') as create, patch.object(gui, 'UpdateTicketDialog') as update, \
             patch.object(gui, 'DeleteTicketDialog') as delete:
            viewer.open_create_ticket()
            viewer.open_update_ticket()
            viewer.open_delete_ticket()
        create.assert_not_called()
        update.assert_not_called()
        delete.assert_not_called()
        self.assertEqual(manager.focus.call_count, 3)
        for field in ('_create_dialog', '_update_dialog', '_delete_dialog'):
            with self.subTest(field=field):
                viewer = viewer_without_window()
                existing = MagicMock(is_open=True)
                setattr(viewer, field, existing)
                with patch.object(gui, 'TechnicianManagementWindow') as management:
                    viewer.open_technician_management()
                management.assert_not_called()
                existing.focus.assert_called_once_with()

    def test_main_close_waits_for_technician_write_then_can_close_manager(self):
        for saving in (True, False):
            with self.subTest(saving=saving):
                viewer = viewer_without_window()
                manager = MagicMock(is_open=True, is_saving=saving)
                viewer._technician_window = manager
                viewer.close()
                if saving:
                    manager.focus.assert_called_once_with()
                    manager.cancel.assert_not_called()
                    viewer.root.destroy.assert_not_called()
                else:
                    manager.cancel.assert_called_once_with()
                    viewer.root.destroy.assert_called_once_with()

    def test_closed_main_ignores_management_requests(self):
        viewer = viewer_without_window()
        viewer._closed = True
        with patch.object(gui, 'TechnicianManagementWindow') as manager:
            viewer.open_technician_management()
        manager.assert_not_called()


class TechnicianTableTests(unittest.TestCase):
    def test_rows_format_all_five_fields_including_timestamp_and_inactive_status(self):
        current = technician('Inactive')
        self.assertEqual(tech_gui.technician_row_values(current),
                         ('12', 'Alex Reyes', 'alex@example.com', 'Inactive', '2026-10-06 10:30'))
        self.assertEqual(current, technician('Inactive'))
        row = {**current, 'full_name': 'José\nReyes', 'created_at': None}
        self.assertEqual(tech_gui.technician_row_values(row)[1], 'José\\nReyes')
        self.assertEqual(tech_gui.technician_row_values(row)[4], '-')

    def test_construction_has_single_table_all_columns_scrollbars_and_only_requested_actions(self):
        with patch.object(tech_gui.tk, 'Toplevel') as window, patch.object(tech_gui.tk, 'StringVar'), \
             patch.object(tech_gui.ttk, 'Frame'), patch.object(tech_gui.ttk, 'Label'), \
             patch.object(tech_gui.ttk, 'Treeview') as tree, patch.object(tech_gui.ttk, 'Scrollbar') as scrollbar, \
             patch.object(tech_gui.ttk, 'Button') as button, \
             patch.object(tech_gui.TechnicianManagementWindow, 'refresh') as refresh:
            manager = tech_gui.TechnicianManagementWindow(MagicMock(),
                                                          permissions=SessionPermissions({'role': 'Admin', 'status': 'Active'}))
        tree.assert_called_once()
        self.assertEqual(tree.call_args.kwargs['columns'],
                         ('technician_id', 'full_name', 'email', 'status', 'created_at'))
        self.assertEqual(tree.call_args.kwargs['selectmode'], 'browse')
        self.assertEqual([item.kwargs['text'] for item in manager.tree.heading.call_args_list],
                         ['Technician ID', 'Full Name', 'Email', 'Status', 'Created At'])
        self.assertEqual({item.kwargs['text']: item.kwargs['command'] for item in button.call_args_list},
                         {'Add Technician': manager.open_add, 'Change Technician Status': manager.open_status,
                          'Refresh': refresh, 'Close': manager.close})
        self.assertEqual([item.kwargs['orient'] for item in scrollbar.call_args_list], ['vertical', 'horizontal'])
        window.return_value.title.assert_called_once_with('Technician Management')
        window.return_value.grab_set.assert_called_once_with()
        refresh.assert_called_once_with()

    def test_refresh_starts_one_background_worker_and_keeps_tk_on_main_thread(self):
        manager = manager_without_window()
        with patch.object(tech_gui, 'Thread') as worker:
            manager.refresh()
            manager.refresh()
        worker.assert_called_once_with(target=manager._load_technicians, daemon=True)
        self.assertTrue(manager._loading)
        manager.refresh_button.state.assert_called_once_with(['disabled'])
        manager.window.reset_mock()
        manager.feedback.reset_mock()
        with patch.object(tech_gui, 'get_technicians', return_value=[technician('Inactive')]) as read:
            manager._load_technicians()
        read.assert_called_once_with()
        self.assertEqual(manager._results.get_nowait(), ([technician('Inactive')], None))
        self.assertEqual(manager.window.mock_calls, [])
        self.assertEqual(manager.feedback.mock_calls, [])

    def test_refresh_shows_active_and_inactive_and_keeps_selected_row(self):
        manager = manager_without_window()
        manager.tree.selection.return_value = ('12',)
        manager.tree.exists.return_value = True
        rows = [technician(), technician('Inactive', 35)]
        manager._results.put((rows, None))
        manager._check_refresh()
        self.assertEqual(manager.tree.insert.call_count, 2)
        self.assertEqual(manager.tree.insert.call_args.kwargs['values'][3], 'Inactive')
        manager.tree.selection_set.assert_called_once_with('12')
        manager.feedback.set.assert_called_once_with('2 technicians loaded.')

    def test_empty_result_is_friendly_and_clears_old_rows(self):
        manager = manager_without_window()
        manager._results.put(([], None))
        manager._check_refresh()
        manager.tree.delete.assert_called_once_with('old-row')
        manager.tree.insert.assert_not_called()
        self.assertIn('No technicians found', manager.feedback.set.call_args.args[0])

    def test_read_errors_preserve_rows_and_allow_refresh(self):
        for error in (tech_gui.TechnicianReadError('Check MySQL.'), RuntimeError('private details')):
            with self.subTest(error=type(error).__name__):
                manager = manager_without_window()
                with patch.object(tech_gui, 'get_technicians', side_effect=error):
                    manager._load_technicians()
                manager._check_refresh()
                manager.tree.delete.assert_not_called()
                self.assertNotIn('private details', manager.feedback.set.call_args.args[0])
                manager.refresh_button.state.assert_called_once_with(['!disabled'])
                self.assertFalse(manager._loading)

    def test_save_during_load_discards_old_result_and_requests_fresh_rows(self):
        for result in (([technician()], None), (None, 'Check MySQL.')):
            with self.subTest(result=result):
                manager = manager_without_window()
                manager._loading = True
                manager._request_refresh()
                manager._results.put(result)
                with patch.object(tech_gui, 'Thread') as worker:
                    manager._check_refresh()
                worker.assert_called_once_with(target=manager._load_technicians, daemon=True)
                manager.tree.insert.assert_not_called()
                self.assertFalse(manager._refresh_pending)

    def test_pending_poll_and_close_ignore_late_reads_and_open_requests(self):
        manager = manager_without_window()
        manager._check_refresh()
        manager.window.after.assert_called_once_with(100, manager._check_refresh)
        manager.close()
        manager._results.put(([technician()], None))
        with patch.object(tech_gui, 'AddTechnicianDialog') as add:
            manager._check_refresh()
            manager.open_add()
        manager.tree.insert.assert_not_called()
        add.assert_not_called()


class ManagerActionsTests(unittest.TestCase):
    def test_missing_multiple_or_invalid_selections_never_open_status_form(self):
        for selection in ((), ('12', '35'), ('0',), ('2147483648',), ('12 OR 1=1',)):
            with self.subTest(selection=selection):
                manager = manager_without_window()
                manager.tree.selection.return_value = selection
                with patch.object(tech_gui, 'ChangeTechnicianStatusDialog') as status, \
                     patch.object(tech_gui.messagebox, 'showinfo') as message:
                    manager.open_status()
                status.assert_not_called()
                message.assert_called_once()

    def test_valid_selection_uses_actual_id_and_only_one_child_dialog_opens(self):
        manager = manager_without_window()
        manager.tree.selection.return_value = ('35',)
        with patch.object(tech_gui, 'ChangeTechnicianStatusDialog') as status, \
             patch.object(tech_gui, 'AddTechnicianDialog') as add:
            status.return_value.is_open = True
            manager.open_status()
            manager.open_status()
            manager.open_add()
        status.assert_called_once_with(manager, 35)
        add.assert_not_called()
        self.assertEqual(status.return_value.focus.call_count, 2)

    def test_add_form_reopens_after_close_and_prevents_concurrent_status_form(self):
        manager = manager_without_window()
        with patch.object(tech_gui, 'AddTechnicianDialog') as add, \
             patch.object(tech_gui, 'ChangeTechnicianStatusDialog') as status:
            add.return_value.is_open = True
            manager.open_add()
            manager.open_status()
            add.return_value.focus.assert_called_once_with()
            add.return_value.is_open = False
            manager.open_add()
        self.assertEqual(add.call_count, 2)
        status.assert_not_called()

    def test_close_cancels_unsaved_child_but_waits_for_save(self):
        for saving in (False, True):
            with self.subTest(saving=saving):
                manager = manager_without_window()
                child = MagicMock(is_open=True, is_saving=saving)
                manager._child_dialog = child
                manager.close()
                if saving:
                    child.focus.assert_called_once_with()
                    manager.window.destroy.assert_not_called()
                else:
                    child.cancel.assert_called_once_with()
                    manager.window.destroy.assert_called_once_with()


class AddTechnicianTests(unittest.TestCase):
    def test_validation_reuses_shared_rules_trims_values_and_snapshots_save(self):
        dialog = dialog_without_window()
        with patch.object(tech_gui, 'validate_text', wraps=tech_gui.validate_text) as validate, \
             patch.object(tech_gui, 'Thread') as worker:
            dialog.save()
            dialog.save()
        self.assertEqual(validate.call_count, 2)
        worker.assert_called_once_with(target=dialog._add_technician, args=('Alex Reyes', 'alex@example.com'), daemon=True)
        self.assertTrue(dialog.is_saving)
        dialog.cancel()
        dialog.window.destroy.assert_not_called()

    def test_invalid_name_and_email_never_start_save(self):
        for field, values in {'full_name': ('', '  ', '3', '!!!', 'A' * 101),
                              'email': ('', ' \t ', 'a' * 151)}.items():
            for value in values:
                with self.subTest(field=field, size=len(value)):
                    dialog = dialog_without_window()
                    dialog.fields[field].get.return_value = value
                    with patch.object(tech_gui, 'Thread') as worker:
                        dialog.save()
                    worker.assert_not_called()
                    self.assertTrue(dialog.is_open)
                    dialog.feedback.set.assert_called_once()

    def test_worker_reuses_repository_without_reading_fields_or_accessing_tk(self):
        dialog = dialog_without_window()
        with patch.object(tech_gui, 'add_technician', return_value=42) as add:
            dialog._add_technician('Alex Reyes', 'alex@example.com')
        add.assert_called_once_with('Alex Reyes', 'alex@example.com')
        self.assertEqual(dialog._results.get_nowait(), (42, None))
        self.assertEqual(dialog.window.mock_calls, [])
        for field in dialog.fields.values():
            field.get.assert_not_called()

    def test_success_shows_id_refreshes_table_once_and_restores_manager_grab(self):
        dialog = dialog_without_window()
        dialog._saving = True
        dialog._results.put((42, None))
        with patch.object(dialog.manager, '_request_refresh') as refresh, \
             patch.object(tech_gui.messagebox, 'showinfo') as message:
            dialog._check_save()
            dialog._check_save()
        refresh.assert_called_once_with()
        dialog.window.destroy.assert_called_once_with()
        dialog.manager.window.grab_set.assert_called_once_with()
        message.assert_called_once_with('Technician Added',
                                        'Technician created successfully.\nNew Technician ID: 42', parent=dialog.parent)

    def test_duplicate_and_other_errors_keep_form_values_and_restore_controls(self):
        for error in (tech_gui.TechnicianCreateError('A technician with that email already exists.'),
                      ValueError('Invalid input'), RuntimeError('private details')):
            with self.subTest(error=type(error).__name__):
                dialog = dialog_without_window()
                dialog._saving = True
                with patch.object(tech_gui, 'add_technician', side_effect=error):
                    dialog._add_technician('Alex Reyes', 'alex@example.com')
                with patch.object(dialog.manager, '_request_refresh') as refresh:
                    dialog._check_save()
                self.assertTrue(dialog.is_open)
                self.assertFalse(dialog.is_saving)
                self.assertNotIn('private details', dialog.feedback.set.call_args.args[0])
                self.assertEqual(dialog.fields['email'].get(), '  alex@example.com  ')
                dialog.save_button.state.assert_called_once_with(['!disabled'])
                refresh.assert_not_called()

    def test_cancel_without_save_closes_and_never_refreshes_or_writes(self):
        dialog = dialog_without_window()
        with patch.object(tech_gui, 'Thread') as worker, patch.object(dialog.manager, '_request_refresh') as refresh:
            dialog.cancel()
            dialog.save()
        worker.assert_not_called()
        refresh.assert_not_called()
        dialog.manager.window.grab_set.assert_called_once_with()

    def test_construction_contains_only_name_and_email_and_generated_fields_are_not_editable(self):
        with patch.object(tech_gui.tk, 'Toplevel') as window, patch.object(tech_gui.tk, 'StringVar'), \
             patch.object(tech_gui.ttk, 'Frame'), patch.object(tech_gui.ttk, 'Label'), \
             patch.object(tech_gui.ttk, 'Entry') as entry, patch.object(tech_gui.ttk, 'Button'), \
             patch.object(tech_gui, 'Thread') as worker:
            dialog = tech_gui.AddTechnicianDialog(manager_without_window())
        self.assertEqual(set(dialog.fields), {'full_name', 'email'})
        self.assertEqual(entry.call_count, 2)
        worker.assert_not_called()
        window.return_value.protocol.assert_called_once_with('WM_DELETE_WINDOW', dialog.cancel)
        self.assertEqual(window.return_value.bind.call_args.args[0], '<Escape>')


class StatusChangeTests(unittest.TestCase):
    def test_loads_fresh_details_by_id_without_using_table_text(self):
        dialog = dialog_without_window(True)
        fresh = technician('Inactive')
        with patch.object(tech_gui, 'get_technician', return_value=fresh) as read:
            dialog._load_technician()
        read.assert_called_once_with(12)
        self.assertEqual(dialog.window.mock_calls, [])
        dialog._check_load()
        self.assertEqual(dialog._current, fresh)
        dialog.status.set.assert_called_once_with('Inactive')
        dialog.status_combo.configure.assert_called_once_with(state='readonly')
        self.assertIn('Current Status: Inactive', dialog.summary.set.call_args.args[0])

    def test_both_transitions_require_confirmation_and_snapshot_the_status(self):
        for old, new in (('Active', 'Inactive'), ('Inactive', 'Active')):
            with self.subTest(old=old):
                dialog = dialog_without_window(True, technician(old))
                dialog.status.get.return_value = new
                with patch.object(tech_gui.messagebox, 'askyesno', return_value=True) as confirm, \
                     patch.object(tech_gui, 'Thread') as worker:
                    dialog.save()
                    dialog.save()
                confirm.assert_called_once()
                self.assertIn(f'{old} -> {new}', confirm.call_args.args[1])
                self.assertEqual(confirm.call_args.kwargs['default'], tech_gui.messagebox.NO)
                worker.assert_called_once_with(target=dialog._change_status, args=(new,), daemon=True)
                self.assertEqual(dialog._target_status, new)
                self.assertTrue(dialog.is_saving)

    def test_declining_confirmation_keeps_dialog_without_write(self):
        dialog = dialog_without_window(True)
        with patch.object(tech_gui.messagebox, 'askyesno', return_value=False), patch.object(tech_gui, 'Thread') as worker:
            dialog.save()
        worker.assert_not_called()
        self.assertTrue(dialog.is_open)
        self.assertIn('cancelled', dialog.feedback.set.call_args.args[0])

    def test_invalid_or_unchanged_status_and_incomplete_load_do_not_confirm(self):
        for value in ('Pending', 'active', 'Inactive; DELETE', '', 'Active'):
            with self.subTest(value=value):
                dialog = dialog_without_window(True)
                dialog.status.get.return_value = value
                with patch.object(tech_gui.messagebox, 'askyesno') as confirm, patch.object(tech_gui, 'Thread') as worker:
                    dialog.save()
                confirm.assert_not_called()
                worker.assert_not_called()
        dialog = dialog_without_window(True)
        dialog._loading = True
        with patch.object(tech_gui.messagebox, 'askyesno') as confirm:
            dialog.save()
        confirm.assert_not_called()

    def test_worker_uses_existing_repository_id_and_status_without_reading_widgets(self):
        dialog = dialog_without_window(True)
        with patch.object(tech_gui, 'update_technician_status', return_value=True) as save:
            dialog._change_status('Inactive')
        save.assert_called_once_with(12, 'Inactive')
        dialog.status.get.assert_not_called()
        self.assertEqual(dialog.window.mock_calls, [])
        self.assertEqual(dialog._results.get_nowait(), (True, None))

    def test_success_and_repository_no_op_close_and_refresh_with_accurate_feedback(self):
        for saved in (True, False):
            with self.subTest(saved=saved):
                dialog = dialog_without_window(True)
                dialog._results.put((saved, None))
                with patch.object(dialog.manager, '_request_refresh') as refresh, \
                     patch.object(tech_gui.messagebox, 'showinfo') as message:
                    dialog._check_save()
                self.assertFalse(dialog.is_open)
                refresh.assert_called_once_with()
                self.assertEqual(message.call_args.args[0], 'Technician Status Changed' if saved else 'No Changes')

    def test_read_errors_and_missing_technicians_produce_friendly_feedback(self):
        dialog = dialog_without_window(True)
        dialog._current = None
        with patch.object(tech_gui, 'get_technician', side_effect=tech_gui.TechnicianReadError('Check MySQL.')):
            dialog._load_technician()
        dialog._check_load()
        dialog.save_button.state.assert_not_called()
        self.assertTrue(dialog.is_open)
        self.assertEqual(dialog.feedback.set.call_args.args[0], 'Check MySQL.')
        dialog._results.put((None, None))
        with patch.object(tech_gui.messagebox, 'showinfo') as message, \
             patch.object(dialog.manager, '_request_refresh') as refresh:
            dialog._check_load()
        self.assertFalse(dialog.is_open)
        refresh.assert_called_once_with()
        self.assertEqual(message.call_args.args[0], 'Technician Not Found')

    def test_save_failure_restores_readonly_status_and_keeps_selected_value(self):
        dialog = dialog_without_window(True)
        dialog._saving = True
        with patch.object(tech_gui, 'update_technician_status', side_effect=tech_gui.TechnicianUpdateError('Check MySQL.')):
            dialog._change_status('Inactive')
        dialog._check_save()
        self.assertTrue(dialog.is_open)
        dialog.status_combo.configure.assert_called_once_with(state='readonly')
        self.assertEqual(dialog.status.get(), 'Inactive')
        self.assertEqual(dialog.feedback.set.call_args.args[0], 'Check MySQL.')

    def test_pending_load_save_and_closed_dialog_ignore_late_results(self):
        dialog = dialog_without_window(True)
        dialog._check_load()
        dialog.window.after.assert_called_once_with(100, dialog._check_load)
        dialog.window.reset_mock()
        dialog._check_save()
        dialog.window.after.assert_called_once_with(100, dialog._check_save)
        dialog._closed = True
        dialog._results.put((technician(), None))
        dialog._check_load()
        dialog._check_save()
        dialog.summary.set.assert_not_called()
        dialog.window.destroy.assert_not_called()

    def test_constructor_has_only_active_inactive_choices_and_loads_before_editing(self):
        with patch.object(tech_gui.tk, 'Toplevel'), patch.object(tech_gui.tk, 'StringVar'), \
             patch.object(tech_gui.ttk, 'Frame'), patch.object(tech_gui.ttk, 'Label'), \
             patch.object(tech_gui.ttk, 'Combobox') as combo, patch.object(tech_gui.ttk, 'Button'), \
             patch.object(tech_gui, 'Thread') as worker:
            dialog = tech_gui.ChangeTechnicianStatusDialog(manager_without_window(), 35)
        self.assertEqual(combo.call_args.kwargs['values'], ('Active', 'Inactive'))
        self.assertEqual(combo.call_args.kwargs['state'], 'disabled')
        worker.assert_called_once_with(target=dialog._load_technician, daemon=True)
        self.assertTrue(dialog._loading)


class RepositoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value

    def test_gui_add_uses_parameterized_insert_and_database_generated_fields(self):
        self.cursor.lastrowid = 42
        dialog = dialog_without_window()
        with patch.object(repository, 'get_connection', return_value=self.connection):
            dialog._add_technician("  Alex O'Brien  ", '  alex@example.com  ')
        self.cursor.execute.assert_called_once_with(
            'INSERT INTO helpdesk.technicians (full_name, email) VALUES (%s, %s)',
            ("Alex O'Brien", 'alex@example.com'),
        )
        self.assertEqual(dialog._results.get_nowait(), (42, None))
        self.connection.commit.assert_called_once_with()

    def test_real_duplicate_error_reaches_gui_and_rolls_back(self):
        self.cursor.execute.side_effect = mysql.connector.Error(errno=1062)
        dialog = dialog_without_window()
        with patch.object(repository, 'get_connection', return_value=self.connection):
            dialog._add_technician('Alex Reyes', 'alex@example.com')
        dialog._check_save()
        self.assertIn('email already exists', dialog.feedback.set.call_args.args[0])
        self.assertTrue(dialog.is_open)
        self.connection.rollback.assert_called_once_with()
        self.connection.commit.assert_not_called()

    def test_status_write_changes_only_technician_status_with_existing_transaction(self):
        self.cursor.fetchone.return_value = technician()
        dialog = dialog_without_window(True)
        with patch.object(repository, 'get_connection', return_value=self.connection):
            dialog._change_status('Inactive')
        self.assertEqual(dialog._results.get_nowait(), (True, None))
        self.assertEqual(self.cursor.execute.call_args_list, [
            call('SELECT technician_id, status FROM helpdesk.technicians WHERE technician_id = %s FOR UPDATE', (12,)),
            call('UPDATE helpdesk.technicians SET status = %s WHERE technician_id = %s', ('Inactive', 12)),
        ])
        self.connection.commit.assert_called_once_with()

    def test_status_changes_refresh_active_ticket_choices_and_preserve_existing_ticket_name(self):
        stored = technician()
        assigned_ticket = dict(ticket_id=7, employee_name='Alice Reyes', department='IT', category='Hardware',
                               subject='PC 3', description='Checked cable.', priority='Medium', status='Assigned',
                               assigned_to=stored['full_name'], resolved_at=None)
        def execute(query, parameters=None):
            if query.startswith('UPDATE helpdesk.technicians SET status'):
                stored['status'] = parameters[0]
            elif query.startswith('SELECT ') and 'FROM helpdesk.technicians' in query:
                rows = [] if 'WHERE status = %s' in query and stored['status'] != 'Active' else [stored.copy()]
                self.cursor.fetchall.return_value = rows
                self.cursor.fetchone.return_value = rows[0] if rows else None
            elif query.startswith('SELECT ') and 'FROM helpdesk.tickets' in query:
                self.cursor.fetchall.return_value = [assigned_ticket.copy()]
            else:
                self.fail(f'Unexpected SQL: {query}')
        self.cursor.execute.side_effect = execute
        with patch.object(repository, 'get_connection', return_value=self.connection), \
             patch.object(tickets, 'get_connection', return_value=self.connection):
            for new_status, expected_choices in (('Inactive', []), ('Active', [technician()])):
                change = dialog_without_window(True)
                change._change_status(new_status)
                self.assertEqual(change._results.get_nowait(), (True, None))
                self.assertEqual(repository.get_technicians(), [technician(new_status)])
                update = update_gui.UpdateTicketDialog.__new__(update_gui.UpdateTicketDialog)
                update.ticket_id = 7
                update._results = Queue()
                update._load_ticket()
                (current, choices), error = update._results.get_nowait()
                self.assertIsNone(error)
                self.assertEqual(choices, expected_choices)
                self.assertEqual(current['assigned_to'], 'Alex Reyes')
        writes = [item.args[0] for item in self.cursor.execute.call_args_list
                  if not item.args[0].startswith('SELECT ')]
        self.assertEqual(writes, ['UPDATE helpdesk.technicians SET status = %s WHERE technician_id = %s'] * 2)


if __name__ == '__main__':
    unittest.main()
