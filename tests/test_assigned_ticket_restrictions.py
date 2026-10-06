"""Ownership and race checks with mocked MySQL transactions; no live writes."""
import unittest
from unittest.mock import MagicMock, patch

import mysql.connector
import ticket_access as access
import ticket_comment_repository as comments
import ticket_repository as tickets
from test_gui_authentication import account
from test_gui_update_ticket import ticket
from user_repository import INVALID_TECHNICIAN_LINK


def technician_user(link=12):
    return {**account('Technician'), 'technician_id': link}


def assigned_ticket(link=12, name='Same Technician Name', status='Assigned'):
    return {**ticket(status, name), 'assigned_technician_id': link}


class TicketWriteTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.lastrowid = 51
        for repository in (tickets, comments):
            patcher = patch.object(repository, 'get_connection', return_value=self.connection)
            patcher.start()
            self.addCleanup(patcher.stop)

    def prepare(self, current=None, user=None, author=True):
        self.cursor.fetchone.side_effect = [current if current is not None else assigned_ticket(),
                                           user if user is not None else technician_user(),
                                           {'technician_id': 12, 'full_name': 'Same Technician Name'} if author else None]

    def update(self, changes=None, session_link=12, selection=None):
        return tickets.update_ticket_for_user(7, 7, {'priority': 'High'} if changes is None else changes,
                                              session_technician_id=session_link, technician_id=selection)

    def note(self, session_link=12, selection=None):
        return comments.add_ticket_comment_for_user(7, 7, '  Checked network cable.  ',
                                                     technician_id=selection, session_technician_id=session_link)

    def writes(self):
        return [call.args for call in self.cursor.execute.call_args_list
                if not call.args[0].startswith('SELECT ')]

    def assert_denied(self, function, error_type, message):
        with self.assertRaises(error_type) as caught:
            function()
        self.assertEqual(str(caught.exception), message)
        self.assertEqual(self.writes(), [])
        self.connection.commit.assert_not_called()
        self.connection.rollback.assert_called()

    def test_technician_can_update_own_fields_status_priority_and_history(self):
        self.prepare()
        self.assertTrue(self.update({'status': 'Resolved', 'priority': 'Critical', 'subject': 'PC 3 fault'}))
        statements = self.cursor.execute.call_args_list
        self.assertTrue(all('FOR UPDATE' in item.args[0] for item in statements[:3]))
        self.assertIn('helpdesk.tickets', statements[0].args[0])
        self.assertEqual(statements[1].args[1], (7,))
        self.assertEqual(statements[2].args[1], (12, 'Active'))
        sql, parameters = next(item for item in self.writes() if item[0].startswith('UPDATE '))
        self.assertIn('assigned_technician_id = %s', sql)
        self.assertEqual(sql.count('%s'), len(parameters))
        self.assertEqual(parameters[-4:], (12, True, None, 7))
        history = [params[1] for query, params in self.writes() if 'ticket_history' in query]
        self.assertEqual(history, ['Status Changed', 'Priority Changed', 'Ticket Information Updated'])
        self.connection.commit.assert_called_once()

    def test_other_unassigned_and_ambiguous_legacy_tickets_cannot_be_updated(self):
        for link in (35, None):
            with self.subTest(link=link):
                self.cursor.reset_mock()
                self.prepare(assigned_ticket(link))
                self.assert_denied(self.update, tickets.TicketUpdateError, access.UPDATE_ASSIGNED_ONLY)

    def test_equal_names_cannot_grant_access_to_another_id(self):
        self.prepare(assigned_ticket(35, name='Same Technician Name'))
        self.assert_denied(self.update, tickets.TicketUpdateError, access.UPDATE_ASSIGNED_ONLY)

    def test_names_do_not_revoke_an_owned_id(self):
        self.prepare(assigned_ticket(12, name='Legacy Display Name'))
        self.assertTrue(self.update())

    def test_technician_assignment_attempts_are_rejected_even_through_repository(self):
        for changes, selection in (({}, 12), ({}, 35), ({'assigned_to': None}, None)):
            with self.subTest(selection=selection, unassign=bool(changes)):
                self.cursor.reset_mock()
                self.prepare()
                self.assert_denied(lambda: self.update(changes, selection=selection),
                                   tickets.TicketUpdateError, access.ADMIN_ASSIGNMENT_ONLY)

    def test_technician_cannot_claim_unassigned_ticket(self):
        self.prepare(assigned_ticket(None, None, 'Open'))
        self.assert_denied(lambda: self.update({}, selection=12), tickets.TicketUpdateError, access.UPDATE_ASSIGNED_ONLY)

    def test_invalid_inactive_or_changed_link_blocks_update_and_note(self):
        for write, error_type in ((self.update, tickets.TicketUpdateError), (self.note, comments.TicketCommentCreateError)):
            for user_link, session_link, active in ((None, None, True), (12, None, True),
                                                   (12, 35, True), (12, 12, False), (True, True, True),
                                                   (1, True, True), (12, 12.0, True)):
                with self.subTest(write=write.__name__, user_link=user_link, session_link=session_link, active=active):
                    self.cursor.reset_mock()
                    self.prepare(user=technician_user(user_link), author=active)
                    self.assert_denied(lambda: write(session_link=session_link), error_type, INVALID_TECHNICIAN_LINK)

    def test_current_account_must_still_be_active_and_have_supported_role(self):
        for user in ({**technician_user(), 'status': 'Inactive'}, account('Unknown')):
            for write, error_type in ((self.update, tickets.TicketUpdateError), (self.note, comments.TicketCommentCreateError)):
                self.cursor.reset_mock()
                self.prepare(user=user)
                self.assert_denied(write, error_type, access.PERMISSION_DENIED)
        self.cursor.reset_mock()
        self.cursor.fetchone.side_effect = [assigned_ticket(), None]
        self.assert_denied(self.update, tickets.TicketUpdateError, access.PERMISSION_DENIED)

    def test_ownership_is_checked_even_for_no_change_request(self):
        self.prepare(assigned_ticket(35))
        self.assert_denied(lambda: self.update({}), tickets.TicketUpdateError, access.UPDATE_ASSIGNED_ONLY)
        self.cursor.reset_mock()
        self.prepare()
        self.assertFalse(self.update({}))
        self.assertEqual(self.writes(), [])

    def test_admin_can_update_any_ticket_and_assign_reassign_or_unassign(self):
        for current_id, selection, changes in ((None, 12, {}), (35, 12, {}),
                                               (12, None, {'assigned_to': None}), (35, None, {'priority': 'Critical'})):
            with self.subTest(current=current_id, selection=selection, changes=changes):
                self.cursor.reset_mock()
                self.prepare(assigned_ticket(current_id), account())
                self.assertTrue(self.update(changes, session_link=None, selection=selection))
                _, parameters = next(item for item in self.writes() if item[0].startswith('UPDATE '))
                self.assertEqual(parameters[-4], None if 'assigned_to' in changes else selection or current_id)

    def test_reassigning_between_equal_names_records_the_identity_change(self):
        self.prepare(assigned_ticket(35), account())
        self.assertTrue(self.update({}, selection=12))
        entries = [params for sql, params in self.writes() if 'ticket_history' in sql]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1], 'Technician Reassigned')
        self.assertIn('ID: 35', entries[0][2])
        self.assertIn('ID: 12', entries[0][2])

    def test_note_on_own_ticket_uses_linked_author_without_ticket_or_history_changes(self):
        self.prepare()
        self.assertEqual(self.note(), 51)
        self.assertEqual(len(self.writes()), 1)
        self.assertTrue(self.writes()[0][0].startswith('INSERT INTO helpdesk.ticket_comments'))
        self.assertEqual(self.writes()[0][1], (7, 12, 'Checked network cable.'))
        self.connection.commit.assert_called_once()

    def test_other_and_unassigned_tickets_cannot_receive_technician_notes(self):
        for link in (35, None):
            self.cursor.reset_mock()
            self.prepare(assigned_ticket(link))
            self.assert_denied(self.note, comments.TicketCommentCreateError, access.NOTE_ASSIGNED_ONLY)

    def test_technician_cannot_change_note_author(self):
        self.prepare()
        self.assert_denied(lambda: self.note(selection=35), comments.TicketCommentCreateError, INVALID_TECHNICIAN_LINK)

    def test_admin_can_add_notes_on_any_ticket_with_existing_active_author_choice(self):
        for link in (None, 12, 35):
            self.cursor.reset_mock()
            self.prepare(assigned_ticket(link), account())
            self.assertEqual(self.note(session_link=None, selection=12), 51)
            self.assertEqual(self.writes()[0][1], (7, 12, 'Checked network cable.'))

    def test_cli_retains_update_and_note_paths_without_gui_account_requirement(self):
        self.cursor.fetchone.side_effect = [assigned_ticket(), {'technician_id': 35, 'full_name': 'Other Technician'}]
        self.assertTrue(tickets.update_ticket(7, {}, technician_id=35))
        self.assertFalse(any('helpdesk.users' in call.args[0] for call in self.cursor.execute.call_args_list))
        self.cursor.reset_mock()
        self.cursor.fetchone.side_effect = [assigned_ticket(35), {'technician_id': 12, 'full_name': 'Author'}]
        self.assertEqual(comments.add_ticket_comment(7, 12, 'Checked cable.'), 51)
        self.assertFalse(any('helpdesk.users' in call.args[0] for call in self.cursor.execute.call_args_list))

    def test_assignment_id_is_not_a_manually_editable_field(self):
        with self.assertRaises(ValueError):
            self.update({'assigned_technician_id': 12})
        self.cursor.execute.assert_not_called()

    def test_missing_migration_and_database_errors_are_safe(self):
        for write, error_type in ((self.update, tickets.TicketUpdateError), (self.note, comments.TicketCommentCreateError)):
            for errno in (1054, 2003):
                self.cursor.execute.side_effect = mysql.connector.Error('private details', errno=errno)
                with self.assertRaises(error_type) as caught:
                    write()
                self.assertNotIn('private details', str(caught.exception))
                if errno == 1054:
                    self.assertIn('setup_ticket_assignments.py', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
