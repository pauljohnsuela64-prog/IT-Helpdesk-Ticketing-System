"""Inspect helpdesk.tickets using SELECT queries only."""
import sys

import mysql.connector
from database import get_connection


def main():
    connection = None
    cursor = None
    try:
        # The existing helper rejects any DB_NAME other than helpdesk.
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute('SELECT DATABASE()')
        if cursor.fetchone() != ('helpdesk',):
            print('Verification stopped: the selected database is not helpdesk.')
            return 1

        cursor.execute(
            'SELECT TABLE_NAME FROM information_schema.TABLES '
            'WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s '
            'AND TABLE_TYPE = %s',
            ('helpdesk', 'tickets', 'BASE TABLE'),
        )
        if not cursor.fetchall():
            print('The tickets table does not exist in helpdesk.')
            return 1
        print('Verified: helpdesk.tickets exists.')

        cursor.execute('SELECT * FROM helpdesk.tickets')
        columns = [column[0] for column in cursor.description]
        print('Columns: ' + ', '.join(columns))

        count = 0
        for count, row in enumerate(cursor, start=1):
            print(f'\nTicket row {count}:')
            for name, value in zip(columns, row):
                # repr keeps embedded newlines/control characters readable.
                print(f'  {name}: {value!r}')
        print(f'\nTotal tickets displayed: {count}')
        return 0
    except ValueError as error:
        print(f'Configuration error: {error}')
        return 1
    except mysql.connector.Error as error:
        # Do not expose credentials or connection details in error messages.
        print(f'Database verification failed (MySQL error code: {error.errno}).')
        return 1
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            if connection is not None:
                connection.close()


if __name__ == '__main__':
    sys.exit(main())
