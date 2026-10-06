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
the last successful rows. The GUI supports viewing, creating, searching, updating,
and deleting tickets, plus technician management and ticket history viewing. The
existing CLI remains available with `python app.py`.

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

## Create Ticket in the GUI

Click **Create Ticket** next to Refresh. The dialog contains Employee Name,
Department, Category, Subject, a multiline Description, and Priority. Category
and Priority use readonly dropdowns with the same choices as the CLI; Priority
defaults to Medium. The shared validation trims text and enforces the existing
name, length, and database-capacity rules. Validation and database errors keep
the form open with a friendly message and preserve entered values.

Save uses the existing repository, including its generated defaults and automatic
Ticket Created history entry. A successful save closes the form, displays the new
Ticket ID, and automatically refreshes the main table. If a table load is already
running, another refresh follows it so the new ticket appears. Cancel, Escape, or
the dialog's close button discards an unsaved form. Only one Create Ticket dialog
opens at a time. During saving, inputs and Save/Cancel are disabled, and closing
waits for the save result to prevent duplicate or uncertain submissions. No schema
change or additional dependency is required.

Manual checks:

1. Launch `.venv/Scripts/python.exe gui_app.py`, click Create Ticket, and check all
   six fields, readonly dropdowns, multiline Description, and Medium priority.
2. Save a blank form. Try Employee Name `3`, Department `!!!`, Subject `ab`, and
   Description `1234`, correcting each earlier invalid field before the next check.
   Each invalid save should show a friendly message, keep values, and leave the
   form open. Overlong names or subjects should also be rejected.
3. Create a test ticket with Employee Name `  Alice Reyes  `, Department `  IT  `,
   Category Hardware, Subject `  PC 3  `, a description containing two lines, and
   Priority Medium. Save once; expect a message containing the new Ticket ID, the
   dialog to close, and the ticket to appear automatically in the table.
4. Use the existing CLI to inspect that Ticket ID. Verify trimmed text, multiline
   description, Open status, generated timestamps, and one Ticket Created history
   entry. Check other priority/category choices using another disposable test ticket.
5. Enter data in a new form and Cancel. Reopen and close with Escape or the window
   close button. None of those actions should save a ticket. Repeated requests to
   open Create Ticket should reuse the existing dialog.
6. With MySQL temporarily unavailable, Save a valid form. Expect a friendly error
   and retained inputs. Restore MySQL and retry; expect normal success. While a
   save is running, Save/Cancel must stay disabled.
7. Verify manual Refresh, row selection, scrolling, and existing CLI menus still
   work, then run `.venv/Scripts/python.exe -m unittest discover -s tests -v`.

## Search Tickets in the GUI

Use the search box above the existing ticket table, then click **Search** or press
Enter while the box is focused. The existing repository performs a case-insensitive
substring search across Ticket ID, Employee Name, Department, Category, Subject,
Priority, Status, and Assigned Technician. Leading/trailing spaces are trimmed,
and numeric searches such as `3` remain valid. SQL values stay parameterized; `%`,
`_`, and `!` are treated literally rather than as user-supplied SQL wildcards.

Matching rows replace the contents of the same table, including Created At. An
empty result clears the rows and shows `No matching tickets found.` **Clear Search**
empties the search box and restores all tickets. Submitting a blank/whitespace-only
search also restores all tickets.

**Refresh reruns the last submitted search.** Editing the box alone does not change
the active filter until Search or Enter is used. Clear Search removes that filter.
Create Ticket also refreshes the active filter: a newly created matching ticket
appears automatically; use Clear Search to see one that does not match. Database
errors retain the last successfully loaded rows with a friendly error message.
Reads remain in the background, and a newer search replaces a pending request so
older results cannot overwrite it. No schema changes are required.

Manual checks:

1. Launch `.venv/Scripts/python.exe gui_app.py`. Search for a known Ticket ID,
   including `3` when appropriate, and confirm numeric input is accepted.
2. Search for known employee, department, category, subject, priority, status,
   and technician text. Repeat a known term in uppercase/lowercase; matching
   results should be the same. Created At should remain visible.
3. Search `  Hardware  ` and confirm trimming. Type a term in the focused box and
   press Enter; expect the same result as clicking Search.
