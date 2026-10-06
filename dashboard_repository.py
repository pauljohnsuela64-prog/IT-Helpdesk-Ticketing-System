"""Read-only dashboard statistics from the existing helpdesk tables."""
import mysql.connector

from database import get_connection


STATISTIC_FIELDS = (
    'total_tickets', 'open_tickets', 'assigned_tickets', 'in_progress_tickets',
    'resolved_tickets', 'closed_tickets', 'critical_tickets', 'active_technicians',
)


class DashboardReadError(Exception):
    """A safe, user-facing error when dashboard statistics cannot be loaded."""


def get_dashboard_statistics():
    """Aggregate global ticket and technician counts in one read-only statement.

    The technician scalar subquery avoids multiplying ticket counts through a join.
    Search and GUI status filters deliberately do not affect these global counts.
    """
    try:
        with get_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:
                cursor.execute(
                    'SELECT COUNT(*) AS total_tickets, '
                    'COALESCE(SUM(status = %s), 0) AS open_tickets, '
                    'COALESCE(SUM(status = %s), 0) AS assigned_tickets, '
                    'COALESCE(SUM(status = %s), 0) AS in_progress_tickets, '
                    'COALESCE(SUM(status = %s), 0) AS resolved_tickets, '
                    'COALESCE(SUM(status = %s), 0) AS closed_tickets, '
                    'COALESCE(SUM(priority = %s), 0) AS critical_tickets, '
                    '(SELECT COUNT(*) FROM helpdesk.technicians WHERE status = %s) '
                    'AS active_technicians FROM helpdesk.tickets',
                    ('Open', 'Assigned', 'In Progress', 'Resolved', 'Closed', 'Critical', 'Active'),
                )
                counts = cursor.fetchone()
                return {field: int(counts[field] or 0) for field in STATISTIC_FIELDS}
    except ValueError:
        raise DashboardReadError(
            'Check your environment settings. DB_NAME must be helpdesk and '
            'all required connection settings must be supplied.'
        ) from None
    except mysql.connector.Error as error:
        raise DashboardReadError(
            f'Unable to retrieve dashboard statistics (MySQL error code: {error.errno}). '
            'Check that MySQL is running and the helpdesk tables are available.'
        ) from None
