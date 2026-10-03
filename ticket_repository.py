"""Ticket database operations, separate from the command-line interface."""
import mysql.connector

from database import get_connection


class TicketReadError(Exception):
    """A safe, user-facing error when tickets cannot be retrieved."""


def get_tickets():
    """Return ticket dictionaries from the helpdesk database only."""
    return _read_tickets(
        'SELECT ticket_id, employee_name, department, category, '
        'subject, priority, status, assigned_to '
        'FROM helpdesk.tickets ORDER BY ticket_id'
    )


def search_tickets(search_term):
    """Find case-insensitive literal substrings using a read-only SELECT."""
    search_term = search_term.strip()
    if not search_term:
        return []
    # Escape LIKE wildcards so %, _ and ! in the term match literal text.
    pattern = '%' + search_term.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
    return _read_tickets(
        'SELECT ticket_id, employee_name, department, category, '
        'subject, priority, status, assigned_to FROM helpdesk.tickets '
        "WHERE LOWER(CAST(ticket_id AS CHAR)) LIKE LOWER(%s) ESCAPE '!' "
        "OR LOWER(employee_name) LIKE LOWER(%s) ESCAPE '!' "
        "OR LOWER(department) LIKE LOWER(%s) ESCAPE '!' "
        "OR LOWER(category) LIKE LOWER(%s) ESCAPE '!' "
        "OR LOWER(subject) LIKE LOWER(%s) ESCAPE '!' "
        "OR LOWER(priority) LIKE LOWER(%s) ESCAPE '!' "
        "OR LOWER(status) LIKE LOWER(%s) ESCAPE '!' "
        "OR LOWER(assigned_to) LIKE LOWER(%s) ESCAPE '!' "
        'ORDER BY ticket_id',
        (pattern,) * 8,
    )


def _read_tickets(query, parameters=None):
    """Execute the fixed ticket read queries and translate connection errors."""
    try:
        with get_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute(query, parameters)
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
STATUSES = ('Open', 'Assigned', 'In Progress', 'Resolved', 'Closed')
EDITABLE_FIELDS = (
    'employee_name', 'department', 'category', 'subject', 'description',
    'priority', 'status', 'assigned_to',
)


class TicketUpdateError(Exception):
    """A safe, user-facing error when updating a ticket fails."""


def validate_ticket_id(ticket_id):
    if type(ticket_id) is not int or not 1 <= ticket_id <= 2147483647:
        raise ValueError('Ticket ID must be a positive number up to 2147483647.')


def get_ticket(ticket_id):
    """Return one complete ticket, or None when the ID does not exist."""
    validate_ticket_id(ticket_id)
    tickets = _read_tickets(
        'SELECT ticket_id, employee_name, department, category, subject, '
        'description, priority, status, assigned_to, created_at, updated_at, '
        'resolved_at FROM helpdesk.tickets WHERE ticket_id = %s',
        (ticket_id,),
    )
    return tickets[0] if tickets else None


def update_ticket(ticket_id, changes):
    """Validate editable fields and atomically update an existing ticket.

    Return False for an unchanged ticket. Resolution time is preserved while
    Resolved, set on entering Resolved, and cleared for all other statuses.
    """
    validate_ticket_id(ticket_id)
    limits = {'employee_name': 100, 'department': 100, 'category': 50,
              'subject': 150, 'priority': 20, 'status': 20, 'assigned_to': 100}
    validated = {}
    for field, value in changes.items():
        if field not in EDITABLE_FIELDS:
            raise ValueError('Only editable ticket fields can be changed.')
        if field == 'assigned_to' and value is None:
            validated[field] = None
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{field.replace("_", " ").capitalize()} is required.')
        value = value.strip()
        if field in limits and len(value) > limits[field]:
            raise ValueError(f'{field} must be at most {limits[field]} characters.')
        if field == 'description' and len(value.encode('utf-8')) > 65535:
            raise ValueError('Description is too long (maximum 65535 UTF-8 bytes).')
        choices = {'category': CATEGORIES, 'priority': PRIORITIES, 'status': STATUSES}
        if field in choices and value not in choices[field]:
            raise ValueError(f'{field.capitalize()} must be one of: ' + ', '.join(choices[field]))
        validated[field] = value

    try:
        with get_connection() as connection:
            try:
                with connection.cursor(dictionary=True) as cursor:
                    # Recheck existence and lock the row for consistent status transitions.
                    cursor.execute(
                        'SELECT employee_name, department, category, subject, description, '
                        'priority, status, assigned_to, resolved_at FROM helpdesk.tickets '
                        'WHERE ticket_id = %s FOR UPDATE', (ticket_id,),
                    )
                    current = cursor.fetchone()
                    if current is None:
                        raise TicketUpdateError('No ticket found with that ID.')
                    if all(current[field] == value for field, value in validated.items()):
                        connection.rollback()
                        return False
                    values = {**current, **validated}
                    entering_resolved = values['status'] == 'Resolved' and current['status'] != 'Resolved'
                    resolved_at = current['resolved_at'] if values['status'] == 'Resolved' else None
                    cursor.execute(
                        'UPDATE helpdesk.tickets SET employee_name = %s, department = %s, '
                        'category = %s, subject = %s, description = %s, priority = %s, '
                        'status = %s, assigned_to = %s, '
                        'resolved_at = CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE %s END '
                        'WHERE ticket_id = %s',
                        tuple(values[field] for field in EDITABLE_FIELDS)
                        + (entering_resolved, resolved_at, ticket_id),
                    )
                connection.commit()
                return True
            except (mysql.connector.Error, TicketUpdateError):
                try:
                    connection.rollback()
                except mysql.connector.Error:
                    pass
                raise
    except ValueError:
        raise TicketUpdateError(
            'Check your environment settings. DB_NAME must be helpdesk and '
            'all required connection settings must be supplied.'
        ) from None
    except mysql.connector.Error as error:
        raise TicketUpdateError(
            f'Ticket update could not be confirmed (MySQL error code: {error.errno}). '
            'Check your connection and use View Tickets before retrying.'
        ) from None


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
