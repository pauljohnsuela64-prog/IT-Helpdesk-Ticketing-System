"""Switch the same Tk root between login and authenticated Help Desk sessions."""
from gui_login import LoginScreen
from user_repository import public_user


class HelpDeskApplication:
    def __init__(self, root, viewer_factory):
        self.root = root
        self._viewer_factory = viewer_factory
        self._viewer = None
        self._login = None
        self.user = None
        self._show_login()

    def _show_login(self):
        self.user = None
        self._login = LoginScreen(self.root, self._open_helpdesk)

    def _open_helpdesk(self, user):
        self.user = public_user(user)
        self._login.dispose()
        self._login = None
        self._viewer = self._viewer_factory(self.root, user=self.user, on_logout=self._return_to_login)

    def _return_to_login(self):
        self._viewer = None
        self._show_login()
