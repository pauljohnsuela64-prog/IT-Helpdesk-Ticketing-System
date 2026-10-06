"""Salted scrypt password hashes; never log credentials or hash values."""
import hashlib
import hmac
import secrets


SCRYPT_N = 2 ** 17
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32
MAX_PASSWORD_BYTES = 1024
MAX_MEMORY = 256 * 1024 * 1024


class PasswordHashError(Exception):
    """A safe error when secure password hashing is unavailable."""


def validate_password(password):
    """Reject blank inputs without trimming or silently truncating a password."""
    if not isinstance(password, str) or not password.strip():
        raise ValueError('Password cannot be blank.')
    try:
        encoded = password.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('Password contains unsupported characters.') from None
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(f'Password must be at most {MAX_PASSWORD_BYTES} UTF-8 bytes.')
    return password


def _derive(password, salt):
    try:
        return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=SCRYPT_N,
                              r=SCRYPT_R, p=SCRYPT_P, dklen=KEY_BYTES, maxmem=MAX_MEMORY)
    except (ValueError, MemoryError, AttributeError):
        raise PasswordHashError('Secure password hashing is unavailable. Please check your Python installation and available memory.') from None


def _format_hash(salt, key):
    return f'scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${key.hex()}'


def hash_password(password):
    validate_password(password)
    salt = secrets.token_bytes(SALT_BYTES)
    return _format_hash(salt, _derive(password, salt))


def verify_password(password, stored_hash):
    try:
        validate_password(password)
        if not isinstance(stored_hash, str):
            return False
        parts = stored_hash.split('$')
        if len(parts) != 6 or parts[:4] != ['scrypt', str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P)]:
            return False
        salt, expected = bytes.fromhex(parts[4]), bytes.fromhex(parts[5])
        if len(salt) != SALT_BYTES or len(expected) != KEY_BYTES:
            return False
    except ValueError:
        return False
    return hmac.compare_digest(_derive(password, salt), expected)


# Unknown usernames still perform the same derivation. This random comparison
# value is not an account, a default password, or a stored database credential.
DUMMY_PASSWORD_HASH = _format_hash(secrets.token_bytes(SALT_BYTES), secrets.token_bytes(KEY_BYTES))
