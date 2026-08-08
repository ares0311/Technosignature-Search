"""Canonical Hunter parameter validators.

``docs/CLI_UX_SPEC.md`` UX-IN-04 requires that interactive guided entry and
scriptable operation use the *same* validation functions, so an operator can
never reach a state the scripted path would have rejected (or vice versa).

Every validator raises :class:`FieldValidationError` with an operator-facing
sentence. Raw ``argparse`` usage dumps are not an acceptable normal interactive
error response, so the shell renders these messages directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


class FieldValidationError(ValueError):
    """A guided or scriptable field value failed canonical validation."""


def validate_target_count(raw: str | int | None) -> int:
    """Validate the requested target count (UX-IN-03 type and range sentinels)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise FieldValidationError("Targets is required — enter a positive whole number.")
    if isinstance(raw, bool):
        raise FieldValidationError("Invalid — enter a positive whole number.")
    if isinstance(raw, int):
        value = raw
    else:
        text = raw.strip()
        try:
            value = int(text)
        except ValueError:
            raise FieldValidationError(
                "Invalid — enter a positive whole number."
            ) from None
    if value <= 0:
        raise FieldValidationError("Invalid — targets must be greater than zero.")
    return value


def validate_mode(raw: str | None) -> str:
    """Validate the search mode enumeration."""
    if raw is None or not str(raw).strip():
        raise FieldValidationError("Invalid — mode must be one of: new, follow-up.")
    value = str(raw).strip().casefold()
    aliases = {
        "new": "new",
        "follow-up": "follow-up",
        "follow_up": "follow-up",
        "followup": "follow-up",
    }
    if value not in aliases:
        raise FieldValidationError("Invalid — mode must be one of: new, follow-up.")
    return aliases[value]


def validate_optional_float(
    raw: str | float | None,
    *,
    label: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    """Validate an optional numeric field, returning ``None`` when omitted."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise FieldValidationError(f"Invalid — {label} must be a number.") from None
    if value != value:  # NaN
        raise FieldValidationError(f"Invalid — {label} must be a real number.")
    if minimum is not None and value < minimum:
        raise FieldValidationError(f"Invalid — {label} must be at least {minimum:g}.")
    if maximum is not None and value > maximum:
        raise FieldValidationError(f"Invalid — {label} must be at most {maximum:g}.")
    return value


def validate_existing_directory(raw: str | Path | None, *, label: str) -> Path | None:
    """Validate an optional directory path for existence and readability."""
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        return None
    path = Path(str(raw)).expanduser()
    if not path.exists():
        raise FieldValidationError(f"Invalid — {label} does not exist: {path}")
    if not path.is_dir():
        raise FieldValidationError(f"Invalid — {label} is not a directory: {path}")
    return path


def validate_search_id(raw: str | None) -> str | None:
    """Validate a durable search identifier's syntax."""
    if raw is None or not str(raw).strip():
        return None
    value = str(raw).strip()
    if not value.startswith("SEARCH-"):
        raise FieldValidationError(
            "Invalid — a search ID looks like SEARCH-20260730T101500Z-1A2B3C4D."
        )
    return value


def validate_target_reference(raw: str | None) -> str:
    """Validate a rank-or-identity reference for the detail view."""
    if raw is None or not str(raw).strip():
        raise FieldValidationError(
            "Target is required — enter a rank number or a catalog identity."
        )
    value = str(raw).strip()
    if len(value) > 64:
        raise FieldValidationError("Invalid — a target reference is at most 64 characters.")
    return value


def validate_ra_dec_window(
    *,
    min_ra_deg: float | None,
    max_ra_deg: float | None,
    min_dec_deg: float | None,
    max_dec_deg: float | None,
) -> None:
    """Reject incompatible coordinate-window combinations (UX-IN-03)."""
    if min_ra_deg is not None and max_ra_deg is not None and min_ra_deg > max_ra_deg:
        raise FieldValidationError(
            "Invalid — minimum RA must not exceed maximum RA."
        )
    if min_dec_deg is not None and max_dec_deg is not None and min_dec_deg > max_dec_deg:
        raise FieldValidationError(
            "Invalid — minimum declination must not exceed maximum declination."
        )


@dataclass(frozen=True)
class ValidatedConstraints:
    """Canonically validated optional scientific constraints."""

    min_ra_deg: float | None = None
    max_ra_deg: float | None = None
    min_dec_deg: float | None = None
    max_dec_deg: float | None = None
    min_abs_galactic_latitude_deg: float | None = None
    max_estimated_download_gb: float | None = None
    target_prefixes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "min_ra_deg": self.min_ra_deg,
            "max_ra_deg": self.max_ra_deg,
            "min_dec_deg": self.min_dec_deg,
            "max_dec_deg": self.max_dec_deg,
            "min_abs_galactic_latitude_deg": self.min_abs_galactic_latitude_deg,
            "max_estimated_download_gb": self.max_estimated_download_gb,
            "target_prefixes": self.target_prefixes,
        }

    def described(self) -> str:
        """Render a one-line operator description for the action preview."""
        parts: list[str] = []
        if self.min_ra_deg is not None or self.max_ra_deg is not None:
            parts.append(f"RA {self.min_ra_deg or 0:g}-{self.max_ra_deg or 360:g} deg")
        if self.min_dec_deg is not None or self.max_dec_deg is not None:
            parts.append(f"Dec {self.min_dec_deg or -90:g}-{self.max_dec_deg or 90:g} deg")
        if self.min_abs_galactic_latitude_deg is not None:
            parts.append(f"|b| >= {self.min_abs_galactic_latitude_deg:g} deg")
        if self.max_estimated_download_gb is not None:
            parts.append(f"<= {self.max_estimated_download_gb:g} GB per target")
        if self.target_prefixes:
            parts.append("prefixes " + ",".join(self.target_prefixes))
        return "; ".join(parts) if parts else "none (any eligible target)"


