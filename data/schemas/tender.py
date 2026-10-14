"""Schemas for procurement records (tenders, awards)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from .common import DateRange, Money, Provenance, Unit


@dataclass(frozen=True)
class ProcurementItem:
    """A single item or category within a procurement."""
    description: str
    quantity: Decimal
    unit: Unit
    unit_price: Optional[Money] = None

    def total_value(self) -> Optional[Money]:
        """Calculate total value if unit price is available."""
        if self.unit_price is None:
            return None
        return self.unit_price * float(self.quantity)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "description": self.description,
            "quantity": float(self.quantity),
            "unit": self.unit.to_dict(),
        }
        if self.unit_price:
            result["unit_price"] = self.unit_price.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProcurementItem":
        unit = Unit.from_code(data["unit"]["code"])
        unit_price = None
        if data.get("unit_price"):
            unit_price = Money.from_dict(data["unit_price"])
        return cls(
            description=data["description"],
            quantity=Decimal(str(data["quantity"])),
            unit=unit,
            unit_price=unit_price,
        )


@dataclass(frozen=True)
class Supplier:
    """A supplier participant in a procurement."""
    id: str
    name: str
    is_winner: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "is_winner": self.is_winner,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Supplier":
        return cls(
            id=data["id"],
            name=data["name"],
            is_winnder=data.get("is_winner", False),
        )


@dataclass(frozen=True)
class Award:
    """A procurement award resulting from a tender process."""
    procurement_id: str
    supplier_id: str
    award_date: date
    total_amount: Money
    items: list[ProcurementItem]
    provenance: Provenance

    def to_dict(self) -> dict[str, Any]:
        return {
            "procurement_id": self.procurement_id,
            "supplier_id": self.supplier_id,
            "award_date": self.award_date.isoformat(),
            "total_amount": self.total_amount.to_dict(),
            "items": [i.to_dict() for i in self.items],
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Award":
        items = [ProcurementItem.from_dict(i) for i in data["items"]]
        provenance = Provenance.from_dict(data["provenance"])
        return cls(
            procurement_id=data["procurement_id"],
            supplier_id=data["supplier_id"],
            award_date=date.fromisoformat(data["award_date"]),
            total_amount=Money.from_dict(data["total_amount"]),
            items=items,
            provenance=provenance,
        )


@dataclass(frozen=True)
class Tender:
    """A procurement tender notice."""
    id: str
    title: str
    description: str
    publication_date: date
    closing_date: Optional[date] = None
    expected_value: Optional[Money] = None
    categories: list[str] = None  # type: ignore
    procurement_type: str = "open"

    def __post_init__(self):
        if self.categories is None:
            object.__setattr__(self, "categories", [])

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "publication_date": self.publication_date.isoformat(),
            "procurement_type": self.procurement_type,
        }
        if self.closing_date:
            result["closing_date"] = self.closing_date.isoformat()
        if self.expected_value:
            result["expected_value"] = self.expected_value.to_dict()
        if self.categories:
            result["categories"] = self.categories
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Tender":
        expected_value = None
        if data.get("expected_value"):
            expected_value = Money.from_dict(data["expected_value"])
        closing_date = None
        if data.get("closing_date"):
            closing_date = date.fromisoformat(data["closing_date"])
        return cls(
            id=data["id"],
            title=data["title"],
            description=data["description"],
            publication_date=date.fromisoformat(data["publication_date"]),
            closing_date=closing_date,
            expected_value=expected_value,
            categories=data.get("categories", []),
            procurement_type=data.get("procurement_type", "open"),
        )


@dataclass(frozen=True)
class ProcurementRecord:
    """A complete procurement record with tender and award information."""
    tender: Tender
    award: Award

    def to_dict(self) -> dict[str, Any]:
        return {
            "tender": self.tender.to_dict(),
            "award": self.award.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProcurementRecord":
        return cls(
            tender=Tender.from_dict(data["tender"]),
            award=Award.from_dict(data["award"]),
        )


def calculate_median_price(prices: list[Money]) -> Optional[Money]:
    """Calculate median price from a list of Money objects."""
    if not prices:
        return None
    validate_currency_consistency(prices)
    sorted_prices = sorted(prices, key=lambda m: m.amount)
    n = len(sorted_prices)
    mid = n // 2
    if n % 2 == 0:
        median_amount = (sorted_prices[mid - 1].amount + sorted_prices[mid].amount) / 2
    else:
        median_amount = sorted_prices[mid].amount
    return Money(amount=median_amount, currency=prices[0].currency)


def validate_currency_consistency(moneys: list[Money]) -> str:
    """Validate that all Money objects have the same currency."""
    if not moneys:
        raise ValueError("Cannot validate empty list of Money objects")
    first = moneys[0].currency
    if any(m.currency != first for m in moneys):
        raise ValueError("Currency mismatch in Money objects")
    return first