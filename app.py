"""Command-line entry point for the IT Help Desk Ticketing System."""
from input_validation import validate_text
from ticket_repository import (
    CATEGORIES, PRIORITIES, TicketCreateError, TicketReadError,
    STATUSES, TicketDeleteError, TicketUpdateError, create_ticket, delete_ticket,
    get_ticket, get_tickets, search_tickets, update_ticket, validate_ticket_id,
)
from technician_repository import (
    TECHNICIAN_STATUSES, TechnicianCreateError, TechnicianReadError, TechnicianUpdateError,
    add_technician, get_active_technicians, get_technician, get_technicians,
    update_technician_status, validate_technician_id,
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


def display_ticket_details(ticket):
    display_tickets([ticket])
    for label, field in (('Description', 'description'), ('Created at', 'created_at'),
                         ('Updated at', 'updated_at'), ('Resolved at', 'resolved_at')):
        print(f'{label:<12}: {display_value(ticket[field], field)}')


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
        value = input(f'{label}: ')
        try:
            return validate_text(value, label, max_length)
        except ValueError as error:
            print(error)


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


EDIT_FIELDS = (
    ('Employee name', 'employee_name', 100, None),
    ('Department', 'department', 100, None),
    ('Category', 'category', 50, CATEGORIES),
    ('Subject', 'subject', 150, None),
    ('Description', 'description', None, None),
    ('Priority', 'priority', 20, PRIORITIES),
    ('Status', 'status', 20, STATUSES),
    ('Assigned To', 'assigned_to', 100, None),
)


def prompt_edit(label, field, current, max_length, choices):
    if choices:
        print(f'{label} options: ' + ', '.join(choices))
    while True:
        value = input(f'{label} [{display_value(current, field)}]: ').strip()
        if not value:
            return current
        if choices:
            for choice in choices:
                if value.casefold() == choice.casefold():
                    return choice
            print('Enter one of: ' + ', '.join(choices))
        else:
            try:
                return validate_text(value, label, max_length)
            except ValueError as error:
                print(error)


def prompt_assigned_technician(current):
    technicians = get_active_technicians()
    print(f'\nAssigned To: {display_value(current, "assigned_to")}')
    if technicians:
        print('Active technicians:')
        for number, technician in enumerate(technicians, start=1):
            print(f'{number}. {display_value(technician["full_name"], "full_name")}')
    else:
        print('No active technicians are available. You can keep or remove the current assignment.')
    print('0. Unassign technician')
    while True:
        selection = input('Select a technician number (Enter to keep current): ').strip()
        if not selection:
            return current, None
        if selection == '0':
            return None, None
        if selection.isascii() and selection.isdecimal():
            try:
                number = int(selection)
            except ValueError:
                number = -1
            if 1 <= number <= len(technicians):
                technician = technicians[number - 1]
                return technician['full_name'], technician['technician_id']
        print('Invalid selection. Choose a listed number, 0 to unassign, or Enter to keep current.')


def update_ticket_interactively():
    print('\nUPDATE TICKET')
    raw_id = input('Ticket ID: ').strip()
    if not raw_id.isascii() or not raw_id.isdecimal():
        print('Ticket ID must be a positive number up to 2147483647.')
        return
    try:
        ticket = get_ticket(int(raw_id))
    except (TicketReadError, ValueError) as error:
        print(f'\n{error}')
        return
    if ticket is None:
        print('\nNo ticket found with that ID.')
        return

    print('\nCurrent ticket information:')
    display_ticket_details(ticket)
    print('\nPress Enter to keep each current value.')
    changes = {}
    selected_technician_id = None
    for label, field, limit, choices in EDIT_FIELDS:
        if field == 'assigned_to':
            try:
                value, selected_technician_id = prompt_assigned_technician(ticket[field])
            except TechnicianReadError as error:
                print(f'\n{error}')
                return
        else:
            value = prompt_edit(label, field, ticket[field], limit, choices)
        if value != ticket[field] or (field == 'assigned_to' and selected_technician_id is not None):
            changes[field] = value
    if not changes:
        print('\nNo changes made.')
        return

    proposed_changes = changes.copy()
    if (selected_technician_id is not None and ticket['status'] == 'Open'
            and changes.get('status', 'Open') == 'Open'):
        proposed_changes['status'] = 'Assigned'
        print('\nAssigning a technician changes an Open ticket to Assigned.')
    print('\nProposed changes:')
    for label, field, _, _ in EDIT_FIELDS:
        if field in proposed_changes:
            print(f'{label}: {display_value(ticket[field], field)} -> '
                  f'{display_value(proposed_changes[field], field)}')
    if 'status' in proposed_changes:
        print('Resolved at will be set to the save time.' if proposed_changes['status'] == 'Resolved'
              else 'Resolved at will be cleared.')
    while True:
        confirmation = input('Save these changes? (y/n): ').strip().casefold()
        if confirmation in ('n', 'no', ''):
            print('\nUpdate cancelled. No changes saved.')
            return
        if confirmation in ('y', 'yes'):
            break
        print('Enter y or n.')
    try:
        if selected_technician_id is not None:
            # The repository resolves the selected ID to its current active name.
            repository_changes = {
                field: value for field, value in changes.items() if field != 'assigned_to'
            }
            saved = update_ticket(ticket['ticket_id'], repository_changes,
                                  technician_id=selected_technician_id)
        else:
            saved = update_ticket(ticket['ticket_id'], changes)
    except (TicketUpdateError, ValueError) as error:
        print(f'\n{error}')
        return
    print('\nTicket updated successfully.' if saved else '\nNo changes made.')


def delete_ticket_interactively():
    print('\nDELETE TICKET')
    raw_id = input('Ticket ID: ').strip()
    if not raw_id.isascii() or not raw_id.isdecimal():
        print('Ticket ID must be a positive number up to 2147483647.')
        return
    try:
        ticket_id = int(raw_id)
        validate_ticket_id(ticket_id)
        ticket = get_ticket(ticket_id)
    except (TicketReadError, ValueError) as error:
        print(f'\n{error}')
        return
    if ticket is None:
        print('\nNo ticket found with that ID.')
        return

    print('\nTicket to delete:')
    display_ticket_details(ticket)
    try:
        confirmation = input('Type Y or YES to permanently delete this ticket: ').strip()
    except (EOFError, KeyboardInterrupt):
        print('\nDeletion cancelled. No ticket was deleted.')
        return
    if confirmation not in ('Y', 'YES'):
        print('\nDeletion cancelled. No ticket was deleted.')
        return
    try:
        deleted = delete_ticket(ticket_id)
    except (TicketDeleteError, ValueError) as error:
        print(f'\n{error}')
        return
    if deleted:
        print(f'\nTicket {ticket_id} deleted successfully.')
    else:
        print('\nNo ticket found with that ID. No ticket was deleted.')


def view_technicians():
    try:
        technicians = get_technicians()
    except TechnicianReadError as error:
        print(f'\n{error}')
        return
    if not technicians:
        print('\nNo technicians found yet. Choose Add Technician to add one.')
        return
    display_technicians(technicians)


def display_technicians(technicians):
    for technician in technicians:
        print('\n' + '-' * 48)
        for label, field in (('Technician ID', 'technician_id'), ('Full Name', 'full_name'),
                             ('Email', 'email'), ('Status', 'status')):
            print(f'{label:<13}: {display_value(technician[field], field)}')
    print(f'\nTotal technicians: {len(technicians)}')


def add_technician_interactively():
    print('\nADD TECHNICIAN')
    full_name = prompt_required('Full name', 100)
    email = prompt_required('Email', 150)
    try:
        technician_id = add_technician(full_name, email)
    except (TechnicianCreateError, ValueError) as error:
        print(f'\n{error}')
        return
    print(f'\nTechnician added successfully. New technician ID: {technician_id}')


def change_technician_status_interactively():
    print('\nCHANGE TECHNICIAN STATUS')
    try:
        technicians = get_technicians()
    except TechnicianReadError as error:
        print(f'\n{error}')
        return
    if not technicians:
        print('\nNo technicians found yet. Choose Add Technician to add one.')
        return
    display_technicians(technicians)
    raw_id = input('Technician ID: ').strip()
    if not raw_id.isascii() or not raw_id.isdecimal():
        print('Technician ID must be a positive number up to 2147483647.')
        return
    try:
        technician_id = int(raw_id)
        validate_technician_id(technician_id)
        technician = get_technician(technician_id)
    except (TechnicianReadError, ValueError) as error:
        print(f'\n{error}')
        return
    if technician is None:
        print('\nNo technician found with that ID.')
        return
    print('\nSelected technician:')
    display_technicians([technician])
    status = prompt_choice('New status', TECHNICIAN_STATUSES)
    if status == technician['status']:
        print(f'\nTechnician is already {status}. No changes made.')
        return
    print(f'\nProposed status change: {display_value(technician["status"], "status")} -> {status}')
    try:
        confirmation = input('Save this status change? (y/n): ').strip().casefold()
    except (EOFError, KeyboardInterrupt):
        print('\nStatus change cancelled. No changes saved.')
        return
    if confirmation not in ('y', 'yes'):
        print('\nStatus change cancelled. No changes saved.')
        return
    try:
        saved = update_technician_status(technician_id, status)
    except (TechnicianUpdateError, ValueError) as error:
        print(f'\n{error}')
        return
    print(f'\nTechnician {technician_id} status changed to {status} successfully.'
          if saved else '\nNo changes made.')


def manage_technicians():
    while True:
        print('\nMANAGE TECHNICIANS')
        print('1. View Technicians')
        print('2. Add Technician')
        print('3. Change Technician Status')
        print('4. Back')
        choice = input('Select an option: ').strip()
        if choice == '1':
            view_technicians()
        elif choice == '2':
            add_technician_interactively()
        elif choice == '3':
            change_technician_status_interactively()
        elif choice == '4':
            return
        else:
            print('Invalid option. Please choose a number from 1 to 4.')


def main():
    while True:
        print('\nIT HELP DESK TICKETING SYSTEM')
        print('1. View Tickets')
        print('2. Create Ticket')
        print('3. Search Tickets')
        print('4. Update Ticket')
        print('5. Delete Ticket')
        print('6. Manage Technicians')
        print('7. Exit')
        try:
            choice = input('Select an option: ').strip()
            if choice == '1':
                view_tickets()
            elif choice == '2':
                create_ticket_interactively()
            elif choice == '3':
                search_tickets_interactively()
            elif choice == '4':
                update_ticket_interactively()
            elif choice == '5':
                delete_ticket_interactively()
            elif choice == '6':
                manage_technicians()
            elif choice == '7':
                print('Goodbye!')
                return
            else:
                print('Invalid option. Please choose a number from 1 to 7.')
        except (EOFError, KeyboardInterrupt):
            print('\nGoodbye!')
            return


if __name__ == '__main__':
    main()