4. Search a term absent from your tickets; expect an empty table and
   `No matching tickets found.` Then Clear Search; expect an empty box and all
   tickets restored. A whitespace-only search should also restore all tickets.
5. Search a known category, then Refresh; the filter should stay active. Edit
   the box without submitting and Refresh again; the previous filter should still
   apply. Submit the edited term to apply it.
6. Quickly submit different searches or Clear Search during a load. The final
   table should follow the latest request without duplicate rows or a frozen GUI.
7. Create a test ticket matching the active search; it should appear automatically
   after saving. Create one that does not match; Clear Search should reveal it.
8. With MySQL temporarily unavailable, Search should show a friendly error and
   preserve previous rows. Restore MySQL, then Search, Refresh, or Clear Search;
   the GUI should recover. Check row selection, scrolling, and the existing CLI.
9. Run `.venv/Scripts/python.exe -m unittest discover -s tests -v`.

## Update Ticket in the GUI

Select one row in the existing table, then click **Update Ticket** beside Create
Ticket and Refresh. With no selection, a friendly message asks you to select a
ticket. The dialog loads fresh, complete ticket details and Active technicians
through the existing repositories in a background thread. Ticket ID and created,
updated, and resolved timestamps are displayed as information rather than inputs.

Edit Employee Name, Department, Category, Subject, multiline Description, Priority,
Status, and Assigned Technician. Category, Priority, Status, and technician choices
are readonly dropdowns. Text uses the shared CLI/repository validation and is
trimmed before saving; blank or invalid values keep the form open with feedback.
Leaving prefilled values untouched keeps them. Technician choices show names and
IDs to distinguish duplicate names. **Keep current** preserves the existing name,
including an inactive or legacy assignment; **Unassign technician** removes it.
Only Active technicians are offered for new assignments. If none are available,
other edits, keeping the assignment, and unassigning remain possible.

**Save Changes** asks for confirmation before using the existing update transaction.
Assigning an active technician changes an Open ticket to Assigned when its requested
status is still Open; other statuses and explicit status changes are preserved.
Entering Resolved sets the resolution timestamp, remaining Resolved preserves it,
and leaving Resolved clears it, exactly as in the CLI. Automatic history logging
remains in that transaction, and unchanged values generate no activity. The selected
technician's Active status is rechecked at save time.

Success closes the dialog, shows the Ticket ID, and refreshes the existing table.
**The last submitted search filter stays active.** A ticket that no longer matches
disappears from the filtered results; Clear Search restores the full list. A save
during a table load queues another refresh. Database errors retain form values and
allow retrying. Cancel, Escape, and the close button discard unsaved edits; during a
save, controls are disabled and closing waits for the result. One editing/creation
dialog is open at a time. No database migration or extra dependency is required.

Manual checks (use disposable test tickets):

1. In Git Bash, run `.venv/Scripts/python.exe gui_app.py`. Click Update Ticket with
   no selected row; expect a friendly message and no dialog or database update.
2. Select a ticket and click Update Ticket. Verify that all eight editable fields,
   the full description, current assignment, and read-only timestamps are shown.
   Category, Priority, Status, and Assigned Technician must be readonly dropdowns.
3. Save unchanged values; expect `No changes made.` Try Employee Name `3`, Department
   `!!!`, Subject `ab`, Description `1234`, and blank/whitespace text, correcting each
   earlier invalid field before checking the next. No invalid save should occur.
4. Edit Subject to `  Error 404  ` and Description to a valid multiline value. Click
   Save Changes, decline confirmation, and verify the form stays open and nothing
   changed. Save again and confirm; expect success, a closed dialog, and refreshed
   rows. Reopen to check trimming and the preserved multiline description.
5. On an Open test ticket, select an Active technician and confirm. Verify the name
   and Assigned status. On In Progress, Resolved, and Closed test tickets, change
   the technician and verify those statuses remain unchanged. Explicitly choose
   In Progress on an Open ticket while assigning; In Progress must be preserved.
6. Choose Keep current, edit another field, and verify the assignment remains.
   Choose Unassign technician and verify the assignment clears without changing
   status. Existing inactive assignments must remain visible in Keep current while
   inactive technicians are absent from the new-assignment choices. With no Active
   technicians, expect friendly feedback and working keep/unassign/other edits.
