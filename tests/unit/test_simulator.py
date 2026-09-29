from dataclasses import replace
from datetime import datetime, timezone

from fx_hedging.data.models import MarketRegime, TradeDirection
from fx_hedging.data.simulator import SyntheticMarketConfig, SyntheticMarketGenerator


def small_config(**overrides: object) -> SyntheticMarketConfig:
    config = SyntheticMarketConfig(
        seed=19,
        start_time=datetime(2026, 1, 2, 8, 0, tzinfo=timezone.utc),
        num_events=36,
        event_interval_ms=250,
        min_regime_duration=2,
        max_regime_duration=2,
    )
    return replace(config, **overrides)


def test_same_seed_produces_identical_events() -> None:
    first = SyntheticMarketGenerator(small_config()).generate()
    second = SyntheticMarketGenerator(small_config()).generate()

    assert first == second


def test_different_seed_changes_events() -> None:
    first = SyntheticMarketGenerator(small_config(seed=1)).generate()
    second = SyntheticMarketGenerator(small_config(seed=2)).generate()

    assert first != second


def test_events_are_chronological_and_quotes_are_valid() -> None:
    events = SyntheticMarketGenerator(small_config()).generate()

    assert len(events) == 36
    assert [event.timestamp for event in events] == sorted(event.timestamp for event in events)
    assert all(event.bid_price < event.ask_price for event in events)
    assert all(event.bid_size > 0 and event.ask_size > 0 for event in events)
    assert all(event.currency_pair == "EURUSD" for event in events)
    for event in events:
        if event.trade_direction is TradeDirection.BUY:
            assert event.trade_price == event.ask_price
        elif event.trade_direction is TradeDirection.SELL:
            assert event.trade_price == event.bid_price
        else:
            assert event.trade_price is None and event.trade_size == 0


def test_schedule_exercises_every_configured_regime() -> None:
    events = SyntheticMarketGenerator(small_config()).generate()

    assert {event.regime for event in events} == set(MarketRegime)


def test_generator_does_not_mutate_global_numpy_state() -> None:
    import numpy as np

    np.random.seed(123)
    expected = np.random.random()
    np.random.seed(123)
    SyntheticMarketGenerator(small_config()).generate()
    actual = np.random.random()

    assert actual == expected

