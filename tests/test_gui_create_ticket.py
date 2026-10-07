"""GUI Create Ticket checks without opening a window or writing to MySQL."""
import unittest
from queue import Queue
from unittest.mock import MagicMock, patch

import gui_create_ticket as create_gui


def form_data():
    return dict(employee_name='  Alice Reyes  ', department='  IT  ', category='Hardware',
                subject='  PC 3  ', description='  Checked cable.\nRestarted router.  ', priority='Medium')


def dialog_without_window(data=None):
    values = form_data() if data is None else data
    dialog = create_gui.CreateTicketDialog.__new__(create_gui.CreateTicketDialog)
    dialog.parent = MagicMock()
    dialog.window = MagicMock()
    dialog.on_created = MagicMock()
    dialog.user = dict(user_id=7, role='Admin', status='Active')
    dialog.permissions = create_gui.SessionPermissions(dialog.user)
    dialog.fields = {}
    for field in ('employee_name', 'department', 'category', 'subject', 'priority'):
        variable = MagicMock()
        variable.get.return_value = values[field]
        dialog.fields[field] = variable
    dialog.description = MagicMock()
    dialog.description.get.return_value = values['description']
    dialog.feedback = MagicMock()
    dialog.save_button = MagicMock()
    dialog.cancel_button = MagicMock()
    dialog._widgets = [(MagicMock(), 'normal'), (MagicMock(), 'readonly'),
                       (dialog.description, 'normal'), (MagicMock(), 'readonly')]
    dialog._results = Queue()
    dialog._saving = False
    dialog._closed = False
    dialog._poll_id = None
    return dialog


