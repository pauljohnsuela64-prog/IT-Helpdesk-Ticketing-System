"""Single-note deletion checks using mocks; never delete live database records."""
import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from unittest.mock import MagicMock, call, patch

import mysql.connector

import app
import ticket_comment_repository as comments


def ticket():
    return dict(ticket_id=7, subject='Printer issue', status='In Progress')


def note(comment_id=51, ticket_id=7):
    return dict(comment_id=comment_id, ticket_id=ticket_id, technician_id=12,
                technician_name='Mark Santos', created_at=datetime(2026, 10, 5, 12, 45),
                comment_text='Checked network cable and restarted the router.')


class DeleteNoteRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = (51,)
        self.cursor.rowcount = 1
        self.connect = patch.object(comments, 'get_connection', return_value=self.connection).start()
        self.addCleanup(patch.stopall)

    def test_deletes_only_one_note_with_both_ids_bound_and_leaves_ticket_and_history_alone(self):
        self.assertTrue(comments.delete_ticket_comment(7, 51))
        self.assertEqual(self.cursor.execute.call_args_list, [
            call('SELECT comment_id FROM helpdesk.ticket_comments '
                 'WHERE ticket_id = %s AND comment_id = %s FOR UPDATE', (7, 51)),
            call('DELETE FROM helpdesk.ticket_comments '
                 'WHERE ticket_id = %s AND comment_id = %s LIMIT 1', (7, 51)),
        ])
        self.connect.assert_called_once_with()
        self.connection.commit.assert_called_once_with()
        self.connection.rollback.assert_not_called()

    def test_missing_note_or_other_ticket_note_never_runs_delete(self):
        self.cursor.fetchone.return_value = None
        self.assertFalse(comments.delete_ticket_comment(7, 51))
        self.cursor.execute.assert_called_once_with(
            'SELECT comment_id FROM helpdesk.ticket_comments '
            'WHERE ticket_id = %s AND comment_id = %s FOR UPDATE', (7, 51),
        )
        self.connection.commit.assert_not_called()
        self.connection.rollback.assert_called_once_with()

    def test_invalid_ids_reject_bulk_and_injection_inputs_before_connecting(self):
        for invalid in (None, True, 0, -1, 1.5, 2147483648, '51', 'Mark Santos',
                        'Checked cable', '51 OR 1=1', [51, 52], (51, 52)):
            with self.subTest(ticket_id=invalid), self.assertRaises(ValueError):
                comments.delete_ticket_comment(invalid, 51)
            with self.subTest(comment_id=invalid), self.assertRaises(ValueError):
                comments.delete_ticket_comment(7, invalid)
        self.connect.assert_not_called()

    def test_select_and_delete_errors_roll_back_without_success(self):
        for failures in ([mysql.connector.Error(errno=2003)],
                         [None, mysql.connector.Error(errno=1142)]):
            with self.subTest(failures=len(failures)):
                self.connection.reset_mock()
                self.cursor.execute.side_effect = failures
                with self.assertRaises(comments.TicketCommentDeleteError):
                    comments.delete_ticket_comment(7, 51)
                self.connection.rollback.assert_called_once_with()
                self.connection.commit.assert_not_called()

    def test_commit_failure_reports_unconfirmed_deletion(self):
        self.connection.commit.side_effect = mysql.connector.Error(errno=2013)
        with self.assertRaisesRegex(comments.TicketCommentDeleteError, 'could not be confirmed'):
            comments.delete_ticket_comment(7, 51)
        self.connection.rollback.assert_called_once_with()

    def test_unexpected_affected_counts_do_not_commit(self):
        for count in (0, 2, -1):
            with self.subTest(count=count):
                self.connection.reset_mock()
                self.cursor.rowcount = count
                with self.assertRaises(comments.TicketCommentDeleteError):
                    comments.delete_ticket_comment(7, 51)
                self.connection.commit.assert_not_called()
                self.connection.rollback.assert_called_once_with()

    def test_configuration_and_connection_errors_hide_private_details(self):
        for failure in (ValueError('private configuration'),
                        mysql.connector.Error('private connection', errno=2003)):
            with self.subTest(failure=type(failure).__name__):
                self.connect.side_effect = failure
                with self.assertRaises(comments.TicketCommentDeleteError) as raised:
                    comments.delete_ticket_comment(7, 51)
                self.assertNotIn('private', str(raised.exception))

    def test_missing_table_has_existing_setup_hint(self):
        self.cursor.execute.side_effect = mysql.connector.Error(errno=1146)
        with self.assertRaisesRegex(comments.TicketCommentDeleteError, 'python setup_ticket_comments.py'):
            comments.delete_ticket_comment(7, 51)
        self.connection.commit.assert_not_called()

    def test_rollback_failure_preserves_original_error(self):
        self.cursor.execute.side_effect = mysql.connector.Error(errno=1142)
        self.connection.rollback.side_effect = mysql.connector.Error(errno=2003)
        with self.assertRaisesRegex(comments.TicketCommentDeleteError, '1142'):
            comments.delete_ticket_comment(7, 51)


