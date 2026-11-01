"""Auto-discovered indicator registry for reusable procurement scans."""

from dataclasses import dataclass
import importlib
from pathlib import Path
import pkgutil
from typing import Any, Callable


@dataclass(frozen=True)
class IndicatorMetadata:
    """Human-readable metadata attached to one indicator function."""

    name: str
    description: str
    version: str = "1.0"


@dataclass(frozen=True)
class IndicatorSpec:
    """Registered indicator metadata and callable implementation."""

    metadata: IndicatorMetadata
    function: Callable[..., Any]


_REGISTRY: dict[str, IndicatorSpec] = {}
_DISCOVERED = False


def register(metadata: IndicatorMetadata) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator used by indicator modules to register a typed function."""
    def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
        if metadata.name in _REGISTRY:
            raise ValueError(f"Duplicate indicator: {metadata.name}")
        _REGISTRY[metadata.name] = IndicatorSpec(metadata, function)
        return function

    return decorator


def discover() -> None:
    """Import every module in this package once so decorators register indicators."""
    global _DISCOVERED
    if _DISCOVERED:
        return
    package_dir = Path(__file__).parent
    for module in pkgutil.iter_modules([str(package_dir)]):
        if module.name != "registry":
            importlib.import_module(f"{__package__}.{module.name}")
    _DISCOVERED = True


def get_indicator(name: str) -> IndicatorSpec:
    """Return a discovered indicator or raise a clear configuration error."""
    discover()
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"Unknown indicator: {name}") from exc


def list_indicators() -> tuple[IndicatorSpec, ...]:
    """Return all discovered indicators in stable name order."""
    discover()
    return tuple(_REGISTRY[name] for name in sorted(_REGISTRY))
