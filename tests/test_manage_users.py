"""Interactive creation using runtime-generated secrets and mocked hidden input."""
from contextlib import redirect_stdout
import getpass
import io
import secrets
import unittest
import warnings
from unittest.mock import patch

import manage_users as manage
from user_repository import UserCreateError


class InteractiveUserTests(unittest.TestCase):
    def test_hides_passwords_and_submits_only_valid_trimmed_fields(self):
        credential = secrets.token_urlsafe(32)
        output = io.StringIO()
        with patch('builtins.input', side_effect=['  test_account  ', '  Test Operator  ', 'Technician']), \
             patch.object(manage.getpass, 'getpass', side_effect=[credential, credential]) as prompt, \
             patch.object(manage, 'create_user', return_value=7) as create, redirect_stdout(output):
            self.assertEqual(manage.main(), 0)
        self.assertTrue(create.call_count == 1)
        self.assertTrue(create.call_args.args == ('test_account', 'Test Operator', 'Technician', credential))
        self.assertEqual([item.args[0] for item in prompt.call_args_list], ['Password: ', 'Confirm password: '])
        self.assertTrue(credential not in output.getvalue())
        self.assertIn('New User ID: 7', output.getvalue())

    def test_invalid_username_name_and_role_retry_without_creating_invalid_accounts(self):
        credential = secrets.token_urlsafe(32)
        output = io.StringIO()
        with patch('builtins.input', side_effect=['', ' ', 'user', '333', '!!!', 'Test Name', 'Other', 'Admin']), \
             patch.object(manage.getpass, 'getpass', side_effect=[credential, credential]), \
             patch.object(manage, 'create_user', return_value=7) as create, redirect_stdout(output):
            self.assertEqual(manage.main(), 0)
        self.assertTrue(create.call_count == 1)
        self.assertTrue(create.call_args.args[:3] == ('user', 'Test Name', 'Admin'))
        self.assertIn('alphabetic letter', output.getvalue())
        self.assertIn('Role must be', output.getvalue())

    def test_blank_password_and_mismatched_confirmation_are_rejected(self):
        credential = secrets.token_urlsafe(32)
        other = secrets.token_urlsafe(32)
        output = io.StringIO()
        with patch('builtins.input', side_effect=['user', 'Test Name', 'Admin']), \
             patch.object(manage.getpass, 'getpass', side_effect=['', '  ', credential, other, credential, credential]), \
             patch.object(manage, 'create_user', return_value=7) as create, redirect_stdout(output):
            self.assertEqual(manage.main(), 0)
        self.assertTrue(create.call_count == 1)
        self.assertIn('confirmation does not match', output.getvalue())
        self.assertTrue(credential not in output.getvalue() and other not in output.getvalue())

    def test_duplicate_username_fails_with_friendly_message_without_printing_password(self):
        credential = secrets.token_urlsafe(32)
        output = io.StringIO()
        with patch('builtins.input', side_effect=['user', 'Test Name', 'Admin']), \
             patch.object(manage.getpass, 'getpass', side_effect=[credential, credential]), \
             patch.object(manage, 'create_user', side_effect=UserCreateError('A user with that username already exists.')), redirect_stdout(output):
            self.assertEqual(manage.main(), 1)
        self.assertIn('already exists', output.getvalue())
        self.assertTrue(credential not in output.getvalue())

    def test_echoing_fallback_is_stopped_before_reading_a_password(self):
        def unsafe_prompt(prompt):
            warnings.warn('Cannot hide input.', getpass.GetPassWarning)
            raise AssertionError('Echoing fallback must never be reached.')
        output = io.StringIO()
        with patch('builtins.input', side_effect=['user', 'Test Name', 'Admin']), \
             patch.object(manage.getpass, 'getpass', side_effect=unsafe_prompt), \
             patch.object(manage, 'create_user') as create, redirect_stdout(output):
            self.assertEqual(manage.main(), 1)
        create.assert_not_called()
        self.assertIn('winpty .venv/Scripts/python.exe manage_users.py', output.getvalue())

    def test_interrupt_or_eof_never_creates_an_account(self):
        for failure in (KeyboardInterrupt, EOFError):
            output = io.StringIO()
            with patch('builtins.input', side_effect=failure), patch.object(manage, 'create_user') as create, redirect_stdout(output):
                self.assertEqual(manage.main(), 0)
            create.assert_not_called()

    def test_unexpected_error_does_not_print_exception_credentials_or_traceback(self):
        credential = secrets.token_urlsafe(32)
        output = io.StringIO()
        with patch('builtins.input', side_effect=['user', 'Test Name', 'Admin']), \
             patch.object(manage.getpass, 'getpass', side_effect=[credential, credential]), \
             patch.object(manage, 'create_user', side_effect=RuntimeError(credential)), redirect_stdout(output):
            self.assertEqual(manage.main(), 1)
        self.assertTrue(credential not in output.getvalue())
        self.assertNotIn('Traceback', output.getvalue())


if __name__ == '__main__':
    unittest.main()