7. Change a test ticket to Resolved, then use the CLI to inspect its resolved_at.
   Edit its priority while keeping Resolved; the timestamp must stay the same.
   Reopen it as Open or In Progress; resolved_at must clear. Use CLI option 7 to
   check history for status, priority, assignment/reassignment/unassignment, and
   information updates. Unchanged or cancelled edits must create no activity.
8. Search for a category, update a matching test ticket to another category, and
   confirm the filter stays active and the ticket disappears. Clear Search to see
   it again. Check Create Ticket, Refresh, scrolling, selection, and CLI menus.
9. Open a test ticket, temporarily make MySQL unavailable, and confirm a valid
   update. Expect a friendly error and retained values. Restore MySQL and retry.
   A ticket deleted or technician made Inactive through the CLI after opening the
   dialog must produce friendly save feedback rather than a crash.
10. Run `.venv/Scripts/python.exe -m unittest discover -s tests -v`. Tests use mocks
    and require neither an interactive GUI session nor a live database.

## Delete Ticket in the GUI

Select one ticket row, then click **Delete Ticket** beside Update Ticket. With no
selection, a friendly message asks you to select one ticket; no deletion is attempted.
The confirmation window loads fresh ticket information through the existing
repository and displays Ticket ID, Employee Name, Subject, Status, and Assigned
Technician, including existing inactive assignments. It clearly warns that deletion
is permanent and removes that ticket's activity history and notes.

**Opening the window does not delete anything.** Cancel is focused by default.
Cancel, Escape, or the window's close button dismisses the preview without changing
the ticket. Only clicking **Permanently Delete** confirms deletion. That button is
disabled until current ticket details load, and both buttons are disabled during
deletion to prevent repeated requests or hiding a pending result. Closing the main
window waits for any pending deletion. Only one create/update/delete dialog opens
at a time.

Reads and deletion run in background threads. The existing `delete_ticket` repository
function rechecks and locks the selected ID, deletes at most one ticket with
parameterized SQL, and commits or rolls back using its existing safeguards. The
GUI contains no DELETE SQL and does not delete notes/history separately; the
existing foreign keys handle their `ON DELETE CASCADE` relationships. No schema
change, setup command, or extra dependency is required.

A successful deletion closes the dialog, shows a success message, immediately
removes the selected row, and refreshes the existing table. **The last submitted
search filter stays active.** Other matching tickets remain visible; deleting the
last match produces `No matching tickets found.` Clear Search restores the full
remaining list. If a table load is already running, its older result is discarded
and a new refresh follows so it cannot restore the deleted ticket. Even if the
follow-up refresh fails, the confirmed deleted row remains removed.

Missing tickets produce friendly feedback and refresh the table without reporting
deletion success. Database errors leave the confirmation window open, restore its
buttons, and show guidance. If deletion cannot be confirmed, Cancel and use Refresh
to check the ticket before deciding whether to retry. Existing ticket creation,
updates, search, assignment, CLI history, and CLI notes behavior are unchanged.

Manual checks (delete only disposable test tickets):

1. In Git Bash, launch `.venv/Scripts/python.exe gui_app.py`. Without selecting a
   row, click Delete Ticket; expect a friendly selection message and no deletion.
2. Select a test ticket and click Delete Ticket. Check the fresh Ticket ID, employee,
   subject, status, assigned technician, and warning that deletion is permanent.
   Cancel should have focus; loading the preview must not delete the ticket.
3. Click Cancel and verify the ticket still appears after Refresh. Reopen and test
   Escape and the dialog's close button; both must also leave the ticket unchanged.
4. Reopen the confirmation for the same test ticket and click Permanently Delete.
   Expect a success message containing its ID, a closed dialog, immediate removal
   of that row, and a refreshed table. Other tickets and technicians must remain.
5. Search for a test ticket by its ID or subject, delete it, and verify the active
   search remains. Other matches should stay visible; no remaining matches should
   show the friendly empty result. Clear Search should show all remaining tickets.
6. Create another disposable ticket and add a note through CLI option 8. Confirm
   it has history through CLI option 7, then delete it in the GUI. The deletion
   should succeed without foreign-key errors; CLI ticket/history/notes lookups for
   its ID should say the ticket does not exist. Its technician must remain.
7. Open a disposable ticket's confirmation, then delete that ticket through the
   CLI before clicking Permanently Delete in the GUI. Expect Ticket Not Found and
   a refreshed table, with no false success or crash.
