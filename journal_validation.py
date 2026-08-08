"""Strict validation for persisted trading-journal records."""

from __future__ import annotations

from datetime import datetime

_TEXT_LIMITS = {
    "asset": 100,
    "strategy": 100,
    "went_well": 2000,
    "to_improve": 2000,
}
_GRADES = {"", "A", "B", "C", "D"}
_MENTAL_STATES = {"", "Focused", "FOMO", "Frustrated", "Impatient", "Calm", "Confident"}


class JournalValidationError(ValueError):
    """A journal route received a malformed date or payload."""


def validate_journal_date(value: str) -> str:
    text = str(value or "")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise JournalValidationError("Invalid date (use a real YYYY-MM-DD calendar date).") from exc
    if parsed.strftime("%Y-%m-%d") != text:
        raise JournalValidationError("Invalid date (use YYYY-MM-DD).")
    return text


def clean_journal_payload(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise JournalValidationError("Journal data must be a JSON object.")

    clean: dict[str, str] = {}
    for field, limit in _TEXT_LIMITS.items():
        value = payload.get(field, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise JournalValidationError(f"{field} must be text.")
        clean[field] = value.strip()[:limit]

    grade = payload.get("grade", "")
    mental_state = payload.get("mental_state", "")
    if not isinstance(grade, str) or grade not in _GRADES:
        raise JournalValidationError("grade must be A, B, C, D, or blank.")
    if not isinstance(mental_state, str) or mental_state not in _MENTAL_STATES:
        raise JournalValidationError("mental_state is not a supported choice.")
    clean["grade"] = grade
    clean["mental_state"] = mental_state
    return clean
