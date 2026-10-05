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
Choose **4. Back** to return to the main menu, or **9. Exit** from the main menu.
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
first. The main menu's Exit option is now **9**. Creation records actual priority
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

## Ticket Comments / Notes

From the project directory in Git Bash, using the existing `.env` with
`DB_NAME=helpdesk`, run:

```bash
python setup_ticket_comments.py
python app.py
```

Setup creates only `helpdesk.ticket_comments` and is safe to rerun. It leaves
tickets, technicians, and history unchanged. The setup account needs CREATE
permission and REFERENCES permission on tickets and technicians. Normal use needs
SELECT, INSERT, and DELETE on comments, plus the existing ticket/technician permissions.
If setup reports insufficient permissions, run the same SQL with a local MySQL
administrator account, entering the password when prompted:

```bash
"/c/Program Files/MySQL/MySQL Server 8.0/bin/mysql.exe" -h 127.0.0.1 -P 3306 -u root -p helpdesk < database/ticket_comments.sql
```

Choose **8. Ticket Comments / Notes**. Its submenu contains **1. View Ticket Notes**,
**2. Add Ticket Note**, **3. Delete Ticket Note**, and **4. Back**. Main-menu options 1 through 7 retain their
existing behavior, and **9. Exit** closes the program.

Add Ticket Note verifies a numeric Ticket ID and shows a short ticket summary.
Select the note's author by number from the active-technician list, then enter
the note. Leading/trailing spaces are trimmed, and at least three letters or
numbers are required; spaces and punctuation do not count toward that minimum.
Numeric notes such as `404` remain valid. Notes exceeding MySQL TEXT's 65535-byte
UTF-8 capacity are rejected. The repository rechecks ticket existence and active
author status before saving the technician ID and note in one transaction.

View Ticket Notes shows date/time, technician name, and note text from oldest
to newest, with the comment ID breaking timestamp ties. Inactive authors' names
remain visible. A missing author displays as `Unknown technician`. Notes do not
change ticket status or write automatic history. The ticket foreign key uses
`ON DELETE CASCADE`, so existing ticket deletion removes its notes; the technician
foreign key uses `ON DELETE SET NULL` to retain notes if an author is ever removed.
Delete Ticket Note asks for the Ticket ID first, verifies it exists, then shows
that ticket's notes with Comment IDs, technician names, timestamps, and text.
Enter one Comment ID to preview the selected note again. Only uppercase `Y` or
`YES` confirms permanent deletion; any other response cancels. Both IDs are
rechecked in the deletion transaction, and only one matching note can be deleted.
Missing notes and database errors produce friendly messages. Deleting a note
does not change ticket status or automatic history. This feature requires no
schema changes. Comment editing is not available.

Manual checks (use a test ticket):

1. Run setup twice; both runs should succeed without changing existing data.
2. Choose **8 → 1** for a ticket without notes; expect a friendly empty message.
3. Choose **8 → 2**, enter the ticket ID, and select an active technician. Add
   `  Checked network cable and restarted the router. Connection is stable now.  `.
   Expect success; **8 → 1** should show the trimmed note, author, and timestamp.
4. Add a second note and view again; verify oldest-to-newest ordering. Check
   View Tickets and View Ticket History: status and history should be unchanged.
5. Change the author to Inactive through **6 → 3**. Existing notes should still
   display their name, while Add Ticket Note should omit them from its choices.
   Reactivate them and verify that they appear again.
6. Try Ticket IDs `abc`, `0`, and a nonexistent positive ID in both note actions.
   Expect friendly messages. Try blank, `0`, text, and an out-of-range technician
   choice; expect another selection prompt. If no technicians are Active, expect
   a friendly message and no saved note.
7. Try blank, whitespace-only, `ab`, `a b`, and `!!!` notes; expect validation and
   another prompt. Try `PC 3` and `Error 404`; both should be accepted.
8. Delete the disposable test ticket using **5** and the existing explicit
   confirmation. Deletion should succeed despite its notes; both notes and
   history should then report that the ticket does not exist.
9. Check the other ticket and technician menus, submenu Back, invalid menu inputs,
   and **9. Exit**. Run the automated regression suite:

   ```bash
   python -m unittest discover -s tests -v
   ```

Delete Ticket Note checks (use disposable notes):

1. Add at least two notes to a test ticket and one to a second ticket. Choose
   **8 → 3**, enter the first ticket's ID, and check the displayed Comment IDs,
   authors, timestamps, and text.
2. Enter one displayed Comment ID. Confirm that its details appear again before
   the confirmation prompt. Enter `N`, Enter, `y`, or another response; expect
   cancellation and all notes to remain.
3. Repeat with `Y` or `YES`; expect success. View Ticket Notes and verify that
   only the selected note disappeared. Ticket status and history must be unchanged.
4. Try Ticket IDs `abc`, `0`, or a nonexistent ID, and Comment IDs `abc`, `0`,
   an unknown ID, or the second ticket's Comment ID. Expect friendly messages and
   no deletion. A ticket with no notes should return without asking for a Comment ID.
5. Try a note from an inactive technician; it should still display and be deletable
   after confirmation. Check **4. Back**, existing View/Add options, and **9. Exit**.

## GUI Foundation / View Tickets

From the project directory in Git Bash, launch the separate Tkinter application:

```bash
.venv/Scripts/python.exe gui_app.py
```

With your Python environment already active, `python gui_app.py` also works.
Tkinter and ttk are included with the project's Windows Python installation;
no additional GUI package or database migration is needed. The viewer uses the
existing `.env` settings and accepts only `DB_NAME=helpdesk` through the shared
database connection function.

The resizable 1240 × 720 window displays Ticket ID, Employee, Department, Category,
Subject, Priority, Status, Assigned To, and Created At. It loads tickets on startup.
Use Refresh to reload from MySQL, the scrollbars to browse larger tables, and click
one row to select it. Database reads run in a background thread so the window
remains responsive. Failed refreshes show a friendly status message and retain
the last successful rows. The GUI is read-only; the existing CLI remains available
with `python app.py`.

Manual checks:

1. Launch the GUI and check its title, header, columns, and loaded-ticket message.
2. Resize the window, scroll in both directions, and select a single ticket row.
3. Make a test ticket change through the existing CLI, then click Refresh in the
   GUI. Verify that the table updates without restarting and rows are not duplicated.
4. If the database contains no tickets, expect `No tickets found.` Do not delete
   existing tickets merely to test the empty state; it is covered automatically.
5. With MySQL temporarily unavailable, Refresh should show `Unable to load tickets.`
   with connection guidance, retain existing rows, and remain responsive. Restore
   MySQL and click Refresh again; loading should recover.
6. Close the window, then check the existing CLI still works. Run the automated
   tests, which require neither an interactive GUI nor a live database:

   ```bash
   .venv/Scripts/python.exe -m unittest discover -s tests -v
   ```