class FormValidationTests(unittest.TestCase):
    def test_shared_validation_trims_text_and_preserves_multiline_description(self):
        dialog = dialog_without_window()
        with patch.object(create_gui, 'validate_text', wraps=create_gui.validate_text) as validate:
            self.assertEqual(dialog._validated_values(), (
                'Alice Reyes', 'IT', 'Hardware', 'PC 3', 'Checked cable.\nRestarted router.', 'Medium',
            ))
        self.assertEqual(validate.call_count, 4)
        dialog.description.get.assert_called_once_with('1.0', 'end-1c')

    def test_blank_whitespace_numeric_names_and_short_text_keep_form_open(self):
        invalid_values = {
            'employee_name': ('', '   ', '3', '!!!'),
            'department': ('', '   ', '123', '???'),
            'subject': ('', '   ', 'ab', ' ab '),
            'description': ('', ' \n\t ', '1234', ' 1234 '),
        }
        for field, values in invalid_values.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    dialog = dialog_without_window({**form_data(), field: value})
                    with patch.object(create_gui, 'Thread') as worker:
                        dialog.save()
                    worker.assert_not_called()
                    dialog.feedback.set.assert_called_once()
                    self.assertTrue(dialog.is_open)
                    self.assertFalse(dialog.is_saving)
                    dialog.window.destroy.assert_not_called()
                    dialog.save_button.state.assert_not_called()

    def test_length_limits_and_invalid_choices_fail_before_background_save(self):
        invalid_values = (('employee_name', 'A' * 101), ('department', 'A' * 101),
                          ('subject', 'A' * 151), ('description', 'A' * 65536),
                          ('category', ''), ('category', 'Invalid'), ('priority', ''),
                          ('priority', 'Urgent'))
        for field, value in invalid_values:
            with self.subTest(field=field, size=len(value)):
                dialog = dialog_without_window({**form_data(), field: value})
                with patch.object(create_gui, 'Thread') as worker:
                    dialog.save()
                worker.assert_not_called()
                dialog.feedback.set.assert_called_once()
                self.assertTrue(dialog.is_open)

    def test_valid_numeric_subjects_and_descriptions_use_existing_cli_rules(self):
        dialog = dialog_without_window({**form_data(), 'subject': '404', 'description': '12345'})
        self.assertEqual(dialog._validated_values()[3:5], ('404', '12345'))


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.dialog = dialog_without_window()

    def test_valid_save_starts_one_worker_with_snapshot_and_disables_controls(self):
        values = self.dialog._validated_values()
        self.dialog.window.after.return_value = 'save-poll'
        with patch.object(create_gui, 'Thread') as worker:
            self.dialog.save()
            self.dialog.save()
        worker.assert_called_once_with(target=self.dialog._save_ticket, args=(values,), daemon=True)
        worker.return_value.start.assert_called_once_with()
        self.assertTrue(self.dialog.is_saving)
        self.dialog.feedback.set.assert_called_once_with('Saving ticket...')
        self.dialog.save_button.state.assert_called_once_with(['disabled'])
        self.dialog.cancel_button.state.assert_called_once_with(['disabled'])
        for widget, _ in self.dialog._widgets:
            widget.configure.assert_called_once_with(state='disabled')
        self.dialog.window.after.assert_called_once_with(100, self.dialog._check_save)
        self.assertEqual(self.dialog._poll_id, 'save-poll')

    def test_worker_reuses_create_repository_without_reading_or_updating_tkinter(self):
        values = ('Alice Reyes', 'IT', 'Hardware', 'PC 3', 'Checked cable.', 'Medium')
        with patch.object(create_gui, 'create_ticket_for_user', return_value=42) as save:
            self.dialog._save_ticket(values)
        save.assert_called_once_with(7, *values)
        self.assertEqual(self.dialog._results.get_nowait(), (42, None))
        self.assertEqual(self.dialog.window.mock_calls, [])
        self.assertEqual(self.dialog.feedback.mock_calls, [])
        for variable in self.dialog.fields.values():
            variable.get.assert_not_called()
        self.dialog.description.get.assert_not_called()

    def test_database_and_repository_validation_errors_are_queued(self):
        for failure in (create_gui.TicketCreateError('Check MySQL.'), ValueError('Invalid category.')):
            with self.subTest(failure=type(failure).__name__):
                with patch.object(create_gui, 'create_ticket_for_user', side_effect=failure):
                    self.dialog._save_ticket(('Alice', 'IT', 'Hardware', 'PC 3', 'Checked cable.', 'Medium'))
                self.assertEqual(self.dialog._results.get_nowait(), (None, str(failure)))
                self.dialog.window.destroy.assert_not_called()
                self.dialog.on_created.assert_not_called()

    def test_unexpected_error_does_not_expose_private_details_or_claim_success(self):
        with patch.object(create_gui, 'create_ticket_for_user', side_effect=RuntimeError('private details')):
            self.dialog._save_ticket(('Alice', 'IT', 'Hardware', 'PC 3', 'Checked cable.', 'Medium'))
        ticket_id, error = self.dialog._results.get_nowait()
        self.assertIsNone(ticket_id)
        self.assertNotIn('private', error)
        self.assertIn('could not be confirmed', error)
        self.assertIn('Refresh', error)

    def test_save_error_keeps_values_and_restores_readonly_dropdowns_and_buttons(self):
        self.dialog._saving = True
        self.dialog._results.put((None, 'Check MySQL.'))
        with patch.object(create_gui.messagebox, 'showinfo') as success:
            self.dialog._check_save()
        self.assertTrue(self.dialog.is_open)
        self.assertFalse(self.dialog.is_saving)
        self.dialog.feedback.set.assert_called_once_with('Check MySQL.')
        for field, variable in self.dialog.fields.items():
            self.assertEqual(variable.get(), form_data()[field])
        self.assertEqual(self.dialog.description.get(), form_data()['description'])
        for widget, normal_state in self.dialog._widgets:
            widget.configure.assert_called_once_with(state=normal_state)
        self.dialog.save_button.state.assert_called_once_with(['!disabled'])
        self.dialog.cancel_button.state.assert_called_once_with(['!disabled'])
        self.dialog.window.destroy.assert_not_called()
        self.dialog.on_created.assert_not_called()
        success.assert_not_called()
        with patch.object(create_gui, 'Thread') as worker:
            self.dialog.save()
        worker.return_value.start.assert_called_once_with()

    def test_success_closes_form_refreshes_parent_and_displays_id_once(self):
        self.dialog._saving = True
        self.dialog._results.put((42, None))
        with patch.object(create_gui.messagebox, 'showinfo') as success:
            self.dialog._check_save()
            self.dialog._check_save()
        self.assertFalse(self.dialog.is_open)
        self.assertFalse(self.dialog.is_saving)
        self.dialog.window.destroy.assert_called_once_with()
        self.dialog.on_created.assert_called_once_with()
        success.assert_called_once_with('Ticket Created',
                                        'Ticket created successfully.\nNew Ticket ID: 42',
                                        parent=self.dialog.parent)

    def test_pending_save_schedules_another_poll(self):
        self.dialog._saving = True
        self.dialog.window.after.return_value = 'next-poll'
        self.dialog._check_save()
        self.dialog.window.after.assert_called_once_with(100, self.dialog._check_save)
        self.assertEqual(self.dialog._poll_id, 'next-poll')
        self.assertTrue(self.dialog.is_saving)
        self.dialog.window.destroy.assert_not_called()

    def test_cancel_before_save_closes_once_without_saving_or_refreshing(self):
        with patch.object(create_gui, 'create_ticket_for_user') as save, patch.object(create_gui, 'Thread') as worker:
            self.dialog.cancel()
            self.dialog.cancel()
            self.dialog.save()
        self.assertFalse(self.dialog.is_open)
        self.dialog.window.grab_release.assert_called_once_with()
        self.dialog.window.destroy.assert_called_once_with()
        self.dialog.on_created.assert_not_called()
        save.assert_not_called()
        worker.assert_not_called()

    def test_cancel_during_save_waits_for_result(self):
        self.dialog._saving = True
        self.dialog.cancel()
        self.assertTrue(self.dialog.is_open)
        self.dialog.window.destroy.assert_not_called()

    def test_closed_dialog_ignores_late_result(self):
        self.dialog._closed = True
        self.dialog._results.put((42, None))
        with patch.object(create_gui.messagebox, 'showinfo') as success:
            self.dialog._check_save()
        self.dialog.on_created.assert_not_called()
        self.dialog.feedback.set.assert_not_called()
        success.assert_not_called()


