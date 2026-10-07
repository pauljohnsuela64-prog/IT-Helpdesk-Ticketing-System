"""Read-only, Admin-authorized ticket reporting in helpdesk."""
import mysql.connector

from database import get_connection
from technician_repository import validate_technician_id
from ticket_repository import CATEGORIES, PRIORITIES, STATUSES
from user_repository import UserManagementPermissionError, _require_active_admin, validate_user_id


class ReportReadError(Exception):
    """Safe report feedback without credentials or raw server messages."""


class ReportPermissionError(ReportReadError):
    """An Active Admin account is required to read or export reports."""


def validate_report_filters(status='All', priority='All', category='All', assigned_technician_id=None):
    filters = {}
    for field, value, choices in (('status', status, STATUSES), ('priority', priority, PRIORITIES),
                                  ('category', category, CATEGORIES)):
        if value is None or value == 'All':
            filters[field] = None
        elif value in choices:
            filters[field] = value
        else:
            raise ValueError(f'Choose All or one of the listed {field} options.')
    if assigned_technician_id is not None:
        validate_technician_id(assigned_technician_id)
    filters['assigned_technician_id'] = assigned_technician_id
    return filters


def _admin_read(user_id, query=None, parameters=()):
    try:
        validate_user_id(user_id)
    except ValueError:
        raise ReportPermissionError('You do not have permission to perform this action.') from None
    try:
        with get_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                _require_active_admin(cursor, user_id)
                if query is None:
                    return None
                cursor.execute(query, parameters)
                return cursor.fetchall()
    except UserManagementPermissionError as error:
        raise ReportPermissionError(str(error)) from None
    except ValueError:
        raise ReportReadError('Check the connection settings. DB_NAME must be helpdesk.') from None
    except mysql.connector.Error as error:
        raise ReportReadError(f'Unable to load reports (MySQL error code: {error.errno}). '
                              'Check the database connection and try Generate Report again.') from None


def authorize_report_access(user_id):
    """Recheck the current account before exporting an already displayed snapshot."""
    _admin_read(user_id)


def get_report_technicians(user_id):
    """Inactive technicians remain available for reporting on older assignments."""
    return _admin_read(user_id,
                       'SELECT technician_id, full_name, status FROM helpdesk.technicians ORDER BY technician_id')


def get_ticket_report(user_id, *, status='All', priority='All', category='All', assigned_technician_id=None):
    """Combine optional exact filters with AND; bind every supplied filter value."""
    filters = validate_report_filters(status, priority, category, assigned_technician_id)
    clauses, parameters = [], []
    # Only these fixed identifiers enter the query; input is always a parameter.
    for field, value in filters.items():
        if value is not None:
            clauses.append(f'{field} = %s')
            parameters.append(value)
    query = ('SELECT ticket_id, employee_name, department, category, subject, priority, status, '
             'assigned_to, created_at, updated_at, resolved_at FROM helpdesk.tickets '
             + ('WHERE ' + ' AND '.join(clauses) + ' ' if clauses else '') + 'ORDER BY ticket_id')
    return _admin_read(user_id, query, tuple(parameters))