8. Open a confirmation, temporarily make MySQL unavailable, and click Permanently
   Delete. Expect friendly feedback and usable Cancel after the failed request.
   Restore MySQL, Cancel, and Refresh to check the ticket before retrying. With
   MySQL unavailable when opening the preview, deletion must remain disabled.
9. Verify View, Refresh, Create, Search, Update, and CLI menus still work. Repeated
   attempts to open Delete Ticket must reuse the same confirmation, and repeated
   confirmation clicks while deleting must not start additional deletions.
10. Run `.venv/Scripts/python.exe -m unittest discover -s tests -v`. Automated checks
    use mocks and require neither an interactive GUI nor live database changes.

## Technician Management in the GUI

Click **Manage Technicians** in the main window's header to open the separate
Technician Management window. Its resizable table shows Technician ID, Full Name,
Email, Status, and Created At, with single-row selection and both scrollbars. It
loads all technicians, including Inactive ones. **Refresh** reloads the table;
an empty list shows friendly feedback. Database errors preserve previously loaded
rows and leave Refresh available for recovery.

**Add Technician** opens a small Full Name/Email form. It reuses shared validation
and the existing repository: text is trimmed, blank values are rejected, names
require at least one alphabetic letter, and existing field-length limits apply.
Duplicate emails are prevented by the existing database UNIQUE constraint and
shown as friendly feedback without closing the form. MySQL supplies the technician
ID, Active status, and creation timestamp. A successful save shows the new ID,
closes the form, and automatically refreshes the technician table.

**Change Technician Status** requires selecting one technician row. The dialog
loads that technician's fresh name, email, and status through the repository, then
offers a readonly Active/Inactive dropdown. Save Changes asks for confirmation
before using the existing status update transaction. Unchanged status makes no
update; declining confirmation keeps the form open without saving. A successful
change closes the form, shows feedback, and refreshes the table. Only availability
changes: technicians remain in the database and assigned ticket names, ticket
history, and ticket notes are preserved.

Technician management is modal: close it to return to tickets. One management
window and one add/status form open at a time; child forms return focus to their
management window when closed. Cancel, Escape, and a form's close button discard
unsaved changes. **Close** exits management. Pending saves disable form controls
and prevent closing until their result is known. Database reads and writes run in
background threads; widget updates stay on Tkinter's main thread. If an older table
load overlaps a save, it is discarded and followed by a fresh read.

After making a technician Inactive, close management and open Update Ticket:
the technician is absent from new-assignment choices while Keep current preserves
their name on an already assigned ticket. Reactivate them through management,
then reopen Update Ticket to see them available again. The existing repository
also rechecks Active status when saving an assignment, including changes made
through the CLI. The main ticket search filter stays unchanged while managing
technicians. No migration, setup command, schema change, or new dependency is needed.

Manual checks (use a test technician and disposable tickets):

1. In Git Bash, run `.venv/Scripts/python.exe gui_app.py`, then click Manage
   Technicians. Check the five columns, timestamps, Active/Inactive rows, scrolling,
   selection, Refresh, and Close. Repeated open requests should reuse this window.
2. Click Change Technician Status without selecting a row; expect a friendly
   message and no save. Select a technician to check their current details and
   the readonly Active/Inactive choices.
3. Click Add Technician. Test blank/whitespace values, name `3`, and name `!!!`.
   Each invalid save must keep the form open. Test blank email and overlong name
   (over 100 characters) or email (over 150 characters) as well.
4. Add Full Name `  GUI Test Technician  ` and a unique Email such as
   `  gui-tech-test@example.com  `. Expect a success message with a generated ID,
   automatic table refresh, trimmed text, Active status, and Created At. Try the
   same email again; expect friendly duplicate feedback and no additional row.
5. Select the test technician, choose Inactive, and decline confirmation. Refresh
   to verify Active is unchanged. Repeat and confirm; verify Inactive stays visible
   in the technician table. Saving the current status should make no change.
6. Close management and open Update Ticket. Verify the inactive test technician is
   absent from assignment choices. If a disposable ticket was already assigned to
   them, its name must remain visible in the table and Keep current option.
7. Cancel the ticket dialog, reopen management, reactivate the technician with
   confirmation, then close management. Open Update Ticket again; the technician
   must now be selectable. Verify existing assignments, history, and notes remain.
