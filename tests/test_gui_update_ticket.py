"""GUI update checks with mocked widgets/connections; no live database writes."""
import unittest
from queue import Queue
from unittest.mock import MagicMock, patch

import gui_app as gui
import gui_update_ticket as update_gui
import ticket_repository as repository
from test_gui_app import viewer_without_window
from test_gui_authentication import account
from gui_permissions import SessionPermissions


TECHNICIANS = [
    dict(technician_id=12, full_name='Mark Santos', status='Active'),
    dict(technician_id=35, full_name='Anna Reyes', status='Active'),
]


def ticket(status='Open', assigned_to=None):
    return dict(ticket_id=7, employee_name='Alice Reyes', department='IT', category='Hardware',
                subject='Printer issue', description='Paper jam', priority='Medium', status=status,
                assigned_to=assigned_to, created_at='created time', updated_at='updated time',
                resolved_at='resolved time' if status == 'Resolved' else None)


def dialog_without_window(current=None):
    dialog = update_gui.UpdateTicketDialog.__new__(update_gui.UpdateTicketDialog)
    dialog.parent = MagicMock()
    dialog.user = account()
    dialog.permissions = SessionPermissions(dialog.user)
    dialog.window = MagicMock()
    dialog.form = MagicMock()
    dialog.ticket_id = 7
    dialog.on_updated = MagicMock()
    dialog._ticket = ticket() if current is None else current
    dialog._technicians = TECHNICIANS
    dialog.fields = {}
    for field in ('employee_name', 'department', 'category', 'subject', 'priority', 'status', 'assigned_to'):
        variable = MagicMock()
        variable.get.return_value = dialog._ticket[field] or ''
        dialog.fields[field] = variable
    dialog.description = MagicMock()
    dialog.description.get.return_value = dialog._ticket['description']
    dialog.assignee = MagicMock()
    dialog.assignee.current.return_value = 0
    dialog.feedback = MagicMock()
    dialog.summary = MagicMock()
    dialog.employee_entry = MagicMock()
    dialog.save_button = MagicMock()
    dialog.cancel_button = MagicMock()
    dialog._widgets = [(dialog.description, 'normal'), (dialog.assignee, 'readonly')]
    dialog._results = Queue()
    dialog._loading = False
    dialog._saving = False
    dialog._closed = False
    dialog._poll_id = None
    return dialog


class ViewerUpdateTests(unittest.TestCase):
    def test_no_selection_shows_friendly_message_without_opening_or_reading(self):
        viewer = viewer_without_window()
        with patch.object(gui, 'UpdateTicketDialog') as dialog, \
             patch.object(gui.messagebox, 'showinfo') as message:
            viewer.open_update_ticket()
        dialog.assert_not_called()
        self.assertIn('select a ticket row', message.call_args.args[1])

    def test_selection_opens_one_dialog_with_repository_id_and_refresh_callback(self):
        viewer = viewer_without_window()
        viewer.tree.selection.return_value = ('7',)
        with patch.object(gui, 'UpdateTicketDialog') as dialog:
            dialog.return_value.is_open = True
            viewer.open_update_ticket()
            viewer.open_update_ticket()
        dialog.assert_called_once_with(viewer.root, 7, viewer._request_refresh,
                                       permissions=viewer.permissions, user=viewer.user)
        dialog.return_value.focus.assert_called_once_with()

    def test_update_success_keeps_filter_and_queues_refresh_if_loading(self):
        viewer = viewer_without_window()
        viewer._active_search = 'Hardware'
        viewer._loading = True
        viewer.tree.selection.return_value = ('7',)
        with patch.object(gui, 'UpdateTicketDialog') as dialog:
            viewer.open_update_ticket()
            dialog.call_args.args[2]()
        self.assertTrue(viewer._refresh_pending)
        viewer._results.put(([], None))
        with patch.object(gui, 'Thread') as worker:
            viewer._check_refresh()
        self.assertEqual(worker.call_args.kwargs['args'], ('Hardware',))
        viewer.tree.delete.assert_not_called()

    def test_main_close_cancels_unsaved_update_but_waits_for_pending_save(self):
        for saving in (False, True):
            with self.subTest(saving=saving):
                viewer = viewer_without_window()
                dialog = MagicMock(is_open=True, is_saving=saving)
                viewer._update_dialog = dialog
                viewer.close()
                if saving:
                    dialog.focus.assert_called_once_with()
                    dialog.cancel.assert_not_called()
                    viewer.root.destroy.assert_not_called()
                else:
                    dialog.cancel.assert_called_once_with()
                    viewer.root.destroy.assert_called_once_with()

    def test_create_and_update_reuse_the_existing_open_form(self):
        viewer = viewer_without_window()
        dialog = MagicMock(is_open=True)
        viewer._update_dialog = dialog
        with patch.object(gui, 'CreateTicketDialog') as create:
            viewer.open_create_ticket()
        create.assert_not_called()
        dialog.focus.assert_called_once_with()
        viewer._update_dialog = None
        viewer._create_dialog = dialog
        with patch.object(gui, 'UpdateTicketDialog') as update:
            viewer.open_update_ticket()
        update.assert_not_called()

    def test_closed_viewer_does_not_open_a_dialog(self):
        viewer = viewer_without_window()
        viewer._closed = True
        with patch.object(gui, 'UpdateTicketDialog') as dialog:
            viewer.open_update_ticket()
        dialog.assert_not_called()


