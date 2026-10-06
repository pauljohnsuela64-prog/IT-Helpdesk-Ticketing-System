"""Application accounts and authentication restricted to the helpdesk database."""
from pathlib import Path
import mysql.connector

from database import get_connection
from input_validation import validate_text
from password_security import DUMMY_PASSWORD_HASH, PasswordHashError, hash_password, validate_password, verify_password


USER_ROLES = ('Admin', 'Technician')
USER_STATUSES = ('Active', 'Inactive')
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


class UserReadError(Exception):
    """A safe error when application users cannot be read."""


class UserUpdateError(Exception):
    """A safe error when an account status change cannot be confirmed."""


class UserManagementPermissionError(Exception):
    """The acting account is no longer an Active Admin."""


class UserStatusChangeError(UserUpdateError):
    """A status change would disable the current or last Active Admin."""


class UserPasswordChangeError(Exception):
    """A safe error when a self-service password change cannot be confirmed."""


class UserAuthorizationError(UserCreateError):
    """GUI creation requires a newly authorized Active Admin."""


class _AccountAuthorization:
    """Proof of authentication for one dialog; contains no password or hash."""

    def __init__(self, user_id):
        self.user_id = user_id
        self.active = True

    def revoke(self):
        self.active = False


def authorize_account_creation(username, password):
    user = authenticate_user(username, password)
    if user is None or user['role'] != 'Admin' or user['status'] != 'Active':
        return None
    return _AccountAuthorization(user['user_id'])


def get_user_count():
    """Count all application users, including Inactive accounts."""
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT COUNT(*) FROM helpdesk.users')
                return cursor.fetchone()[0]
    except ValueError:
        raise UserReadError(CONFIGURATION_MESSAGE) from None
    except mysql.connector.Error as error:
        if error.errno == 1146:
            raise UserReadError(SETUP_MESSAGE) from None
        raise UserReadError('Unable to check application accounts. Please check the database connection and try again.') from None


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


def validate_user_id(user_id):
    if type(user_id) is not int or not 1 <= user_id <= 2147483647:
        raise ValueError('User ID must be a positive number up to 2147483647.')


def validate_password_change(current_password, new_password, confirmation):
    """Reuse password rules without trimming or including secrets in errors."""
    try:
        validate_password(current_password)
    except ValueError:
        raise ValueError('Current password is incorrect.') from None
    if not isinstance(new_password, str) or not new_password.strip():
        raise ValueError('New password cannot be blank.')
    validate_password(new_password)
    if new_password != confirmation:
        raise ValueError('New passwords do not match.')
    if new_password == current_password:
        raise ValueError('New password must be different from the current password.')


def change_password(user_id, current_password, new_password, confirmation):
    """Change only the session account's password after verifying its credential.

    There is no separate target account or administrator override. Lock the row
    until commit so concurrent requests must verify the latest stored password.
    """
    validate_user_id(user_id)
    validate_password_change(current_password, new_password, confirmation)
    try:
        with get_connection() as connection:
            try:
                with connection.cursor(dictionary=True) as cursor:
                    cursor.execute(
                        'SELECT password_hash, role, status FROM helpdesk.users WHERE user_id = %s FOR UPDATE',
                        (user_id,),
                    )
                    user = cursor.fetchone()
                    if user is None or user['status'] != 'Active' or user['role'] not in USER_ROLES:
                        raise UserPasswordChangeError('Unable to change password for this account. Please log in again.')
                    if not verify_password(current_password, user['password_hash']):
                        raise UserPasswordChangeError('Current password is incorrect.')
                    password_hash = hash_password(new_password)
                    cursor.execute(
                        'UPDATE helpdesk.users SET password_hash = %s WHERE user_id = %s LIMIT 1',
                        (password_hash, user_id),
                    )
                    if cursor.rowcount != 1:
                        raise UserPasswordChangeError('Password change could not be confirmed. Please log out and check your credentials before retrying.')
                connection.commit()
                return True
            except (mysql.connector.Error, PasswordHashError, UserPasswordChangeError):
                try:
                    connection.rollback()
                except mysql.connector.Error:
                    pass
                raise
    except ValueError:
        raise UserPasswordChangeError(CONFIGURATION_MESSAGE) from None
    except PasswordHashError as error:
        raise UserPasswordChangeError(str(error)) from None
    except mysql.connector.Error as error:
        if error.errno == 1146:
            raise UserPasswordChangeError(SETUP_MESSAGE) from None
        raise UserPasswordChangeError('Password change could not be confirmed. Please check the database connection and try again.') from None


def _require_active_admin(cursor, acting_user_id, for_update=False):
    cursor.execute(
        'SELECT user_id, role, status FROM helpdesk.users WHERE user_id = %s LIMIT 1'
        + (' FOR UPDATE' if for_update else ''),
        (acting_user_id,),
    )
    actor = cursor.fetchone()
    if actor is None or actor['role'] != 'Admin' or actor['status'] != 'Active':
        raise UserManagementPermissionError('You do not have permission to perform this action.')


def get_users(acting_user_id):
    """Only Active Admins may read users; never select passwords or hashes."""
    return _read_users(acting_user_id)


def get_user(user_id, acting_user_id):
    validate_user_id(user_id)
    users = _read_users(acting_user_id, user_id)
    return users[0] if users else None


