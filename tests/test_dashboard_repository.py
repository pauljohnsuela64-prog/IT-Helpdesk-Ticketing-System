"""Dashboard SELECT safeguards; no live database or schema changes required."""
from decimal import Decimal
import unittest
from unittest.mock import MagicMock, patch

import mysql.connector
import dashboard_repository as repository


class DashboardRepositoryTests(unittest.TestCase):
    def connection(self, counts):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = counts
        return connection, cursor

    def test_all_eight_counts_are_python_integers_from_one_parameterized_select(self):
        expected = dict(total_tickets=25, open_tickets=6, assigned_tickets=4,
                        in_progress_tickets=5, resolved_tickets=8, closed_tickets=2,
                        critical_tickets=3, active_technicians=4)
        connection, cursor = self.connection({key: Decimal(value) for key, value in expected.items()})
        with patch.object(repository, 'get_connection', return_value=connection):
            result = repository.get_dashboard_statistics()
        self.assertEqual(result, expected)
        self.assertTrue(all(type(value) is int for value in result.values()))
        connection.cursor.assert_called_once_with(dictionary=True)
        cursor.execute.assert_called_once()
        sql, parameters = cursor.execute.call_args.args
        self.assertTrue(sql.startswith('SELECT COUNT(*) AS total_tickets, '))
        self.assertEqual(parameters, ('Open', 'Assigned', 'In Progress', 'Resolved', 'Closed', 'Critical', 'Active'))
        self.assertEqual(sql.count('%s'), len(parameters))
        for field in ('open_tickets', 'assigned_tickets', 'in_progress_tickets', 'resolved_tickets', 'closed_tickets'):
            self.assertIn(f'COALESCE(SUM(status = %s), 0) AS {field}', sql)
        self.assertIn('COALESCE(SUM(priority = %s), 0) AS critical_tickets', sql)
        self.assertIn('(SELECT COUNT(*) FROM helpdesk.technicians WHERE status = %s) AS active_technicians', sql)
        self.assertTrue(sql.endswith('FROM helpdesk.tickets'))
        self.assertNotIn('JOIN', sql)
        self.assertNotIn('GROUP BY', sql)
        self.assertNotIn('FOR UPDATE', sql)
        for mutation in ('INSERT', 'UPDATE ', 'DELETE', 'CREATE', 'ALTER', 'DROP'):
            self.assertNotIn(mutation, sql)
        connection.commit.assert_not_called()
        connection.rollback.assert_not_called()

    def test_empty_ticket_aggregates_are_zero_while_active_technicians_are_counted_independently(self):
        counts = dict.fromkeys(repository.STATISTIC_FIELDS, None)
        counts['total_tickets'] = 0
        counts['active_technicians'] = 4
        connection, _ = self.connection(counts)
        with patch.object(repository, 'get_connection', return_value=connection):
            result = repository.get_dashboard_statistics()
        self.assertEqual(result, {**dict.fromkeys(repository.STATISTIC_FIELDS, 0), 'active_technicians': 4})

    def test_configuration_failure_is_friendly_and_does_not_disclose_private_values(self):
        with patch.object(repository, 'get_connection', side_effect=ValueError('private setting')), \
             self.assertRaises(repository.DashboardReadError) as caught:
            repository.get_dashboard_statistics()
        self.assertIn('DB_NAME must be helpdesk', str(caught.exception))
        self.assertNotIn('private setting', str(caught.exception))

    def test_connection_or_query_failures_are_safe_and_never_commit(self):
        for during_query in (False, True):
            with self.subTest(during_query=during_query):
                connection, cursor = self.connection({})
                error = mysql.connector.Error('private credentials', errno=1146)
                if during_query:
                    cursor.execute.side_effect = error
                with patch.object(repository, 'get_connection', return_value=connection,
                                  side_effect=None if during_query else error), \
                     self.assertRaises(repository.DashboardReadError) as caught:
                    repository.get_dashboard_statistics()
                self.assertIn('1146', str(caught.exception))
                self.assertNotIn('private credentials', str(caught.exception))
                connection.commit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
