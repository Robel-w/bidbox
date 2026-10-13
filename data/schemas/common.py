"""Shared types for procurement data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Optional


CurrencyCode = Literal["USD", "EUR", "GBP", "CAD", "AUD", "JPY", "MXN", "INR"]
"""Supported currency codes for procurement records."""


@dataclass(frozen=True)
class Money:
    """Represents a monetary amount with currency."""
    amount: Decimal
    currency: CurrencyCode

    def __post_init__(self):
        # Convert int/float to Decimal if needed
        if not isinstance(self.amount, Decimal):
            object.__setattr__(self, "amount", Decimal(str(self.amount)))

        if self.amount < 0:
            raise ValueError("Amount cannot be negative")
        if self.amount.is_nan():
            raise ValueError("Amount cannot be NaN")

    def __add__(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise ValueError(f"Cannot add {self.currency} to {other.currency}")
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise ValueError(f"Cannot subtract {other.currency} from {self.currency}")
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __mul__(self, factor: float) -> "Money":
        if factor < 0:
            raise ValueError("Multiplier cannot be negative")
        return Money(amount=self.amount * Decimal(str(factor)), currency=self.currency)

    def relative_difference(self, other: "Money") -> Decimal:
        """Calculate percentage difference from this to other value."""
        if self.amount == 0:
            if other.amount == 0:
                return Decimal("0")
            return Decimal("inf")
        diff = other.amount - self.amount
        return (diff / self.amount) * Decimal("100")

    def to_dict(self) -> dict[str, Any]:
        return {"amount": float(self.amount), "currency": self.currency}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Money":
        return cls(amount=Decimal(str(data["amount"])), currency=data["currency"])


@dataclass(frozen=True)
class DateRange:
    """Represents a date range with start and optional end."""
    start: date
    end: Optional[date] = None

    def contains(self, dt: date) -> bool:
        if self.end is None:
            return dt >= self.start
        return self.start <= dt <= self.end

    def days(self) -> Optional[int]:
        """Return number of days in range, or None if no end date."""
        if self.end is None:
            return None
        return (self.end - self.start).days

    def to_dict(self) -> dict[str, Any]:
        result = {"start": self.start.isoformat()}
        if self.end:
            result["end"] = self.end.isoformat()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DateRange":
        start = date.fromisoformat(data["start"])
        end = date.fromisoformat(data["end"]) if data.get("end") else None
        return cls(start=start, end=end)


UnitCode = Literal["unit", "kg", "g", "lb", "oz", "m", "cm", "km", "l", "ml", "hr", "day", "week", "month", "year"]
"""Standard unit codes for procurement items."""

ProvenanceType = Literal["public_database", "official_document", "synthetic", "estimated"]


@dataclass(frozen=True)
class Provenance:
    """Tracks the source and type of a data record."""
    source: str
    provenance_type: ProvenanceType
    original_id: Optional[str] = None
    url: Optional[str] = None
    note: Optional[str] = None

    def is_synthetic(self) -> bool:
        return self.provenance_type == "synthetic"

    def is_official(self) -> bool:
        return self.provenance_type in ("public_database", "official_document")

    def to_dict(self) -> dict[str, Any]:
        result = {
            "source": self.source,
            "provenance_type": self.provenance_type,
        }
        if self.original_id:
            result["original_id"] = self.original_id
        if self.url:
            result["url"] = self.url
        if self.note:
            result["note"] = self.note
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Provenance":
        return cls(
            source=data["source"],
            provenance_type=data["provenance_type"],
            original_id=data.get("original_id"),
            url=data.get("url"),
            note=data.get("note"),
        )


@dataclass(frozen=True)
class Unit:
    """Represents a unit of measure."""
    code: UnitCode
    name: str

    @classmethod
    def from_code(cls, code: str) -> "Unit":
        units = {
            "unit": "Unit",
            "kg": "Kilogram",
            "g": "Gram",
            "lb": "Pound",
            "oz": "Ounce",
            "m": "Meter",
            "cm": "Centimeter",
            "km": "Kilometer",
            "l": "Liter",
            "ml": "Milliliter",
            "hr": "Hour",
            "day": "Day",
            "week": "Week",
            "month": "Month",
            "year": "Year",
            "ton": "Ton",
        }
        return cls(code=code, name=units.get(code, code))

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "name": self.name}


def validate_currency_consistency(moneys: list[Money]) -> CurrencyCode:
    """Validate that all Money objects have the same currency."""
    if not moneys:
        raise ValueError("Cannot validate empty list of Money objects")
    first = moneys[0].currency
    if any(m.currency != first for m in moneys):
        raise ValueError("Currency mismatch in Money objects")
    return first