class FormConstructionTests(unittest.TestCase):
    def test_readonly_choices_medium_default_multiline_description_and_cancel_bindings(self):
        variables = [MagicMock() for _ in range(6)]
        with patch.object(create_gui.tk, 'Toplevel') as window, \
             patch.object(create_gui.tk, 'StringVar', side_effect=variables) as variable, \
             patch.object(create_gui.tk, 'Text') as text, \
             patch.object(create_gui.ttk, 'Frame'), patch.object(create_gui.ttk, 'Label'), \
             patch.object(create_gui.ttk, 'Entry'), patch.object(create_gui.ttk, 'Scrollbar'), \
             patch.object(create_gui.ttk, 'Combobox') as combo, \
             patch.object(create_gui.ttk, 'Button') as button, \
             patch.object(create_gui, 'create_ticket_for_user') as save:
            dialog = create_gui.CreateTicketDialog(MagicMock(), MagicMock())
        self.assertEqual([item.kwargs['values'] for item in combo.call_args_list],
                         [create_gui.CATEGORIES, create_gui.PRIORITIES])
        self.assertTrue(all(item.kwargs['state'] == 'readonly' for item in combo.call_args_list))
        self.assertEqual(variable.call_args_list[4].kwargs['value'], 'Medium')
        self.assertEqual(text.call_args.kwargs['wrap'], 'word')
        self.assertGreater(text.call_args.kwargs['height'], 1)
        self.assertEqual([item.kwargs['text'] for item in button.call_args_list], ['Cancel', 'Save'])
        window.return_value.protocol.assert_called_once_with('WM_DELETE_WINDOW', dialog.cancel)
        self.assertEqual(window.return_value.bind.call_args.args[0], '<Escape>')
        save.assert_not_called()


if __name__ == '__main__':
    unittest.main()