class LoadTests(unittest.TestCase):
    def test_worker_reads_full_ticket_and_only_active_technicians_without_touching_widgets(self):
        dialog = dialog_without_window()
        with patch.object(update_gui, 'get_ticket', return_value=ticket()) as read, \
             patch.object(update_gui, 'get_active_technicians', return_value=TECHNICIANS) as active:
            dialog._load_ticket()
        read.assert_called_once_with(7)
        active.assert_called_once_with()
        self.assertEqual(dialog._results.get_nowait(), ((ticket(), TECHNICIANS), None))
        self.assertEqual(dialog.window.mock_calls, [])
        self.assertEqual(dialog.feedback.mock_calls, [])

    def test_missing_ticket_does_not_load_technicians(self):
        dialog = dialog_without_window()
        with patch.object(update_gui, 'get_ticket', return_value=None), \
             patch.object(update_gui, 'get_active_technicians') as active:
            dialog._load_ticket()
        active.assert_not_called()
        self.assertIn('No ticket found', dialog._results.get_nowait()[1])

    def test_read_errors_keep_save_disabled_and_show_friendly_feedback(self):
        for function, error in (
            ('get_ticket', update_gui.TicketReadError('Check MySQL.')),
            ('get_active_technicians', update_gui.TechnicianReadError('Check technicians.')),
            ('get_ticket', RuntimeError('private details')),
        ):
            with self.subTest(function=function, error=type(error).__name__):
                dialog = dialog_without_window()
                dialog._loading = True
                dialog._ticket = None
                with patch.object(update_gui, 'get_ticket', return_value=ticket()), \
                     patch.object(update_gui, function, side_effect=error):
                    dialog._load_ticket()
                dialog._check_load()
                self.assertNotIn('private details', dialog.feedback.set.call_args.args[0])
                dialog.save_button.state.assert_not_called()
                with patch.object(update_gui, 'Thread') as worker:
                    dialog.save()
                worker.assert_not_called()

    def test_no_active_technicians_still_loads_ticket_for_keep_unassign_and_other_edits(self):
        dialog = dialog_without_window(ticket(assigned_to='Inactive technician'))
        dialog._results.put(((dialog._ticket, []), None))
        with patch.object(dialog, '_build_fields'):
            dialog._check_load()
        self.assertIn('No active technicians', dialog.feedback.set.call_args.args[0])
        self.assertIn('created time', dialog.summary.set.call_args.args[0])
        dialog.save_button.state.assert_called_once_with(['!disabled'])
        self.assertEqual(dialog._validated_changes(), ({}, None))
        dialog.assignee.current.return_value = 1
        self.assertEqual(dialog._validated_changes(), ({'assigned_to': None}, None))

    def test_pending_load_and_cancel_ignore_late_results(self):
        dialog = dialog_without_window()
        dialog._check_load()
        dialog.window.after.assert_called_once_with(100, dialog._check_load)
        dialog.cancel()
        dialog._results.put(((ticket(), TECHNICIANS), None))
        with patch.object(dialog, '_build_fields') as build:
            dialog._check_load()
        build.assert_not_called()
        dialog.window.destroy.assert_called_once_with()

    def test_constructor_loads_in_background_and_prefills_readonly_choices(self):
        current = ticket('Resolved', 'Inactive technician')
        duplicates = [dict(technician_id=12, full_name='Same Name'),
                      dict(technician_id=35, full_name='Same Name')]
        with patch.object(update_gui.tk, 'Toplevel'), patch.object(update_gui.tk, 'StringVar') as variable, \
             patch.object(update_gui.tk, 'Text') as text, patch.object(update_gui.ttk, 'Frame'), \
             patch.object(update_gui.ttk, 'Label'), patch.object(update_gui.ttk, 'Entry'), \
             patch.object(update_gui.ttk, 'Scrollbar'), patch.object(update_gui.ttk, 'Button'), \
             patch.object(update_gui.ttk, 'Combobox') as combo, patch.object(update_gui, 'Thread') as worker:
            dialog = update_gui.UpdateTicketDialog(MagicMock(), 7, MagicMock(), user=account())
            worker.assert_called_once_with(target=dialog._load_ticket, daemon=True)
            self.assertTrue(dialog._loading)
            dialog._results.put(((current, duplicates), None))
            dialog._check_load()
        self.assertEqual([item.kwargs['values'] for item in combo.call_args_list[:3]],
                         [update_gui.CATEGORIES, update_gui.PRIORITIES, update_gui.STATUSES])
        self.assertTrue(all(item.kwargs['state'] == 'readonly' for item in combo.call_args_list))
        assignment = combo.call_args_list[3].kwargs['values']
        self.assertEqual(assignment, ('Keep current: Inactive technician', 'Unassign technician',
                                      'Same Name (ID: 12)', 'Same Name (ID: 35)'))
        self.assertEqual(set(dialog.fields), {'employee_name', 'department', 'category', 'subject',
                                             'priority', 'status', 'assigned_to'})
        self.assertIn('Resolved', [item.kwargs.get('value') for item in variable.call_args_list])
        text.return_value.insert.assert_called_once_with('1.0', 'Paper jam')
        self.assertEqual(text.call_args.kwargs['wrap'], 'word')
        self.assertFalse(dialog._loading)


