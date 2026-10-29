import tempfile, json
from pathlib import Path
from src.portfolio.paper_portfolio import PaperPortfolio

def test_seed_and_snapshot():
    with tempfile.TemporaryDirectory() as tmp:
        snap_dir = Path(tmp) / "snapshots"
        pp = PaperPortfolio.seed(str(snap_dir), initial_cash=50000)
        assert pp.cash == 50000
        assert pp.positions == {}
        # Snapshot after seed (no prices needed)
        p = pp.save_snapshot({}, fill_model="next_open")
        assert p.exists()
        data = json.loads(p.read_text())
        assert data["total_equity"] == 50000
        assert data["data_source"] == "yfinance"

def test_apply_fills_buy_sell():
    with tempfile.TemporaryDirectory() as tmp:
        snap_dir = Path(tmp) / "snapshots"
        pp = PaperPortfolio.seed(str(snap_dir), initial_cash=10000)
        # Buy 10 XLK @ 100
        pp.apply_fills([{"symbol": "XLK", "side": "BUY", "quantity": 10, "fill_price": 100, "commission": 0}])
        assert pp.cash == 9000
        assert pp.positions["XLK"]["quantity"] == 10
        # Sell 4 @ 110
        pp.apply_fills([{"symbol": "XLK", "side": "SELL", "quantity": 4, "fill_price": 110, "commission": 0}])
        assert pp.cash == 9440  # 9000 + 440
        assert pp.positions["XLK"]["quantity"] == 6
        # Snapshot with prices
        p = pp.save_snapshot({"XLK": 105}, fill_model="next_open")
        data = json.loads(p.read_text())
        # 6*105=630 + 9440=10070
        assert abs(data["total_equity"] - 10070) < 1e-6

def test_empty_sell_no_crash():
    with tempfile.TemporaryDirectory() as tmp:
        snap_dir = Path(tmp) / "snapshots"
        pp = PaperPortfolio.seed(str(snap_dir), initial_cash=1000)
        pp.apply_fills([{"symbol": "XLK", "side": "SELL", "quantity": 5, "fill_price": 100, "commission": 0}])
        assert "XLK" not in pp.positions


def test_buy_is_clipped_to_available_cash():
    with tempfile.TemporaryDirectory() as tmp:
        snap_dir = Path(tmp) / "snapshots"
        pp = PaperPortfolio.seed(str(snap_dir), initial_cash=1000)
        # Order asks for $2000 of stock with $1000 cash
        pp.apply_fills([{"symbol": "XLK", "side": "BUY", "quantity": 20, "fill_price": 100, "commission": 0}])
        assert pp.cash >= 0
        assert pp.positions["XLK"]["quantity"] == 10
        assert pp.cash == 0


def test_zero_cash_buy_is_skipped():
    with tempfile.TemporaryDirectory() as tmp:
        snap_dir = Path(tmp) / "snapshots"
        pp = PaperPortfolio.seed(str(snap_dir), initial_cash=0)
        pp.apply_fills([{"symbol": "XLK", "side": "BUY", "quantity": 5, "fill_price": 100, "commission": 0}])
        assert pp.positions == {}
        assert pp.cash == 0
