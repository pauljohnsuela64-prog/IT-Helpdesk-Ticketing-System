"""Exercise real scrypt with credentials generated only in memory at runtime."""
import secrets
import unittest
from unittest.mock import patch

import password_security as security


class PasswordSecurityTests(unittest.TestCase):
    def test_random_salts_different_hashes_correct_and_incorrect_passwords(self):
        credential = secrets.token_urlsafe(32)
        first = security.hash_password(credential)
        second = security.hash_password(credential)
        # Boolean assertions avoid printing credentials or hashes on failure.
        self.assertTrue(first != second)
        self.assertTrue(first.split('$')[4] != second.split('$')[4])
        self.assertTrue(len(first) <= 255)
        self.assertTrue(credential not in first)
        self.assertTrue(security.verify_password(credential, first))
        self.assertFalse(security.verify_password(secrets.token_urlsafe(32), first))

    def test_unicode_and_surrounding_spaces_are_preserved(self):
        credential = ' ' + chr(233) + secrets.token_urlsafe(20) + ' '
        stored = security.hash_password(credential)
        self.assertTrue(security.verify_password(credential, stored))
        self.assertFalse(security.verify_password(credential.strip(), stored))

    def test_blank_invalid_or_excessive_input_is_rejected_before_hashing(self):
        with patch.object(security.hashlib, 'scrypt') as derive:
            for value in (None, '', ' \t\n ', secrets.token_urlsafe(1025), chr(0xD800)):
                with self.assertRaises(ValueError):
                    security.hash_password(value)
        derive.assert_not_called()

    def test_corrupted_or_unsupported_hash_does_not_trigger_expensive_unbounded_parameters(self):
        random_hash = security.DUMMY_PASSWORD_HASH
        parts = random_hash.split('$')
        mutations = [None, '', 'invalid', '$'.join([parts[0], str(2 ** 30), *parts[2:]]),
                     '$'.join([*parts[:4], 'zz', parts[5]]), '$'.join(parts[:5])]
        credential = secrets.token_urlsafe(20)
        with patch.object(security.hashlib, 'scrypt') as derive:
            for stored in mutations:
                self.assertFalse(security.verify_password(credential, stored))
        derive.assert_not_called()

    def test_profile_and_constant_time_comparison_are_used(self):
        credential = secrets.token_urlsafe(20)
        with patch.object(security.hashlib, 'scrypt', return_value=secrets.token_bytes(32)) as derive:
            stored = security.hash_password(credential)
            with patch.object(security.hmac, 'compare_digest', return_value=True) as compare:
                self.assertTrue(security.verify_password(credential, stored))
        self.assertTrue(derive.call_count == 2)
        self.assertTrue(derive.call_args.kwargs['n'] == 2 ** 17)
        self.assertTrue(derive.call_args.kwargs['r'] == 8)
        self.assertTrue(derive.call_args.kwargs['p'] == 1)
        self.assertTrue(derive.call_args.kwargs['dklen'] == 32)
        self.assertTrue(compare.call_count == 1)

    def test_memory_failure_is_safe_and_does_not_downgrade_security(self):
        credential = secrets.token_urlsafe(20)
        with patch.object(security.hashlib, 'scrypt', side_effect=MemoryError(credential)), \
             self.assertRaises(security.PasswordHashError) as caught:
            security.hash_password(credential)
        self.assertTrue(credential not in str(caught.exception))


if __name__ == '__main__':
    unittest.main()
