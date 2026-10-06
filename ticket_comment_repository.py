"""Ticket note setup, reads, and writes in the helpdesk database only."""
from pathlib import Path

import mysql.connector

from database import get_connection
from input_validation import validate_comment_text
from technician_repository import get_active_technician, validate_technician_id
from ticket_repository import validate_ticket_id
from user_repository import INVALID_TECHNICIAN_LINK, validate_user_id


CREATE_TICKET_COMMENTS_SQL = (
    Path(__file__).parent / 'database' / 'ticket_comments.sql'
).read_text(encoding='utf-8').strip()
CONFIGURATION_MESSAGE = (
    'Check your environment settings. DB_NAME must be helpdesk and '
    'all required connection settings must be supplied.'
)
SETUP_MESSAGE = 'Run python setup_ticket_comments.py to set up helpdesk.ticket_comments.'


class TicketCommentReadError(Exception):
    """A safe, user-facing error when ticket notes cannot be retrieved."""


class TicketCommentCreateError(Exception):
    """A safe, user-facing error when a ticket note cannot be saved."""


class TicketCommentDeleteError(Exception):
    """A safe, user-facing error when a ticket note cannot be deleted."""


class TicketCommentSetupError(Exception):
    """A safe, user-facing error when ticket comments setup fails."""


def setup_ticket_comments_table():
    """Create only the comments table; leave existing tables unchanged."""
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(CREATE_TICKET_COMMENTS_SQL)
                # MySQL commits CREATE TABLE automatically.
    except ValueError:
        raise TicketCommentSetupError(CONFIGURATION_MESSAGE) from None
    except mysql.connector.Error as error:
        raise TicketCommentSetupError(
            f'Ticket comments setup failed (MySQL error code: {error.errno}). '
            'Check that helpdesk.tickets and helpdesk.technicians exist and '
            'your account has CREATE and REFERENCES permission.'
        ) from None


def get_ticket_comments(ticket_id):
    """Read notes oldest first, including notes written by inactive technicians."""
    validate_ticket_id(ticket_id)
    try:
        with get_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute(
                    'SELECT c.comment_id, c.ticket_id, c.technician_id, c.comment_text, '
                    'c.created_at, t.full_name AS technician_name '
                    'FROM helpdesk.ticket_comments AS c '
                    'LEFT JOIN helpdesk.technicians AS t ON t.technician_id = c.technician_id '
                    'WHERE c.ticket_id = %s ORDER BY c.created_at, c.comment_id',
                    (ticket_id,),
                )
                return cursor.fetchall()
    except ValueError:
        raise TicketCommentReadError(CONFIGURATION_MESSAGE) from None
    except mysql.connector.Error as error:
        if error.errno == 1146:
            raise TicketCommentReadError(SETUP_MESSAGE) from None
        raise TicketCommentReadError(
            f'Unable to retrieve ticket notes (MySQL error code: {error.errno}). '
            'Check that MySQL is running and your connection settings are correct.'
        ) from None


def add_ticket_comment(ticket_id, technician_id, comment_text):
    """Save a note with an active author, without changing the ticket or its history."""
    validate_technician_id(technician_id)
    return _save_ticket_comment(ticket_id, technician_id, comment_text)


def add_ticket_comment_for_user(ticket_id, user_id, comment_text, technician_id=None):
    """Technicians use their database link; Admins may choose an Active author."""
    validate_user_id(user_id)
    if technician_id is not None:
        validate_technician_id(technician_id)
    return _save_ticket_comment(ticket_id, technician_id, comment_text, user_id=user_id)


