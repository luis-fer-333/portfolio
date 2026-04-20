"""Input validators for Spanish DNI/NIE, phone numbers, and patient names."""

import re

DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def normalize_spanish_phone(phone: str) -> str | None:
    """Normalize a Spanish phone number to +34XXXXXXXXX format.

    Strips non-digit characters and handles the optional +34 country prefix.
    Returns None if the input doesn't resolve to a valid 9-digit number.
    """
    phone = re.sub(r"\D", "", phone or "")
    if phone.startswith("34") and len(phone) == 11:
        phone = phone[2:]
    if len(phone) == 9:
        return "+34" + phone
    return None


def validate_spanish_phone(phone: str) -> str | None:
    """Validate a Spanish mobile/landline number (must start with 6/7/8/9).

    Returns the normalized +34 number or None if invalid.
    """
    normalized = normalize_spanish_phone(phone)
    if not normalized:
        return None
    digits = normalized[-9:]
    if not re.match(r"^[6789]\d{8}$", digits):
        return None
    return normalized


def validate_name(name: str) -> str | None:
    """Validate a patient name (2–60 letters, accents, hyphens, apostrophes).

    Returns the title-cased name or None if invalid.
    """
    if not name:
        return None
    name = name.strip()
    if not re.match(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ' -]{2,60}$", name):
        return None
    name = re.sub(r"\s+", " ", name)
    return name.title()


def validate_dni_nie(document: str) -> str | None:
    """Validate a Spanish DNI (8 digits + letter) or NIE (X/Y/Z + 7 digits + letter).

    Verifies the check-letter using the modulo-23 algorithm.
    Returns the uppercase document string or None if invalid.
    """
    if not document:
        return None
    s = document.strip().upper().replace(" ", "").replace("-", "")
    # DNI: 8 digits + 1 letter
    if re.match(r"^\d{8}[A-Z]$", s):
        num = int(s[:8])
        letter = s[-1]
        return s if DNI_LETTERS[num % 23] == letter else None
    # NIE: X/Y/Z + 7 digits + 1 letter
    if re.match(r"^[XYZ]\d{7}[A-Z]$", s):
        prefix_map = {"X": "0", "Y": "1", "Z": "2"}
        num = int(prefix_map[s[0]] + s[1:8])
        letter = s[-1]
        return s if DNI_LETTERS[num % 23] == letter else None
    return None
