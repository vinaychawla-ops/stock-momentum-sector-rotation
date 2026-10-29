import pandas as pd
import pytest
import yaml
from unittest.mock import patch
from src.signals.momentum import (
    compute_monthly_returns, compute_12_1_momentum, rank_by_momentum,
    generate_target_weights, generate_momentum_signal, resolve_universe,
    SECTOR_NAMES, FULL_11, CLASSIC_9,
)

def test_12_1_momentum_basic():
    # Build 14 months of synthetic monthly prices for 3 tickers: A outperforms
    idx = pd.date_range("2024-01-31", periods=14, freq="ME")
    # Simple: A +2% monthly, B +1%, C 0%
    prices = pd.DataFrame({
        "XLK": [100 * (1.02 ** i) for i in range(14)],
        "XLF": [100 * (1.01 ** i) for i in range(14)],
        "XLE": [100 for _ in range(14)],
    }, index=idx)
    monthly_returns = compute_monthly_returns(prices)
    assert len(monthly_returns) >= 13
    mom = compute_12_1_momentum(monthly_returns)
    assert mom["XLK"] > mom["XLF"] > mom["XLE"]
    ranked = rank_by_momentum(mom)
    assert ranked.iloc[0]["symbol"] == "XLK"
    assert ranked.iloc[0]["rank"] == 1
    ranked_w = generate_target_weights(ranked, top_n=2)
    # Top 2 should have weight 0.5 each
    assert ranked_w[ranked_w["target_weight"] > 0].shape[0] == 2
    assert abs(ranked_w.loc[ranked_w["symbol"]=="XLK","target_weight"].iloc[0] - 0.5) < 1e-9

def test_min_momentum_threshold_cash():
    idx = pd.date_range("2024-01-31", periods=14, freq="ME")
    prices = pd.DataFrame({"XLK": [100 - i for i in range(14)], "XLF": [100 - i for i in range(14)]}, index=idx)
    mr = compute_monthly_returns(prices)
    mom = compute_12_1_momentum(mr)
    ranked = rank_by_momentum(mom)
    ranked_w = generate_target_weights(ranked, top_n=2, min_momentum=0.0)
    # All negative -> no allocation
    assert (ranked_w["target_weight"] == 0).all()


def _write_config(tmp_path, universe):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump({"universe": universe}))


def test_classic_9_mode_wins_over_tickers(tmp_path, monkeypatch):
    _write_config(tmp_path, {"mode": "classic_9", "tickers": FULL_11})
    monkeypatch.chdir(tmp_path)
    assert resolve_universe() == CLASSIC_9


def test_custom_mode_uses_explicit_tickers(tmp_path, monkeypatch):
    _write_config(tmp_path, {"mode": "custom", "tickers": ["XLK", "XLF"]})
    monkeypatch.chdir(tmp_path)
    assert resolve_universe() == ["XLK", "XLF"]


def test_symbol_with_short_history_is_dropped():
    idx = pd.date_range("2024-01-31", periods=14, freq="ME")
    prices = pd.DataFrame(
        {
            "XLK": [100 * (1.02 ** i) for i in range(14)],
            "XLF": [100 * (1.01 ** i) for i in range(14)],
            # Only 4 months of history -> incomplete 12-1 window
            "XLC": [float("nan")] * 10 + [100 * (1.03 ** i) for i in range(4)],
        },
        index=idx,
    )
    with patch("src.signals.momentum.download_prices", return_value=prices):
        signal = generate_momentum_signal(symbols=["XLK", "XLF", "XLC"], top_n=2)
    symbols = signal["ranked"]["symbol"].tolist()
    assert "XLC" not in symbols
    assert signal["ranked"].iloc[0]["symbol"] == "XLK"


def test_all_short_history_raises():
    idx = pd.date_range("2024-01-31", periods=14, freq="ME")
    prices = pd.DataFrame(
        {"XLK": [float("nan")] * 10 + [100.0 + i for i in range(4)]}, index=idx
    )
    with patch("src.signals.momentum.download_prices", return_value=prices):
        with pytest.raises(ValueError):
            generate_momentum_signal(symbols=["XLK"], top_n=1)
