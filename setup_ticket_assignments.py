"""Repeatable assignment-ID migration for helpdesk.tickets only."""
import argparse
import getpass
import sys

import mysql.connector
from setup_user_technicians import UserTechnicianSetupError, _column, _setup_connection


class TicketAssignmentSetupError(UserTechnicianSetupError):
    """Safe feedback when ticket assignment setup cannot be completed."""


BACKFILL_SQL = (
    'UPDATE helpdesk.tickets AS ticket JOIN ('
    'SELECT CONVERT(full_name USING utf8mb4) COLLATE utf8mb4_unicode_ci AS match_name, '
    'MIN(technician_id) AS technician_id FROM helpdesk.technicians '
    'GROUP BY match_name HAVING COUNT(*) = 1) AS technician '
    'ON technician.match_name = CONVERT(ticket.assigned_to USING utf8mb4) COLLATE utf8mb4_unicode_ci '
    'SET ticket.assigned_technician_id = technician.technician_id, ticket.updated_at = ticket.updated_at '
    'WHERE ticket.assigned_technician_id IS NULL AND ticket.assigned_to IS NOT NULL'
)


def migrate_ticket_assignments(connection):
    """Keep display names; backfill only unambiguous legacy name assignments."""
    with connection.cursor(dictionary=True) as cursor:
        cursor.execute('SELECT GET_LOCK(%s, %s) AS acquired', ('helpdesk.tickets.assignment_setup', 5))
        if cursor.fetchone()['acquired'] != 1:
            raise TicketAssignmentSetupError('Ticket assignment setup is busy. Try again after other setup processes close.')
        try:
            target = _column(cursor, 'technicians', 'technician_id')
            if target is None or _column(cursor, 'tickets', 'ticket_id') is None:
                raise TicketAssignmentSetupError('Create helpdesk.tickets and helpdesk.technicians before running this migration.')
            if target['DATA_TYPE'] != 'int':
                raise TicketAssignmentSetupError('The technician ID type is incompatible. No column was changed.')
            unsigned = 'unsigned' in target['COLUMN_TYPE'].lower()
            current = _column(cursor, 'tickets', 'assigned_technician_id')
            if current is None:
                cursor.execute('ALTER TABLE helpdesk.tickets ADD COLUMN assigned_technician_id INT UNSIGNED NULL' if unsigned else
                               'ALTER TABLE helpdesk.tickets ADD COLUMN assigned_technician_id INT NULL')
            elif (current['DATA_TYPE'] != 'int' or current['IS_NULLABLE'] != 'YES'
                  or ('unsigned' in current['COLUMN_TYPE'].lower()) != unsigned):
                raise TicketAssignmentSetupError('The existing assigned_technician_id column is incompatible. No existing column was changed.')
            cursor.execute(
                'SELECT INDEX_NAME FROM information_schema.STATISTICS '
                'WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s AND SEQ_IN_INDEX = 1',
                ('helpdesk', 'tickets', 'assigned_technician_id'),
            )
            if not cursor.fetchall():
                cursor.execute('ALTER TABLE helpdesk.tickets ADD KEY idx_tickets_assigned_technician (assigned_technician_id)')
            cursor.execute(
                'SELECT REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME '
                'FROM information_schema.KEY_COLUMN_USAGE '
                'WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s '
                'AND REFERENCED_TABLE_NAME IS NOT NULL', ('helpdesk', 'tickets', 'assigned_technician_id'),
            )
            references = cursor.fetchall()
            if references:
                if any((row['REFERENCED_TABLE_SCHEMA'], row['REFERENCED_TABLE_NAME'], row['REFERENCED_COLUMN_NAME'])
                       != ('helpdesk', 'technicians', 'technician_id') for row in references):
                    raise TicketAssignmentSetupError('The existing ticket assignment foreign key is incompatible. No foreign key was changed.')
            else:
                cursor.execute('ALTER TABLE helpdesk.tickets ADD CONSTRAINT fk_tickets_assigned_technician '
                               'FOREIGN KEY (assigned_technician_id) REFERENCES helpdesk.technicians (technician_id) '
                               'ON DELETE RESTRICT ON UPDATE RESTRICT')
            # No ticket names, statuses, resolution times, history or notes change.
            # Group and compare with the same explicit charset/collation, even
            # when legacy tickets and technicians have different defaults.
            try:
                cursor.execute(BACKFILL_SQL)
                connection.commit()
            except mysql.connector.Error:
                try:
                    connection.rollback()
                except mysql.connector.Error:
                    pass
                raise
            cursor.execute('SELECT COUNT(*) AS unresolved FROM helpdesk.tickets '
                           'WHERE assigned_to IS NOT NULL AND assigned_technician_id IS NULL')
            return cursor.fetchone()['unresolved']
        finally:
            cursor.execute('SELECT RELEASE_LOCK(%s)', ('helpdesk.tickets.assignment_setup',))
            cursor.fetchone()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Add ID-based ticket assignments in helpdesk safely.')
    parser.add_argument('--admin', action='store_true', help='Prompt privately for the MySQL root password.')
    args = parser.parse_args(argv)
    try:
        with _setup_connection(args.admin) as connection:
            unresolved = migrate_ticket_assignments(connection)
    except UserTechnicianSetupError as error:
        print(error)
        return 1
    except ValueError:
        print('Check the connection settings. DB_NAME must be helpdesk.')
        return 1
    except mysql.connector.Error as error:
        if error.errno in (1142, 1143):
            print(f'Ticket assignment setup failed (MySQL error code: {error.errno}). '
                  'This MySQL account lacks a required setup privilege. '
                  'Run: winpty .venv/Scripts/python.exe setup_ticket_assignments.py --admin')
            return 1
        if error.errno == 1267:
            print('Ticket assignment setup failed (MySQL error code: 1267). '
                  'The legacy name comparison encountered incompatible text collations. '
                  'Use the updated setup_ticket_assignments.py script and rerun the same command. '
                  'Existing table collations do not need to be changed.')
            return 1
        print(f'Ticket assignment setup failed (MySQL error code: {error.errno}). '
              'Check existing tables and ALTER/INDEX/REFERENCES/UPDATE permissions. Partial setup can be rerun. '
              'For root setup in Git Bash, run: winpty .venv/Scripts/python.exe setup_ticket_assignments.py --admin')
        return 1
    except (getpass.GetPassWarning, OSError):
        print('Hidden input is unavailable. Run: winpty .venv/Scripts/python.exe setup_ticket_assignments.py --admin')
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nSetup interrupted. It is safe to rerun the migration.')
        return 1
    print('Ticket assignment ID setup complete in helpdesk.tickets.')
    if unresolved:
        print(f'{unresolved} legacy assignments could not be linked uniquely. '
              'An Admin must select the intended technician in Update Ticket before Technician edits are allowed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
