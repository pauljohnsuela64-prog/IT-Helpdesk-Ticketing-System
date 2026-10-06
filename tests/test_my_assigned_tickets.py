"""Assignment scope and search using mocked connections and an in-memory SQL fixture."""
import sqlite3
import unittest
from unittest.mock import MagicMock, patch

import mysql.connector
import ticket_repository as repository
from technician_repository import TechnicianReadError
from test_gui_app import ticket


class AssignedTicketRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchall.return_value = [ticket()]
        self.connect = patch.object(repository, 'get_connection', return_value=self.connection)
        self.connect_mock = self.connect.start()
        self.addCleanup(self.connect.stop)
        self.lookup = patch.object(repository, 'get_technician', return_value={
            'technician_id': 12, 'full_name': "Mark O'Santos", 'status': 'Active',
        })
        self.lookup_mock = self.lookup.start()
        self.addCleanup(self.lookup.stop)

    def test_resolves_record_by_id_then_binds_assignment_id(self):
        self.assertEqual(repository.get_assigned_tickets(12), [ticket()])
        self.lookup_mock.assert_called_once_with(12)
        query, params = self.cursor.execute.call_args.args
        self.assertIn('FROM helpdesk.tickets WHERE assigned_technician_id = %s ORDER BY ticket_id', query)
        self.assertNotIn('LIKE', query)
        self.assertNotIn("Mark O'Santos", query)
        self.assertEqual(params, (12,))
        self.connection.commit.assert_not_called()

    def test_search_is_parameterized_case_insensitive_and_parenthesized_inside_scope(self):
        repository.get_assigned_tickets(12, ' 3 ')
        query, params = self.cursor.execute.call_args.args
        self.assertIn('WHERE assigned_technician_id = %s AND (', query)
        self.assertTrue(query.endswith(") ORDER BY ticket_id"))
        self.assertEqual(query.count('LIKE LOWER(%s)'), 8)
        self.assertEqual(params, (12,) + ('%3%',) * 8)
        self.assertTrue(query.startswith('SELECT '))
        self.connection.commit.assert_not_called()

    def test_wildcards_and_sql_text_remain_literal_bound_values(self):
        for term, pattern in (('  50%_!  ', '%50!%!_!!%'), ("' OR 1=1 --", "%' OR 1=1 --%")):
            repository.get_assigned_tickets(12, term)
            query, params = self.cursor.execute.call_args.args
            self.assertEqual(params, (12,) + (pattern,) * 8)
            self.assertNotIn(term.strip(), query)
            self.assertEqual(query.count("ESCAPE '!'"), 8)

    def test_blank_search_returns_full_assignment_list(self):
        for term in ('', ' \t '):
            repository.get_assigned_tickets(12, term)
            self.assertNotIn('AND (', self.cursor.execute.call_args.args[0])
            self.assertEqual(self.cursor.execute.call_args.args[1], (12,))

    def test_missing_or_malformed_session_link_has_friendly_feedback_without_querying(self):
        for ident in (None, True, '12', 0, -1, 2147483648, '12 OR 1=1'):
            with self.assertRaises(repository.AssignedTicketsLinkError) as caught:
                repository.get_assigned_tickets(ident)
            self.assertEqual(str(caught.exception), repository.MISSING_TECHNICIAN_LINK)
        self.lookup_mock.assert_not_called()
        self.connect_mock.assert_not_called()

    def test_nonexistent_or_invalid_record_does_not_fall_back_to_all_tickets(self):
        for row in (None, {'technician_id': 12, 'full_name': ''}):
            self.lookup_mock.return_value = row
            with self.assertRaises(repository.AssignedTicketsLinkError):
                repository.get_assigned_tickets(12)
        self.connect_mock.assert_not_called()

    def test_inactive_record_keeps_assigned_tickets_viewable(self):
        self.lookup_mock.return_value['status'] = 'Inactive'
        self.assertEqual(repository.get_assigned_tickets(12), [ticket()])
        self.assertNotIn('Active', self.cursor.execute.call_args.args[1])

    def test_lookup_and_ticket_read_errors_are_safe_and_distinct_from_missing_link(self):
        self.lookup_mock.side_effect = TechnicianReadError('Unable to retrieve technicians.')
        with self.assertRaises(repository.TicketReadError) as caught:
            repository.get_assigned_tickets(12)
        self.assertNotIsInstance(caught.exception, repository.AssignedTicketsLinkError)
        self.connect_mock.assert_not_called()
        self.lookup_mock.side_effect = None
        for error in (mysql.connector.Error('private server details', errno=2003), ValueError('private settings')):
            self.connect_mock.side_effect = error
            with self.assertRaises(repository.TicketReadError) as caught:
                repository.get_assigned_tickets(12, '3')
            self.assertNotIn('private', str(caught.exception))


