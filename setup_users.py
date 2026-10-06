"""Idempotent users-table setup: python setup_users.py."""
import sys
from user_repository import UserSetupError, setup_users_table


def main():
    try:
        setup_users_table()
    except UserSetupError as error:
        print(error)
        return 1
    print('Users setup complete: helpdesk.users is available.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
