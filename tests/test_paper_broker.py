import tempfile, csv
from pathlib import Path
from unittest.mock import patch
from src.broker.paper_broker import PaperBroker
from src.portfolio.paper_portfolio import PaperPortfolio


def _mock_prices(symbols):
    return {s: 100.0 for s in symbols}


def _config(tmp, fill_model="next_open", slippage=5):
    return {
        "paths": {
            "executions": str(Path(tmp) / "executions"),
            "snapshots": str(Path(tmp) / "snapshots"),
        },
        "data": {"fill_model": fill_model, "slippage_bps": slippage, "commission_per_share": 0.0},
        "execution": {"fill_model": fill_model},
    }


def _trade(shares=10, symbol="XLK", action="BUY"):
    return [{"symbol": symbol, "action": action, "shares": shares, "price": 100,
             "target_weight": 0.33, "current_weight": 0, "trade_value": shares * 100, "reason": "test"}]


def test_dry_run_no_files():
    with tempfile.TemporaryDirectory() as tmp:
        config = _config(tmp)
        with patch("src.broker.yfinance_provider.get_current_prices", side_effect=_mock_prices):
            broker = PaperBroker(config, dry_run=True)
            results = broker.submit_batch(_trade())
            assert len(results) == 1
            assert results[0].status == "dry_run"
            assert not list(Path(config["paths"]["executions"]).glob("*.csv"))


def test_live_writes_csv_and_updates_portfolio():
    with tempfile.TemporaryDirectory() as tmp:
        snap_dir = Path(tmp) / "snapshots"
        exec_dir = Path(tmp) / "executions"
        snap_dir.mkdir(parents=True)
        exec_dir.mkdir(parents=True)
        portfolio = PaperPortfolio.seed(str(snap_dir), initial_cash=10000)
        portfolio.save_snapshot({}, fill_model="next_open")
        config = _config(tmp)
        with patch("src.broker.yfinance_provider.get_current_prices", side_effect=_mock_prices):
            broker = PaperBroker(config, dry_run=False, portfolio=portfolio)
            results = broker.submit_batch(_trade())
            assert results[0].status == "filled"
            # Slippage 5 bps on BUY: 100 * 1.0005 = 100.05
            assert abs(results[0].fill_price - 100.05) < 1e-6
            csv_files = list(exec_dir.glob("*.csv"))
            assert len(csv_files) == 1
            rows = list(csv.DictReader(open(csv_files[0])))
            assert rows[0]["symbol"] == "XLK"
            assert rows[0]["data_source"] == "yfinance_paper"
            assert rows[0]["fill_model"] == "next_open"
            assert "XLK" in portfolio.positions


def test_next_open_uses_open_of_first_session_after_signal():
    with tempfile.TemporaryDirectory() as tmp:
        portfolio = PaperPortfolio.seed(str(Path(tmp) / "snapshots"), initial_cash=10000)
        config = _config(tmp)
        sessions = {"XLK": {"date": "2026-09-01", "open": 101.0, "close": 105.0}}
        with patch("src.broker.yfinance_provider.get_latest_session_ohlc", return_value=sessions), \
             patch("src.broker.yfinance_provider.get_current_prices", side_effect=_mock_prices):
            broker = PaperBroker(config, dry_run=False, portfolio=portfolio)
            results = broker.submit_batch(_trade(), signal_date="2026-08-31")
        assert results[0].status == "filled"
        # Open 101 * 1.0005 — the close (105) must not leak into the fill
        assert abs(results[0].fill_price - 101.0505) < 1e-6
        rows = list(csv.DictReader(open(next(Path(config["paths"]["executions"]).glob("*.csv")))))
        assert float(rows[0]["intended_price"]) == 101.0


def test_next_open_is_pending_until_session_opens():
    with tempfile.TemporaryDirectory() as tmp:
        portfolio = PaperPortfolio.seed(str(Path(tmp) / "snapshots"), initial_cash=10000)
        config = _config(tmp)
        # Latest session is the signal date itself -> next open not available yet
        sessions = {"XLK": {"date": "2026-08-31", "open": 101.0, "close": 105.0}}
        with patch("src.broker.yfinance_provider.get_latest_session_ohlc", return_value=sessions), \
             patch("src.broker.yfinance_provider.get_current_prices", side_effect=_mock_prices):
            broker = PaperBroker(config, dry_run=False, portfolio=portfolio)
            results = broker.submit_batch(_trade(), signal_date="2026-08-31")
        assert results[0].status == "pending"
        assert results[0].fill_price is None
        assert not list(Path(config["paths"]["executions"]).glob("*.csv"))
        assert portfolio.positions == {}


def test_close_model_fills_at_close():
    with tempfile.TemporaryDirectory() as tmp:
        portfolio = PaperPortfolio.seed(str(Path(tmp) / "snapshots"), initial_cash=10000)
        config = _config(tmp, fill_model="close")
        with patch("src.broker.yfinance_provider.get_current_prices", side_effect=_mock_prices):
            broker = PaperBroker(config, dry_run=False, portfolio=portfolio)
            results = broker.submit_batch(_trade(), signal_date="2026-08-31")
        assert results[0].status == "filled"
        assert abs(results[0].fill_price - 100.05) < 1e-6


def test_buy_is_clipped_to_available_cash():
    with tempfile.TemporaryDirectory() as tmp:
        portfolio = PaperPortfolio.seed(str(Path(tmp) / "snapshots"), initial_cash=500)
        config = _config(tmp)
        sessions = {"XLK": {"date": "2026-09-01", "open": 100.0, "close": 100.0}}
        with patch("src.broker.yfinance_provider.get_latest_session_ohlc", return_value=sessions), \
             patch("src.broker.yfinance_provider.get_current_prices", side_effect=_mock_prices):
            broker = PaperBroker(config, dry_run=False, portfolio=portfolio)
            results = broker.submit_batch(_trade(shares=10), signal_date="2026-08-31")
        # 500 cash / 100.05 per share -> 4 shares
        assert results[0].status == "filled"
        assert results[0].shares == 4
        assert portfolio.cash >= 0
        assert portfolio.positions["XLK"]["quantity"] == 4


def test_unknown_fill_model_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        config = _config(tmp, fill_model="magic")
        try:
            PaperBroker(config, dry_run=True)
        except ValueError as e:
            assert "fill_model" in str(e)
        else:
            raise AssertionError("expected ValueError")
