"""Read-only ticket retrieval, separate from the command-line interface."""
import mysql.connector

from database import get_connection


class TicketReadError(Exception):
    """A safe, user-facing error when tickets cannot be retrieved."""


def get_tickets():
    """Return ticket dictionaries from the helpdesk database only."""
    try:
        with get_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute(
                    'SELECT ticket_id, employee_name, department, category, '
                    'subject, priority, status, assigned_to '
                    'FROM helpdesk.tickets ORDER BY ticket_id'
                )
                return cursor.fetchall()
    except ValueError:
        raise TicketReadError(
            'Check your environment settings. DB_NAME must be helpdesk and '
            'all required connection settings must be supplied.'
        ) from None
    except mysql.connector.Error as error:
        raise TicketReadError(
            f'Unable to retrieve tickets (MySQL error code: {error.errno}). '
            'Check that MySQL is running and your connection settings are correct.'
        ) from None
