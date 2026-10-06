"""User creation and authentication restricted to the helpdesk database."""
from pathlib import Path
import mysql.connector

from database import get_connection
from input_validation import validate_text
from password_security import DUMMY_PASSWORD_HASH, PasswordHashError, hash_password, validate_password, verify_password


USER_ROLES = ('Admin', 'Technician')
PUBLIC_USER_FIELDS = ('user_id', 'username', 'full_name', 'role', 'status', 'created_at')
CREATE_USERS_SQL = (Path(__file__).parent / 'database' / 'users.sql').read_text(encoding='utf-8').strip()
CONFIGURATION_MESSAGE = 'Check your environment settings. DB_NAME must be helpdesk and all required connection settings must be supplied.'
SETUP_MESSAGE = 'Run python setup_users.py or apply database/users.sql as the MySQL administrator to set up helpdesk.users.'


class UserSetupError(Exception):
    """A safe error when the users table cannot be created."""


class UserCreateError(Exception):
    """A safe error when account creation cannot be confirmed."""


class UserAuthenticationError(Exception):
    """A safe database or hashing error, distinct from invalid credentials."""


def validate_user_details(username, full_name, role):
    username = validate_text(username, 'Username', 50)
    full_name = validate_text(full_name, 'Full name', 100)
    role = validate_text(role, 'Role', 20)
    if role not in USER_ROLES:
        raise ValueError('Role must be Admin or Technician.')
    return username, full_name, role


def public_user(user):
    """Return session data without a password or password hash."""
    return {field: user.get(field) for field in PUBLIC_USER_FIELDS}


def setup_users_table():
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(CREATE_USERS_SQL)
    except ValueError:
        raise UserSetupError(CONFIGURATION_MESSAGE) from None
    except mysql.connector.Error as error:
        raise UserSetupError(
            f'Users setup failed (MySQL error code: {error.errno}). '
            'If the application account lacks CREATE permission, run database/users.sql with the MySQL root administrator account.'
        ) from None


def create_user(username, full_name, role, password):
    username, full_name, role = validate_user_details(username, full_name, role)
    validate_password(password)
    try:
        password_hash = hash_password(password)
    except PasswordHashError as error:
        raise UserCreateError(str(error)) from None
    try:
        with get_connection() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        'INSERT INTO helpdesk.users (username, password_hash, full_name, role) '
                        'VALUES (%s, %s, %s, %s)',
                        (username, password_hash, full_name, role),
                    )
                    user_id = cursor.lastrowid
                connection.commit()
                return user_id
            except mysql.connector.Error:
                try:
                    connection.rollback()
                except mysql.connector.Error:
                    pass
                raise
    except ValueError:
        raise UserCreateError(CONFIGURATION_MESSAGE) from None
    except mysql.connector.Error as error:
        if error.errno == 1062:
            raise UserCreateError('A user with that username already exists.') from None
        if error.errno == 1146:
            raise UserCreateError(SETUP_MESSAGE) from None
        raise UserCreateError(
            f'Account creation could not be confirmed (MySQL error code: {error.errno}). '
            'Check your connection before retrying.'
        ) from None


def authenticate_user(username, password):
    """Return public data only for an Active user with valid credentials."""
    try:
        username = validate_text(username, 'Username', 50)
        validate_password(password)
    except ValueError:
        return None
    try:
        with get_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute(
                    'SELECT user_id, username, full_name, role, status, created_at, password_hash '
                    'FROM helpdesk.users WHERE username = %s LIMIT 1', (username,),
                )
                user = cursor.fetchone()
    except ValueError:
        raise UserAuthenticationError(CONFIGURATION_MESSAGE) from None
    except mysql.connector.Error as error:
        if error.errno == 1146:
            raise UserAuthenticationError(SETUP_MESSAGE) from None
        raise UserAuthenticationError('Unable to sign in. Please check the database connection and try again.') from None
    try:
        valid = verify_password(password, user['password_hash'] if user is not None else DUMMY_PASSWORD_HASH)
    except PasswordHashError as error:
        raise UserAuthenticationError(str(error)) from None
    if not valid or user is None or user['status'] != 'Active':
        return None
    return public_user(user)
