"""Ticket history setup, reads, and writes in the helpdesk database only."""
from pathlib import Path

import mysql.connector

from database import get_connection


CREATE_TICKET_HISTORY_SQL = (
    Path(__file__).parent / 'database' / 'ticket_history.sql'
).read_text(encoding='utf-8').strip()
CONFIGURATION_MESSAGE = (
    'Check your environment settings. DB_NAME must be helpdesk and '
    'all required connection settings must be supplied.'
)
SETUP_MESSAGE = 'Run python setup_ticket_history.py to set up helpdesk.ticket_history.'


class TicketHistoryReadError(Exception):
    """A safe, user-facing error when ticket history cannot be retrieved."""


class TicketHistorySetupError(Exception):
    """A safe, user-facing error when ticket history setup fails."""


def setup_ticket_history_table():
    """Create only the history table, leaving existing ticket rows unchanged."""
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(CREATE_TICKET_HISTORY_SQL)
                # MySQL commits CREATE TABLE automatically.
    except ValueError:
        raise TicketHistorySetupError(CONFIGURATION_MESSAGE) from None
    except mysql.connector.Error as error:
        raise TicketHistorySetupError(
            f'Ticket history setup failed (MySQL error code: {error.errno}). '
            'Check that helpdesk.tickets exists and your account has CREATE and REFERENCES permission.'
        ) from None


def get_ticket_history(ticket_id):
    """Read one ticket's activity oldest first, with a stable order for ties."""
    if type(ticket_id) is not int or not 1 <= ticket_id <= 2147483647:
        raise ValueError('Ticket ID must be a positive number up to 2147483647.')
    try:
        with get_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute(
                    'SELECT history_id, ticket_id, action, details, created_at '
                    'FROM helpdesk.ticket_history WHERE ticket_id = %s '
                    'ORDER BY created_at, history_id', (ticket_id,),
                )
                return cursor.fetchall()
    except ValueError:
        raise TicketHistoryReadError(CONFIGURATION_MESSAGE) from None
    except mysql.connector.Error as error:
        if error.errno == 1146:
            raise TicketHistoryReadError(SETUP_MESSAGE) from None
        raise TicketHistoryReadError(
            f'Unable to retrieve ticket history (MySQL error code: {error.errno}). '
            'Check that MySQL is running and your connection settings are correct.'
        ) from None


def record_ticket_created(cursor, ticket_id):
    """Record the actual generated defaults using the ticket's transaction."""
    cursor.execute(
        'INSERT INTO helpdesk.ticket_history (ticket_id, action, details) '
        "SELECT ticket_id, %s, CONCAT('Priority: ', priority, ', Status: ', status) "
        'FROM helpdesk.tickets WHERE ticket_id = %s',
        ('Ticket Created', ticket_id),
    )


def _record_activity(cursor, ticket_id, action, details):
    cursor.execute(
        'INSERT INTO helpdesk.ticket_history (ticket_id, action, details) '
        'VALUES (%s, %s, %s)', (ticket_id, action, details),
    )


def _summarize(value):
    # Long descriptions must not make a valid ticket update exceed TEXT capacity.
    text = '-' if value is None else str(value)
    return text if len(text) <= 250 else text[:250] + '...'


def record_ticket_updated(cursor, ticket_id, before, after):
    """Record only changed fields, using the existing ticket update transaction."""
    old_technician = before['assigned_to'] or None
    new_technician = after['assigned_to'] or None
    id_changed = before.get('assigned_technician_id') != after.get('assigned_technician_id')
    if old_technician != new_technician or id_changed:
        if new_technician is None:
            action, details = 'Technician Unassigned', old_technician
        elif old_technician is None:
            action, details = 'Technician Assigned', new_technician
        else:
            action, details = 'Technician Reassigned', f'{old_technician} -> {new_technician}'
            if old_technician == new_technician:
                details = (f'{old_technician} (ID: {before.get("assigned_technician_id") or "unknown"}) -> '
                           f'{new_technician} (ID: {after.get("assigned_technician_id")})')
        _record_activity(cursor, ticket_id, action, details)
    for field, action in (('status', 'Status Changed'), ('priority', 'Priority Changed')):
        if before[field] != after[field]:
            _record_activity(cursor, ticket_id, action, f'{before[field]} -> {after[field]}')
    information = []
    for field, label in (('employee_name', 'Employee name'), ('department', 'Department'),
                         ('category', 'Category'), ('subject', 'Subject'), ('description', 'Description')):
        if before[field] != after[field]:
            information.append(f'{label}: {_summarize(before[field])} -> {_summarize(after[field])}')
    if information:
        _record_activity(cursor, ticket_id, 'Ticket Information Updated', '; '.join(information))
