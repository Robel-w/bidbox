"""Connector manifest loading and validation (credentials are env NAMES, never values)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

KINDS = ("data_source", "messaging", "payments", "records", "registry", "file_drop")
TARGET_MODES = ("live", "sandbox")
DATA_CLASSES = ("public", "internal", "personal")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ActionSpec(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    consequential: bool = False
    mode: str = ""  # read | act; empty = inferred from connector kind
    input_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def snake_name(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
            raise ValueError("action name must be lower_snake_case")
        return value

    @field_validator("mode")
    @classmethod
    def known_mode(cls, value: str) -> str:
        if value and value not in ("read", "act"):
            raise ValueError("mode must be 'read' or 'act'")
        return value

    def resolved_mode(self, kind: str) -> str:
        if self.mode:
            return self.mode
        return "act" if kind in ("messaging", "payments") else "read"


class Limits(BaseModel):
    timeout_s: float = Field(default=30, gt=0, le=300)
    retries: int = Field(default=1, ge=0, le=5)
    rate_per_min: int = Field(default=60, ge=1)


class Manifest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    kind: str
    target_mode: str
    description: str = ""
    credentials: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    data_class: str = "public"
    actions: list[ActionSpec] = Field(min_length=1)
    limits: Limits = Field(default_factory=Limits)
    field_allowlist: list[str] = Field(default_factory=list)
    next_week_checklist: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def snake_name(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
            raise ValueError("connector name must be lower_snake_case")
        return value

    @field_validator("kind")
    @classmethod
    def known_kind(cls, value: str) -> str:
        if value not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        return value

    @field_validator("target_mode")
    @classmethod
    def known_target_mode(cls, value: str) -> str:
        if value not in TARGET_MODES:
            raise ValueError(f"target_mode must be one of {TARGET_MODES}")
        return value

    @field_validator("data_class")
    @classmethod
    def known_data_class(cls, value: str) -> str:
        if value not in DATA_CLASSES:
            raise ValueError(f"data_class must be one of {DATA_CLASSES}")
        return value

    @field_validator("credentials")
    @classmethod
    def names_only(cls, value: list[str]) -> list[str]:
        for item in value:
            if not ENV_NAME.fullmatch(item):
                raise ValueError(f"credential {item!r} must be an environment variable NAME, never a value")
        return value

    @model_validator(mode="after")
    def personal_data_needs_allowlist(self) -> "Manifest":
        if self.data_class == "personal" and not self.field_allowlist:
            raise ValueError("connectors with data_class 'personal' must declare a non-empty field_allowlist")
        return self

    def action(self, name: str) -> ActionSpec | None:
        return next((item for item in self.actions if item.name == name), None)


class ManifestError(ValueError):
    """A connector manifest that failed validation; the connector is refused at startup."""


def load_manifest(folder: Path) -> tuple[Manifest | None, list[str]]:
    """Load and validate connectors/<name>/connector.yaml. Returns (manifest, problems)."""
    path = folder / "connector.yaml"
    problems: list[str] = []
    if not path.exists():
        return None, [f"missing connector.yaml at {path}"]
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return None, [f"invalid YAML in {path.name}: {exc}"]
    if not isinstance(raw, dict):
        return None, [f"{path.name} must be a YAML mapping"]
    if raw.get("name") and raw["name"] != folder.name:
        problems.append(f"manifest name {raw['name']!r} does not match folder name {folder.name!r}")
    try:
        manifest = Manifest(**raw)
    except ValidationError as exc:
        return None, [f"manifest validation failed: {exc}"]
    if manifest.data_class == "personal" and not manifest.field_allowlist:
        problems.append("connectors with data_class 'personal' must declare a non-empty field_allowlist")  # pragma: no cover - model validator already enforces
    return manifest, problems
