"""Ticket database operations, separate from the command-line interface."""
import mysql.connector

from database import get_connection
from input_validation import validate_text
from technician_repository import TechnicianReadError, get_active_technician, get_technician, validate_technician_id
from ticket_history_repository import (
    SETUP_MESSAGE, TicketHistoryWriteError, record_ticket_created, record_ticket_updated,
)
from ticket_access import (
    ADMIN_ASSIGNMENT_ONLY, ASSIGNMENT_SETUP_MESSAGE, TicketAccessError, authorize_ticket_write,
)
from user_repository import validate_user_id


class TicketReadError(Exception):
    """A safe, user-facing error when tickets cannot be retrieved."""


MISSING_TECHNICIAN_LINK = 'Your account is not linked to a technician record. Contact an administrator.'


class AssignedTicketsLinkError(TicketReadError):
    """The current session cannot resolve a technician record for its own view."""


_TICKET_LIST_SQL = (
    'SELECT ticket_id, employee_name, department, category, '
    'subject, priority, status, assigned_to, created_at, assigned_technician_id FROM helpdesk.tickets '
)
_SEARCH_CLAUSE = (
    "LOWER(CAST(ticket_id AS CHAR)) LIKE LOWER(%s) ESCAPE '!' "
    "OR LOWER(employee_name) LIKE LOWER(%s) ESCAPE '!' "
    "OR LOWER(department) LIKE LOWER(%s) ESCAPE '!' "
    "OR LOWER(category) LIKE LOWER(%s) ESCAPE '!' "
    "OR LOWER(subject) LIKE LOWER(%s) ESCAPE '!' "
    "OR LOWER(priority) LIKE LOWER(%s) ESCAPE '!' "
    "OR LOWER(status) LIKE LOWER(%s) ESCAPE '!' "
    "OR LOWER(assigned_to) LIKE LOWER(%s) ESCAPE '!'"
)


def _search_pattern(search_term):
    search_term = search_term.strip()
    if not search_term:
        return None
    # Escape LIKE wildcards so %, _ and ! match literal text in either view.
    return '%' + search_term.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'


def get_tickets():
    """Return ticket dictionaries from the helpdesk database only."""
    return _read_tickets(
        _TICKET_LIST_SQL + 'ORDER BY ticket_id'
    )


def search_tickets(search_term):
    """Find case-insensitive literal substrings using a read-only SELECT."""
    pattern = _search_pattern(search_term)
    if pattern is None:
        return []
    return _read_tickets(
        _TICKET_LIST_SQL + 'WHERE ' + _SEARCH_CLAUSE + ' ORDER BY ticket_id',
        (pattern,) * 8,
    )


def get_assigned_tickets(technician_id, search_term=''):
    """Read assignments by linked ID, including inactive records, within a search."""
    try:
        validate_technician_id(technician_id)
    except ValueError:
        raise AssignedTicketsLinkError(MISSING_TECHNICIAN_LINK) from None
    try:
        technician = get_technician(technician_id)
    except TechnicianReadError as error:
        raise TicketReadError(str(error)) from None
    if technician is None or not technician.get('full_name'):
        raise AssignedTicketsLinkError(MISSING_TECHNICIAN_LINK)
    pattern = _search_pattern(search_term)
    # Parentheses keep every search field inside the exact assignment scope.
    return _read_tickets(
        _TICKET_LIST_SQL + 'WHERE assigned_technician_id = %s '
        + ('AND (' + _SEARCH_CLAUSE + ') ' if pattern is not None else '')
        + 'ORDER BY ticket_id',
        (technician_id,) + ((pattern,) * 8 if pattern is not None else ()),
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
        if error.errno == 1054:
            raise TicketReadError(ASSIGNMENT_SETUP_MESSAGE) from None
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
        'resolved_at, assigned_technician_id FROM helpdesk.tickets WHERE ticket_id = %s',
        (ticket_id,),
    )
    return tickets[0] if tickets else None


class TicketDeleteError(Exception):
    """A safe, user-facing error when deleting a ticket fails."""


