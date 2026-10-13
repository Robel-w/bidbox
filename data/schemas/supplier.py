"""Schemas for supplier data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from .common import DateRange, Money, Provenance


@dataclass(frozen=True)
class SupplierBasic:
    """Basic supplier identification."""
    id: str
    name: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SupplierBasic":
        return cls(id=data["id"], name=data["name"])


@dataclass(frozen=True)
class Award:
    """A procurement award resulting from a tender process."""
    procurement_id: str
    supplier_id: str
    award_date: date
    total_amount: Money
    items: list["ProcurementItem"]
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
class ProcurementItem:
    """A single item or category within a procurement."""
    description: str
    quantity: Decimal
    unit: "Unit"
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
class Unit:
    """Represents a unit of measure."""
    code: str
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


@dataclass(frozen=True)
class SupplierHistory:
    """Historical procurement data for a supplier."""
    supplier_id: str
    awards: list[Award]

    @property
    def total_value(self) -> Money:
        """Sum of all award values."""
        if not self.awards:
            return Money(amount=Decimal("0"), currency="USD")
        return sum((a.total_amount for a in self.awards), start=Money(amount=Decimal("0"), currency=self.awards[0].total_amount.currency))

    @property
    def award_count(self) -> int:
        """Number of awards."""
        return len(self.awards)

    def get_awards_in_range(self, start: date, end: Optional[date] = None) -> list[Award]:
        """Get awards within a date range."""
        result = []
        for award in self.awards:
            if end is None:
                if award.award_date >= start:
                    result.append(award)
            else:
                if start <= award.award_date <= end:
                    result.append(award)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "supplier_id": self.supplier_id,
            "awards": [a.to_dict() for a in self.awards],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SupplierHistory":
        awards = [Award.from_dict(a) for a in data["awards"]]
        return cls(supplier_id=data["supplier_id"], awards=awards)


@dataclass(frozen=True)
class SupplierComparison:
    """Comparison data for supplier price analysis."""
    supplier_id: str
    supplier_name: str
    average_price: Money
    median_price: Money
    min_price: Money
    max_price: Money
    award_count: int
    provenance: Provenance
    date_range: Optional[DateRange] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "supplier_id": self.supplier_id,
            "supplier_name": self.supplier_name,
            "average_price": self.average_price.to_dict(),
            "median_price": self.median_price.to_dict(),
            "min_price": self.min_price.to_dict(),
            "max_price": self.max_price.to_dict(),
            "award_count": self.award_count,
            "provenance": self.provenance.to_dict(),
        }
        if self.date_range:
            result["date_range"] = self.date_range.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SupplierComparison":
        date_range = None
        if data.get("date_range"):
            date_range = DateRange.from_dict(data["date_range"])
        return cls(
            supplier_id=data["supplier_id"],
            supplier_name=data["supplier_name"],
            average_price=Money.from_dict(data["average_price"]),
            median_price=Money.from_dict(data["median_price"]),
            min_price=Money.from_dict(data["min_price"]),
            max_price=Money.from_dict(data["max_price"]),
            award_count=data["award_count"],
            provenance=Provenance.from_dict(data["provenance"]),
            date_range=date_range,
        )


@dataclass(frozen=True)
class SupplierRiskSignal:
    """A potential risk signal detected in supplier history."""
    signal_type: str
    severity: str
    description: str
    evidence: list[str]
    provenance: Provenance

    SEVERITY_LOW = "low"
    SEVERITY_MEDIUM = "medium"
    SEVERITY_HIGH = "high"

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_type": self.signal_type,
            "severity": self.severity,
            "description": self.description,
            "evidence": self.evidence,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SupplierRiskSignal":
        return cls(
            signal_type=data["signal_type"],
            severity=data["severity"],
            description=data["description"],
            evidence=data["evidence"],
            provenance=Provenance.from_dict(data["provenance"]),
        )