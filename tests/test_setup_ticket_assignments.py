"""Repeatable, scoped assignment migration and conservative legacy backfill."""
from contextlib import redirect_stdout
import io
import sqlite3
import unicodedata
import unittest
from unittest.mock import MagicMock, patch

import mysql.connector
import setup_ticket_assignments as setup


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.state = {'column': False, 'index': False, 'foreign': False}
        self.integer = {'DATA_TYPE': 'int', 'COLUMN_TYPE': 'int', 'IS_NULLABLE': 'YES'}
        self.target = self.integer.copy()
        self.current = None
        self.rows = []
        self.ddl = []

        def execute(sql, params=None):
            self.rows = []
            if 'GET_LOCK' in sql:
                self.current = {'acquired': 1}
            elif 'information_schema.COLUMNS' in sql:
                if params[1] == 'technicians':
                    self.current = self.target
                elif params[2] == 'ticket_id':
                    self.current = self.integer
                else:
                    self.current = self.integer if self.state['column'] else None
            elif 'information_schema.STATISTICS' in sql:
                self.rows = [{'INDEX_NAME': 'existing_index'}] if self.state['index'] else []
            elif 'information_schema.KEY_COLUMN_USAGE' in sql:
                self.rows = [{'REFERENCED_TABLE_SCHEMA': 'helpdesk', 'REFERENCED_TABLE_NAME': 'technicians',
                              'REFERENCED_COLUMN_NAME': 'technician_id'}] if self.state['foreign'] else []
            elif sql.startswith('ALTER TABLE helpdesk.tickets'):
                self.ddl.append(sql)
                self.state['column' if 'ADD COLUMN' in sql else 'index' if 'ADD KEY' in sql else 'foreign'] = True
            elif sql == setup.BACKFILL_SQL:
                pass
            elif 'COUNT(*) AS unresolved' in sql:
                self.current = {'unresolved': 2}
            elif 'RELEASE_LOCK' not in sql:
                raise AssertionError('Unexpected setup SQL.')
        self.execute = execute
        self.cursor.execute.side_effect = execute
        self.cursor.fetchone.side_effect = lambda: self.current
        self.cursor.fetchall.side_effect = lambda: self.rows

    def test_setup_is_idempotent_and_scoped_to_helpdesk_ticket_relationship(self):
        self.assertEqual(setup.migrate_ticket_assignments(self.connection), 2)
        self.assertEqual(setup.migrate_ticket_assignments(self.connection), 2)
        self.assertEqual(len(self.ddl), 3)
        self.assertIn('assigned_technician_id INT NULL', self.ddl[0])
        self.assertIn('idx_tickets_assigned_technician', self.ddl[1])
        self.assertIn('REFERENCES helpdesk.technicians (technician_id)', self.ddl[2])
        self.assertIn('ON DELETE RESTRICT ON UPDATE RESTRICT', self.ddl[2])
        for call in self.cursor.execute.call_args_list:
            sql = call.args[0]
            self.assertTrue(sql.startswith(('SELECT', 'ALTER TABLE helpdesk.tickets', 'UPDATE helpdesk.tickets')))
            if 'information_schema' in sql:
                self.assertEqual(call.args[1][0], 'helpdesk')
        self.assertEqual(self.connection.commit.call_count, 2)

    def test_existing_equivalent_constraints_are_reused(self):
        self.state.update(column=True, index=True, foreign=True)
        setup.migrate_ticket_assignments(self.connection)
        self.assertEqual(self.ddl, [])

    def test_unsigned_id_type_is_matched(self):
        self.target['COLUMN_TYPE'] = 'int unsigned'
        setup.migrate_ticket_assignments(self.connection)
        self.assertIn('INT UNSIGNED NULL', self.ddl[0])

    def test_missing_table_and_incompatible_existing_column_are_refused(self):
        self.target = None
        with self.assertRaises(setup.TicketAssignmentSetupError):
            setup.migrate_ticket_assignments(self.connection)
        self.assertEqual(self.ddl, [])
        self.target = self.integer
        self.state['column'] = True
        self.integer['IS_NULLABLE'] = 'NO'
        with self.assertRaises(setup.TicketAssignmentSetupError):
            setup.migrate_ticket_assignments(self.connection)
        self.assertEqual(self.ddl, [])

    def test_incompatible_foreign_key_is_not_replaced(self):
        self.state.update(column=True, index=True, foreign=True)
        def incompatible(sql, params=None):
            self.execute(sql, params)
            if 'KEY_COLUMN_USAGE' in sql:
                self.rows[0]['REFERENCED_TABLE_SCHEMA'] = 'unexpected_schema'
        self.cursor.execute.side_effect = incompatible
        with self.assertRaises(setup.TicketAssignmentSetupError):
            setup.migrate_ticket_assignments(self.connection)
        self.assertEqual(self.ddl, [])

    def test_partial_setup_can_be_rerun_after_ddl_failure(self):
        def fail_index(sql, params=None):
            if 'ADD KEY' in sql:
                raise mysql.connector.Error(errno=1142)
            self.execute(sql, params)
        self.cursor.execute.side_effect = fail_index
        with self.assertRaises(mysql.connector.Error):
            setup.migrate_ticket_assignments(self.connection)
        self.assertTrue(self.state['column'])
        self.cursor.execute.side_effect = self.execute
        setup.migrate_ticket_assignments(self.connection)
        self.assertEqual(len(self.ddl), 3)
        self.assertTrue(any('RELEASE_LOCK' in call.args[0] for call in self.cursor.execute.call_args_list))

    def test_busy_setup_does_not_change_schema_or_data(self):
        self.cursor.execute.side_effect = None
        self.cursor.fetchone.side_effect = None
        self.cursor.fetchone.return_value = {'acquired': 0}
        with self.assertRaises(setup.TicketAssignmentSetupError):
            setup.migrate_ticket_assignments(self.connection)
        self.assertEqual(self.cursor.execute.call_count, 1)
        self.connection.commit.assert_not_called()

    def test_failed_backfill_rolls_back_and_rerun_reuses_completed_schema(self):
        def fail_backfill(sql, params=None):
            if sql == setup.BACKFILL_SQL:
                raise mysql.connector.Error(errno=1267)
            self.execute(sql, params)
        self.cursor.execute.side_effect = fail_backfill
        with self.assertRaises(mysql.connector.Error):
            setup.migrate_ticket_assignments(self.connection)
        self.connection.rollback.assert_called_once()
        self.connection.commit.assert_not_called()
        self.assertEqual(self.state, {'column': True, 'index': True, 'foreign': True})
        self.assertIn('RELEASE_LOCK', self.cursor.execute.call_args.args[0])
        self.cursor.execute.side_effect = self.execute
        setup.migrate_ticket_assignments(self.connection)
        self.assertEqual(len(self.ddl), 3)
        self.connection.commit.assert_called_once()


