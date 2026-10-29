from datetime import datetime
from zoneinfo import ZoneInfo

from src.scheduler import (
    _is_trading_day,
    _is_first_trading_day_of_month,
    _rebalance_due,
)

NY = ZoneInfo("America/New_York")


def test_is_trading_day_weekday():
    # Monday 2026-08-24 should be trading (whether XNYS calendar or fallback)
    assert _is_trading_day(datetime(2026, 8, 24, 12, 0, tzinfo=NY)) is True
    # Sunday 2026-08-23 should be non-trading
    assert _is_trading_day(datetime(2026, 8, 23, 12, 0, tzinfo=NY)) is False


def test_first_trading_day_of_month():
    # 2026-09-01 is the Tuesday after Labor Day weekend
    assert _is_first_trading_day_of_month(datetime(2026, 9, 1, 12, 0, tzinfo=NY)) is True
    assert _is_first_trading_day_of_month(datetime(2026, 9, 2, 12, 0, tzinfo=NY)) is False
    # 2026-08-01 is a Saturday, so the first session is Monday 2026-08-03
    assert _is_first_trading_day_of_month(datetime(2026, 8, 3, 12, 0, tzinfo=NY)) is True
    assert _is_first_trading_day_of_month(datetime(2026, 8, 1, 12, 0, tzinfo=NY)) is False


def test_rebalance_cadence_monthly():
    cfg = {"rebalance_cadence": "monthly"}
    assert _rebalance_due(datetime(2026, 9, 1, 16, 10, tzinfo=NY), cfg) is True
    assert _rebalance_due(datetime(2026, 9, 15, 16, 10, tzinfo=NY), cfg) is False


def test_rebalance_cadence_weekly_daily_off():
    weekly = {"rebalance_cadence": "weekly", "rebalance_day": "sunday"}
    assert _rebalance_due(datetime(2026, 9, 6, 20, 0, tzinfo=NY), weekly) is True  # Sunday
    assert _rebalance_due(datetime(2026, 9, 8, 20, 0, tzinfo=NY), weekly) is False  # Tuesday

    assert _rebalance_due(datetime(2026, 9, 15, 16, 10, tzinfo=NY), {"rebalance_cadence": "daily"}) is True
    assert _rebalance_due(datetime(2026, 9, 1, 16, 10, tzinfo=NY), {"rebalance_cadence": "off"}) is False
