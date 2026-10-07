from pathlib import Path
import os
import sys

import mysql.connector
from dotenv import load_dotenv


def _environment_file():
    """Keep private configuration outside a frozen bundle, beside its executable."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent / '.env'
    return Path(__file__).resolve().with_name('.env')


def get_connection():
    """Open the existing database; never create or change its schema."""
    load_dotenv(_environment_file(), override=False, interpolate=False)
    database_name = os.environ.get('DB_NAME')
    if database_name != 'helpdesk':
        raise ValueError('DB_NAME must be exactly helpdesk. Connection refused.')

    required = ('DB_HOST', 'DB_PORT', 'DB_USER', 'DB_PASSWORD', 'DB_NAME')
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError('Missing environment variables: ' + ', '.join(missing))

    try:
        port = int(os.environ['DB_PORT'])
    except ValueError:
        raise ValueError('DB_PORT must be a number.') from None
    if not 1 <= port <= 65535:
        raise ValueError('DB_PORT must be between 1 and 65535.')

    host = os.environ['DB_HOST']
    ca = os.environ.get('DB_SSL_CA')
    if host not in ('localhost', '127.0.0.1', '::1') and not ca:
        raise ValueError('Remote connections require DB_SSL_CA for verified TLS.')
    tls = {}
    if ca:
        tls = {'ssl_ca': ca, 'ssl_verify_cert': True, 'ssl_verify_identity': True}

    return mysql.connector.connect(
        host=host,
        port=port,
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD'],
        database=database_name,
        connection_timeout=5,
        autocommit=False,
        allow_local_infile=False,
        **tls,
    )