class DeleteNoteCliTests(unittest.TestCase):
    def run_delete(self, inputs, current=None, notes=None, deleted=True,
                   ticket_error=None, notes_error=None, delete_error=None):
        output = io.StringIO()
        with patch('builtins.input', side_effect=inputs), \
             patch.object(app, 'get_ticket', return_value=current, side_effect=ticket_error) as lookup, \
             patch.object(app, 'get_ticket_comments', return_value=notes or [],
                          side_effect=notes_error) as read, \
             patch.object(app, 'delete_ticket_comment', return_value=deleted,
                          side_effect=delete_error) as delete, redirect_stdout(output):
            app.delete_ticket_note_interactively()
        return output.getvalue(), lookup, read, delete

    def test_invalid_ticket_ids_never_read_or_delete_notes(self):
        for raw_id in ('', ' ', 'abc', '0', '-1', '7.0', '2147483648', '7 OR 1=1', '７', '9' * 5000):
            with self.subTest(raw_id=raw_id[:20]):
                output, lookup, read, delete = self.run_delete([raw_id])
                lookup.assert_not_called()
                read.assert_not_called()
                delete.assert_not_called()
                self.assertIn('Ticket ID must be a positive number', output)

    def test_missing_ticket_returns_without_reading_notes(self):
        output, lookup, read, delete = self.run_delete(['7'])
        lookup.assert_called_once_with(7)
        read.assert_not_called()
        delete.assert_not_called()
        self.assertIn('No ticket found', output)

    def test_no_notes_returns_without_asking_for_comment_id(self):
        output, _, read, delete = self.run_delete(['7'], ticket())
        read.assert_called_once_with(7)
        delete.assert_not_called()
        self.assertIn('There is nothing to delete.', output)

    def test_invalid_comment_ids_never_reach_delete_or_confirmation(self):
        for raw_id in ('', ' ', 'abc', '0', '-1', '51.0', '2147483648', '51 OR 1=1', '51,52',
                       '５１', '9' * 5000):
            with self.subTest(raw_id=raw_id[:20]):
                output, _, _, delete = self.run_delete(['7', raw_id], ticket(), [note()])
                delete.assert_not_called()
                self.assertIn('Comment ID must be a positive number', output)
                self.assertNotIn('Note to delete:', output)

    def test_unknown_or_other_ticket_comment_ids_are_rejected(self):
        for selected in ('999', '52'):
            output, _, _, delete = self.run_delete(['7', selected], ticket(), [note()])
            delete.assert_not_called()
            self.assertIn('No note found with that Comment ID for this ticket.', output)
            self.assertNotIn('Note to delete:', output)
        # Even a mismatched row from a stale or faulty reader cannot pass the CLI ownership check.
        output, _, _, delete = self.run_delete(['7', '52'], ticket(), [note(52, ticket_id=8)])
        delete.assert_not_called()
        self.assertIn('No note found with that Comment ID for this ticket.', output)

    def test_shows_all_notes_and_selected_note_again_before_confirmation(self):
        first, second = note(), {**note(52), 'technician_name': 'Anna Reyes',
                                 'comment_text': 'Connection is stable now.'}
        output = io.StringIO()

        def respond(prompt):
            if prompt == 'Ticket ID: ':
                return '7'
            if prompt == 'Comment ID: ':
                for item in (first, second):
                    self.assertIn(f'Comment ID: {item["comment_id"]}', output.getvalue())
                    self.assertIn(item['technician_name'], output.getvalue())
                    self.assertIn(item['comment_text'], output.getvalue())
                self.assertIn('2026-10-05 12:45', output.getvalue())
                return '52'
            self.assertEqual(prompt, 'Type Y or YES to permanently delete this note: ')
            preview = output.getvalue().split('Note to delete:')[1]
            self.assertIn('Comment ID: 52', preview)
            self.assertIn('Anna Reyes', preview)
            self.assertIn('2026-10-05 12:45', preview)
            self.assertIn(second['comment_text'], preview)
            self.assertNotIn(first['comment_text'], preview)
            self.assertEqual(output.getvalue().count(second['comment_text']), 2)
            return 'N'

        with patch('builtins.input', side_effect=respond), \
             patch.object(app, 'get_ticket', return_value=ticket()), \
             patch.object(app, 'get_ticket_comments', return_value=[first, second]), \
             patch.object(app, 'delete_ticket_comment') as delete, redirect_stdout(output):
            app.delete_ticket_note_interactively()
        delete.assert_not_called()
        self.assertIn('Deletion cancelled.', output.getvalue())

    def test_only_uppercase_y_or_yes_confirms_one_selected_note(self):
        for response in ('Y', 'YES', ' Y ', ' YES '):
            with self.subTest(response=response):
                output, _, _, delete = self.run_delete([' 7 ', ' 0051 ', response], ticket(), [note(), note(52)])
                delete.assert_called_once_with(7, 51)
                self.assertIn('Note 51 deleted successfully from ticket 7.', output)

    def test_every_other_confirmation_response_cancels(self):
        for response in ('', ' ', 'N', 'NO', 'y', 'yes', 'Yes', 'Y please', '1', 'maybe'):
            with self.subTest(response=response):
                output, _, _, delete = self.run_delete(['7', '51', response], ticket(), [note()])
                delete.assert_not_called()
                self.assertIn('Deletion cancelled. No note was deleted.', output)
                self.assertNotIn('successfully', output)

    def test_confirmation_interruptions_cancel(self):
        for interruption in (EOFError(), KeyboardInterrupt()):
            with self.subTest(interruption=type(interruption).__name__):
                output, _, _, delete = self.run_delete(['7', '51', interruption], ticket(), [note()])
                delete.assert_not_called()
                self.assertIn('Deletion cancelled.', output)

    def test_note_disappearing_after_preview_is_friendly(self):
        output, _, _, delete = self.run_delete(['7', '51', 'Y'], ticket(), [note()], deleted=False)
        delete.assert_called_once_with(7, 51)
        self.assertIn('No note found with that Comment ID for this ticket.', output)
        self.assertNotIn('successfully', output)

    def test_inactive_or_missing_author_and_control_characters_do_not_block_safe_deletion(self):
        for author in ('Inactive technician', None):
            stored = {**note(), 'technician_name': author, 'comment_text': 'Checked\x1b[2J cable'}
            output, _, _, delete = self.run_delete(['7', '51', 'Y'], ticket(), [stored])
            delete.assert_called_once_with(7, 51)
            self.assertIn(author or 'Unknown technician', output)
            self.assertNotIn('\x1b', output)
            self.assertIn('\\x1b', output)

    def test_database_errors_are_friendly_and_never_claim_success(self):
        output, _, read, delete = self.run_delete(['7'], ticket_error=app.TicketReadError('Ticket read failed'))
        read.assert_not_called()
        delete.assert_not_called()
        self.assertIn('Ticket read failed', output)
        output, _, _, delete = self.run_delete(['7'], ticket(),
                                              notes_error=comments.TicketCommentReadError('Notes read failed'))
        delete.assert_not_called()
        self.assertIn('Notes read failed', output)
        output, _, _, _ = self.run_delete(['7', '51', 'YES'], ticket(), [note()],
                                         delete_error=comments.TicketCommentDeleteError('Note delete failed'))
        self.assertIn('Note delete failed', output)
        self.assertNotIn('successfully', output)

    def test_cancel_returns_to_submenu_and_back_returns_to_main(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=['8', '3', '7', '51', 'N', '4', '9']), \
             patch.object(app, 'get_ticket', return_value=ticket()), \
             patch.object(app, 'get_ticket_comments', return_value=[note()]), \
             patch.object(app, 'delete_ticket_comment') as delete, redirect_stdout(output):
            app.main()
        delete.assert_not_called()
        self.assertIn('Deletion cancelled.', output.getvalue())
        self.assertGreaterEqual(output.getvalue().count('TICKET COMMENTS / NOTES'), 2)
        self.assertIn('Goodbye!', output.getvalue())


if __name__ == '__main__':
    unittest.main()
