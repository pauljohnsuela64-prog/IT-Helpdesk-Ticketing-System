"""GUI role permissions shared by one authenticated session and its dialogs."""
from tkinter import messagebox


PERMISSION_DENIED = 'You do not have permission to perform this action.'
TECHNICIAN_PERMISSIONS = frozenset({
    'dashboard', 'view_tickets', 'search_tickets', 'create_ticket', 'update_ticket',
    'view_history', 'view_notes', 'add_note', 'refresh', 'change_password',
})
ADMIN_PERMISSIONS = TECHNICIAN_PERMISSIONS | {
    'delete_ticket', 'manage_technicians', 'delete_note', 'manage_users', 'reports', 'export_reports',
}
ROLE_PERMISSIONS = {'Admin': ADMIN_PERMISSIONS, 'Technician': TECHNICIAN_PERMISSIONS}


class SessionPermissions:
    """Snapshot the authenticated role; revoke child-window access on logout."""

    def __init__(self, user=None):
        self._permissions = ROLE_PERMISSIONS.get(user.get('role'), frozenset()) if user else frozenset()
        self._active = user is not None and user.get('status') == 'Active'

    def allows(self, action):
        return self._active and action in self._permissions

    def revoke(self):
        self._active = False


def require_permission(permissions, action, parent):
    """Check handlers independently of button state; call only on the Tk thread."""
    if permissions.allows(action):
        return True
    messagebox.showinfo('Permission Denied', PERMISSION_DENIED, parent=parent)
    return False
