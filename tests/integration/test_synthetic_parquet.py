from datetime import datetime, timezone

import polars as pl

from fx_hedging.data.simulator import SyntheticMarketConfig, SyntheticMarketGenerator


def test_parquet_round_trip_has_expected_schema_and_rows(tmp_path) -> None:
    output = tmp_path / "events.parquet"
    config = SyntheticMarketConfig(
        seed=42,
        start_time=datetime(2026, 1, 2, 8, 0, tzinfo=timezone.utc),
        num_events=25,
        min_regime_duration=3,
        max_regime_duration=5,
        output_path=output,
    )

    written = SyntheticMarketGenerator(config).write_parquet()
    frame = pl.read_parquet(written)

    assert written == output
    assert frame.height == 25
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
    assert frame["timestamp"].dtype == pl.Datetime(time_unit="us", time_zone="UTC")
    assert frame["timestamp"].is_sorted()
    assert frame["currency_pair"].unique().to_list() == ["EURUSD"]

