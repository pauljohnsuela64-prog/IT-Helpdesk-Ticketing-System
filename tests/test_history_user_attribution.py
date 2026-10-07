"""Attributed activity transactions and session checks; no live database writes."""
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import MagicMock, patch

import mysql.connector

import gui_create_ticket as create_gui
import gui_ticket_history as history_gui
import ticket_history_repository as history
import ticket_repository as tickets
from gui_permissions import SessionPermissions
from test_gui_create_ticket import dialog_without_window
from test_ticket_history import ticket


VALUES = ('Alice', 'IT', 'Hardware', 'PC 3', 'Checked cable.', 'Medium')


class AttributedTransactionTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.lastrowid = 91
        self.connect = patch.object(tickets, 'get_connection', return_value=self.connection).start()
        self.addCleanup(patch.stopall)

    def history_writes(self):
        return [call.args for call in self.cursor.execute.call_args_list
                if call.args[0].startswith('INSERT INTO helpdesk.ticket_history')]

    def test_both_roles_create_one_event_with_user_id_and_actual_ticket_defaults(self):
        for role, user_id in (('Admin', 42), ('Technician', 84)):
            with self.subTest(role=role):
                self.cursor.execute.reset_mock()
                self.connection.commit.reset_mock()
                self.cursor.fetchone.return_value = (role, 'Active')
                self.assertEqual(tickets.create_ticket_for_user(user_id, *VALUES), 91)
                events = self.history_writes()
                self.assertEqual(len(events), 1)
                query, parameters = events[0]
                self.assertIn('performed_by_user_id', query)
                self.assertIn("CONCAT('Priority: ', priority, ', Status: ', status)", query)
                self.assertEqual(parameters, ('Ticket Created', user_id, 91))
                self.assertEqual(query.count('%s'), len(parameters))
                self.cursor.execute.assert_any_call(
                    'SELECT role, status FROM helpdesk.users WHERE user_id = %s FOR UPDATE', (user_id,))
                self.connection.commit.assert_called_once_with()

    def test_missing_inactive_or_unsupported_create_actor_is_rejected_before_inserts(self):
        for actor in (None, ('Admin', 'Inactive'), ('Technician', 'Inactive'), ('Unknown', 'Active')):
            with self.subTest(actor=actor):
                self.cursor.execute.reset_mock()
                self.cursor.fetchone.return_value = actor
                with self.assertRaisesRegex(tickets.TicketCreateError, 'permission'):
                    tickets.create_ticket_for_user(42, *VALUES)
                self.assertFalse(any(call.args[0].startswith('INSERT') for call in self.cursor.execute.call_args_list))
                self.connection.commit.assert_not_called()
                self.connection.rollback.assert_called()

    def test_authenticated_create_cannot_fall_back_to_null_actor(self):
        for user_id in (None, True, 0, -1, '42', '42 OR 1=1', 2147483648):
            with self.subTest(user_id=user_id), self.assertRaises(ValueError):
                tickets.create_ticket_for_user(user_id, *VALUES)
        self.connect.assert_not_called()

    def test_admin_multi_field_update_attributes_every_existing_event_once(self):
        current = ticket('Open', None)
        self.cursor.fetchone.side_effect = [current, dict(role='Admin', status='Active'),
                                           dict(technician_id=12, full_name='Mark Santos')]
        self.assertTrue(tickets.update_ticket_for_user(7, 42, {'priority': 'Critical', 'subject': 'New issue'},
                                                      technician_id=12))
        events = [params for _, params in self.history_writes()]
        self.assertEqual([event[1] for event in events], [
            'Technician Assigned', 'Status Changed', 'Priority Changed', 'Ticket Information Updated'])
        self.assertTrue(all(event[0] == 7 and event[3] == 42 for event in events))
        self.assertEqual(events[1][2], 'Open -> Assigned')
        self.connection.commit.assert_called_once_with()

    def test_admin_reassignment_and_unassignment_record_acting_user_not_technician(self):
        for changes, selection, action in (({}, 35, 'Technician Reassigned'),
                                           ({'assigned_to': None}, None, 'Technician Unassigned')):
            with self.subTest(action=action):
                self.cursor.execute.reset_mock()
                rows = [ticket(), dict(role='Admin', status='Active')]
                if selection:
                    rows.append(dict(technician_id=35, full_name='Anna Reyes'))
                self.cursor.fetchone.side_effect = rows
                tickets.update_ticket_for_user(7, 42, changes, technician_id=selection)
                self.assertEqual(len(self.history_writes()), 1)
                parameters = self.history_writes()[0][1]
                self.assertEqual(parameters[1], action)
                self.assertEqual(parameters[3], 42)

    def test_technician_update_uses_account_id_after_ownership_check(self):
        self.cursor.fetchone.side_effect = [ticket(), dict(role='Technician', status='Active', technician_id=12),
                                           dict(technician_id=12, full_name='Mark Santos')]
        tickets.update_ticket_for_user(7, 84, {'status': 'Resolved', 'priority': 'High'}, session_technician_id=12)
        parameters = [params for _, params in self.history_writes()]
        self.assertEqual(parameters, [(7, 'Status Changed', 'Assigned -> Resolved', 84),
                                      (7, 'Priority Changed', 'Medium -> High', 84)])

    def test_rejected_technician_update_and_noop_admin_update_add_no_history(self):
        self.cursor.fetchone.side_effect = [ticket(), dict(role='Technician', status='Active', technician_id=35),
                                           dict(technician_id=35, full_name='Anna Reyes')]
        with self.assertRaisesRegex(tickets.TicketUpdateError, 'only update tickets assigned to you'):
            tickets.update_ticket_for_user(7, 84, {'status': 'Resolved'}, session_technician_id=35)
        self.assertEqual(self.history_writes(), [])
        self.cursor.fetchone.side_effect = [ticket(), dict(role='Admin', status='Active')]
        self.assertFalse(tickets.update_ticket_for_user(7, 42, {'priority': 'Medium'}))
        self.assertEqual(self.history_writes(), [])
        self.connection.commit.assert_not_called()

    def test_failed_history_insert_rolls_back_create_and_update_without_false_success(self):
        for action in ('create', 'update'):
            for errno in (1054, 1146, 1452, 2003):
                with self.subTest(action=action, errno=errno):
                    self.cursor.execute.reset_mock()
                    self.connection.rollback.reset_mock()
                    self.connection.commit.reset_mock()
                    self.cursor.fetchone.side_effect = ([("Admin", "Active")] if action == 'create' else
                                                       [ticket(), dict(role='Admin', status='Active')])

                    def execute(query, parameters):
                        if query.startswith('INSERT INTO helpdesk.ticket_history'):
                            raise mysql.connector.Error('private database detail', errno=errno)

                    self.cursor.execute.side_effect = execute
                    error_type = tickets.TicketCreateError if action == 'create' else tickets.TicketUpdateError
                    with self.assertRaises(error_type) as raised:
                        if action == 'create':
                            tickets.create_ticket_for_user(42, *VALUES)
                        else:
                            tickets.update_ticket_for_user(7, 42, {'priority': 'High'})
                    self.assertNotIn('private', str(raised.exception))
                    if errno == 1054:
                        self.assertIn('setup_history_user_attribution.py', str(raised.exception))
                    self.connection.rollback.assert_called_once_with()
                    self.connection.commit.assert_not_called()