def _save_ticket_comment(ticket_id, technician_id, comment_text, user_id=None):
    validate_ticket_id(ticket_id)
    comment_text = validate_comment_text(comment_text)
    try:
        with get_connection() as connection:
            try:
                with connection.cursor(dictionary=True) as cursor:
                    # Locks prevent deletion or deactivation between checking and saving.
                    cursor.execute(
                        'SELECT ticket_id FROM helpdesk.tickets '
                        'WHERE ticket_id = %s FOR UPDATE', (ticket_id,),
                    )
                    if cursor.fetchone() is None:
                        raise TicketCommentCreateError('No ticket found with that ID. No note was saved.')
                    if user_id is not None:
                        cursor.execute('SELECT role, status, technician_id FROM helpdesk.users '
                                       'WHERE user_id = %s FOR UPDATE', (user_id,))
                        user = cursor.fetchone()
                        if user is None or user['status'] != 'Active' or user['role'] not in ('Admin', 'Technician'):
                            raise TicketCommentCreateError('You do not have permission to perform this action. Please log in again.')
                        if user['role'] == 'Technician':
                            if user['technician_id'] is None or (technician_id is not None and technician_id != user['technician_id']):
                                raise TicketCommentCreateError(INVALID_TECHNICIAN_LINK)
                            technician_id = user['technician_id']
                        elif technician_id is None:
                            raise TicketCommentCreateError('Please select an Active technician as the note author.')
                    if get_active_technician(technician_id, cursor) is None:
                        if user_id is not None and user['role'] == 'Technician':
                            raise TicketCommentCreateError(INVALID_TECHNICIAN_LINK)
                        raise TicketCommentCreateError(
                            'The selected technician is no longer available or Active. '
                            'No note was saved. Please select an active technician again.'
                        )
                    cursor.execute(
                        'INSERT INTO helpdesk.ticket_comments (ticket_id, technician_id, comment_text) '
                        'VALUES (%s, %s, %s)',
                        (ticket_id, technician_id, comment_text),
                    )
                    comment_id = cursor.lastrowid
                connection.commit()
                return comment_id
            except (mysql.connector.Error, TicketCommentCreateError):
                try:
                    connection.rollback()
                except mysql.connector.Error:
                    pass
                raise
    except ValueError:
        raise TicketCommentCreateError(CONFIGURATION_MESSAGE) from None
    except mysql.connector.Error as error:
        if user_id is not None and error.errno == 1054:
            raise TicketCommentCreateError('Run python setup_user_technicians.py to add the technician relationship.') from None
        if error.errno == 1146:
            raise TicketCommentCreateError(SETUP_MESSAGE) from None
        raise TicketCommentCreateError(
            f'Ticket note creation could not be confirmed (MySQL error code: {error.errno}). '
            'Check your connection and use View Ticket Notes before retrying.'
        ) from None


def validate_comment_id(comment_id):
    if type(comment_id) is not int or not 1 <= comment_id <= 2147483647:
        raise ValueError('Comment ID must be a positive number up to 2147483647.')


def delete_ticket_comment(ticket_id, comment_id):
    """Delete one note belonging to this ticket; return False if it is absent."""
    validate_ticket_id(ticket_id)
    validate_comment_id(comment_id)
    try:
        with get_connection() as connection:
            try:
                with connection.cursor() as cursor:
                    # Recheck ownership and lock the note after the CLI preview.
                    cursor.execute(
                        'SELECT comment_id FROM helpdesk.ticket_comments '
                        'WHERE ticket_id = %s AND comment_id = %s FOR UPDATE',
                        (ticket_id, comment_id),
                    )
                    if cursor.fetchone() is None:
                        connection.rollback()
                        return False
                    cursor.execute(
                        'DELETE FROM helpdesk.ticket_comments '
                        'WHERE ticket_id = %s AND comment_id = %s LIMIT 1',
                        (ticket_id, comment_id),
                    )
                    if cursor.rowcount != 1:
                        raise TicketCommentDeleteError(
                            'Note deletion could not be confirmed. '
                            'Use View Ticket Notes before retrying.'
                        )
                connection.commit()
                return True
            except (mysql.connector.Error, TicketCommentDeleteError):
                try:
                    connection.rollback()
                except mysql.connector.Error:
                    pass
                raise
    except ValueError:
        raise TicketCommentDeleteError(CONFIGURATION_MESSAGE) from None
    except mysql.connector.Error as error:
        if error.errno == 1146:
            raise TicketCommentDeleteError(SETUP_MESSAGE) from None
        raise TicketCommentDeleteError(
            f'Note deletion could not be confirmed (MySQL error code: {error.errno}). '
            'Check your connection and use View Ticket Notes before retrying.'
        ) from None