class ValidationAndConfirmationTests(unittest.TestCase):
    def test_shared_validation_trims_changes_and_keeps_current_assignment(self):
        dialog = dialog_without_window(ticket(assigned_to='Inactive technician'))
        dialog.fields['employee_name'].get.return_value = '  Bob Reyes  '
        dialog.fields['subject'].get.return_value = '  PC 3  '
        dialog.description.get.return_value = '  Error 404\nRestarted PC.  '
        with patch.object(update_gui, 'validate_text', wraps=update_gui.validate_text) as validate:
            changes, technician_id = dialog._validated_changes()
        self.assertEqual(changes, dict(employee_name='Bob Reyes', subject='PC 3',
                                       description='Error 404\nRestarted PC.'))
        self.assertIsNone(technician_id)
        self.assertEqual(validate.call_count, 4)

    def test_invalid_inputs_and_choices_do_not_confirm_or_start_save(self):
        invalid = {
            'employee_name': ('', '   ', '3', '!!!', 'A' * 101),
            'department': ('', '   ', '123', '???', 'A' * 101),
            'subject': ('', '  ', 'ab', ' ab ', 'A' * 151),
            'description': ('', ' \n\t ', '1234', 'é' * 32768),
            'category': ('', 'Invalid'), 'priority': ('Urgent',), 'status': ('Done',),
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, size=len(value)):
                    dialog = dialog_without_window()
                    if field == 'description':
                        dialog.description.get.return_value = value
                    else:
                        dialog.fields[field].get.return_value = value
                    with patch.object(update_gui.messagebox, 'askyesno') as confirm, \
                         patch.object(update_gui, 'Thread') as worker:
                        dialog.save()
                    confirm.assert_not_called()
                    worker.assert_not_called()
                    self.assertTrue(dialog.is_open)
                    dialog.feedback.set.assert_called_once()

    def test_technician_selection_uses_id_and_rejects_invalid_selection(self):
        dialog = dialog_without_window()
        dialog.assignee.current.return_value = 3
        self.assertEqual(dialog._validated_changes(), ({}, 35))
        for selection in (-1, 4):
            dialog.assignee.current.return_value = selection
            with self.assertRaises(ValueError):
                dialog._validated_changes()

    def test_unchanged_save_does_not_confirm_or_log_history(self):
        dialog = dialog_without_window()
        with patch.object(update_gui.messagebox, 'askyesno') as confirm, \
             patch.object(update_gui, 'Thread') as worker:
            dialog.save()
        confirm.assert_not_called()
        worker.assert_not_called()
        dialog.feedback.set.assert_called_once_with('No changes made.')

    def test_confirmation_decline_keeps_dialog_and_values_without_saving(self):
        dialog = dialog_without_window()
        dialog.fields['subject'].get.return_value = 'Changed subject'
        with patch.object(update_gui.messagebox, 'askyesno', return_value=False) as confirm, \
             patch.object(update_gui, 'Thread') as worker:
            dialog.save()
        self.assertEqual(confirm.call_args.kwargs['default'], update_gui.messagebox.NO)
        worker.assert_not_called()
        self.assertTrue(dialog.is_open)
        self.assertEqual(dialog.fields['subject'].get(), 'Changed subject')
        self.assertIn('cancelled', dialog.feedback.set.call_args.args[0])

    def test_assignment_confirmation_matches_existing_status_rules(self):
        for status in update_gui.STATUSES:
            dialog = dialog_without_window(ticket(status))
            text = dialog._confirmation_text({}, 35)
            self.assertEqual('Open to Assigned' in text, status == 'Open')
        dialog = dialog_without_window()
        self.assertNotIn('Open to Assigned', dialog._confirmation_text({'status': 'In Progress'}, 35))
        self.assertIn('will be set', dialog._confirmation_text({'status': 'Resolved'}, None))
        dialog = dialog_without_window(ticket('Resolved'))
        self.assertIn('will be cleared', dialog._confirmation_text({'status': 'Open'}, None))


