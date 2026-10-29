import tempfile, json, os
from pathlib import Path
import pandas as pd
from src.performance import load_snapshots, compute_equity_curve, total_return, sharpe_ratio, max_drawdown, compute_all_metrics

def test_compute_metrics_with_snapshots():
    with tempfile.TemporaryDirectory() as tmp:
        snap_dir = Path(tmp) / "snapshots"
        exec_dir = Path(tmp) / "executions"
        snap_dir.mkdir(parents=True)
        exec_dir.mkdir(parents=True)
        # Create 5 snapshots with rising equity
        for i, eq in enumerate([100000, 101000, 100500, 102000, 103000]):
            day = f"2026-01-0{1+i}"
            data = {"timestamp": f"{day}T21:05:00Z", "total_equity": eq, "cash": 5000, "positions": []}
            (snap_dir / f"{day}.json").write_text(json.dumps(data))
        metrics = compute_all_metrics(str(snap_dir), str(exec_dir))
        assert metrics["num_snapshots"] == 5
        assert metrics["total_return"] > 0
        assert metrics["max_drawdown"] >= 0

def test_load_snapshots_empty():
    with tempfile.TemporaryDirectory() as tmp:
        df = load_snapshots(tmp)
        assert df.empty
