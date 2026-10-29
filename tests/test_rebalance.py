from src.signals.rebalance import calculate_trades


def test_buys_cannot_exceed_available_cash():
    trades = calculate_trades(
        current_weights={},
        target_weights={"XLK": 0.5, "XLF": 0.5},
        total_equity=1000,
        current_prices={"XLK": 100, "XLF": 100},
        current_positions={},
    )
    # $1000 cash buys 10 shares total, not 5 + 5 at $500 each plus a phantom $0
    assert trades["shares_to_trade"].sum() == 10
    assert all(trades["shares_to_trade"] > 0)


def test_sell_proceeds_fund_the_buy():
    trades = calculate_trades(
        current_weights={"XLE": 1.0},
        target_weights={"XLK": 1.0},
        total_equity=1000,
        current_prices={"XLE": 100, "XLK": 100},
        current_positions={"XLE": 10},
    )
    by_symbol = {r["symbol"]: r for _, r in trades.iterrows()}
    assert by_symbol["XLE"]["action"] == "SELL"
    assert by_symbol["XLE"]["shares_to_trade"] == 10
    assert by_symbol["XLK"]["action"] == "BUY"
    assert by_symbol["XLK"]["shares_to_trade"] == 10
