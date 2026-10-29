"""Slippage analysis — modeled slippage for paper (yfinance)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def analyze_slippage(executions_dir: str, start_date: Optional[str] = None, end_date: Optional[str] = None, outlier_threshold_bps: float = 10) -> dict:
    exec_path = Path(executions_dir)
    if not exec_path.exists():
        return {"executions": pd.DataFrame(), "stats": {}, "outliers": pd.DataFrame(), "empty": True}
    files = sorted(exec_path.glob("*.csv"))
    dfs = []
    for f in files:
        fd = f.stem
        if start_date and fd < start_date:
            continue
        if end_date and fd > end_date:
            continue
        try:
            dfs.append(pd.read_csv(f))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed {f}: {e}")
    if not dfs:
        return {"executions": pd.DataFrame(), "stats": {}, "outliers": pd.DataFrame(), "empty": True}
    df = pd.concat(dfs, ignore_index=True)
    if "slippage_bps" not in df.columns:
        df["slippage_bps"] = 0
    stats = {
        "count": len(df),
        "mean_bps": float(df["slippage_bps"].mean()) if not df.empty else 0,
        "median_bps": float(df["slippage_bps"].median()) if not df.empty else 0,
        "max_bps": float(df["slippage_bps"].max()) if not df.empty else 0,
        "min_bps": float(df["slippage_bps"].min()) if not df.empty else 0,
    }
    outliers = df[df["slippage_bps"].abs() > outlier_threshold_bps] if not df.empty else pd.DataFrame()
    return {"executions": df, "stats": stats, "outliers": outliers, "empty": False}


def format_slippage_report(analysis: dict, outlier_threshold_bps: float = 10) -> str:
    if analysis.get("empty"):
        return "No executions found. Run 'python main.py execute --live' (paper) to generate fills."
    stats = analysis["stats"]
    outliers = analysis["outliers"]
    lines = [
        "SLIPPAGE ANALYSIS (Paper — Modeled)",
        "=" * 60,
        f"Total fills: {stats['count']}",
        f"Mean slippage: {stats['mean_bps']:.2f} bps",
        f"Median:        {stats['median_bps']:.2f} bps",
        f"Max:           {stats['max_bps']:.2f} bps",
        f"Min:           {stats['min_bps']:.2f} bps",
        f"Outliers (>{outlier_threshold_bps} bps): {len(outliers)}",
        "",
        "Note: Slippage is modeled as slippage_bps from config (default 5 bps) applied to next_open fill. Not broker-observed.",
        "",
    ]
    if not outliers.empty:
        lines.append("Outliers:")
        for _, r in outliers.iterrows():
            lines.append(f"  {r['timestamp']} {r['symbol']} {r['side']} {r['quantity']} slippage {r['slippage_bps']} bps fill ${r['fill_price']:.2f}")
        lines.append("")
    return "\n".join(lines)
