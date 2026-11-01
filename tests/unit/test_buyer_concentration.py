"""Tests for the auto-registered buyer concentration indicator."""

from bidbox.indicators import get_indicator, list_indicators


def test_buyer_concentration_is_registered_and_typed() -> None:
    spec = get_indicator("buyer_concentration")
    result = spec.function([
        {"buyer_name": "Buyer A"},
        {"buyer_name": "Buyer A"},
        {"buyer_name": "Buyer B"},
    ])

    assert spec.metadata.name == "buyer_concentration"
    assert result.share == 0.667
    assert result.top_buyer == "Buyer A"
    assert any(item.metadata.name == "price_benchmark" for item in list_indicators())


def test_buyer_concentration_empty_is_neutral() -> None:
    result = get_indicator("buyer_concentration").function([])

    assert result.share == 0.0
    assert result.top_buyer is None
    assert result.pattern == "No historical awards found"
