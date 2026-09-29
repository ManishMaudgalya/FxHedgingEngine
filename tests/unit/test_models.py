from datetime import datetime, timedelta, timezone

import pytest

from fx_hedging.data.models import (
    ClientOrder,
    Execution,
    MarketEvent,
    MarketRegime,
    Side,
    TradeDirection,
)


NOW = datetime(2026, 1, 2, 8, 0, tzinfo=timezone.utc)


def test_market_event_calculates_mid_and_spread() -> None:
    event = MarketEvent(
        timestamp=NOW,
        currency_pair="EURUSD",
        bid_price=1.0999,
        ask_price=1.1001,
        bid_size=2_000_000,
        ask_size=3_000_000,
        trade_price=None,
        trade_size=0,
        trade_direction=TradeDirection.NONE,
        regime=MarketRegime.HIGH_LIQUIDITY,
    )

    assert event.mid_price == pytest.approx(1.1)
    assert event.spread == pytest.approx(0.0002)


def test_quote_only_event_rejects_trade_values() -> None:
    with pytest.raises(ValueError, match="quote-only"):
        MarketEvent(
            timestamp=NOW,
            currency_pair="EURUSD",
            bid_price=1.0999,
            ask_price=1.1001,
            bid_size=1_000_000,
            ask_size=1_000_000,
            trade_price=1.1,
            trade_size=10_000,
            trade_direction=TradeDirection.NONE,
            regime=MarketRegime.LOW_VOLATILITY,
        )


def test_client_order_requires_valid_benchmark_window() -> None:
    with pytest.raises(ValueError, match="benchmark_time"):
        ClientOrder(
            timestamp=NOW,
            currency_pair="EURUSD",
            quantity=100_000_000,
            side=Side.BUY,
            benchmark_time=NOW - timedelta(minutes=1),
        )


def test_execution_cost_components_must_reconcile() -> None:
    with pytest.raises(ValueError, match="total_cost"):
        Execution(
            timestamp=NOW,
            currency_pair="EURUSD",
            side=Side.BUY,
            requested_quantity=1_000_000,
            filled_quantity=900_000,
            price=1.1,
            spread_cost=10.0,
            market_impact=2.0,
            slippage_cost=1.0,
            total_cost=12.0,
        )


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ClientOrder(
            timestamp=datetime(2026, 1, 2),
            currency_pair="EURUSD",
            quantity=1_000_000,
            side=Side.SELL,
            benchmark_time=NOW,
        )