8. Open a new Add Technician form and Cancel, Escape, or close it; no technician
   should be saved, and management controls should work afterward. Close and reopen
   management, then verify Create, Search, Update, Delete, and the ticket Refresh
   button still work, including any active search filter.
9. With MySQL temporarily unavailable, technician Refresh should show friendly
   feedback and preserve old rows. An attempted add/status save should keep the
   form values and restore controls after the error. Restore MySQL and refresh to
   check the saved state before retrying. A failed status-detail load must leave
   Save Changes disabled while Cancel remains usable.
10. Check CLI technician actions through option 6, ticket history through option 7,
    and notes through option 8. Run `.venv/Scripts/python.exe -m unittest discover -s
    tests -v`; the tests require no interactive GUI or live database changes.

## Ticket History in the GUI

Select one ticket in the main table, then click **View History** beside the ticket
controls. With no selection, a friendly message asks you to select a ticket and
no history window opens. The separate window shows the selected Ticket ID,
employee name, and subject, followed by a table with Date/Time, Action, and Details.
Entries follow the existing repository's order: oldest to newest, with History ID
breaking ties between equal timestamps. The table includes both scrollbars and
alternating rows. Selecting an activity displays its complete, multiline details
in a read-only pane below the table.

**Refresh** reloads that window's original Ticket ID, verifies it still exists,
and reloads its current information and history. It does not follow changes to
the selected row in the main ticket table. Refresh preserves the selected activity
when it still exists, and an empty history shows `No history found for this ticket.`
Database errors preserve previously loaded data and show friendly feedback;
Refresh becomes available again for recovery. If the ticket was deleted, Refresh
clears obsolete history and explains that the ticket no longer exists.

The history window is modeless, so it can remain open while you update a ticket
through the GUI, then Refresh to see the new automatic activity. Clicking View
History again for the same ticket focuses its existing window; choosing a different
ticket replaces the history window with one for that ticket. Close, Escape, or the
window's close button closes it. Closing the main application also closes history
and cancels pending GUI callbacks. Background reads never update Tkinter widgets
directly, and late results from a closed window are ignored.

Viewing and refreshing use only the existing `get_ticket` and `get_ticket_history`
repository functions. They do not write history, change tickets, or create any
tables. Automatic history logging, the CLI, assignment behavior, and ticket notes
are unchanged. The main ticket search filter is preserved. No migration, setup
command, or new dependency is required.

Manual checks (use disposable test tickets for changes):

1. In Git Bash, run `.venv/Scripts/python.exe gui_app.py`. Click View History with
   no selected row; expect friendly feedback and no new window.
2. Select a ticket with history and click View History. Check its Ticket ID,
   employee, subject, and Date/Time, Action, and Details columns. Compare the entries
   with CLI option 7 and verify that they run oldest to newest.
3. Select an activity with a long or multiline description update. Verify the full
   details appear in the lower pane, can be read using its scrollbar, and cannot
   be edited. Check both table scrollbars and resize the window.
4. Leave history open, update the same disposable ticket's status, priority, or
   assignment through the GUI, and confirm the save. Click Refresh in history;
   expect the new automatic entries without reopening or duplicating rows. Select
   another main-table ticket without clicking View History; Refresh must still
   load the history window's original ticket.
5. Click View History again for the same ticket; it should focus the existing
   window. Select a different ticket and click View History; the window should now
   show that ticket rather than old ticket information.
6. If you have a legacy ticket without history, view it and expect `No history
   found for this ticket.` Refresh and Close must remain usable. Do not remove
   existing history merely to test this; the empty state is covered automatically.
7. Leave history open for a disposable ticket, delete that ticket through the GUI,
   then Refresh history. Expect cleared rows and friendly missing-ticket feedback.
8. With MySQL temporarily unavailable, click the history Refresh button. Expect
   friendly feedback, retained previous rows/details, and no crash. Restore MySQL
   and Refresh again to verify recovery. Close during a load should remain safe.
9. Close history with Close, Escape, and its window close button. Reopen it and
   close the main application; both windows should close. Verify ticket View,
   Create, Search, Update, Delete, Refresh, technician management, and CLI menus.
10. Run `.venv/Scripts/python.exe -m unittest discover -s tests -v`. Tests require
    no interactive GUI or live database writes.
