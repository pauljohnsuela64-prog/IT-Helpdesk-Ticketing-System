"""Idempotent helpdesk.users technician-link migration, with optional Admin login."""
import argparse
from contextlib import contextmanager
import getpass
import os
import sys
import warnings

import mysql.connector
from database import get_connection


class UserTechnicianSetupError(Exception):
    """Safe setup feedback without database credentials or raw server errors."""


def _column(cursor, table, column):
    cursor.execute(
        'SELECT DATA_TYPE, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS '
        'WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s',
        ('helpdesk', table, column),
    )
    return cursor.fetchone()


def migrate_user_technicians(connection):
    """Add only a nullable column, unique index, and foreign key; keep all data."""
    with connection.cursor(dictionary=True) as cursor:
        cursor.execute('SELECT GET_LOCK(%s, %s) AS acquired', ('helpdesk.users.create', 5))
        if cursor.fetchone()['acquired'] != 1:
            raise UserTechnicianSetupError('Account management is busy. Close other setup processes and try again.')
        try:
            target = _column(cursor, 'technicians', 'technician_id')
            if target is None or _column(cursor, 'users', 'user_id') is None:
                raise UserTechnicianSetupError('Create helpdesk.users and helpdesk.technicians first using their existing setup scripts.')
            if target['DATA_TYPE'] != 'int':
                raise UserTechnicianSetupError('The existing technician ID type is incompatible. No migration was applied.')
            unsigned = 'unsigned' in target['COLUMN_TYPE'].lower()
            current = _column(cursor, 'users', 'technician_id')
            if current is None:
                cursor.execute('ALTER TABLE helpdesk.users ADD COLUMN technician_id INT UNSIGNED NULL' if unsigned else
                               'ALTER TABLE helpdesk.users ADD COLUMN technician_id INT NULL')
            elif (current['DATA_TYPE'] != 'int' or current['IS_NULLABLE'] != 'YES'
                  or ('unsigned' in current['COLUMN_TYPE'].lower()) != unsigned):
                raise UserTechnicianSetupError('The existing users.technician_id column is incompatible. No existing column was changed.')
            cursor.execute(
                'SELECT INDEX_NAME FROM information_schema.STATISTICS '
                'WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s GROUP BY INDEX_NAME '
                'HAVING MIN(NON_UNIQUE) = 0 AND COUNT(*) = 1 AND MAX(COLUMN_NAME) = %s',
                ('helpdesk', 'users', 'technician_id'),
            )
            if not cursor.fetchall():
                cursor.execute('ALTER TABLE helpdesk.users ADD UNIQUE KEY uq_users_technician_id (technician_id)')
            cursor.execute(
                'SELECT REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME '
                'FROM information_schema.KEY_COLUMN_USAGE '
                'WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s '
                'AND REFERENCED_TABLE_NAME IS NOT NULL', ('helpdesk', 'users', 'technician_id'),
            )
            references = cursor.fetchall()
            if references:
                if any((row['REFERENCED_TABLE_SCHEMA'], row['REFERENCED_TABLE_NAME'], row['REFERENCED_COLUMN_NAME'])
                       != ('helpdesk', 'technicians', 'technician_id') for row in references):
                    raise UserTechnicianSetupError('The existing technician foreign key is incompatible. No foreign key was changed.')
            else:
                cursor.execute('ALTER TABLE helpdesk.users ADD CONSTRAINT fk_users_technician '
                               'FOREIGN KEY (technician_id) REFERENCES helpdesk.technicians (technician_id) '
                               'ON DELETE RESTRICT ON UPDATE RESTRICT')
            # MySQL commits ALTER TABLE automatically. Partial setup can be rerun.
        finally:
            cursor.execute('SELECT RELEASE_LOCK(%s)', ('helpdesk.users.create',))
            cursor.fetchone()  # Consume the result before closing an unbuffered cursor.


@contextmanager
def _setup_connection(administrator=False):
    if not administrator:
        with get_connection() as connection:
            yield connection
        return
    # This standalone process uses the configured helpdesk host/TLS settings.
    # Restore environment credentials immediately after connecting.
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        password = getpass.getpass('MySQL root password: ')
    if not password:
        raise UserTechnicianSetupError('The MySQL root password cannot be blank.')
    previous = {key: os.environ.get(key) for key in ('DB_USER', 'DB_PASSWORD')}
    try:
        os.environ['DB_USER'] = 'root'
        os.environ['DB_PASSWORD'] = password
        connection = get_connection()
    finally:
        password = None
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    with connection:
        yield connection


def main(argv=None):
    parser = argparse.ArgumentParser(description='Add the technician relationship to helpdesk.users safely.')
    parser.add_argument('--admin', action='store_true', help='Prompt privately for the MySQL root password.')
    args = parser.parse_args(argv)
    try:
        with _setup_connection(args.admin) as connection:
            migrate_user_technicians(connection)
    except UserTechnicianSetupError as error:
        print(error)
        return 1
    except ValueError:
        print('Check the connection settings. DB_NAME must be helpdesk.')
        return 1
    except mysql.connector.Error as error:
        print(f'Technician-link setup failed (MySQL error code: {error.errno}). '
              'Check the existing tables and ALTER/INDEX/REFERENCES permissions. Partial setup can be rerun. '
              'For MySQL root setup in Git Bash, run: winpty .venv/Scripts/python.exe setup_user_technicians.py --admin')
        return 1
    except (getpass.GetPassWarning, OSError):
        print('Hidden input is unavailable. Run: winpty .venv/Scripts/python.exe setup_user_technicians.py --admin')
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nSetup interrupted. It is safe to rerun the migration.')
        return 1
    print('Technician-link setup complete: helpdesk.users has a nullable unique technician relationship.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
