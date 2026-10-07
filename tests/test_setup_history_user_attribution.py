"""Idempotence, partial-setup recovery, and safeguards without live DDL."""
from contextlib import redirect_stdout
import getpass
import io
import unittest
from unittest.mock import MagicMock, patch

import mysql.connector

import setup_history_user_attribution as setup


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.state = {'column': False, 'index': False, 'foreign': False}
        self.integer = dict(DATA_TYPE='int', COLUMN_TYPE='int', IS_NULLABLE='YES')
        self.target = self.integer.copy()
        self.current = self.integer.copy()
        self.history_exists = True
        self.acquired = 1
        self.foreign = dict(REFERENCED_TABLE_SCHEMA='helpdesk', REFERENCED_TABLE_NAME='users',
                            REFERENCED_COLUMN_NAME='user_id', DELETE_RULE='SET NULL', COLUMN_COUNT=1)
        self.rows = []
        self.row = None
        self.ddl = []
        self.fail_on = None

        def execute(sql, parameters=None):
            self.rows = []
            if 'GET_LOCK' in sql:
                self.row = {'acquired': self.acquired}
            elif 'information_schema.COLUMNS' in sql:
                if parameters[1] == 'users':
                    self.row = self.target
                elif parameters[2] == 'history_id':
                    self.row = self.integer if self.history_exists else None
                else:
                    self.row = self.current if self.state['column'] else None
            elif 'information_schema.KEY_COLUMN_USAGE' in sql:
                self.rows = [self.foreign] if self.state['foreign'] else []
            elif 'information_schema.STATISTICS' in sql:
                self.rows = [{'INDEX_NAME': 'existing_index'}] if self.state['index'] else []
            elif sql.startswith('ALTER TABLE helpdesk.ticket_history'):
                stage = 'column' if 'ADD COLUMN' in sql else 'index' if 'ADD KEY' in sql else 'foreign'
                if self.fail_on == stage:
                    raise mysql.connector.Error('private server details', errno=1142)
                self.ddl.append(sql)
                self.state[stage] = True
            elif 'RELEASE_LOCK' not in sql:
                raise AssertionError('Unexpected migration query.')

        self.cursor.execute.side_effect = execute
        self.cursor.fetchone.side_effect = lambda: self.row
        self.cursor.fetchall.side_effect = lambda: self.rows

    def test_two_runs_preserve_data_and_add_only_history_column_index_and_set_null_fk(self):
        setup.migrate_history_user_attribution(self.connection)
        setup.migrate_history_user_attribution(self.connection)
        self.assertEqual(len(self.ddl), 3)
        self.assertIn('performed_by_user_id INT NULL', self.ddl[0])
        self.assertIn('ADD KEY idx_ticket_history_performed_by_user (performed_by_user_id)', self.ddl[1])
        self.assertIn('REFERENCES helpdesk.users (user_id) ON DELETE SET NULL ON UPDATE RESTRICT', self.ddl[2])
        for call in self.cursor.execute.call_args_list:
            sql = call.args[0]
            self.assertTrue(sql.startswith(('SELECT', 'ALTER TABLE helpdesk.ticket_history')))
            self.assertNotIn('DROP ', sql)
            self.assertNotIn('MODIFY ', sql)
            if 'information_schema' in sql:
                self.assertEqual(call.args[1][0], 'helpdesk')
        self.connection.commit.assert_not_called()  # No data transaction or backfill.

    def test_existing_compatible_column_index_and_constraint_are_reused(self):
        self.state.update(column=True, index=True, foreign=True)
        setup.migrate_history_user_attribution(self.connection)
        self.assertEqual(self.ddl, [])

    def test_partial_ddl_failure_can_be_rerun_without_duplicate_column(self):
        self.fail_on = 'foreign'
        with self.assertRaises(mysql.connector.Error):
            setup.migrate_history_user_attribution(self.connection)
        self.assertEqual(len(self.ddl), 2)
        self.cursor.execute.assert_any_call('SELECT RELEASE_LOCK(%s)', (setup.LOCK_NAME,))
        self.fail_on = None
        setup.migrate_history_user_attribution(self.connection)
        self.assertEqual(len(self.ddl), 3)

    def test_unsigned_user_id_is_matched(self):
        self.target['COLUMN_TYPE'] = 'int unsigned'
        setup.migrate_history_user_attribution(self.connection)
        self.assertIn('INT UNSIGNED NULL', self.ddl[0])

    def test_missing_tables_and_incompatible_target_type_are_refused_before_ddl(self):
        for target, history_exists in ((None, True), (self.integer, False),
                                       ({**self.integer, 'DATA_TYPE': 'bigint'}, True)):
            with self.subTest(target=target, history_exists=history_exists):
                self.target = target
                self.history_exists = history_exists
                with self.assertRaises(setup.HistoryAttributionSetupError):
                    setup.migrate_history_user_attribution(self.connection)
                self.assertEqual(self.ddl, [])

    def test_incompatible_existing_column_is_never_rewritten(self):
        self.state['column'] = True
        for current in ({**self.integer, 'IS_NULLABLE': 'NO'}, {**self.integer, 'DATA_TYPE': 'varchar'},
                        {**self.integer, 'COLUMN_TYPE': 'int unsigned'}):
            with self.subTest(current=current):
                self.current = current
                with self.assertRaisesRegex(setup.HistoryAttributionSetupError, 'column is incompatible'):
                    setup.migrate_history_user_attribution(self.connection)
                self.assertEqual(self.ddl, [])

    def test_wrong_existing_foreign_key_or_deletion_rule_is_refused_without_dropping_it(self):
        self.state.update(column=True, index=True, foreign=True)
        for change in ({'REFERENCED_TABLE_SCHEMA': 'unexpected_schema'}, {'REFERENCED_TABLE_NAME': 'technicians'},
                       {'REFERENCED_COLUMN_NAME': 'technician_id'}, {'DELETE_RULE': 'CASCADE'},
                       {'DELETE_RULE': 'RESTRICT'}, {'COLUMN_COUNT': 2}):
            with self.subTest(change=change):
                saved = self.foreign
                self.foreign = {**saved, **change}
                with self.assertRaisesRegex(setup.HistoryAttributionSetupError, 'foreign key is incompatible'):
                    setup.migrate_history_user_attribution(self.connection)
                self.foreign = saved
                self.assertEqual(self.ddl, [])

    def test_busy_lock_causes_no_schema_queries_or_changes(self):
        self.acquired = 0
        with self.assertRaisesRegex(setup.HistoryAttributionSetupError, 'busy'):
            setup.migrate_history_user_attribution(self.connection)
        self.cursor.execute.assert_called_once_with('SELECT GET_LOCK(%s, %s) AS acquired', (setup.LOCK_NAME, 5))
        self.assertEqual(self.ddl, [])


