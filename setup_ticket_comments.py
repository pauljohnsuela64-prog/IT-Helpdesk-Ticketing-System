"""Create helpdesk.ticket_comments: python setup_ticket_comments.py."""
import sys

from ticket_comment_repository import TicketCommentSetupError, setup_ticket_comments_table


def main():
    try:
        setup_ticket_comments_table()
    except TicketCommentSetupError as error:
        print(error)
        return 1
    print('Ticket comments setup complete: helpdesk.ticket_comments is available.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
