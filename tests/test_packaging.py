"""Configuration location and windowed build checks, without a live database."""
import ast
import os
from pathlib import Path
import secrets
import sys
import unittest
from unittest.mock import patch

import database


PROJECT = Path(__file__).resolve().parent.parent


class ExternalConfigurationTests(unittest.TestCase):
    def test_development_configuration_is_beside_database_module(self):
        with patch.object(sys, 'frozen', False, create=True):
            self.assertEqual(database._environment_file(), PROJECT / '.env')

    def test_frozen_configuration_is_beside_executable(self):
        executable = PROJECT / 'dist' / 'ITHelpDesk.exe'
        with patch.object(sys, 'frozen', True, create=True), \
                patch.object(sys, 'executable', str(executable)):
            self.assertEqual(database._environment_file(), executable.parent / '.env')

    def test_frozen_configuration_does_not_use_extracted_module_directory(self):
        executable = PROJECT / 'dist' / 'ITHelpDesk.exe'
        with patch.object(sys, 'frozen', True, create=True), \
                patch.object(sys, 'executable', str(executable)), \
                patch.object(database, '__file__', str(PROJECT / '_MEI_test' / 'database.py')):
            self.assertEqual(database._environment_file(), executable.parent / '.env')

    def test_connection_preserves_environment_precedence_and_literal_loading(self):
        settings = {'DB_HOST': '127.0.0.1', 'DB_PORT': '3306', 'DB_USER': 'configuration_test',
                    'DB_PASSWORD': secrets.token_urlsafe(24), 'DB_NAME': 'helpdesk'}
        with patch.dict(os.environ, settings, clear=True), \
                patch.object(sys, 'frozen', False, create=True), \
                patch('database.load_dotenv') as load, \
                patch('database.mysql.connector.connect') as connect:
            self.assertIs(database.get_connection(), connect.return_value)
            load.assert_called_once_with(PROJECT / '.env', override=False, interpolate=False)
            self.assertEqual(connect.call_args.kwargs['database'], 'helpdesk')
            self.assertFalse(connect.call_args.kwargs['allow_local_infile'])

    def test_other_database_is_still_rejected_in_frozen_mode(self):
        with patch.object(sys, 'frozen', True, create=True), \
                patch.dict(os.environ, {'DB_NAME': 'not_the_application_database'}, clear=True), \
                patch('database.load_dotenv'), \
                patch('database.mysql.connector.connect') as connect:
            with self.assertRaisesRegex(ValueError, 'DB_NAME must be exactly helpdesk'):
                database.get_connection()
            connect.assert_not_called()


class BuildSpecificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse((PROJECT / 'ITHelpDesk.spec').read_text(encoding='utf-8'))

    def test_executable_is_named_and_windowed(self):
        exe = next(node for node in ast.walk(self.tree)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'EXE')
        options = {keyword.arg: keyword.value for keyword in exe.keywords}
        self.assertEqual(ast.literal_eval(options['name']), 'ITHelpDesk')
        self.assertIs(ast.literal_eval(options['console']), False)

    def test_data_allowlist_contains_only_required_sql_templates(self):
        resources = next(node.value for node in self.tree.body
                         if isinstance(node, ast.Assign) and any(
                             isinstance(target, ast.Name) and target.id == 'sql_resources'
                             for target in node.targets))
        names = ast.literal_eval(resources)
        self.assertEqual(set(names), {'users.sql', 'ticket_history.sql', 'ticket_comments.sql'})
        for name in names:
            self.assertTrue((PROJECT / 'database' / name).is_file())
        analysis = next(node for node in ast.walk(self.tree)
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'Analysis')
        data_argument = next(keyword.value for keyword in analysis.keywords if keyword.arg == 'datas')
        self.assertIsInstance(data_argument, ast.Name)
        self.assertEqual(data_argument.id, 'datas')


if __name__ == '__main__':
    unittest.main()