def validate_constraints(
    *,
    min_ra_deg: str | float | None = None,
    max_ra_deg: str | float | None = None,
    min_dec_deg: str | float | None = None,
    max_dec_deg: str | float | None = None,
    min_abs_galactic_latitude_deg: str | float | None = None,
    max_estimated_download_gb: str | float | None = None,
    target_prefixes: Sequence[str] | None = None,
) -> ValidatedConstraints:
    """Validate every optional scientific constraint through one canonical path."""
    resolved_min_ra = validate_optional_float(
        min_ra_deg, label="minimum RA", minimum=0.0, maximum=360.0
    )
    resolved_max_ra = validate_optional_float(
        max_ra_deg, label="maximum RA", minimum=0.0, maximum=360.0
    )
    resolved_min_dec = validate_optional_float(
        min_dec_deg, label="minimum declination", minimum=-90.0, maximum=90.0
    )
    resolved_max_dec = validate_optional_float(
        max_dec_deg, label="maximum declination", minimum=-90.0, maximum=90.0
    )
    validate_ra_dec_window(
        min_ra_deg=resolved_min_ra,
        max_ra_deg=resolved_max_ra,
        min_dec_deg=resolved_min_dec,
        max_dec_deg=resolved_max_dec,
    )
    prefixes = tuple(
        prefix.strip().upper() for prefix in (target_prefixes or ()) if prefix.strip()
    )
    return ValidatedConstraints(
        min_ra_deg=resolved_min_ra,
        max_ra_deg=resolved_max_ra,
        min_dec_deg=resolved_min_dec,
        max_dec_deg=resolved_max_dec,
        min_abs_galactic_latitude_deg=validate_optional_float(
            min_abs_galactic_latitude_deg,
            label="minimum absolute Galactic latitude",
            minimum=0.0,
            maximum=90.0,
        ),
        max_estimated_download_gb=validate_optional_float(
            max_estimated_download_gb,
            label="maximum estimated download",
            minimum=0.0,
        ),
        target_prefixes=prefixes,
    )
