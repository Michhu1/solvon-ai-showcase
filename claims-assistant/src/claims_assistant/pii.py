"""Masks personal data before any text leaves the service (to the LLM, traces or the audit log)."""

from __future__ import annotations

import hashlib
import re

_PESEL_WEIGHTS = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)


def is_valid_pesel(digits: str) -> bool:
    """Polish national ID: 11 digits with a weighted checksum."""
    if not re.fullmatch(r"\d{11}", digits):
        return False
    total = sum(int(d) * w for d, w in zip(digits[:10], _PESEL_WEIGHTS, strict=True))
    return (10 - total % 10) % 10 == int(digits[10])


# Order matters: longer, more specific patterns first.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    # IBAN or the domestic 26-digit account number (NRB), with or without spaces.
    ("IBAN", re.compile(r"(?:\bPL\s?|\b)\d{2}(?:\s?\d{4}){6}\b")),
    ("POLICY_NO", re.compile(r"\b[A-Z]{3}-\d{4}-\d{6}\b")),
    # Polish phone numbers: 9 digits, optionally grouped and with +48.
    ("PHONE", re.compile(r"(?:\+48[ -]?|\b)\d{3}[ -]?\d{3}[ -]?\d{3}\b")),
]


def mask_pii(text: str) -> tuple[str, list[str]]:
    """Returns the masked text and the kinds of data that were found (never the values)."""
    found: list[str] = []

    def pesel(match: re.Match[str]) -> str:
        if is_valid_pesel(match.group()):
            found.append("PESEL")
            return "[PESEL]"
        return match.group()

    masked = re.sub(r"\b\d{11}\b", pesel, text)
    for label, pattern in _PATTERNS:
        masked, count = pattern.subn(f"[{label}]", masked)
        found.extend([label] * count)
    return masked, sorted(set(found))


def fingerprint(text: str) -> str:
    """Stable reference to the original input for the audit log, without storing it."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