def delete_ticket(ticket_id):
    """Delete one exact ID after CLI confirmation; return False if absent."""
    validate_ticket_id(ticket_id)
    try:
        with get_connection() as connection:
            try:
                with connection.cursor() as cursor:
                    # Recheck and lock the ticket in case it disappeared after preview.
                    cursor.execute(
                        'SELECT ticket_id FROM helpdesk.tickets '
                        'WHERE ticket_id = %s FOR UPDATE', (ticket_id,),
                    )
                    if cursor.fetchone() is None:
                        connection.rollback()
                        return False
                    cursor.execute(
                        'DELETE FROM helpdesk.tickets WHERE ticket_id = %s LIMIT 1',
                        (ticket_id,),
                    )
                    if cursor.rowcount != 1:
                        raise TicketDeleteError(
                            'Ticket deletion could not be confirmed. '
                            'Use View Tickets before retrying.'
                        )
                connection.commit()
                return True
            except (mysql.connector.Error, TicketDeleteError):
                try:
                    connection.rollback()
                except mysql.connector.Error:
                    pass
                raise
    except ValueError:
        raise TicketDeleteError(
            'Check your environment settings. DB_NAME must be helpdesk and '
            'all required connection settings must be supplied.'
        ) from None
    except mysql.connector.Error as error:
        raise TicketDeleteError(
            f'Ticket deletion could not be confirmed (MySQL error code: {error.errno}). '
            'Check your connection and use View Tickets before retrying.'
        ) from None


def update_ticket_for_user(ticket_id, user_id, changes, *, session_technician_id=None, technician_id=None):
    """Authenticated GUI updates always recheck current ownership in the transaction."""
    validate_user_id(user_id)
    return update_ticket(ticket_id, changes, technician_id=technician_id,
                         user_id=user_id, session_technician_id=session_technician_id)


def update_ticket(ticket_id, changes, *, technician_id=None, user_id=None, session_technician_id=None):
    """Validate editable fields and atomically update an existing ticket.

    Return False for an unchanged ticket. Resolution time is preserved while
    Resolved, set on entering Resolved, and cleared for all other statuses.
    A technician ID selects an active technician; assigned_to=None unassigns.
    Selecting a technician changes Open to Assigned unless another status is chosen.
    """
    validate_ticket_id(ticket_id)
    if user_id is not None:
        validate_user_id(user_id)
    if technician_id is not None:
        validate_technician_id(technician_id)
        if 'assigned_to' in changes:
            raise ValueError('Choose a technician or unassign, not both.')
    limits = {'employee_name': 100, 'department': 100, 'category': 50,
              'subject': 150, 'priority': 20, 'status': 20, 'assigned_to': 100}
    validated = {}
    for field, value in changes.items():
        if field not in EDITABLE_FIELDS:
            raise ValueError('Only editable ticket fields can be changed.')
        if field == 'assigned_to':
            if value is not None:
                raise ValueError('Select an active technician instead of typing a name.')
            validated[field] = None
            continue
        value = validate_text(value, field.replace('_', ' ').capitalize(), limits.get(field))
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
                        'priority, status, assigned_to, resolved_at, assigned_technician_id FROM helpdesk.tickets '
                        'WHERE ticket_id = %s FOR UPDATE', (ticket_id,),
                    )
                    current = cursor.fetchone()
                    if current is None:
                        raise TicketUpdateError('No ticket found with that ID.')
                    if user_id is not None:
                        actor = authorize_ticket_write(cursor, current, user_id, 'update', session_technician_id)
                        if actor['role'] == 'Technician' and (technician_id is not None or 'assigned_to' in changes):
                            raise TicketAccessError(ADMIN_ASSIGNMENT_ONLY)
                    assigned_id = current.get('assigned_technician_id')
                    if technician_id is not None:
                        technician = get_active_technician(technician_id, cursor)
                        if technician is None:
                            raise TicketUpdateError(
                                'The selected technician is no longer active or does not exist. '
                                'Choose an active technician and try again.'
                            )
                        validated['assigned_to'] = technician['full_name']
                        assigned_id = technician_id
                        if current['status'] == 'Open' and validated.get('status', 'Open') == 'Open':
                            validated['status'] = 'Assigned'
                    elif 'assigned_to' in validated:
                        assigned_id = None
                    if (all(current[field] == value for field, value in validated.items())
                            and assigned_id == current.get('assigned_technician_id')):
                        connection.rollback()
                        return False
                    values = {**current, **validated}
                    values['assigned_technician_id'] = assigned_id
                    entering_resolved = values['status'] == 'Resolved' and current['status'] != 'Resolved'
                    resolved_at = current['resolved_at'] if values['status'] == 'Resolved' else None
                    cursor.execute(
                        'UPDATE helpdesk.tickets SET employee_name = %s, department = %s, '
                        'category = %s, subject = %s, description = %s, priority = %s, '
                        'status = %s, assigned_to = %s, assigned_technician_id = %s, '
                        'resolved_at = CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE %s END '
                        'WHERE ticket_id = %s',
                        tuple(values[field] for field in EDITABLE_FIELDS)
                        + (assigned_id, entering_resolved, resolved_at, ticket_id),
                    )
                    record_ticket_updated(cursor, ticket_id, current, values, performed_by_user_id=user_id)
                connection.commit()
                return True
            except (mysql.connector.Error, TicketUpdateError, TicketAccessError, TicketHistoryWriteError):
                try:
                    connection.rollback()
                except mysql.connector.Error:
                    pass
                raise
    except TicketAccessError as error:
        raise TicketUpdateError(str(error)) from None
    except TicketHistoryWriteError as error:
        raise TicketUpdateError(str(error)) from None
    except ValueError:
        raise TicketUpdateError(
            'Check your environment settings. DB_NAME must be helpdesk and '
            'all required connection settings must be supplied.'
        ) from None
    except mysql.connector.Error as error:
        if error.errno == 1054:
            raise TicketUpdateError(ASSIGNMENT_SETUP_MESSAGE) from None
        raise TicketUpdateError(
            f'Ticket update could not be confirmed (MySQL error code: {error.errno}). '
            'Check your connection and use View Tickets before retrying. '
            + (SETUP_MESSAGE if error.errno == 1146 else '')
        ) from None


