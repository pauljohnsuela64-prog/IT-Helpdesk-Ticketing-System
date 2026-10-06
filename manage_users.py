"""Create one account interactively; never accept credentials as command arguments."""
import getpass
import sys
import warnings

from input_validation import validate_text
from password_security import validate_password
from user_repository import USER_ROLES, UserCreateError, create_user


def _prompt_text(label, validator):
    while True:
        try:
            return validator(input(f'{label}: '))
        except ValueError as error:
            print(error)


def _validate_role(role):
    role = role.strip()
    if role not in USER_ROLES:
        raise ValueError('Role must be Admin or Technician.')
    return role


def _prompt_password():
    # getpass warns BEFORE its echoing fallback. Raising that warning prevents
    # the fallback from reading a password visibly or through redirected input.
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        while True:
            password = getpass.getpass('Password: ')
            try:
                validate_password(password)
            except ValueError as error:
                print(error)
                continue
            confirmation = getpass.getpass('Confirm password: ')
            if password == confirmation:
                return password
            print('Password confirmation does not match. Please try again.')


def main():
    password = None
    try:
        username = _prompt_text('Username', lambda value: validate_text(value, 'Username', 50))
        full_name = _prompt_text('Full name', lambda value: validate_text(value, 'Full name', 100))
        role = _prompt_text('Role (Admin/Technician)', _validate_role)
        password = _prompt_password()
        user_id = create_user(username, full_name, role, password)
    except (KeyboardInterrupt, EOFError):
        print('\nAccount creation interrupted. Check the username before retrying.')
        return 0
    except (getpass.GetPassWarning, OSError):
        print('Hidden password input is unavailable. In Git Bash, run: winpty .venv/Scripts/python.exe manage_users.py')
        return 1
    except (UserCreateError, ValueError) as error:
        print(error)
        return 1
    except Exception:
        print('Account creation could not be confirmed. Check your connection before retrying.')
        return 1
    finally:
        password = None
    print(f'User created successfully. New User ID: {user_id}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
