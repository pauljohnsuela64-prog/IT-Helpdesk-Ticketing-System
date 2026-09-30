"""Run a read-only connection check: python check_connection.py."""
import sys

import mysql.connector
from database import get_connection


def main():
    connection = None
    cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute('SELECT 1')
        if cursor.fetchone() != (1,):
            print('Unexpected response from MySQL.')
            return 1
        print('Connection successful. MySQL answered SELECT 1.')
        return 0
    except ValueError as error:
        print(f'Configuration error: {error}')
        return 1
    except mysql.connector.Error as error:
        # Avoid printing connection details or credentials.
        print(f'MySQL connection failed (error code: {error.errno}).')
        print('Check the server, .env settings, account access, and TLS settings.')
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