class SetupCommandTests(unittest.TestCase):
    def test_normal_and_admin_modes_reuse_secure_helpdesk_connection_helper(self):
        for argv, administrator in (([], False), (['--admin'], True)):
            with self.subTest(argv=argv), patch.object(setup, '_setup_connection') as connect, \
                 patch.object(setup, 'migrate_history_user_attribution') as migrate, redirect_stdout(io.StringIO()) as output:
                self.assertEqual(setup.main(argv), 0)
                connect.assert_called_once_with(administrator)
                migrate.assert_called_once_with(connect.return_value.__enter__.return_value)
                self.assertIn('Existing history was preserved.', output.getvalue())

    def test_setup_failures_are_friendly_and_hide_database_details(self):
        for failure in (ValueError('private config'), mysql.connector.Error('private server detail', errno=1142),
                        mysql.connector.Error('private server detail', errno=1215),
                        setup.HistoryAttributionSetupError('Existing column is incompatible.'),
                        getpass.GetPassWarning('private console details'), EOFError(), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__), \
                 patch.object(setup, '_setup_connection', side_effect=failure), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(setup.main([]), 1)
                self.assertNotIn('private', output.getvalue())
                if isinstance(failure, mysql.connector.Error):
                    self.assertIn('winpty .venv/Scripts/python.exe setup_history_user_attribution.py --admin', output.getvalue())


if __name__ == '__main__':
    unittest.main()
