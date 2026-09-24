"""Framework-independent security and lifecycle rules for the signing domain."""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class EnvelopeState(StrEnum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    VIEWED = "VIEWED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    COMPLETED = "COMPLETED"
    DECLINED = "DECLINED"
    VOIDED = "VOIDED"
    EXPIRED = "EXPIRED"


ALLOWED_TRANSITIONS: dict[EnvelopeState, frozenset[EnvelopeState]] = {
    EnvelopeState.DRAFT: frozenset({EnvelopeState.SENT}),
    EnvelopeState.SENT: frozenset(
        {
            EnvelopeState.DELIVERED,
            EnvelopeState.VIEWED,
            EnvelopeState.PARTIALLY_COMPLETED,
            EnvelopeState.COMPLETED,
            EnvelopeState.DECLINED,
            EnvelopeState.VOIDED,
            EnvelopeState.EXPIRED,
        }
    ),
    EnvelopeState.DELIVERED: frozenset(
        {
            EnvelopeState.VIEWED,
            EnvelopeState.PARTIALLY_COMPLETED,
            EnvelopeState.COMPLETED,
            EnvelopeState.DECLINED,
            EnvelopeState.VOIDED,
            EnvelopeState.EXPIRED,
        }
    ),
    EnvelopeState.VIEWED: frozenset(
        {
            EnvelopeState.PARTIALLY_COMPLETED,
            EnvelopeState.COMPLETED,
            EnvelopeState.DECLINED,
            EnvelopeState.VOIDED,
            EnvelopeState.EXPIRED,
        }
    ),
    EnvelopeState.PARTIALLY_COMPLETED: frozenset(
        {
            EnvelopeState.COMPLETED,
            EnvelopeState.DECLINED,
            EnvelopeState.VOIDED,
            EnvelopeState.EXPIRED,
        }
    ),
    EnvelopeState.COMPLETED: frozenset(),
    EnvelopeState.DECLINED: frozenset(),
    EnvelopeState.VOIDED: frozenset(),
    EnvelopeState.EXPIRED: frozenset(),
}


class InvalidStateTransition(ValueError):
    pass


def require_transition(current: EnvelopeState, target: EnvelopeState) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidStateTransition(f"Envelope cannot transition from {current} to {target}.")


def normalize_coordinate(pixel: float, rendered_size: float) -> float:
    if rendered_size <= 0:
        raise ValueError("Rendered page size must be positive.")
    normalized = pixel / rendered_size
    if not 0 <= normalized <= 1:
        raise ValueError("Coordinates must remain inside the page.")
    return normalized


def render_coordinate(normalized: float, rendered_size: float) -> float:
    if not 0 <= normalized <= 1 or rendered_size <= 0:
        raise ValueError("Invalid normalized coordinate or page size.")
    return normalized * rendered_size


@dataclass(frozen=True, slots=True)
class PdfRectangle:
    x: float
    y: float
    width: float
    height: float


def normalized_to_pdf_rectangle(
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    page_width: float,
    page_height: float,
    rotation: int = 0,
) -> PdfRectangle:
    """Convert top-left browser coordinates to bottom-left PDF point coordinates."""
    values = (x, y, width, height)
    if any(value < 0 or value > 1 for value in values) or x + width > 1 or y + height > 1:
        raise ValueError("Normalized rectangle must remain inside the page.")
    if page_width <= 0 or page_height <= 0 or rotation % 90:
        raise ValueError("Invalid PDF page geometry.")
    rotation %= 360
    base = PdfRectangle(
        x * page_width,
        page_height - ((y + height) * page_height),
        width * page_width,
        height * page_height,
    )
    if rotation == 0:
        return base
    if rotation == 90:
        return PdfRectangle(base.y, base.x, base.height, base.width)
    if rotation == 180:
        return PdfRectangle(
            page_width - base.x - base.width,
            page_height - base.y - base.height,
            base.width,
            base.height,
        )
    return PdfRectangle(
        page_height - base.y - base.height,
        page_width - base.x - base.width,
        base.height,
        base.width,
    )


def validate_field_value(field_type: str, value: str, validation: dict, *, required: bool) -> str:
    value = value.strip()
    if required and not value:
        raise ValueError("This field is required.")
    if not value:
        return value
    minimum = validation.get("minLength")
    maximum = validation.get("maxLength")
    if minimum and len(value) < int(minimum):
        raise ValueError(f"Enter at least {minimum} characters.")
    if maximum and len(value) > int(maximum):
        raise ValueError(f"Enter no more than {maximum} characters.")
    validation_type = validation.get("type", "none")
    if field_type == "EMAIL" or validation_type == "email":
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address.")
    if field_type == "NUMBER" or validation_type == "number":
        try:
            float(value)
        except ValueError as error:
            raise ValueError("Enter a valid number.") from error
    if field_type in ("DATE", "DATE_SIGNED") or validation_type == "date":
        try:
            date.fromisoformat(value)
        except ValueError as error:
            raise ValueError("Enter a valid ISO date.") from error
    pattern = validation.get("pattern")
    if pattern:
        if len(pattern) > 200 or not re.fullmatch(pattern, value):
            raise ValueError("The value does not match the required format.")
    return value


def create_signing_token(byte_length: int = 32) -> tuple[str, str]:
    if byte_length < 32:
        raise ValueError("Signing tokens require at least 256 bits of entropy.")
    raw_token = secrets.token_urlsafe(byte_length)
    return raw_token, hash_signing_token(raw_token)


def hash_signing_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RouteRecipient:
    identifier: str
    routing_order: int
    status: str


def next_routing_group(
    recipients: Iterable[RouteRecipient],
) -> tuple[RouteRecipient, ...]:
    pending = [recipient for recipient in recipients if recipient.status == "PENDING"]
    if not pending:
        return ()
    next_order = min(recipient.routing_order for recipient in pending)
    return tuple(recipient for recipient in pending if recipient.routing_order == next_order)
