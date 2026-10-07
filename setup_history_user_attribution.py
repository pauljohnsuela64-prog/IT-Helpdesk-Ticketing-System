"""Repeatable, additive user-attribution migration for helpdesk.ticket_history."""
import argparse
import getpass
import sys

import mysql.connector

from setup_user_technicians import UserTechnicianSetupError, _column, _setup_connection


LOCK_NAME = 'helpdesk.ticket_history.user_attribution_setup'


class HistoryAttributionSetupError(UserTechnicianSetupError):
    """Safe setup feedback without credentials or raw database errors."""


def migrate_history_user_attribution(connection):
    """Add a nullable user ID, index, and SET NULL foreign key; never backfill."""
    with connection.cursor(dictionary=True) as cursor:
        cursor.execute('SELECT GET_LOCK(%s, %s) AS acquired', (LOCK_NAME, 5))
        if cursor.fetchone()['acquired'] != 1:
            raise HistoryAttributionSetupError('History attribution setup is busy. Try again after other setup processes close.')
        try:
            target = _column(cursor, 'users', 'user_id')
            if target is None or _column(cursor, 'ticket_history', 'history_id') is None:
                raise HistoryAttributionSetupError('Create helpdesk.users and helpdesk.ticket_history before running this migration.')
            if target['DATA_TYPE'] != 'int':
                raise HistoryAttributionSetupError('The existing user ID type is incompatible. No migration was applied.')
            unsigned = 'unsigned' in target['COLUMN_TYPE'].lower()
            current = _column(cursor, 'ticket_history', 'performed_by_user_id')
            if current is None:
                cursor.execute('ALTER TABLE helpdesk.ticket_history ADD COLUMN performed_by_user_id '
                               + ('INT UNSIGNED NULL' if unsigned else 'INT NULL'))
            elif (current['DATA_TYPE'] != 'int' or current['IS_NULLABLE'] != 'YES'
                  or ('unsigned' in current['COLUMN_TYPE'].lower()) != unsigned):
                raise HistoryAttributionSetupError('The existing performed_by_user_id column is incompatible. No existing column was changed.')
            cursor.execute(
                'SELECT k.REFERENCED_TABLE_SCHEMA, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME, '
                'r.DELETE_RULE, '
                '(SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE AS part '
                'WHERE part.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA AND part.TABLE_NAME = k.TABLE_NAME '
                'AND part.CONSTRAINT_NAME = k.CONSTRAINT_NAME) AS COLUMN_COUNT '
                'FROM information_schema.KEY_COLUMN_USAGE AS k '
                'JOIN information_schema.REFERENTIAL_CONSTRAINTS AS r '
                'ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA AND r.TABLE_NAME = k.TABLE_NAME '
                'AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME '
                'WHERE k.TABLE_SCHEMA = %s AND k.TABLE_NAME = %s AND k.COLUMN_NAME = %s '
                'AND k.REFERENCED_TABLE_NAME IS NOT NULL',
                ('helpdesk', 'ticket_history', 'performed_by_user_id'),
            )
            references = cursor.fetchall()
            if references and (len(references) != 1 or any(
                (row['REFERENCED_TABLE_SCHEMA'], row['REFERENCED_TABLE_NAME'], row['REFERENCED_COLUMN_NAME'],
                 row['DELETE_RULE'], row['COLUMN_COUNT']) != ('helpdesk', 'users', 'user_id', 'SET NULL', 1)
                for row in references
            )):
                raise HistoryAttributionSetupError('The existing history user foreign key is incompatible. No foreign key was changed.')
            cursor.execute(
                'SELECT INDEX_NAME FROM information_schema.STATISTICS '
                'WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s AND SEQ_IN_INDEX = 1',
                ('helpdesk', 'ticket_history', 'performed_by_user_id'),
            )
            if not cursor.fetchall():
                cursor.execute('ALTER TABLE helpdesk.ticket_history ADD KEY '
                               'idx_ticket_history_performed_by_user (performed_by_user_id)')
            if not references:
                cursor.execute('ALTER TABLE helpdesk.ticket_history ADD CONSTRAINT fk_ticket_history_performed_by_user '
                               'FOREIGN KEY (performed_by_user_id) REFERENCES helpdesk.users (user_id) '
                               'ON DELETE SET NULL ON UPDATE RESTRICT')
            # MySQL commits DDL automatically. Each step checks partial prior setup.
            # Existing events retain their data; the new column defaults to NULL.
        finally:
            cursor.execute('SELECT RELEASE_LOCK(%s)', (LOCK_NAME,))
            cursor.fetchone()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Add user attribution to helpdesk.ticket_history safely.')
    parser.add_argument('--admin', action='store_true', help='Prompt privately for the MySQL root password.')
    args = parser.parse_args(argv)
    try:
        with _setup_connection(args.admin) as connection:
            migrate_history_user_attribution(connection)
    except UserTechnicianSetupError as error:
        print(error)
        return 1
    except ValueError:
        print('Check the connection settings. DB_NAME must be helpdesk.')
        return 1
    except mysql.connector.Error as error:
        print(f'History attribution setup failed (MySQL error code: {error.errno}). '
              'Check existing tables and ALTER/INDEX/REFERENCES permissions. Partial setup can be rerun. '
              'For MySQL root setup in Git Bash, run: '
              'winpty .venv/Scripts/python.exe setup_history_user_attribution.py --admin')
        return 1
    except (getpass.GetPassWarning, OSError, EOFError, KeyboardInterrupt):
        print('Setup cancelled or secure password input unavailable. '
              'For Git Bash use: winpty .venv/Scripts/python.exe setup_history_user_attribution.py --admin')
        return 1
    print('Ticket history user attribution is ready. Existing history was preserved.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