class SaveTests(unittest.TestCase):
    def test_confirmed_save_snapshots_changes_and_blocks_duplicate_or_cancel(self):
        dialog = dialog_without_window()
        dialog.fields['subject'].get.return_value = 'Changed subject'
        with patch.object(update_gui.messagebox, 'askyesno', return_value=True) as confirm, \
             patch.object(update_gui, 'Thread') as worker:
            dialog.save()
            dialog.save()
            dialog.cancel()
        confirm.assert_called_once()
        worker.assert_called_once_with(target=dialog._save_ticket,
                                        args=({'subject': 'Changed subject'}, None), daemon=True)
        self.assertTrue(dialog.is_saving)
        dialog.save_button.state.assert_called_once_with(['disabled'])
        dialog.cancel_button.state.assert_called_once_with(['disabled'])
        dialog.window.destroy.assert_not_called()

    def test_worker_reuses_repository_without_reading_or_updating_widgets(self):
        dialog = dialog_without_window()
        with patch.object(update_gui, 'update_ticket_for_user', return_value=True) as update:
            dialog._save_ticket({'priority': 'High'}, 35)
        update.assert_called_once_with(7, 7, {'priority': 'High'}, session_technician_id=None, technician_id=35)
        self.assertEqual(dialog._results.get_nowait(), (True, None))
        self.assertEqual(dialog.window.mock_calls, [])
        self.assertEqual(dialog.assignee.mock_calls, [])
        for variable in dialog.fields.values():
            variable.get.assert_not_called()

    def test_save_errors_keep_form_and_values_and_restore_readonly_controls(self):
        for error in (update_gui.TicketUpdateError('No ticket found with that ID.'),
                      update_gui.TicketUpdateError('The selected technician is no longer active.'),
                      ValueError('Invalid input'), RuntimeError('private details')):
            with self.subTest(error=str(error)):
                dialog = dialog_without_window()
                dialog._saving = True
                with patch.object(update_gui, 'update_ticket_for_user', side_effect=error):
                    dialog._save_ticket({'priority': 'High'}, 35)
                dialog._check_save()
                self.assertNotIn('private details', dialog.feedback.set.call_args.args[0])
                self.assertTrue(dialog.is_open)
                self.assertFalse(dialog.is_saving)
                self.assertEqual(dialog.fields['subject'].get(), 'Printer issue')
                dialog.assignee.configure.assert_called_once_with(state='readonly')
                dialog.save_button.state.assert_called_once_with(['!disabled'])
                dialog.on_updated.assert_not_called()

    def test_success_closes_once_refreshes_parent_and_shows_success(self):
        dialog = dialog_without_window()
        dialog._saving = True
        dialog._results.put((True, None))
        with patch.object(update_gui.messagebox, 'showinfo') as success:
            dialog._check_save()
            dialog._check_save()
        dialog.window.destroy.assert_called_once_with()
        dialog.on_updated.assert_called_once_with()
        success.assert_called_once_with('Ticket Updated', 'Ticket #7 updated successfully.', parent=dialog.parent)

    def test_repository_no_op_stays_open_without_refresh_or_success(self):
        dialog = dialog_without_window()
        dialog._saving = True
        dialog._results.put((False, None))
        with patch.object(update_gui.messagebox, 'showinfo') as success:
            dialog._check_save()
        self.assertTrue(dialog.is_open)
        self.assertFalse(dialog.is_saving)
        self.assertIn('No changes made', dialog.feedback.set.call_args.args[0])
        dialog.on_updated.assert_not_called()
        success.assert_not_called()

    def test_pending_save_schedules_poll_and_closed_dialog_ignores_results(self):
        dialog = dialog_without_window()
        dialog._saving = True
        dialog._check_save()
        dialog.window.after.assert_called_once_with(100, dialog._check_save)
        dialog._closed = True
        dialog._results.put((True, None))
        dialog._check_save()
        dialog.on_updated.assert_not_called()


