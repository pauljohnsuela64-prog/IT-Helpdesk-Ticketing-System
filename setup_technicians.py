"""Create only helpdesk.technicians: python setup_technicians.py."""
import sys

from technician_repository import TechnicianSetupError, setup_technicians_table


def main():
    try:
        setup_technicians_table()
    except TechnicianSetupError as error:
        print(error)
        return 1
    print('Technician table setup complete: helpdesk.technicians is available.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
