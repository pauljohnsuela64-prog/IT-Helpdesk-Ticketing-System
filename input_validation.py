"""Shared text validation for CLI prompts and repository writes."""


LETTER_FIELDS = {'Employee name', 'Department', 'Full name'}
MINIMUM_LENGTHS = {'Subject': 3, 'Description': 5}


def validate_text(value, label, max_length=None):
    """Trim and validate text, applying form rules only to the named fields."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} is required.')
    value = value.strip()
    if max_length is not None and len(value) > max_length:
        raise ValueError(f'{label} must be at most {max_length} characters.')
    minimum = MINIMUM_LENGTHS.get(label, 1)
    if len(value) < minimum:
        raise ValueError(f'{label} must be at least {minimum} characters after trimming.')
    if label in LETTER_FIELDS and not any(character.isalpha() for character in value):
        raise ValueError(f'{label} must contain at least one alphabetic letter.')
    if label == 'Description' and len(value.encode('utf-8')) > 65535:
        raise ValueError('Description is too long (maximum 65535 UTF-8 bytes).')
    return value
