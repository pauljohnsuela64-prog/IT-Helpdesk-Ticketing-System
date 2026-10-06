"""Idempotent, scoped migration and private administrator setup input."""
from contextlib import redirect_stdout
import getpass
import io
import os
import secrets
import unittest
from unittest.mock import MagicMock, patch

import mysql.connector
import setup_user_technicians as setup


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.state = {'column': False, 'unique': False, 'foreign': False}
        self.int_column = {'DATA_TYPE': 'int', 'COLUMN_TYPE': 'int', 'IS_NULLABLE': 'YES'}
        self.target = self.int_column.copy()
        self.current = None
        self.rows = []
        self.ddl = []

        def execute(sql, params=None):
            if 'GET_LOCK' in sql:
                self.current = {'acquired': 1}
            elif 'information_schema.COLUMNS' in sql:
                table, column = params[1:]
                if table == 'technicians':
                    self.current = self.target
                elif column == 'user_id':
                    self.current = self.int_column
                else:
                    self.current = self.int_column if self.state['column'] else None
            elif 'information_schema.STATISTICS' in sql:
                self.rows = [{'INDEX_NAME': 'existing_unique'}] if self.state['unique'] else []
            elif 'information_schema.KEY_COLUMN_USAGE' in sql:
                self.rows = [{'REFERENCED_TABLE_SCHEMA': 'helpdesk', 'REFERENCED_TABLE_NAME': 'technicians',
                              'REFERENCED_COLUMN_NAME': 'technician_id'}] if self.state['foreign'] else []
            elif sql.startswith('ALTER'):
                self.ddl.append(sql)
                key = 'column' if 'ADD COLUMN' in sql else 'unique' if 'ADD UNIQUE' in sql else 'foreign'
                self.state[key] = True
            elif 'RELEASE_LOCK' not in sql:
                raise AssertionError('Unexpected migration SQL.')

        self.execute = execute
        self.cursor.execute.side_effect = execute
        self.cursor.fetchone.side_effect = lambda: self.current
        self.cursor.fetchall.side_effect = lambda: self.rows

    def test_migration_is_idempotent_and_preserves_existing_rows(self):
        setup.migrate_user_technicians(self.connection)
        setup.migrate_user_technicians(self.connection)
        self.assertEqual(len(self.ddl), 3)
        self.assertEqual(self.ddl[0], 'ALTER TABLE helpdesk.users ADD COLUMN technician_id INT NULL')
        self.assertIn('UNIQUE KEY uq_users_technician_id (technician_id)', self.ddl[1])
        self.assertIn('REFERENCES helpdesk.technicians (technician_id)', self.ddl[2])
        self.assertIn('ON DELETE RESTRICT', self.ddl[2])
        for call in self.cursor.execute.call_args_list:
            sql = call.args[0]
            self.assertTrue(sql.startswith(('SELECT', 'ALTER TABLE helpdesk.users')))
            if 'information_schema' in sql:
                self.assertEqual(call.args[1][0], 'helpdesk')
        self.connection.rollback.assert_not_called()
        self.assertEqual(self.cursor.fetchone.call_count, 10)  # Include both lock-release results.

    def test_partial_migration_can_be_rerun_without_readding_column(self):
        def fail_index(sql, params=None):
            if 'ADD UNIQUE' in sql:
                raise mysql.connector.Error(errno=1142)
            self.execute(sql, params)

        self.cursor.execute.side_effect = fail_index
        with self.assertRaises(mysql.connector.Error):
            setup.migrate_user_technicians(self.connection)
        self.assertEqual(self.ddl, ['ALTER TABLE helpdesk.users ADD COLUMN technician_id INT NULL'])
        self.cursor.execute.side_effect = self.execute
        setup.migrate_user_technicians(self.connection)
        self.assertEqual(len(self.ddl), 3)

    def test_existing_equivalent_constraints_under_other_names_are_reused(self):
        self.state.update(column=True, unique=True, foreign=True)
        setup.migrate_user_technicians(self.connection)
        self.assertEqual(self.ddl, [])

    def test_unsigned_technician_id_is_matched(self):
        self.target['COLUMN_TYPE'] = 'int unsigned'
        setup.migrate_user_technicians(self.connection)
        self.assertIn('INT UNSIGNED NULL', self.ddl[0])

    def test_missing_tables_or_incompatible_existing_column_are_not_overwritten(self):
        self.target = None
        with self.assertRaises(setup.UserTechnicianSetupError):
            setup.migrate_user_technicians(self.connection)
        self.assertEqual(self.ddl, [])
        self.target = self.int_column
        self.state['column'] = True
        self.int_column['IS_NULLABLE'] = 'NO'
        with self.assertRaises(setup.UserTechnicianSetupError):
            setup.migrate_user_technicians(self.connection)
        self.assertEqual(self.ddl, [])

    def test_incompatible_foreign_key_is_not_dropped_or_replaced(self):
        self.state.update(column=True, unique=True, foreign=True)
        original = self.execute

        def incompatible(sql, params=None):
            original(sql, params)
            if 'KEY_COLUMN_USAGE' in sql:
                self.rows[0]['REFERENCED_TABLE_NAME'] = 'unexpected_table'

        self.cursor.execute.side_effect = incompatible
        with self.assertRaises(setup.UserTechnicianSetupError):
            setup.migrate_user_technicians(self.connection)
        self.assertEqual(self.ddl, [])

    def test_serialized_setup_lock_timeout_does_not_alter_schema(self):
        self.cursor.execute.side_effect = None
        self.cursor.fetchone.side_effect = None
        self.cursor.fetchone.return_value = {'acquired': 0}
        with self.assertRaises(setup.UserTechnicianSetupError):
            setup.migrate_user_technicians(self.connection)
        self.assertEqual(self.cursor.execute.call_count, 1)