class SessionAttributionTests(unittest.TestCase):
    def test_login_switches_always_use_current_account_id(self):
        old_permissions = None
        for role, user_id in (('Admin', 42), ('Technician', 84), ('Admin', 105)):
            dialog = dialog_without_window()
            dialog.user = dict(user_id=user_id, role=role, status='Active', technician_id=12)
            dialog.permissions = SessionPermissions(dialog.user)
            with patch.object(create_gui, 'create_ticket_for_user', return_value=91) as save:
                dialog._save_ticket(VALUES)
            save.assert_called_once_with(user_id, *VALUES)
            self.assertEqual(dialog._results.get_nowait(), (91, None))
            if old_permissions is not None:
                self.assertFalse(old_permissions.allows('create_ticket'))
            dialog.permissions.revoke()
            old_permissions = dialog.permissions

    def test_logout_revokes_create_handler_and_worker_even_for_open_dialog(self):
        dialog = dialog_without_window()
        dialog.permissions.revoke()
        with patch.object(create_gui, 'create_ticket_for_user') as save, \
             patch.object(create_gui, 'Thread') as worker, patch.object(create_gui.messagebox, 'showinfo') as message:
            dialog.save()
            dialog._save_ticket(VALUES)
        worker.assert_not_called()
        save.assert_not_called()
        message.assert_called_once()
        self.assertEqual(dialog._results.get_nowait(), (None, create_gui.PERMISSION_DENIED))

    def test_missing_gui_identity_never_creates_a_cli_event(self):
        dialog = dialog_without_window()
        dialog.user = None
        with patch.object(create_gui, 'create_ticket_for_user') as save:
            dialog._save_ticket(VALUES)
        save.assert_not_called()
        self.assertEqual(dialog._results.get_nowait(), (None, create_gui.PERMISSION_DENIED))


