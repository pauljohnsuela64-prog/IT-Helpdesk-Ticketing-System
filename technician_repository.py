"""Technician database operations, restricted to the helpdesk database."""
import mysql.connector

from database import get_connection
from input_validation import validate_text


CREATE_TECHNICIANS_SQL = (
    'CREATE TABLE IF NOT EXISTS helpdesk.technicians ('
    'technician_id INT AUTO_INCREMENT PRIMARY KEY, '
    'full_name VARCHAR(100) NOT NULL, '
    'email VARCHAR(150) NOT NULL UNIQUE, '
    "status VARCHAR(20) NOT NULL DEFAULT 'Active', "
    'created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
    ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci'
)
CONFIGURATION_MESSAGE = (
    'Check your environment settings. DB_NAME must be helpdesk and '
    'all required connection settings must be supplied.'
)
SETUP_MESSAGE = 'Run python setup_technicians.py to set up helpdesk.technicians.'
TECHNICIAN_STATUSES = ('Active', 'Inactive')


class TechnicianReadError(Exception):
    """A safe, user-facing error when technicians cannot be retrieved."""


class TechnicianCreateError(Exception):
    """A safe, user-facing error when a technician cannot be saved."""


class TechnicianUpdateError(Exception):
    """A safe, user-facing error when a technician status cannot be saved."""


class TechnicianSetupError(Exception):
    """A safe, user-facing error when technician table setup fails."""


def setup_technicians_table():
    """Create only helpdesk.technicians; leave an existing table unchanged."""
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(CREATE_TECHNICIANS_SQL)
                # MySQL commits CREATE TABLE automatically.
    except ValueError:
        raise TechnicianSetupError(CONFIGURATION_MESSAGE) from None
    except mysql.connector.Error as error:
        raise TechnicianSetupError(
            f'Technician table setup failed (MySQL error code: {error.errno}). '
            'Check your connection settings and CREATE permission on helpdesk.'
        ) from None


def get_technicians():
    """Return technician dictionaries ordered by their generated ID."""
    return _read_technicians(
        'SELECT technician_id, full_name, email, status, created_at '
        'FROM helpdesk.technicians ORDER BY technician_id'
    )


def get_active_technicians():
    """Return only active technicians for the assignment selection list."""
    return _read_technicians(
        'SELECT technician_id, full_name, email, status '
        'FROM helpdesk.technicians WHERE status = %s ORDER BY technician_id',
        ('Active',),
    )


def validate_technician_id(technician_id):
    if type(technician_id) is not int or not 1 <= technician_id <= 2147483647:
        raise ValueError('Technician ID must be a positive number up to 2147483647.')


def get_technician(technician_id):
    """Return an existing technician regardless of status, or None."""
    validate_technician_id(technician_id)
    technicians = _read_technicians(
        'SELECT technician_id, full_name, email, status '
        'FROM helpdesk.technicians WHERE technician_id = %s',
        (technician_id,),
    )
    return technicians[0] if technicians else None


def update_technician_status(technician_id, status):
    """Change only one technician's status; return False if unchanged."""
    validate_technician_id(technician_id)
    status = validate_text(status, 'Status', 20)
    if status not in TECHNICIAN_STATUSES:
        raise ValueError('Technician status must be Active or Inactive.')
    try:
        with get_connection() as connection:
            try:
                with connection.cursor(dictionary=True) as cursor:
                    cursor.execute(
                        'SELECT technician_id, status FROM helpdesk.technicians '
                        'WHERE technician_id = %s FOR UPDATE',
                        (technician_id,),
                    )
                    current = cursor.fetchone()
                    if current is None:
                        raise TechnicianUpdateError('No technician found with that ID.')
                    if current['status'] == status:
                        connection.rollback()
                        return False
                    cursor.execute(
                        'UPDATE helpdesk.technicians SET status = %s WHERE technician_id = %s',
                        (status, technician_id),
                    )
                connection.commit()
                return True
            except (mysql.connector.Error, TechnicianUpdateError):
                try:
                    connection.rollback()
                except mysql.connector.Error:
                    pass
                raise
    except ValueError:
        raise TechnicianUpdateError(CONFIGURATION_MESSAGE) from None
    except mysql.connector.Error as error:
        if error.errno == 1146:
            raise TechnicianUpdateError(SETUP_MESSAGE) from None
        raise TechnicianUpdateError(
            f'Technician status change could not be confirmed (MySQL error code: {error.errno}). '
            'Check your connection and use View Technicians before retrying.'
        ) from None


def get_active_technician(technician_id, cursor):
    """Recheck an assignment using the ticket update's dictionary cursor."""
    validate_technician_id(technician_id)
    cursor.execute(
        'SELECT technician_id, full_name FROM helpdesk.technicians '
        'WHERE technician_id = %s AND status = %s FOR UPDATE',
        (technician_id, 'Active'),
    )
    return cursor.fetchone()


def _read_technicians(query, parameters=None):
    try:
        with get_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                if parameters is None:
                    cursor.execute(query)
                else:
                    cursor.execute(query, parameters)
                return cursor.fetchall()
    except ValueError:
        raise TechnicianReadError(CONFIGURATION_MESSAGE) from None
    except mysql.connector.Error as error:
        if error.errno == 1146:
            raise TechnicianReadError(SETUP_MESSAGE) from None
        raise TechnicianReadError(
            f'Unable to retrieve technicians (MySQL error code: {error.errno}). '
            'Check that MySQL is running and your connection settings are correct.'
        ) from None


def add_technician(full_name, email):
    """Save a technician; MySQL supplies the ID, Active status and timestamp."""
    fields = {'Full name': (full_name, 100), 'Email': (email, 150)}
    validated = []
    for label, (value, limit) in fields.items():
        validated.append(validate_text(value, label, limit))

    try:
        with get_connection() as connection:
            try:
                with connection.cursor() as cursor:
                    # The UNIQUE constraint prevents duplicates even with concurrent adds.
                    cursor.execute(
                        'INSERT INTO helpdesk.technicians (full_name, email) '
                        'VALUES (%s, %s)',
                        tuple(validated),
                    )
                    technician_id = cursor.lastrowid
                connection.commit()
                return technician_id
            except mysql.connector.Error:
                try:
                    connection.rollback()
                except mysql.connector.Error:
                    pass
                raise
    except ValueError:
        raise TechnicianCreateError(CONFIGURATION_MESSAGE) from None
    except mysql.connector.Error as error:
        if error.errno == 1062:
            raise TechnicianCreateError('A technician with that email already exists.') from None
        if error.errno == 1146:
            raise TechnicianCreateError(SETUP_MESSAGE) from None
        raise TechnicianCreateError(
            f'Technician creation could not be confirmed (MySQL error code: {error.errno}). '
            'Check your connection and use View Technicians before retrying.'
        ) from None
