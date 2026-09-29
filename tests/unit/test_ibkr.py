from pathlib import Path

import pytest

from fx_hedging.data.ibkr import IbkrMarketDataConfig, IbkrTickAssembler
from fx_hedging.data.models import TradeDirection


def test_ibkr_config_describes_eurusd_cash_contract() -> None:
    config = IbkrMarketDataConfig(port=7497, output_path=Path("ticks.parquet"))

    assert config.currency_pair == "EURUSD"
    assert config.exchange == "IDEALPRO"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"port": 0}, "port"),
        ({"client_id": -1}, "client_id"),
        ({"base_currency": "eur"}, "base_currency"),
        ({"duration_seconds": 0}, "duration_seconds"),
    ],
)
def test_ibkr_config_rejects_invalid_values(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        IbkrMarketDataConfig(**overrides)


def test_bid_ask_callback_produces_quote_event() -> None:
    assembler = IbkrTickAssembler()

    assembler.on_bid_ask(1_767_340_800, 1.0999, 1.1001, 2_000_000, 3_000_000)

    event = assembler.events[0]
    assert event.currency_pair == "EURUSD"
    assert event.trade_direction is TradeDirection.NONE
    assert event.regime is None
    assert event.bid_price == 1.0999
    assert event.ask_price == 1.1001


def test_trade_callback_uses_latest_quote_without_inventing_direction() -> None:
    assembler = IbkrTickAssembler()
    assembler.on_bid_ask(1_767_340_800, 1.0999, 1.1001, 2_000_000, 3_000_000)
    assembler.on_last(1_767_340_801, 1.1001, 50_000)

    trade = assembler.events[-1]
    assert trade.trade_price == 1.1001
    assert trade.trade_size == 50_000
    assert trade.trade_direction is TradeDirection.UNKNOWN


def test_trade_without_quote_is_counted_and_not_emitted() -> None:
    assembler = IbkrTickAssembler()

    assembler.on_last(1_767_340_800, 1.1, 10_000)

    assert assembler.events == ()
    assert assembler.dropped_trades_without_quote == 1


def test_invalid_ibkr_sentinel_quote_is_ignored() -> None:
    assembler = IbkrTickAssembler()

    assembler.on_bid_ask(1_767_340_800, -1, -1, 0, 0)

    assert assembler.events == ()


def test_ibkr_frame_uses_common_market_event_schema() -> None:
    assembler = IbkrTickAssembler()
    assembler.on_bid_ask(1_767_340_800, 1.0999, 1.1001, 2_000_000, 3_000_000)

    frame = assembler.to_frame()

    assert frame.height == 1
    assert frame.columns == [
        "timestamp",
        "currency_pair",
        "bid_price",
        "ask_price",
        "bid_size",
        "ask_size",
        "trade_price",
        "trade_size",
        "trade_direction",
        "regime",
    ]

