# Windows deployment

Build from this project's root directory in **Git Bash on Windows**. The GUI
entry point remains `gui_app.py`; the spec creates a single, windowed
`ITHelpDesk.exe`. Launching it does not open a command-prompt window. The existing
CLI remains available through `app.py` in the Python development environment.

## 1. Install dependencies

Use the project's existing Windows virtual environment. To create one on a new
build machine, run `py -m venv .venv` first. Use a supported Windows CPython
installation with Tkinter; this build was prepared with Python 3.14.

```bash
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pip install -r requirements-build.txt
```

`requirements-build.txt` contains only the pinned PyInstaller build tool.
Runtime dependencies stay in `requirements.txt`. PyInstaller installs its own
hooks and other build dependencies. The target PC does not need Python,
PyInstaller, or a copy of the virtual environment.

## 2. Test and build

Run the automated tests before building. They use mocked database connections.

```bash
.venv/Scripts/python.exe -m unittest discover -s tests
.venv/Scripts/python.exe -m PyInstaller --clean --noconfirm ITHelpDesk.spec
```

The spec already sets the name, single-file layout, and windowed mode
(`console=False`). Do not add `--onefile` or `--windowed` when building the spec.
It includes the normal imported application modules, Tkinter/Tcl/Tk, MySQL
connector authentication plugins and libraries, and Excel-export dependencies.
Three non-secret SQL templates are included because existing repositories read
them during import. Packaging does not execute those templates or migrations.

Locate the result:

```bash
ls -lh dist/ITHelpDesk.exe
```

Build intermediates are in `build/`; the executable is in `dist/`. Both are
ignored by Git. `ITHelpDesk.spec` is eligible for tracking, together with the
build requirements and this guide. Default generated ticket-report filenames
and `.xlsx` exports in `reports/` are also ignored; keep reports saved elsewhere
out of source control.

## 3. Create external database configuration

**`.env` is not embedded in the executable.** Create a separate `.env` beside
`ITHelpDesk.exe`. The application reads that location even when started from a
shortcut or a different working directory. In Python development mode, it
continues reading `.env` beside `database.py`. Existing environment variables
take precedence in both modes, and values are loaded without interpolation.

For a local build, create the file from the safe template without overwriting
an existing deployment configuration:

```bash
cp -n .env.example dist/.env
notepad.exe "$(cygpath -w "$PWD/dist/.env")"
```

Fill in the connection settings privately using the database's existing
application MySQL account:

- `DB_HOST`: `127.0.0.1` for MySQL on the same PC, or the central server hostname.
- `DB_PORT`: the MySQL TCP port, normally `3306`.
- `DB_USER`: the application MySQL username.
- `DB_PASSWORD`: that database account's password; use single quotes around the
  value when needed, following `.env.example`. Do not enter it on a command line.
- `DB_NAME`: must be exactly `helpdesk`; the application refuses other databases.
- `DB_SSL_CA`: required for a remote server; use an absolute Windows path to a
  trusted MySQL CA certificate, with forward slashes, for example
  `C:/ITHelpDesk/certs/mysql-ca.pem`. The hostname must match the certificate.

Use the normal application database account for runtime access. MySQL root
credentials are not needed to run the GUI. Application login passwords belong
to application accounts and are never configured in `.env`.

Keep `.env` private and restrict access to the deployment folder to the intended
Windows users. Do not commit it, place it in the spec, add it as a build resource,
or include it in a public download. `.env` remains ignored by Git. Share any
deployment credentials privately with the intended recipient.

## 4. Run the application

After configuration, run from Git Bash:

```bash
./dist/ITHelpDesk.exe
```

Alternatively, double-click `ITHelpDesk.exe` in File Explorer. The Login screen
appears first. Continue using existing application accounts and roles. Normal
Python development launches remain:

```bash
.venv/Scripts/python.exe gui_app.py
.venv/Scripts/python.exe app.py
```

## 5. Deploy to another PC

This build produces a Windows x64 executable. Copy `dist/ITHelpDesk.exe` to a
writable folder on a compatible 64-bit Windows PC, such
as `C:\ITHelpDesk`. Create that PC's private `.env` in the same folder. For remote
MySQL, copy the trusted CA certificate too and configure its absolute path.
Distribute `.env.example` and this guide as configuration instructions if useful.
Source files, SQL templates, the virtual environment, `build/`, and Python
packages do not need to accompany the executable.

The packaged application **still requires MySQL/database access**. It does not
embed a database server, tickets, technicians, or application user records. The
existing `helpdesk` schema and all previously required migrations must already
be present. Provision the database separately using the existing SQL/setup
instructions in `README.md`; the executable performs no automatic setup.

### Local MySQL

Install and run MySQL on the target PC. Provision its own `helpdesk` database
with the existing tables/migrations and an application database account. Set
`DB_HOST=127.0.0.1`. A separate local database has its own accounts and data; it
does not automatically share the original PC's tickets.

### Central MySQL

Keep the existing `helpdesk` database on a central MySQL server. Configure each
PC with that server's hostname, port, permitted application database account,
and trusted CA certificate. The server must accept connections from those PCs,
with appropriate MySQL grants and network/firewall configuration. Verified TLS
is required by the existing application for remote connections. Those PCs share
the central database's tickets, technicians, and application accounts; they do
not need a local MySQL server.

## 6. Manual packaged-app checks

Use test records in the existing `helpdesk` database after configuring the
external file; packaging itself does not connect to MySQL.

1. Double-click the executable: confirm Login appears without a console window.
2. Log in as Admin, verify the dashboard/ticket table, and log out. Log in as
   Technician and confirm Admin-only actions remain unavailable.
3. Confirm permitted account creation, user status/link management, and password
   changes still work using the existing authorization rules.
4. Create/search/update a test ticket, check assignment restrictions and
   My Assigned Tickets, and verify history attribution and note permissions.
5. Generate an Admin report and export Excel to a chosen writable folder. Open
   the file and verify its title, filters, headers, timestamps, and rows.
6. Delete only disposable test records through existing confirmations and
   confirm refresh works. Check technician status changes and logout/login.
7. Launch from another directory or a shortcut: confirm it still uses `.env`
   beside the executable. With missing configuration or unreachable MySQL,
   confirm existing friendly error handling and a stable GUI.

Reference: [PyInstaller spec files](https://pyinstaller.org/en/stable/spec-files.html)
and [runtime file locations](https://pyinstaller.org/en/stable/runtime-information.html).