class TicketCreateError(Exception):
    """A safe, user-facing error when saving a ticket fails."""


def create_ticket_for_user(user_id, employee_name, department, category, subject, description, priority):
    """GUI creation requires a real session ID; never fall back to CLI attribution."""
    validate_user_id(user_id)
    return create_ticket(employee_name, department, category, subject, description, priority, user_id=user_id)


def create_ticket(employee_name, department, category, subject, description, priority, *, user_id=None):
    """Validate and save one ticket; MySQL supplies IDs and default values."""
    if user_id is not None:
        validate_user_id(user_id)
    fields = {
        'Employee name': (employee_name, 100),
        'Department': (department, 100),
        'Subject': (subject, 150),
        'Description': (description, None),
    }
    validated = {
        label: validate_text(value, label, limit)
        for label, (value, limit) in fields.items()
    }
    if category not in CATEGORIES:
        raise ValueError('Choose one of the listed categories.')
    if priority not in PRIORITIES:
        raise ValueError('Choose one of the listed priorities.')

    try:
        with get_connection() as connection:
            try:
                with connection.cursor() as cursor:
                    if user_id is not None:
                        cursor.execute('SELECT role, status FROM helpdesk.users WHERE user_id = %s FOR UPDATE',
                                       (user_id,))
                        actor = cursor.fetchone()
                        if actor is None or actor[0] not in ('Admin', 'Technician') or actor[1] != 'Active':
                            raise TicketCreateError('You do not have permission to perform this action. Please log in again.')
                    cursor.execute(
                        'INSERT INTO helpdesk.tickets '
                        '(employee_name, department, category, subject, description, priority) '
                        'VALUES (%s, %s, %s, %s, %s, %s)',
                        (validated['Employee name'], validated['Department'], category,
                         validated['Subject'], validated['Description'], priority),
                    )
                    ticket_id = cursor.lastrowid
                    record_ticket_created(cursor, ticket_id, performed_by_user_id=user_id)
                connection.commit()
                return ticket_id
            except (mysql.connector.Error, TicketCreateError, TicketHistoryWriteError):
                try:
                    connection.rollback()
                except mysql.connector.Error:
                    pass
                raise
    except TicketHistoryWriteError as error:
        raise TicketCreateError(str(error)) from None
    except ValueError:
        raise TicketCreateError(
            'Check your environment settings. DB_NAME must be helpdesk and '
            'all required connection settings must be supplied.'
        ) from None
    except mysql.connector.Error as error:
        raise TicketCreateError(
            f'Ticket creation could not be confirmed (MySQL error code: {error.errno}). '
            'Check your connection and use View Tickets before retrying. '
            + (SETUP_MESSAGE if error.errno == 1146 else '')
        ) from None
