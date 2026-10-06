"""Note validation, transactions, and CLI checks without changing live data."""
import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from unittest.mock import MagicMock, patch

import mysql.connector

import app
import setup_ticket_comments
import ticket_comment_repository as comments
from input_validation import validate_comment_text


def ticket():
    return dict(ticket_id=7, employee_name='Alice', department='IT', category='Hardware',
                subject='Printer issue', description='Paper jam', priority='Medium',
                status='Open', assigned_to=None)


def technicians():
    return [dict(technician_id=12, full_name='Mark Santos', status='Active'),
            dict(technician_id=35, full_name='Anna Reyes', status='Active')]


class NoteValidationTests(unittest.TestCase):
    def test_trims_text_and_accepts_numbers_and_unicode(self):
        for text, expected in (('  Checked cable.  ', 'Checked cable.'), ('404', '404'),
                               ('PC 3', 'PC 3'), ('已重启', '已重启')):
            with self.subTest(text=text):
                self.assertEqual(validate_comment_text(text), expected)

    def test_rejects_blank_short_and_punctuation_only_notes(self):
        for text in (None, 3, '', '   ', 'ab', 'a b', '!!!', 'a!?', '\t\n', '\x1b\x1b\x1b'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_comment_text(text)

    def test_checks_utf8_capacity_before_insert(self):
        self.assertEqual(len(validate_comment_text('a' * 65535)), 65535)
        for text in ('a' * 65536, '界' * 21846):
            with self.subTest(size=len(text)), self.assertRaisesRegex(ValueError, '65535 UTF-8 bytes'):
                validate_comment_text(text)


class NoteRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.side_effect = [{'ticket_id': 7}, technicians()[0]]
        self.cursor.lastrowid = 51
        self.connect = patch.object(comments, 'get_connection', return_value=self.connection).start()
        self.addCleanup(patch.stopall)

    def test_repeatable_setup_has_both_safe_foreign_keys_and_leaves_other_tables_alone(self):
        comments.setup_ticket_comments_table()
        comments.setup_ticket_comments_table()
        self.assertEqual(self.cursor.execute.call_count, 2)
        for call in self.cursor.execute.call_args_list:
            self.assertEqual(call.args, (comments.CREATE_TICKET_COMMENTS_SQL,))
        query = ' '.join(comments.CREATE_TICKET_COMMENTS_SQL.split())
        self.assertTrue(query.startswith('CREATE TABLE IF NOT EXISTS helpdesk.ticket_comments ('))
        self.assertIn('comment_id INT AUTO_INCREMENT PRIMARY KEY', query)
        self.assertIn('comment_text TEXT NOT NULL', query)
        self.assertIn('created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP', query)
        self.assertIn('REFERENCES helpdesk.tickets (ticket_id) ON DELETE CASCADE', query)
        self.assertIn('REFERENCES helpdesk.technicians (technician_id) ON DELETE SET NULL', query)
        for mutation in ('ALTER ', 'UPDATE ', 'DELETE FROM ', 'DROP ', 'INSERT '):
            self.assertNotIn(mutation, query)

    def test_saves_author_id_and_trimmed_note_with_bound_values_in_one_transaction(self):
        note = "  Checked router's logs; Error 404.  "
        self.assertEqual(comments.add_ticket_comment(7, 12, note), 51)
        calls = self.cursor.execute.call_args_list
        self.assertEqual(calls[0].args, (
            'SELECT ticket_id, assigned_technician_id FROM helpdesk.tickets WHERE ticket_id = %s FOR UPDATE', (7,),
        ))
        self.assertEqual(calls[1].args, (
            'SELECT technician_id, full_name FROM helpdesk.technicians '
            'WHERE technician_id = %s AND status = %s FOR UPDATE', (12, 'Active'),
        ))
        self.assertEqual(calls[2].args, (
            'INSERT INTO helpdesk.ticket_comments (ticket_id, technician_id, comment_text) '
            'VALUES (%s, %s, %s)', (7, 12, note.strip()),
        ))
        self.assertEqual(len(calls), 3)
        self.assertNotIn(note.strip(), calls[2].args[0])
        self.connect.assert_called_once_with()
        self.connection.commit.assert_called_once_with()
        self.connection.rollback.assert_not_called()

    def test_note_never_updates_ticket_status_or_writes_history(self):
        comments.add_ticket_comment(7, 12, 'Checked cable')
        queries = [call.args[0] for call in self.cursor.execute.call_args_list]
        writes = [query for query in queries if not query.startswith('SELECT ')]
        self.assertEqual(len(writes), 1)
        self.assertTrue(writes[0].startswith('INSERT INTO helpdesk.ticket_comments '))
        self.assertTrue(all('ticket_history' not in query for query in queries))

    def test_deleted_ticket_is_rechecked_before_saving(self):
        self.cursor.fetchone.side_effect = [None]
        with self.assertRaisesRegex(comments.TicketCommentCreateError, 'No ticket found'):
            comments.add_ticket_comment(7, 12, 'Checked cable')
        self.assertEqual(self.cursor.execute.call_count, 1)
        self.connection.commit.assert_not_called()
        self.connection.rollback.assert_called_once_with()

    def test_inactive_or_missing_author_is_rechecked_before_saving(self):
        self.cursor.fetchone.side_effect = [{'ticket_id': 7}, None]
        with self.assertRaisesRegex(comments.TicketCommentCreateError, 'no longer available or Active'):
            comments.add_ticket_comment(7, 12, 'Checked cable')
        self.assertEqual(self.cursor.execute.call_count, 2)
        self.connection.commit.assert_not_called()
        self.connection.rollback.assert_called_once_with()

    def test_invalid_ids_or_notes_fail_without_connecting(self):
        for invalid in (True, 0, -1, '7', '7 OR 1=1', 7.5, 2147483648):
            with self.subTest(ticket_id=invalid), self.assertRaises(ValueError):
                comments.add_ticket_comment(invalid, 12, 'Checked cable')
            with self.subTest(technician_id=invalid), self.assertRaises(ValueError):
                comments.add_ticket_comment(7, invalid, 'Checked cable')
            with self.subTest(read_id=invalid), self.assertRaises(ValueError):
                comments.get_ticket_comments(invalid)
        for note in ('', '  ', 'ab', '!!!'):
            with self.subTest(note=note), self.assertRaises(ValueError):
                comments.add_ticket_comment(7, 12, note)
        self.connect.assert_not_called()

    def test_read_is_parameterized_stable_and_keeps_inactive_and_missing_authors(self):
        rows = [dict(comment_id=1, technician_name='Inactive author'),
                dict(comment_id=2, technician_name=None)]
        self.cursor.fetchall.return_value = rows
        self.assertEqual(comments.get_ticket_comments(7), rows)
        query, parameters = self.cursor.execute.call_args.args
        self.assertEqual(parameters, (7,))
        self.assertIn('LEFT JOIN helpdesk.technicians AS t ON t.technician_id = c.technician_id', query)
        self.assertIn('WHERE c.ticket_id = %s ORDER BY c.created_at, c.comment_id', query)
        self.assertNotIn('status', query)
        self.assertTrue(query.startswith('SELECT '))
        self.connection.commit.assert_not_called()

    def test_insert_failure_rolls_back_and_hides_private_error_details(self):
        self.cursor.execute.side_effect = [None, None, mysql.connector.Error('private details', errno=2003)]
        with self.assertRaises(comments.TicketCommentCreateError) as raised:
            comments.add_ticket_comment(7, 12, 'Checked cable')
        self.assertNotIn('private', str(raised.exception))
        self.assertIn('View Ticket Notes before retrying', str(raised.exception))
        self.connection.rollback.assert_called_once_with()
        self.connection.commit.assert_not_called()

    def test_commit_failure_is_reported_without_claiming_success(self):
        self.connection.commit.side_effect = mysql.connector.Error(errno=2013)
        with self.assertRaisesRegex(comments.TicketCommentCreateError, 'could not be confirmed'):
            comments.add_ticket_comment(7, 12, 'Checked cable')
        self.connection.rollback.assert_called_once_with()

    def test_missing_table_points_to_setup_for_reads_and_writes(self):
        self.cursor.execute.side_effect = mysql.connector.Error(errno=1146)
        for action, error_type in ((lambda: comments.get_ticket_comments(7), comments.TicketCommentReadError),
                                  (lambda: comments.add_ticket_comment(7, 12, 'Checked cable'),
                                   comments.TicketCommentCreateError)):
            with self.assertRaisesRegex(error_type, 'python setup_ticket_comments.py'):
                action()

    def test_configuration_and_connection_errors_are_safe(self):
        for failure in (ValueError('private settings'), mysql.connector.Error('private details', errno=2003)):
            self.connect.side_effect = failure
            for action, error_type in ((lambda: comments.get_ticket_comments(7), comments.TicketCommentReadError),
                                      (lambda: comments.add_ticket_comment(7, 12, 'Checked cable'),
                                       comments.TicketCommentCreateError),
                                      (comments.setup_ticket_comments_table, comments.TicketCommentSetupError)):
                with self.subTest(error=error_type), self.assertRaises(error_type) as raised:
                    action()
                self.assertNotIn('private', str(raised.exception))


class NotesCliTests(unittest.TestCase):
    def run_add(self, inputs, existing=None, authors=None, read_error=None,
                authors_error=None, save_error=None):
        output = io.StringIO()
        with patch('builtins.input', side_effect=inputs), \
             patch.object(app, 'get_ticket', return_value=existing, side_effect=read_error) as read, \
             patch.object(app, 'get_active_technicians', return_value=authors or [],
                          side_effect=authors_error) as active, \
             patch.object(app, 'add_ticket_comment', return_value=51, side_effect=save_error) as save, \
             redirect_stdout(output):
            app.add_ticket_note_interactively()
        return output.getvalue(), read, active, save

    def run_view(self, raw_id, existing=None, notes=None, read_error=None, notes_error=None):
        output = io.StringIO()
        with patch('builtins.input', return_value=raw_id), \
             patch.object(app, 'get_ticket', return_value=existing, side_effect=read_error) as read, \
             patch.object(app, 'get_ticket_comments', return_value=notes or [],
                          side_effect=notes_error) as get_notes, redirect_stdout(output):
            app.view_ticket_notes()
        return output.getvalue(), read, get_notes

    def test_add_shows_summary_and_active_numbered_choices_and_saves_id(self):
        output, read, active, save = self.run_add(
            [' 7 ', '2', '  Checked network cable.  '], ticket(), technicians(),
        )
        read.assert_called_once_with(7)
        active.assert_called_once_with()
        save.assert_called_once_with(7, 35, 'Checked network cable.')
        for text in ('Selected ticket:', 'Printer issue', 'Alice', 'Open',
                     '1. Mark Santos', '2. Anna Reyes', 'Note added successfully to ticket 7.'):
            self.assertIn(text, output)

    def test_invalid_author_choices_and_notes_reprompt_without_saving_them(self):
        output, _, _, save = self.run_add(
            ['7', '', '0', 'abc', '-1', '3', '12', '2', '', '  ', 'ab', 'a b', '!!!', 'PC 3'],
            ticket(), technicians(),
        )
        self.assertEqual(output.count('Invalid selection.'), 6)
        self.assertIn('Note is required.', output)
        self.assertIn('Note must contain at least 3 letters or numbers.', output)
        save.assert_called_once_with(7, 35, 'PC 3')

    def test_bad_ids_are_friendly_in_both_actions(self):
        for raw_id in ('abc', '', '  ', '-1', '0', '2147483648', '7 OR 1=1', '７', '9' * 5000):
            with self.subTest(raw_id=raw_id[:20]):
                output, read, active, save = self.run_add([raw_id])
                read.assert_not_called()
                active.assert_not_called()
                save.assert_not_called()
                self.assertIn('Ticket ID must be', output)
                output, read, get_notes = self.run_view(raw_id)
                read.assert_not_called()
                get_notes.assert_not_called()
                self.assertIn('Ticket ID must be', output)

    def test_missing_ticket_does_not_prompt_for_author_or_read_notes(self):
        output, read, active, save = self.run_add(['7'])
        read.assert_called_once_with(7)
        active.assert_not_called()
        save.assert_not_called()
        self.assertIn('No ticket found', output)
        output, _, get_notes = self.run_view('7')
        get_notes.assert_not_called()
        self.assertIn('No ticket found', output)

    def test_no_active_technicians_is_friendly_and_does_not_save(self):
        output, _, active, save = self.run_add(['7'], ticket())
        active.assert_called_once_with()
        save.assert_not_called()
        self.assertIn('No active technicians are available', output)

    def test_read_author_and_save_errors_return_without_success(self):
        output, _, active, save = self.run_add(['7'], read_error=app.TicketReadError('Ticket read failed'))
        self.assertIn('Ticket read failed', output)
        active.assert_not_called()
        save.assert_not_called()
        output, _, _, save = self.run_add(['7'], ticket(),
                                        authors_error=app.TechnicianReadError('Author read failed'))
        self.assertIn('Author read failed', output)
        save.assert_not_called()
        output, _, _, _ = self.run_add(['7', '1', 'Checked cable'], ticket(), technicians(),
                                      save_error=comments.TicketCommentCreateError('Note save failed'))
        self.assertIn('Note save failed', output)
        self.assertNotIn('successfully', output)

    def test_view_without_notes_is_friendly(self):
        output, _, get_notes = self.run_view('7', ticket())
        get_notes.assert_called_once_with(7)
        self.assertIn('No notes have been added to this ticket yet.', output)

    def test_view_displays_dates_names_text_and_preserves_repository_order(self):
        notes = [dict(created_at=datetime(2026, 10, 5, 12, 45), technician_name='Mark Santos',
                      comment_text='Checked network cable.'),
                 dict(created_at=datetime(2026, 10, 5, 13, 0), technician_name='Anna Reyes',
                      comment_text='Connection is stable now.')]
        output, _, _ = self.run_view('7', ticket(), notes)
        self.assertIn('2026-10-05 12:45 | Mark Santos\nChecked network cable.', output)
        self.assertIn('2026-10-05 13:00 | Anna Reyes\nConnection is stable now.', output)
        self.assertLess(output.index('Checked network cable.'), output.index('Connection is stable now.'))

    def test_view_missing_author_and_control_characters_are_safe(self):
        note = dict(created_at=None, technician_name=None, comment_text='Checked\x1b[2J cable')
        output, _, _ = self.run_view('7', ticket(), [note])
        self.assertIn('- | Unknown technician', output)
        self.assertNotIn('\x1b', output)
        self.assertIn('\\x1b', output)

    def test_view_errors_are_friendly(self):
        output, _, get_notes = self.run_view('7', read_error=app.TicketReadError('Ticket read failed'))
        get_notes.assert_not_called()
        self.assertIn('Ticket read failed', output)
        output, _, _ = self.run_view('7', ticket(), notes_error=comments.TicketCommentReadError('Notes read failed'))
        self.assertIn('Notes read failed', output)

    def test_submenu_routes_view_add_delete_invalid_options_and_back(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=['abc', '5', '1', '2', '3', '4']), \
             patch.object(app, 'view_ticket_notes') as view, \
             patch.object(app, 'add_ticket_note_interactively') as add, \
             patch.object(app, 'delete_ticket_note_interactively') as delete, redirect_stdout(output):
            app.manage_ticket_notes()
        view.assert_called_once_with()
        add.assert_called_once_with()
        delete.assert_called_once_with()
        for option in ('1. View Ticket Notes', '2. Add Ticket Note', '3. Delete Ticket Note', '4. Back'):
            self.assertIn(option, output.getvalue())
        self.assertEqual(output.getvalue().count('Invalid option.'), 2)

    def test_main_notes_menu_back_and_new_exit(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=['8', '4', '9']), redirect_stdout(output):
            app.main()
        self.assertIn('8. Ticket Comments / Notes', output.getvalue())
        self.assertIn('9. Exit', output.getvalue())
        self.assertIn('TICKET COMMENTS / NOTES', output.getvalue())
        self.assertIn('Goodbye!', output.getvalue())

    def test_note_input_interruption_returns_through_existing_main_handler(self):
        for interruption in (EOFError(), KeyboardInterrupt()):
            output = io.StringIO()
            with patch('builtins.input', side_effect=['8', '2', '7', '1', interruption]), \
                 patch.object(app, 'get_ticket', return_value=ticket()), \
                 patch.object(app, 'get_active_technicians', return_value=technicians()), \
                 patch.object(app, 'add_ticket_comment') as save, redirect_stdout(output):
                app.main()
            save.assert_not_called()
            self.assertIn('Goodbye!', output.getvalue())


class SetupCommandTests(unittest.TestCase):
    def test_success_and_failure_exit_codes(self):
        output = io.StringIO()
        with patch.object(setup_ticket_comments, 'setup_ticket_comments_table'), redirect_stdout(output):
            self.assertEqual(setup_ticket_comments.main(), 0)
        self.assertIn('helpdesk.ticket_comments is available', output.getvalue())
        with patch.object(setup_ticket_comments, 'setup_ticket_comments_table',
                          side_effect=comments.TicketCommentSetupError('Setup failed')), redirect_stdout(output):
            self.assertEqual(setup_ticket_comments.main(), 1)
        self.assertIn('Setup failed', output.getvalue())


if __name__ == '__main__':
    unittest.main()
