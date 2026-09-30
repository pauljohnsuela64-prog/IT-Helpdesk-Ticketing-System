"""Command-line entry point for the IT Help Desk Ticketing System."""
from ticket_repository import TicketReadError, get_tickets


TICKET_FIELDS = (
    ('Ticket ID', 'ticket_id'),
    ('Employee', 'employee_name'),
    ('Department', 'department'),
    ('Category', 'category'),
    ('Subject', 'subject'),
    ('Priority', 'priority'),
    ('Status', 'status'),
    ('Assigned To', 'assigned_to'),
)


def display_value(value, field):
    if value is None or value == '':
        return 'Unassigned' if field == 'assigned_to' else '-'
    # Escape control characters so stored text cannot control the terminal.
    return ''.join(
        character if character.isprintable() else repr(character)[1:-1]
        for character in str(value)
    )


def view_tickets():
    try:
        tickets = get_tickets()
    except TicketReadError as error:
        print(f'\n{error}')
        return

    if not tickets:
        print('\nNo tickets found.')
        return

    for ticket in tickets:
        print('\n' + '-' * 48)
        for label, field in TICKET_FIELDS:
            print(f'{label:<12}: {display_value(ticket[field], field)}')
    print(f'\nTotal tickets: {len(tickets)}')


def main():
    while True:
        print('\nIT HELP DESK TICKETING SYSTEM')
        print('1. View Tickets')
        print('2. Exit')
        try:
            choice = input('Select an option: ').strip()
            if choice == '1':
                view_tickets()
            elif choice == '2':
                print('Goodbye!')
                return
            else:
                print('Invalid option. Enter 1 or 2.')
        except (EOFError, KeyboardInterrupt):
            print('\nGoodbye!')
            return


if __name__ == '__main__':
    main()
