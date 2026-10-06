"""Shared ticket ownership checks; GUI previews and transactional saves use IDs."""
from technician_repository import get_active_technician, validate_technician_id
from user_repository import INVALID_TECHNICIAN_LINK


UPDATE_ASSIGNED_ONLY = 'You can only update tickets assigned to you.'
NOTE_ASSIGNED_ONLY = 'You can only add notes to tickets assigned to you.'
ADMIN_ASSIGNMENT_ONLY = 'Only Admin users can change ticket assignments.'
PERMISSION_DENIED = 'You do not have permission to perform this action. Please log in again.'
ASSIGNMENT_SETUP_MESSAGE = 'Run python setup_ticket_assignments.py to add ticket assignment IDs.'
DATABASE_LINK = object()


class TicketAccessError(Exception):
    """Safe feedback for an invalid session, link, or ticket ownership."""


def check_session_ticket_access(ticket, user, action):
    """Check a GUI snapshot without SQL. Saves must also recheck the database."""
    if not user or user.get('status') != 'Active' or user.get('role') not in ('Admin', 'Technician'):
        raise TicketAccessError(PERMISSION_DENIED)
    if user['role'] == 'Admin':
        return
    try:
        validate_technician_id(user.get('technician_id'))
    except ValueError:
        raise TicketAccessError(INVALID_TECHNICIAN_LINK) from None
    if not ticket or ticket.get('assigned_technician_id') != user['technician_id']:
        raise TicketAccessError(UPDATE_ASSIGNED_ONLY if action == 'update' else NOTE_ASSIGNED_ONLY)


def authorize_ticket_write(cursor, ticket, user_id, action, session_technician_id=DATABASE_LINK):
    """Lock and recheck account/link/author while the caller holds the ticket lock.

    Admin and CLI assignment writes also lock tickets first. Consequently an
    assignment cannot change between this ownership check and the final write.
    """
    cursor.execute('SELECT role, status, technician_id FROM helpdesk.users '
                   'WHERE user_id = %s FOR UPDATE', (user_id,))
    user = cursor.fetchone()
    if not user or user.get('status') != 'Active' or user.get('role') not in ('Admin', 'Technician'):
        raise TicketAccessError(PERMISSION_DENIED)
    if user['role'] == 'Admin':
        return user
    linked_id = user.get('technician_id')
    try:
        validate_technician_id(linked_id)
        if session_technician_id is not DATABASE_LINK:
            validate_technician_id(session_technician_id)
            if session_technician_id != linked_id:
                raise TicketAccessError(INVALID_TECHNICIAN_LINK)
    except ValueError:
        raise TicketAccessError(INVALID_TECHNICIAN_LINK) from None
    if get_active_technician(linked_id, cursor) is None:
        raise TicketAccessError(INVALID_TECHNICIAN_LINK)
    check_session_ticket_access(ticket, user, action)
    return user