class SetupCommandTests(unittest.TestCase):
    def test_normal_setup_uses_existing_guarded_connection(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        output = io.StringIO()
        with patch.object(setup, 'get_connection', return_value=connection), \
             patch.object(setup, 'migrate_user_technicians') as migrate, redirect_stdout(output):
            self.assertEqual(setup.main([]), 0)
        migrate.assert_called_once_with(connection)
        self.assertIn('helpdesk.users', output.getvalue())

    def test_root_setup_prompts_privately_and_restores_environment_credentials(self):
        credential = secrets.token_urlsafe(32)
        previous = secrets.token_urlsafe(32)
        connection = MagicMock()

        def connect():
            self.assertEqual(os.environ['DB_USER'], 'root')
            self.assertTrue(os.environ['DB_PASSWORD'] == credential)
            return connection

        output = io.StringIO()
        with patch.dict(os.environ, {'DB_USER': 'app_user', 'DB_PASSWORD': previous}), \
             patch.object(setup.getpass, 'getpass', return_value=credential), \
             patch.object(setup, 'get_connection', side_effect=connect), redirect_stdout(output):
            with setup._setup_connection(administrator=True):
                self.assertEqual(os.environ['DB_USER'], 'app_user')
                self.assertTrue(os.environ['DB_PASSWORD'] == previous)
            self.assertTrue(credential not in output.getvalue())

    def test_root_connection_failure_restores_environment_and_hides_server_secrets(self):
        credential = secrets.token_urlsafe(32)
        output = io.StringIO()
        with patch.dict(os.environ, {'DB_USER': 'app_user', 'DB_PASSWORD': credential}), \
             patch.object(setup.getpass, 'getpass', return_value=credential), \
             patch.object(setup, 'get_connection', side_effect=mysql.connector.Error(credential, errno=1045)), \
             redirect_stdout(output):
            self.assertEqual(setup.main(['--admin']), 1)
            self.assertEqual(os.environ['DB_USER'], 'app_user')
        self.assertTrue(credential not in output.getvalue())

    def test_echo_fallback_is_blocked_and_blank_admin_password_is_rejected(self):
        for candidate in (getpass.GetPassWarning(), ''):
            output = io.StringIO()
            options = {'side_effect': candidate} if isinstance(candidate, Exception) else {'return_value': candidate}
            with patch.object(setup.getpass, 'getpass', **options), patch.object(setup, 'get_connection') as connect, \
                 redirect_stdout(output):
                self.assertEqual(setup.main(['--admin']), 1)
            connect.assert_not_called()

    def test_database_guard_and_partial_setup_errors_are_friendly(self):
        for error in (ValueError('Private settings.'), mysql.connector.Error(errno=1142),
                      setup.UserTechnicianSetupError('Incompatible column.')):
            output = io.StringIO()
            with patch.object(setup, 'get_connection', side_effect=error), redirect_stdout(output):
                self.assertEqual(setup.main([]), 1)
            self.assertNotIn('Private settings.', output.getvalue())


if __name__ == '__main__':
    unittest.main()