class LegacyBackfillTests(unittest.TestCase):
    def test_only_unique_names_are_linked_and_existing_ids_and_display_fields_are_preserved(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute("ATTACH DATABASE ':memory:' AS helpdesk")
        def name_key(value):
            return ''.join(char for char in unicodedata.normalize('NFD', value.casefold())
                           if not unicodedata.combining(char))
        def compare_names(left, right):
            left, right = name_key(left), name_key(right)
            return (left > right) - (left < right)
        # Approximate the chosen MySQL collation for in-memory predicate checks.
        # Raw columns deliberately have different comparison rules.
        db.create_collation('utf8mb4_unicode_ci', compare_names)
        db.execute('CREATE TABLE helpdesk.technicians (technician_id INT, full_name TEXT COLLATE BINARY, status TEXT)')
        db.execute("CREATE TABLE helpdesk.tickets (ticket_id INT, assigned_to TEXT COLLATE NOCASE, assigned_technician_id INT, status TEXT, updated_at TEXT DEFAULT 'original time')")
        db.executemany('INSERT INTO helpdesk.technicians VALUES (?, ?, ?)', [
            (12, 'Unique Technician', 'Active'), (35, 'Same Name', 'Active'),
            (36, 'same name', 'Inactive'), (40, 'Inactive Unique', 'Inactive'),
            (50, 'José Reyes', 'Active'), (51, 'Jose Reyes', 'Inactive'), (52, 'María Flores', 'Active'),
        ])
        db.executemany('INSERT INTO helpdesk.tickets (ticket_id, assigned_to, assigned_technician_id, status) VALUES (?, ?, ?, ?)', [
            (1, 'Unique Technician', None, 'Open'), (2, 'Same Name', None, 'Assigned'),
            (3, 'Legacy Unknown', None, 'Closed'), (4, None, None, 'Open'),
            (5, 'Unique Technician', 35, 'In Progress'), (6, 'Inactive Unique', None, 'Resolved'),
            (7, 'JOSE REYES', None, 'Assigned'), (8, 'MARIA FLORES', None, 'Assigned'),
        ])
        # Translate only MySQL UPDATE JOIN syntax; use the real grouping/predicates.
        sql = setup.BACKFILL_SQL.replace('UPDATE helpdesk.tickets AS ticket JOIN (',
            'UPDATE helpdesk.tickets AS ticket SET assigned_technician_id = technician.technician_id, updated_at = ticket.updated_at FROM (')
        sql = sql.replace('ON technician.match_name = CONVERT(ticket.assigned_to USING utf8mb4) COLLATE utf8mb4_unicode_ci '
                          'SET ticket.assigned_technician_id = technician.technician_id, ticket.updated_at = ticket.updated_at WHERE ',
                          'WHERE technician.match_name = CONVERT(ticket.assigned_to USING utf8mb4) COLLATE utf8mb4_unicode_ci AND ')
        sql = sql.replace('CONVERT(full_name USING utf8mb4)', 'full_name')
        sql = sql.replace('CONVERT(ticket.assigned_to USING utf8mb4)', 'ticket.assigned_to')
        db.execute(sql)
        first = db.execute('SELECT * FROM helpdesk.tickets ORDER BY ticket_id').fetchall()
        db.execute(sql)
        self.assertEqual(db.execute('SELECT * FROM helpdesk.tickets ORDER BY ticket_id').fetchall(), first)
        self.assertEqual(first, [
            (1, 'Unique Technician', 12, 'Open', 'original time'), (2, 'Same Name', None, 'Assigned', 'original time'),
            (3, 'Legacy Unknown', None, 'Closed', 'original time'), (4, None, None, 'Open', 'original time'),
            (5, 'Unique Technician', 35, 'In Progress', 'original time'), (6, 'Inactive Unique', 40, 'Resolved', 'original time'),
            (7, 'JOSE REYES', None, 'Assigned', 'original time'), (8, 'MARIA FLORES', 52, 'Assigned', 'original time'),
        ])

    def test_sql_normalizes_both_sides_and_groups_by_the_same_comparison_key(self):
        sql = setup.BACKFILL_SQL
        self.assertIn('CONVERT(full_name USING utf8mb4) COLLATE utf8mb4_unicode_ci AS match_name', sql)
        self.assertIn('GROUP BY match_name HAVING COUNT(*) = 1', sql)
        self.assertIn('technician.match_name = CONVERT(ticket.assigned_to USING utf8mb4) COLLATE utf8mb4_unicode_ci', sql)
        self.assertNotIn('GROUP BY full_name', sql)


class SetupCommandTests(unittest.TestCase):
    def test_success_reports_unresolved_names_and_uses_existing_private_connection_helper(self):
        for administrator in (False, True):
            output = io.StringIO()
            with patch.object(setup, '_setup_connection') as connect, \
                 patch.object(setup, 'migrate_ticket_assignments', return_value=2) as migrate, redirect_stdout(output):
                self.assertEqual(setup.main(['--admin'] if administrator else []), 0)
            connect.assert_called_once_with(administrator)
            migrate.assert_called_once_with(connect.return_value.__enter__.return_value)
            self.assertIn('2 legacy assignments', output.getvalue())
            self.assertIn('helpdesk.tickets', output.getvalue())

    def test_schema_connection_hidden_input_and_interruption_errors_are_safe(self):
        for error in (setup.TicketAssignmentSetupError('Incompatible column.'), ValueError('private details'),
                      mysql.connector.Error('private details', errno=1142), setup.getpass.GetPassWarning(), KeyboardInterrupt()):
            output = io.StringIO()
            with patch.object(setup, '_setup_connection', side_effect=error), redirect_stdout(output):
                self.assertEqual(setup.main([]), 1)
            self.assertNotIn('private details', output.getvalue())

    def test_permission_and_collation_errors_have_distinct_actionable_messages(self):
        for errno, message in ((1142, 'privilege'), (1143, 'privilege'), (1267, 'collations')):
            output = io.StringIO()
            with patch.object(setup, '_setup_connection', side_effect=mysql.connector.Error('private details', errno=errno)), \
                 redirect_stdout(output):
                self.assertEqual(setup.main([]), 1)
            self.assertIn(message, output.getvalue())
            self.assertNotIn('private details', output.getvalue())
            if errno == 1267:
                self.assertNotIn('ALTER/INDEX', output.getvalue())
                self.assertIn('updated setup_ticket_assignments.py', output.getvalue())


if __name__ == '__main__':
    unittest.main()