class AssignmentSearchSqlTests(unittest.TestCase):
    def setUp(self):
        # Exercise actual search SQL against disposable memory, never live MySQL.
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.execute("ATTACH DATABASE ':memory:' AS helpdesk")
        self.db.execute('CREATE TABLE helpdesk.tickets ('
                        'ticket_id INTEGER, employee_name TEXT, department TEXT, category TEXT, '
                        'subject TEXT, priority TEXT, status TEXT, assigned_to TEXT, created_at TEXT, assigned_technician_id INTEGER)')
        self.db.executemany('INSERT INTO helpdesk.tickets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', [
            (3, 'Alice Reyes', 'Finance', 'Hardware', 'PC 3', 'High', 'In Progress', 'Mark Santos', None, 12),
            (8, 'Bob Reyes', 'IT', 'Printer', 'Printer 50%_!', 'Medium', 'Resolved', 'Mark Santos', None, 12),
            (33, 'Alice Reyes', 'Finance', 'Hardware', 'PC 3', 'High', 'In Progress', 'Anna Reyes', None, 35),
            (34, 'Alice Reyes', 'Finance', 'Hardware', 'PC 3', 'High', 'Open', None, None, None),
            (35, 'Alice Reyes', 'Finance', 'Hardware', 'PC 3', 'High', 'Assigned', 'Mark Santos Jr', None, 36),
        ])

        def read(query, params):
            self.assertTrue(query.startswith('SELECT '))
            return [dict(row) for row in self.db.execute(query.replace('%s', '?'), params).fetchall()]

        self.reader = patch.object(repository, '_read_tickets', side_effect=read)
        self.reader.start()
        self.addCleanup(self.reader.stop)
        self.technician = {'technician_id': 12, 'full_name': 'Mark Santos', 'status': 'Active'}
        self.lookup = patch.object(repository, 'get_technician', return_value=self.technician)
        self.lookup.start()
        self.addCleanup(self.lookup.stop)

    def ids(self, term=''):
        return [row['ticket_id'] for row in repository.get_assigned_tickets(12, term)]

    def test_own_list_excludes_other_technicians_unassigned_and_partial_names(self):
        self.assertEqual(self.ids(), [3, 8])

    def test_each_search_field_and_numeric_ids_remain_within_assignment_scope(self):
        for term in ('3', 'alice', 'FINANCE', 'hardware', 'PC 3', 'HIGH', 'in progress'):
            with self.subTest(term=term):
                self.assertEqual(self.ids(term), [3])
        self.assertEqual(self.ids('Mark Santos'), [3, 8])
        self.assertEqual(self.ids('Anna Reyes'), [])
        self.assertEqual(self.ids('33'), [])

    def test_clear_and_literal_wildcards_preserve_scope(self):
        self.assertEqual(self.ids('PRINTER'), [8])
        self.assertEqual(self.ids('50%_!'), [8])
        self.assertEqual(self.ids('%'), [8])
        self.assertEqual(self.ids("' OR 1=1 --"), [])
        self.assertEqual(self.ids(' \t '), [3, 8])

    def test_refresh_reflects_reassignment_and_unassignment(self):
        self.db.execute('UPDATE helpdesk.tickets SET assigned_to = ?, assigned_technician_id = ? WHERE ticket_id = ?', ('Anna Reyes', 35, 3))
        self.assertEqual(self.ids(), [8])
        self.db.execute('UPDATE helpdesk.tickets SET assigned_to = NULL, assigned_technician_id = NULL WHERE ticket_id = 8')
        self.assertEqual(self.ids(), [])

    def test_ids_distinguish_records_even_with_the_same_name(self):
        self.db.execute('UPDATE helpdesk.tickets SET assigned_to = ? WHERE ticket_id = ?', ('Mark Santos', 33))
        self.technician['technician_id'] = 35
        self.assertEqual([row['ticket_id'] for row in repository.get_assigned_tickets(35)], [33])


if __name__ == '__main__':
    unittest.main()