class HistoryReadAttributionTests(unittest.TestCase):
    def test_left_join_keeps_legacy_and_inactive_authors_and_uses_ids_not_names(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute("ATTACH DATABASE ':memory:' AS helpdesk")
            db.execute('CREATE TABLE helpdesk.users (user_id INT PRIMARY KEY, full_name TEXT, role TEXT, status TEXT)')
            db.execute('CREATE TABLE helpdesk.ticket_history '
                       '(history_id INT, ticket_id INT, action TEXT, details TEXT, created_at TEXT, performed_by_user_id INT)')
            db.executemany('INSERT INTO helpdesk.users VALUES (?, ?, ?, ?)',
                           [(42, 'Same Name', 'Admin', 'Active'), (84, 'Same Name', 'Technician', 'Inactive')])
            db.executemany('INSERT INTO helpdesk.ticket_history VALUES (?, ?, ?, ?, ?, ?)',
                           [(2, 7, 'Priority Changed', 'Medium -> High', '2026-10-06 10:00', 84),
                            (1, 7, 'Ticket Created', 'Defaults', '2026-10-06 10:00', None),
                            (3, 7, 'Status Changed', 'Assigned -> Resolved', '2026-10-06 11:00', 42),
                            (4, 9, 'Ticket Created', 'Other ticket', '2026-10-06 10:00', 42)])
            db.row_factory = sqlite3.Row
            connection = MagicMock()
            connection.__enter__.return_value = connection
            cursor = connection.cursor.return_value.__enter__.return_value
            result = []

            def execute(sql, params):
                result[:] = [dict(row) for row in db.execute(sql.replace('%s', '?'), params)]

            cursor.execute.side_effect = execute
            cursor.fetchall.side_effect = lambda: result
            with patch.object(history, 'get_connection', return_value=connection):
                entries = history.get_ticket_history(7)
            self.assertEqual([row['history_id'] for row in entries], [1, 2, 3])
            self.assertEqual([history_gui.history_row_values(row)[3] for row in entries],
                             ['System / Legacy', 'Same Name (Technician)', 'Same Name (Admin)'])
            query = cursor.execute.call_args.args[0]
            self.assertNotIn('password', query.lower())
            connection.commit.assert_not_called()

    def test_missing_author_and_control_characters_are_displayed_safely(self):
        entry = dict(performed_by_user_id=84, performed_by_full_name='Mark\nSantos', performed_by_role='Technician')
        self.assertEqual(history_gui.history_row_values(entry)[3], 'Mark\\nSantos (Technician)')
        for missing in ({}, {'performed_by_user_id': None}, {'performed_by_user_id': 42,
                        'performed_by_full_name': None, 'performed_by_role': None}):
            self.assertEqual(history_gui.history_row_values(missing)[3], 'System / Legacy')

    def test_missing_migration_read_error_identifies_the_correct_setup(self):
        with patch.object(history, 'get_connection', side_effect=mysql.connector.Error('private', errno=1054)):
            with self.assertRaisesRegex(history.TicketHistoryReadError, 'setup_history_user_attribution.py') as raised:
                history.get_ticket_history(7)
        self.assertNotIn('private', str(raised.exception))

    def test_invalid_activity_user_ids_never_reach_sql(self):
        cursor = MagicMock()
        for user_id in (True, 0, -1, '42 OR 1=1', 2147483648):
            for operation in (lambda: history.record_ticket_created(cursor, 7, user_id),
                              lambda: history.record_ticket_updated(cursor, 7, ticket(), ticket(), user_id)):
                with self.subTest(user_id=user_id), self.assertRaises(ValueError):
                    operation()
        cursor.execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
