from src.execution.position_sizer import PositionSizer


def _config(commission=0.0, **sizing):
    cfg = {"position_sizing": {"min_trade_value": 1, "min_trade_shares": 1}, "data": {"commission_per_share": commission}}
    cfg["position_sizing"].update(sizing)
    return cfg


def _prices(**kw):
    return {k: float(v) for k, v in kw.items()}


def test_buys_share_a_single_cash_budget():
    sizer = PositionSizer(total_equity=1000, cash_available=1000, current_positions={}, config=_config())
    trades = sizer.generate_trades({"AAA": 0.5, "BBB": 0.5}, _prices(AAA=100, BBB=100))
    assert {t.symbol for t in trades} == {"AAA", "BBB"}
    assert sum(t.shares for t in trades) == 10
    assert sum(t.trade_value for t in trades) == 1000


def test_second_buy_is_starved_when_cash_runs_out():
    sizer = PositionSizer(total_equity=1000, cash_available=500, current_positions={}, config=_config())
    trades = sizer.generate_trades({"AAA": 0.5, "BBB": 0.5}, _prices(AAA=100, BBB=100))
    # Only $500 available: one 5-share position fits, the second gets nothing
    assert len(trades) == 1
    assert trades[0].symbol == "AAA"
    assert trades[0].shares == 5


def test_sell_proceeds_fund_the_buys():
    positions = {"AAA": {"quantity": 10, "avg_cost": 90, "market_value": 1000}}
    sizer = PositionSizer(total_equity=1000, cash_available=0, current_positions=positions, config=_config())
    trades = sizer.generate_trades({"BBB": 1.0}, _prices(AAA=100, BBB=100))
    by_symbol = {t.symbol: t for t in trades}
    assert by_symbol["AAA"].action == "SELL" and by_symbol["AAA"].shares == 10
    assert by_symbol["BBB"].action == "BUY" and by_symbol["BBB"].shares == 10
    assert trades[0].action == "SELL"


def test_commission_is_reserved():
    sizer = PositionSizer(total_equity=1000, cash_available=1000, current_positions={}, config=_config(commission=1.0))
    trades = sizer.generate_trades({"AAA": 1.0}, _prices(AAA=100))
    # 101 per share all-in -> 9 shares, not 10
    assert trades[0].shares == 9


def test_min_trade_shares_blocks_small_orders():
    sizer = PositionSizer(total_equity=1000, cash_available=1000, current_positions={}, config=_config(min_trade_shares=5))
    # Target is 20% -> 2 shares at $100, below the 5-share floor
    trades = sizer.generate_trades({"AAA": 0.2}, _prices(AAA=100))
    assert trades == []


def test_weight_threshold_skips_noise():
    positions = {"AAA": {"quantity": 10, "avg_cost": 100, "market_value": 1000}}
    sizer = PositionSizer(total_equity=1000, cash_available=0, current_positions=positions, config=_config())
    # 1% drift, below the 2% default threshold
    trades = sizer.generate_trades({"AAA": 0.99}, _prices(AAA=100), min_threshold=0.02)
    assert trades == []
