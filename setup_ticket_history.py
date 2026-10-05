"""Create helpdesk.ticket_history: python setup_ticket_history.py."""
import sys

from ticket_history_repository import TicketHistorySetupError, setup_ticket_history_table


def main():
    try:
        setup_ticket_history_table()
    except TicketHistorySetupError as error:
        print(error)
        return 1
    print('Ticket history setup complete: helpdesk.ticket_history is available.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
