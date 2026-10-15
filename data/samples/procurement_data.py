"""Generate synthetic procurement data for development and testing."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from ..schemas import (
    Award,
    EvidenceGap,
    EvidenceItem,
    Money,
    ProcurementItem,
    ProcurementRecord,
    Provenance,
    Tender,
    Unit,
    validate_currency_consistency,
)


def generate_procurement_records() -> list[ProcurementRecord]:
    """Generate a realistic set of synthetic procurement records."""
    records = []

    # Office supplies - normal procurement
    records.append(_create_office_supplies_award())

    # Construction equipment - higher than average price (signal)
    records.append(_create_construction_equipment_outlier())

    # IT services - incomplete evidence (signal)
    records.append(_create_it_services_incomplete())

    # Medical supplies - multiple suppliers, same category
    records.extend(_create_medical_supplies_comparables())

    # Road construction - different currencies (should be filtered)
    records.append(_create_road_construction_different_currency())

    return records


def _create_office_supplies_award() -> ProcurementRecord:
    """Create a normal office supplies procurement."""
    provenance = Provenance(
        source="synthetic_sample_data",
        provenance_type="synthetic",
        note="Generated for development and testing",
    )

    tender = Tender(
        id="TENDER-2024-001",
        title="Office Supplies Annual Contract",
        description="Annual supply of office materials including paper, pens, and stationery",
        publication_date=date(2024, 1, 15),
        closing_date=date(2024, 2, 1),
        categories=["Office Supplies", "Stationery"],
    )

    award = Award(
        procurement_id="TENDER-2024-001",
        supplier_id="SUP-001",
        award_date=date(2024, 2, 15),
        total_amount=Money(amount=Decimal("25000"), currency="USD"),
        items=[
            ProcurementItem(
                description="Printer paper (500 sheets)",
                quantity=Decimal("1000"),
                unit=Unit.from_code("unit"),
                unit_price=Money(amount=Decimal("2.50"), currency="USD"),
            ),
            ProcurementItem(
                description="Ballpoint pens (pack of 10)",
                quantity=Decimal("500"),
                unit=Unit.from_code("unit"),
                unit_price=Money(amount=Decimal("5.00"), currency="USD"),
            ),
        ],
        provenance=provenance,
    )

    return ProcurementRecord(tender=tender, award=award)


def _create_construction_equipment_outlier() -> ProcurementRecord:
    """Create a construction equipment procurement with unusual price."""
    provenance = Provenance(
        source="synthetic_sample_data",
        provenance_type="synthetic",
        note="Generated for development - price outlier case",
    )

    tender = Tender(
        id="TENDER-2024-002",
        title="Construction Equipment Rental",
        description="Backhoe loader rental for road project",
        publication_date=date(2024, 3, 1),
        closing_date=date(2024, 3, 15),
        categories=["Construction", "Equipment"],
    )

    award = Award(
        procurement_id="TENDER-2024-002",
        supplier_id="SUP-002",
        award_date=date(2024, 3, 20),
        total_amount=Money(amount=Decimal("85000"), currency="USD"),
        items=[
            ProcurementItem(
                description="Backhoe Loader (CAT 325)",
                quantity=Decimal("1"),
                unit=Unit.from_code("unit"),
                unit_price=Money(amount=Decimal("85000"), currency="USD"),
            ),
        ],
        provenance=provenance,
    )

    return ProcurementRecord(tender=tender, award=award)


def _create_it_services_incomplete() -> ProcurementRecord:
    """Create an IT services procurement with missing documentation."""
    provenance = Provenance(
        source="synthetic_sample_data",
        provenance_type="synthetic",
        note="Generated for development - incomplete evidence case",
    )

    tender = Tender(
        id="TENDER-2024-003",
        title="IT Consulting Services",
        description="Software development and consulting services",
        publication_date=date(2024, 4, 1),
        closing_date=date(2024, 4, 20),
        categories=["IT Services", "Consulting"],
    )

    award = Award(
        procurement_id="TENDER-2024-003",
        supplier_id="SUP-003",
        award_date=date(2024, 4, 25),
        total_amount=Money(amount=Decimal("150000"), currency="USD"),
        items=[
            ProcurementItem(
                description="Software development hours",
                quantity=Decimal("500"),
                unit=Unit.from_code("hr"),
                unit_price=None,  # Missing unit price
            ),
        ],
        provenance=provenance,
    )

    return ProcurementRecord(tender=tender, award=award)


def _create_medical_supplies_comparables() -> list[ProcurementRecord]:
    """Create multiple medical supplies procurements for comparison."""
    records = []
    base_date = date(2024, 1, 1)

    suppliers = [
        ("SUP-004", "MedSupply Inc."),
        ("SUP-005", "HealthCare Products LLC"),
        ("SUP-006", "Global Medical Supplies"),
    ]

    for i, (supplier_id, supplier_name) in enumerate(suppliers):
        provenance = Provenance(
            source="synthetic_sample_data",
            provenance_type="synthetic",
            note=f"Generated for development - medical supplies {supplier_id}",
        )

        tender = Tender(
            id=f"TENDER-2024-00{i+4}",
            title=f"Medical Supplies Contract - Round {i+1}",
            description="Standard medical supplies for clinic",
            publication_date=base_date + timedelta(days=i * 30),
            closing_date=base_date + timedelta(days=i * 30 + 14),
            categories=["Medical Supplies", "Healthcare"],
        )

        award = Award(
            procurement_id=f"TENDER-2024-00{i+4}",
            supplier_id=supplier_id,
            award_date=base_date + timedelta(days=i * 30 + 20),
            total_amount=Money(amount=Decimal("12000"), currency="USD"),
            items=[
                ProcurementItem(
                    description="Surgical gloves (box of 100)",
                    quantity=Decimal("200"),
                    unit=Unit.from_code("unit"),
                    unit_price=Money(amount=Decimal("60.00"), currency="USD"),
                ),
            ],
            provenance=provenance,
        )

        records.append(ProcurementRecord(tender=tender, award=award))

    return records


def _create_road_construction_different_currency() -> ProcurementRecord:
    """Create a procurement in a different currency to test filtering."""
    provenance = Provenance(
        source="synthetic_sample_data",
        provenance_type="synthetic",
        note="Generated for development - currency comparison test",
    )

    tender = Tender(
        id="TENDER-2024-010",
        title="Road Construction Project",
        description="Pavement repair and road maintenance",
        publication_date=date(2024, 5, 1),
        closing_date=date(2024, 5, 20),
        categories=["Construction", "Infrastructure"],
    )

    # This award is in EUR - should be filtered when comparing with USD
    award = Award(
        procurement_id="TENDER-2024-010",
        supplier_id="SUP-010",
        award_date=date(2024, 5, 25),
        total_amount=Money(amount=Decimal("150000"), currency="EUR"),
        items=[
            ProcurementItem(
                description="Asphalt paving",
                quantity=Decimal("1000"),
                unit=Unit.from_code("ton"),
                unit_price=None,
            ),
        ],
        provenance=provenance,
    )

    return ProcurementRecord(tender=tender, award=award)


def get_synthetic_provenance() -> Provenance:
    """Get provenance metadata for synthetic data."""
    return Provenance(
        source="synthetic_sample_data",
        provenance_type="synthetic",
        note="All records in this dataset are synthetic and generated for development/testing",
    )


def create_missing_evidence_gaps() -> list[EvidenceGap]:
    """Create evidence gap examples for testing."""
    return [
        EvidenceGap(
            field_name="unit_price",
            reason="Unit price not provided in award record",
            severity=EvidenceGap.SEVERITY_WARNING,
        ),
        EvidenceGap(
            field_name="procurement_category",
            reason="Category information missing from tender",
            severity=EvidenceGap.SEVERITY_INFO,
        ),
    ]


def create_sample_evidence_items() -> list[EvidenceItem]:
    """Create sample evidence items for testing."""
    provenance = get_synthetic_provenance()
    return [
        EvidenceItem(
            id="EVD-001",
            source="TENDER-2024-001",
            description="Award announcement for office supplies contract",
            provenance=provenance,
            reference_url="https://example.com/awards/TENDER-2024-001",
        ),
    ]
