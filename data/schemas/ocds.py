"""OCDS core schemas and sourced finding models."""

from pydantic import BaseModel, Field, field_validator


class Value(BaseModel):
    """Monetary amount and currency."""

    amount: float
    currency: str = "KES"


class Classification(BaseModel):
    """Item classification such as CPV code."""

    id: str
    scheme: str = "CPV"
    description: str = ""


class Item(BaseModel):
    """Procured line item."""

    id: str
    description: str = ""
    classification: Classification | None = None
    quantity: float = 1.0


class Supplier(BaseModel):
    """Company-level supplier record without individual personal data."""

    id: str
    name: str


class Buyer(BaseModel):
    """Procuring public entity."""

    id: str | None = None
    name: str | None = None


class Award(BaseModel):
    """Historical completed procurement award."""

    id: str
    title: str = ""
    status: str = "active"
    date: str
    value: Value
    suppliers: list[Supplier] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)


class Release(BaseModel):
    """OCDS release document for an award."""

    ocid: str
    id: str
    date: str
    tag: list[str] = Field(default_factory=lambda: ["award"])
    initiationType: str = "tender"
    buyer: Buyer | None = None
    awards: list[Award] = Field(default_factory=list)
    source_url: str = ""
    synthetic: bool = False


class FindingSource(BaseModel):
    """Verifiable reference to an OCDS record field."""

    ocid: str
    award_id: str
    field_path: str
    source_url: str


class Finding(BaseModel):
    """Auditable finding carrying one or more verifiable sources."""

    code: str
    category: str
    message: str
    severity: str = "medium"
    sources: list[FindingSource]

    @field_validator("sources")
    @classmethod
    def require_sources(cls, v: list[FindingSource]) -> list[FindingSource]:
        """Validate that at least one verifiable source is present."""
        if not v:
            raise ValueError("Every finding must carry at least one source.")
        return v
