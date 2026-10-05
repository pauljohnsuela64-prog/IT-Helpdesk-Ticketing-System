# IT Help Desk Ticketing System

A Python + MySQL based IT Help Desk Ticketing System.

## Project Status

🚧 In Development

## Technologies

- Python
- MySQL
- Git
- GitHub

## Features

- [ ] Create tickets
- [ ] View tickets
- [ ] Search tickets
- [ ] Update tickets
- [ ] Assign tickets
- [ ] Ticket status
- [ ] Dashboard
- [ ] User authentication
- [ ] Reports

## Technician Management

From the project directory in Git Bash, use the existing `.env` connection settings
with `DB_NAME=helpdesk` and run this setup command once:

```bash
python setup_technicians.py
```

The database user needs CREATE permission on `helpdesk` for setup. This command
creates only `helpdesk.technicians`; it leaves existing tables unchanged and is
safe to rerun. It uses MySQL-generated IDs and timestamps, defaults new technicians
to `Active`, and enforces unique email addresses without regard to letter case.
Normal CLI use requires SELECT, INSERT, and UPDATE permission on the technician table.

```bash
python app.py
```

Choose **6. Manage Technicians**, then **1. View Technicians** or **2. Add Technician**.
Choose **3. Change Technician Status** to activate or deactivate a technician.
Choose **4. Back** to return to the main menu, or **8. Exit** from the main menu.
Full names and emails are required, with limits of 100 and 150 characters.

Manual checks:

1. View Technicians before adding anyone: expect a friendly empty-list message.
2. Add `Alex Reyes` with `alex.reyes@example.com`: expect the new technician ID.
3. View Technicians: confirm the ID, full name, email, and `Active` status.
4. Add the same email again, including with different letter case: expect a
   friendly duplicate-email message and no additional technician.
5. Try blank or whitespace-only names/emails and inputs exceeding their limits:
   expect a validation message and another prompt.
6. Enter an invalid submenu option, then Back: confirm friendly handling and return
   to the main menu. Verify the existing ticket options still work as before.

Run automated tests without connecting to a live database:

```bash
python -m unittest discover -s tests -v
```

## Assign a Technician to a Ticket

Choose **4. Update Ticket**, enter the ticket ID, and continue to **Assigned To**.
The prompt lists only active technicians. Select a list number to assign that
technician, press Enter to keep the current assignment, or enter `0` to unassign.
Invalid selections are rejected. If none are active, keeping or removing the
current assignment remains available.

Review the proposed changes and confirm saving as usual. Selecting a technician
for an Open ticket changes its status to Assigned unless you explicitly choose
another status. Other current statuses are preserved unless you edit Status.
Unassigning leaves the status unchanged unless you edit it. The repository
rechecks that the selected technician exists and is Active before saving their
name into the existing `assigned_to` field. No schema migration is required.

## Change Technician Status

Choose **6. Manage Technicians → 3. Change Technician Status**. The CLI displays
all technicians with their IDs, full names, emails, and current statuses. Enter
a Technician ID, choose `Active` or `Inactive`, then confirm the proposed change
with `y` or `yes`. Other confirmation responses cancel. An unchanged status does
not trigger a database update. Invalid IDs, missing technicians, and database
errors produce friendly messages.

Inactive technicians remain visible in View Technicians, and existing assigned
tickets keep displaying their names. Only active technicians appear in ticket
assignment choices. Reactivating a technician makes them available again.
Status changes update only the technician row; no schema migration is required.

Manual checks:

1. Assign an active technician to a test ticket and confirm that their name appears.
2. Change that technician to Inactive and confirm. View Technicians should still
   show the same ID, name, and email, now with status Inactive.
3. View/search the assigned ticket: the technician's name should remain. Open
   Update Ticket: the technician should be absent from assignment choices, and
   Enter should still keep the existing assignment.
4. Reactivate the same technician and confirm. They should reappear in assignment
   choices without changing existing assigned tickets.
5. Try `abc`, `0`, and a nonexistent ID; expect friendly messages. Try an invalid
   status; expect another prompt. Cancel a valid change; verify the status stays.

## Ticket Activity History

From the project directory in Git Bash, run this repeatable setup command using
the existing `.env` with `DB_NAME=helpdesk`:

```bash
python setup_ticket_history.py
python app.py
```

Setup creates only `helpdesk.ticket_history`, including a foreign key to tickets.
The account needs CREATE and REFERENCES permission for setup, plus SELECT and
INSERT on the history table for normal use. Existing tickets are left unchanged;
past activity is not backfilled. Set up history before creating or updating tickets.

If setup reports a permission error, use a MySQL administrator account to run
the same reviewed SQL file. With the local MySQL 8.0 installation in Git Bash:

```bash
"/c/Program Files/MySQL/MySQL Server 8.0/bin/mysql.exe" -h 127.0.0.1 -P 3306 -u root -p helpdesk < database/ticket_history.sql
```

Enter the administrator password when prompted; the command does not store it.

Choose **7. View Ticket History**, enter a Ticket ID, and view activity oldest
first. The main menu's Exit option is now **8**. Creation records actual priority
and status defaults. Updates record status, priority, assignment/reassignment/
unassignment, and changes to employee name, department, category, subject, or
description. Long text is shortened in history details; the ticket text stays intact.

Activity is saved in the same transaction as the ticket change. Failed saves,
cancelled updates, and unchanged values produce no activity. History reads do not
modify data. The foreign key uses `ON DELETE CASCADE`: deleting a ticket also
removes its history, preserving the existing Delete Ticket behavior.

Manual checks:

1. Create a test ticket, note its ID, then choose View Ticket History: expect a
   Ticket Created row containing its priority and status.
2. Change its status and priority, confirm, and view history: expect old/new values.
3. Assign an active technician, then another technician, then unassign: expect
   Technician Assigned, Technician Reassigned, and Technician Unassigned rows.
4. Change a subject or description: expect Ticket Information Updated details.
5. Keep all fields with Enter or cancel an update: history must remain unchanged.
6. Try an invalid/nonexistent ID; expect friendly messages. A pre-existing ticket
   with no new activity should show a friendly empty-history message.
7. Delete the test ticket with the existing confirmation flow: deletion should
   succeed, and View Ticket History should report that the ticket does not exist.
