"""Shared presentation settings for the existing Tkinter/ttk screens."""
from tkinter import ttk


BODY_FONT = ('Segoe UI', 10)
BOLD_FONT = ('Segoe UI', 10, 'bold')
TITLE_FONT = ('Segoe UI', 18, 'bold')
SECTION_FONT = ('Segoe UI', 12, 'bold')
BACKGROUND = '#f4f6fa'
SURFACE = '#ffffff'
TEXT_COLOR = '#182a43'
MUTED_COLOR = '#526176'
BORDER_COLOR = '#d7dfe9'
ALTERNATE_ROW = '#f0f4fa'
SELECTION_COLOR = '#245c99'
DANGER_COLOR = '#9c2b2b'
WINDOW_PADDING = 24
BUTTON_GAP = 8

# Tk Text widgets do not inherit ttk styles.
TEXT_OPTIONS = {
    'font': BODY_FONT,
    'background': SURFACE,
    'foreground': TEXT_COLOR,
    'insertbackground': TEXT_COLOR,
    'selectbackground': SELECTION_COLOR,
    'selectforeground': SURFACE,
    'highlightbackground': BORDER_COLOR,
    'highlightcolor': SELECTION_COLOR,
    'highlightthickness': 1,
    'borderwidth': 0,
    'relief': 'flat',
}


def configure_styles(root):
    """Apply the same visual theme at login and when the main screen opens."""
    style = ttk.Style(root)
    themes = style.theme_names()
    if 'vista' in themes:
        style.theme_use('vista')
    elif 'clam' in themes:
        style.theme_use('clam')

    # Native menus and combobox popdowns use the Tk option database.
    root.option_add('*Menu.font', BODY_FONT)
    root.option_add('*Menu.background', SURFACE)
    root.option_add('*Menu.foreground', TEXT_COLOR)
    root.option_add('*Menu.activeBackground', SELECTION_COLOR)
    root.option_add('*Menu.activeForeground', SURFACE)
    root.option_add('*TCombobox*Listbox.font', BODY_FONT)

    for prefix in ('Helpdesk', 'Login'):
        style.configure(f'{prefix}.TFrame', background=BACKGROUND)
        style.configure(f'{prefix}.Title.TLabel', background=BACKGROUND,
                        foreground=TEXT_COLOR, font=TITLE_FONT)
        style.configure(f'{prefix}.Section.TLabel', background=BACKGROUND,
                        foreground=TEXT_COLOR, font=SECTION_FONT)
        style.configure(f'{prefix}.TButton', font=BODY_FONT,
                        padding=(12, 8), width=-10)
    for name in ('Helpdesk.Status.TLabel', 'Helpdesk.Subtitle.TLabel', 'Login.TLabel'):
        style.configure(name, background=BACKGROUND, foreground=MUTED_COLOR, font=BODY_FONT)

    style.configure('Helpdesk.Toolbar.TButton', font=BODY_FONT, padding=(10, 8))
    style.configure('Helpdesk.TMenubutton', font=BODY_FONT, padding=(12, 8))
    for name in ('Danger.Helpdesk.TButton', 'Danger.Helpdesk.Toolbar.TButton'):
        style.configure(name, font=BOLD_FONT, foreground=DANGER_COLOR)
        style.map(name, foreground=[('disabled', '#7c8796'), ('active', '#7b2020')])
    style.configure('TEntry', font=BODY_FONT, padding=(8, 5))
    style.configure('TCombobox', font=BODY_FONT, padding=(8, 5))

    # Leave room around the text even when Windows uses a larger display scale.
    row_height = max(32, int(root.tk.call('font', 'metrics', BODY_FONT, '-linespace')) + 12)
    style.configure('Helpdesk.Treeview', font=BODY_FONT, rowheight=row_height,
                    background=SURFACE, fieldbackground=SURFACE,
                    foreground=TEXT_COLOR, bordercolor=BORDER_COLOR)
    style.configure('Helpdesk.Treeview.Heading', font=BOLD_FONT, padding=(8, 7))
    style.map('Helpdesk.Treeview', background=[('selected', SELECTION_COLOR)],
              foreground=[('selected', SURFACE)])

    style.configure('Dashboard.Card.TFrame', background=SURFACE, bordercolor=BORDER_COLOR)
    style.configure('Dashboard.Title.TLabel', background=SURFACE,
                    foreground=MUTED_COLOR, font=BODY_FONT)
    style.configure('Dashboard.Count.TLabel', background=SURFACE,
                    foreground=TEXT_COLOR, font=('Segoe UI', 24, 'bold'))
