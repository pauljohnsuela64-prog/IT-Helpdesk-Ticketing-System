# Build on Windows: .venv/Scripts/python.exe -m PyInstaller --clean --noconfirm ITHelpDesk.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules


project = Path(SPECPATH)

# Connector/Python dynamically loads authentication plugins and localized errors.
hidden_imports = (
    ['_mysql_connector']
    + collect_submodules('mysql.connector.plugins')
    + collect_submodules('mysql.connector.locales')
)

# These repositories read the SQL templates at import time. Bundling them does
# not execute setup or migrations. Never add .env or the project directory here.
sql_resources = ('users.sql', 'ticket_history.sql', 'ticket_comments.sql')
datas = [(str(project / 'database' / filename), 'database') for filename in sql_resources]

a = Analysis(
    [str(project / 'gui_app.py')],
    pathex=[str(project)],
    binaries=collect_dynamic_libs('mysql'),
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ITHelpDesk',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
