"""Ticket database operations, separate from the command-line interface."""
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


CATEGORIES = ('Hardware', 'Software', 'Network', 'Printer', 'Account', 'Email', 'Security', 'Other')
PRIORITIES = ('Low', 'Medium', 'High', 'Critical')


class TicketCreateError(Exception):
    """A safe, user-facing error when saving a ticket fails."""


def create_ticket(employee_name, department, category, subject, description, priority):
    """Validate and save one ticket; MySQL supplies IDs and default values."""
    fields = {
        'Employee name': (employee_name.strip(), 100),
        'Department': (department.strip(), 100),
        'Subject': (subject.strip(), 150),
        'Description': (description.strip(), None),
    }
    for label, (value, limit) in fields.items():
        if not value:
            raise ValueError(f'{label} is required.')
        if limit is not None and len(value) > limit:
            raise ValueError(f'{label} must be at most {limit} characters.')
    if len(fields['Description'][0].encode('utf-8')) > 65535:
        raise ValueError('Description is too long (maximum 65535 UTF-8 bytes).')
    if category not in CATEGORIES:
        raise ValueError('Choose one of the listed categories.')
    if priority not in PRIORITIES:
        raise ValueError('Choose one of the listed priorities.')

    try:
        with get_connection() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        'INSERT INTO helpdesk.tickets '
                        '(employee_name, department, category, subject, description, priority) '
                        'VALUES (%s, %s, %s, %s, %s, %s)',
                        (fields['Employee name'][0], fields['Department'][0], category,
                         fields['Subject'][0], fields['Description'][0], priority),
                    )
                    ticket_id = cursor.lastrowid
                connection.commit()
                return ticket_id
            except mysql.connector.Error:
                try:
                    connection.rollback()
                except mysql.connector.Error:
                    pass
                raise
    except ValueError:
        raise TicketCreateError(
            'Check your environment settings. DB_NAME must be helpdesk and '
            'all required connection settings must be supplied.'
        ) from None
    except mysql.connector.Error as error:
        raise TicketCreateError(
            f'Ticket creation could not be confirmed (MySQL error code: {error.errno}). '
            'Check your connection and use View Tickets before retrying.'
        ) from None