class RepositoryIntegrationTests(unittest.TestCase):
    def run_update(self, current, changes, technician_id=None):
        dialog = dialog_without_window(current)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [current, account(), TECHNICIANS[1]]
        with patch.object(repository, 'get_connection', return_value=connection):
            dialog._save_ticket(changes, technician_id)
        self.assertEqual(dialog._results.get_nowait(), (True, None))
        calls = cursor.execute.call_args_list
        update = next(item.args for item in calls if item.args[0].startswith('UPDATE helpdesk.tickets'))
        activities = [item.args[1] for item in calls if item.args[0].startswith('INSERT INTO helpdesk.ticket_history')]
        connection.commit.assert_called_once_with()
        self.assertEqual(update[0].count('%s'), len(update[1]))
        return dict(zip(repository.EDITABLE_FIELDS, update[1])), update[1], activities

    def test_gui_assignment_preserves_repository_status_resolution_and_history(self):
        for status in update_gui.STATUSES:
            with self.subTest(status=status):
                values, parameters, activities = self.run_update(ticket(status), {}, 35)
                self.assertEqual(values['assigned_to'], 'Anna Reyes')
                self.assertEqual(values['status'], 'Assigned' if status == 'Open' else status)
                self.assertEqual(parameters[-3:-1], (False, 'resolved time' if status == 'Resolved' else None))
                self.assertIn((7, 'Technician Assigned', 'Anna Reyes'), activities)
                self.assertEqual(any(item[1] == 'Status Changed' for item in activities), status == 'Open')

    def test_gui_resolve_and_reopen_use_existing_timestamp_and_history_behavior(self):
        for old, new, flag, timestamp in (('Open', 'Resolved', True, None),
                                          ('Resolved', 'Open', False, None),
                                          ('Resolved', 'Resolved', False, 'resolved time')):
            with self.subTest(old=old, new=new):
                _, parameters, activities = self.run_update(ticket(old), {'status': new, 'priority': 'High'})
                self.assertEqual(parameters[-3:-1], (flag, timestamp))
                self.assertIn((7, 'Priority Changed', 'Medium -> High'), activities)
                self.assertEqual(any(item[1] == 'Status Changed' for item in activities), old != new)

    def test_gui_reassignment_unassignment_and_information_updates_log_history(self):
        current = ticket('In Progress', 'Mark Santos')
        _, _, activities = self.run_update(current, {}, 35)
        self.assertIn((7, 'Technician Reassigned', 'Mark Santos -> Anna Reyes'), activities)
        values, _, activities = self.run_update(current, {'assigned_to': None, 'subject': 'Error 404'})
        self.assertIsNone(values['assigned_to'])
        self.assertEqual(values['status'], 'In Progress')
        self.assertIn((7, 'Technician Unassigned', 'Mark Santos'), activities)
        self.assertIn((7, 'Ticket Information Updated', 'Subject: Printer issue -> Error 404'), activities)


if __name__ == '__main__':
    unittest.main()