def _read_users(acting_user_id, user_id=None):
    validate_user_id(acting_user_id)
    try:
        with get_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                _require_active_admin(cursor, acting_user_id)
                cursor.execute(
                    'SELECT user_id, username, full_name, role, status, created_at FROM helpdesk.users '
                    + ('ORDER BY user_id' if user_id is None else 'WHERE user_id = %s LIMIT 1'),
                    () if user_id is None else (user_id,),
                )
                return [public_user(user) for user in cursor.fetchall()]
    except ValueError:
        raise UserReadError(CONFIGURATION_MESSAGE) from None
    except mysql.connector.Error as error:
        if error.errno == 1146:
            raise UserReadError(SETUP_MESSAGE) from None
        raise UserReadError('Unable to load application users. Please check the database connection and try again.') from None


def update_user_status(user_id, status, acting_user_id):
    """Update one status with serialized checks for actor, self, and last Admin."""
    validate_user_id(user_id)
    validate_user_id(acting_user_id)
    status = validate_text(status, 'Status', 20)
    if status not in USER_STATUSES:
        raise ValueError('User status must be Active or Inactive.')
    try:
        with get_connection() as connection:
            try:
                with connection.cursor(dictionary=True) as cursor:
                    # Share the creation lock; connection.close releases it on every exit.
                    cursor.execute('SELECT GET_LOCK(%s, %s) AS acquired', ('helpdesk.users.create', 5))
                    if cursor.fetchone()['acquired'] != 1:
                        raise UserUpdateError('User management is busy. Please try again.')
                    _require_active_admin(cursor, acting_user_id, for_update=True)
                    cursor.execute(
                        'SELECT user_id, role, status FROM helpdesk.users WHERE user_id = %s FOR UPDATE',
                        (user_id,),
                    )
                    current = cursor.fetchone()
                    if current is None:
                        raise UserUpdateError('No user found with that ID.')
                    if current['status'] == status:
                        connection.rollback()
                        return False
                    if status == 'Inactive':
                        if current['role'] == 'Admin' and current['status'] == 'Active':
                            cursor.execute(
                                'SELECT COUNT(*) AS active_admins FROM helpdesk.users WHERE role = %s AND status = %s',
                                ('Admin', 'Active'),
                            )
                            if cursor.fetchone()['active_admins'] <= 1:
                                raise UserStatusChangeError('Cannot deactivate the last Active Admin account. '
                                                            'At least one Active Admin must remain.')
                        if user_id == acting_user_id:
                            raise UserStatusChangeError('You cannot deactivate your own account while logged in.')
                    cursor.execute('UPDATE helpdesk.users SET status = %s WHERE user_id = %s LIMIT 1', (status, user_id))
                    if cursor.rowcount != 1:
                        raise UserUpdateError('No user status change was confirmed. Please Refresh and try again.')
                connection.commit()
                return True
            except (mysql.connector.Error, UserUpdateError, UserManagementPermissionError):
                try:
                    connection.rollback()
                except mysql.connector.Error:
                    pass
                raise
    except ValueError:
        raise UserUpdateError(CONFIGURATION_MESSAGE) from None
    except mysql.connector.Error as error:
        if error.errno == 1146:
            raise UserUpdateError(SETUP_MESSAGE) from None
        raise UserUpdateError('User status change could not be confirmed. Use Refresh to check the account before retrying.') from None


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
    """Administrator/fallback tool; retain its existing unrestricted creation API."""
    return _create_user(username, full_name, role, password, administrator_tool=True)


def create_account(username, full_name, role, password, authorization=None):
    """GUI creation: bootstrap an Admin, otherwise require verified authorization."""
    return _create_user(username, full_name, role, password, authorization=authorization)


def _create_user(username, full_name, role, password, authorization=None, administrator_tool=False):
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
                    # The connection closes (and releases this lock) on every exit.
                    # The fallback tool shares the lock, so it cannot race GUI bootstrap.
                    cursor.execute('SELECT GET_LOCK(%s, %s)', ('helpdesk.users.create', 5))
                    if cursor.fetchone()[0] != 1:
                        raise UserCreateError('Account creation is busy. Please try again.')
                    if not administrator_tool:
                        cursor.execute('SELECT COUNT(*) FROM helpdesk.users')
                        if cursor.fetchone()[0] == 0:
                            role = 'Admin'
                        else:
                            if not isinstance(authorization, _AccountAuthorization) or not authorization.active:
                                raise UserAuthorizationError('Admin authorization is required. Cancel and reopen Create Account.')
                            cursor.execute(
                                'SELECT user_id FROM helpdesk.users '
                                'WHERE user_id = %s AND role = %s AND status = %s LIMIT 1 FOR UPDATE',
                                (authorization.user_id, 'Admin', 'Active'),
                            )
                            if cursor.fetchone() is None:
                                raise UserAuthorizationError('Admin authorization is required. Cancel and reopen Create Account.')
                    cursor.execute(
                        'INSERT INTO helpdesk.users (username, password_hash, full_name, role) '
                        'VALUES (%s, %s, %s, %s)',
                        (username, password_hash, full_name, role),
                    )
                    user_id = cursor.lastrowid
                connection.commit()
                return user_id
            except (mysql.connector.Error, UserCreateError):
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
