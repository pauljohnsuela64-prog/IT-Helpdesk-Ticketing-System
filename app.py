"""Command-line entry point for the IT Help Desk Ticketing System."""
from ticket_repository import (
    CATEGORIES, PRIORITIES, TicketCreateError, TicketReadError,
    create_ticket, get_tickets, search_tickets,
)


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

    display_tickets(tickets)


def display_tickets(tickets):
    for ticket in tickets:
        print('\n' + '-' * 48)
        for label, field in TICKET_FIELDS:
            print(f'{label:<12}: {display_value(ticket[field], field)}')
    print(f'\nTotal tickets: {len(tickets)}')


def search_tickets_interactively():
    print('\nSEARCH TICKETS')
    search_term = prompt_required('Search term')
    try:
        tickets = search_tickets(search_term)
    except TicketReadError as error:
        print(f'\n{error}')
        return
    if not tickets:
        print('\nNo tickets match your search. Try another search term.')
        return
    display_tickets(tickets)


def prompt_required(label, max_length=None):
    while True:
        value = input(f'{label}: ').strip()
        if not value:
            print(f'{label} is required.')
        elif max_length is not None and len(value) > max_length:
            print(f'{label} must be at most {max_length} characters.')
        elif label == 'Description' and len(value.encode('utf-8')) > 65535:
            print('Description is too long (maximum 65535 UTF-8 bytes).')
        else:
            return value


def prompt_choice(label, choices):
    print(f'{label} options: ' + ', '.join(choices))
    while True:
        value = input(f'{label}: ').strip()
        for choice in choices:
            if value.casefold() == choice.casefold():
                return choice
        print('Enter one of: ' + ', '.join(choices))


def create_ticket_interactively():
    print('\nCREATE TICKET')
    employee = prompt_required('Employee name', 100)
    department = prompt_required('Department', 100)
    category = prompt_choice('Category', CATEGORIES)
    subject = prompt_required('Subject', 150)
    description = prompt_required('Description')
    priority = prompt_choice('Priority', PRIORITIES)
    try:
        ticket_id = create_ticket(employee, department, category, subject, description, priority)
    except (TicketCreateError, ValueError) as error:
        print(f'\n{error}')
        return
    print(f'\nTicket created successfully. New ticket ID: {ticket_id}')


def main():
    while True:
        print('\nIT HELP DESK TICKETING SYSTEM')
        print('1. View Tickets')
        print('2. Create Ticket')
        print('3. Search Tickets')
        print('4. Exit')
        try:
            choice = input('Select an option: ').strip()
            if choice == '1':
                view_tickets()
            elif choice == '2':
                create_ticket_interactively()
            elif choice == '3':
                search_tickets_interactively()
            elif choice == '4':
                print('Goodbye!')
                return
            else:
                print('Invalid option. Enter 1, 2, 3, or 4.')
        except (EOFError, KeyboardInterrupt):
            print('\nGoodbye!')
            return


if __name__ == '__main__':
    main()
