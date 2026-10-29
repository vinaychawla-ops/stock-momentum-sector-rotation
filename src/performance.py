"""Performance metrics — reads paper snapshots/executions (same as original, USD)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TRADING_DAYS_PER_YEAR = 252


def load_snapshots(snapshots_dir: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
    snap_path = Path(snapshots_dir)
    if not snap_path.exists():
        logger.warning(f"Snapshots directory not found: {snapshots_dir}")
        return pd.DataFrame()
    all_files = sorted(snap_path.glob("*.json"))
    if not all_files:
        logger.warning("No snapshot files found")
        return pd.DataFrame()
    records = []
    for jf in all_files:
        file_date = jf.stem
        if start_date and file_date < start_date:
            continue
        if end_date and file_date > end_date:
            continue
        try:
            with open(jf) as f:
                data = json.load(f)
            records.append({"date": file_date, "timestamp": data.get("timestamp"), "total_equity": data.get("total_equity", 0), "cash": data.get("cash", 0), "num_positions": len(data.get("positions", []))})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to load {jf}: {e}")
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df


def compute_equity_curve(snapshots: pd.DataFrame) -> pd.Series:
    if snapshots.empty:
        return pd.Series(dtype=float)
    return snapshots["total_equity"]


def compute_returns(equity: pd.Series) -> pd.Series:
    if len(equity) < 2:
        return pd.Series(dtype=float)
    return equity.pct_change().dropna()


def total_return(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    return (equity.iloc[-1] / equity.iloc[0]) - 1


def annualized_return(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    total_ret = total_return(equity)
    days = (equity.index[-1] - equity.index[0]).days
    if days <= 0:
        return 0.0
    years = days / 365.25
    if years < 1 / 365.25:
        return total_ret
    return (1 + total_ret) ** (1 / years) - 1


def annualized_volatility(returns: pd.Series) -> float:
    if len(returns) < 2:
        return 0.0
    return float(returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR))


def sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.0) -> float:
    if len(returns) < 2:
        return 0.0
    excess = returns - (risk_free_rate / TRADING_DAYS_PER_YEAR)
    if excess.std() == 0:
        return 0.0
    return float((excess.mean() / excess.std()) * np.sqrt(TRADING_DAYS_PER_YEAR))


def max_drawdown(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    cummax = equity.cummax()
    cummax_safe = cummax.replace(0, np.nan)
    dd = (equity - cummax) / cummax_safe
    if dd.dropna().empty:
        return 0.0
    return abs(float(dd.min()))


def max_drawdown_duration(equity: pd.Series) -> int:
    if len(equity) < 2:
        return 0
    cummax = equity.cummax()
    is_dd = equity < cummax
    if not is_dd.any():
        return 0
    duration = 0
    max_dur = 0
    for in_dd in is_dd:
        if in_dd:
            duration += 1
            max_dur = max(max_dur, duration)
        else:
            duration = 0
    return max_dur


def drawdown_series(equity: pd.Series) -> pd.Series:
    if len(equity) < 2:
        return pd.Series(dtype=float)
    cummax = equity.cummax()
    cummax_safe = cummax.replace(0, np.nan)
    return (equity - cummax) / cummax_safe


def load_executions_for_performance(executions_dir: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
    exec_path = Path(executions_dir)
    if not exec_path.exists():
        return pd.DataFrame()
    all_files = sorted(exec_path.glob("*.csv"))
    if not all_files:
        return pd.DataFrame()
    dfs = []
    for csv_file in all_files:
        fd = csv_file.stem
        if start_date and fd < start_date:
            continue
        if end_date and fd > end_date:
            continue
        try:
            dfs.append(pd.read_csv(csv_file))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to load {csv_file}: {e}")
    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True)


def compute_trade_pnl(executions: pd.DataFrame) -> pd.DataFrame:
    if executions.empty:
        return pd.DataFrame()
    executions = executions.sort_values("timestamp").copy()
    trades: list[dict] = []
    open_positions: dict[str, list[dict]] = {}
    for _, row in executions.iterrows():
        symbol = row["symbol"]
        side = row["side"]
        qty = float(row["quantity"])
        price = float(row["fill_price"])
        commission = float(row.get("commission", 0) or 0)
        if symbol not in open_positions:
            open_positions[symbol] = []
        if side == "BUY":
            open_positions[symbol].append({"quantity": qty, "price": price, "commission": commission, "timestamp": row["timestamp"]})
        else:
            remaining = qty
            total_cost = 0.0
            total_entry_comm = 0.0
            while remaining > 0 and open_positions[symbol]:
                pos = open_positions[symbol][0]
                close_qty = min(remaining, pos["quantity"])
                total_cost += close_qty * pos["price"]
                total_entry_comm += pos["commission"] * (close_qty / pos["quantity"]) if pos["quantity"] else 0
                pos["quantity"] -= close_qty
                remaining -= close_qty
                if pos["quantity"] <= 0:
                    open_positions[symbol].pop(0)
            closed_qty = qty - remaining
            if closed_qty > 0:
                avg_entry = total_cost / closed_qty
                gross = (price - avg_entry) * closed_qty
                net = gross - total_entry_comm - commission
                trades.append({"symbol": symbol, "quantity": closed_qty, "entry_price": avg_entry, "exit_price": price, "gross_pnl": gross, "commission": total_entry_comm + commission, "net_pnl": net, "exit_timestamp": row["timestamp"]})
    return pd.DataFrame(trades)


def win_rate(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    return float((trades["net_pnl"] > 0).mean())


def profit_factor(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    profits = trades[trades["net_pnl"] > 0]["net_pnl"].sum()
    losses = abs(trades[trades["net_pnl"] < 0]["net_pnl"].sum())
    if losses == 0:
        return float("inf") if profits > 0 else 0.0
    return float(profits / losses)


def compute_all_metrics(snapshots_dir: str, executions_dir: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> dict:
    snapshots = load_snapshots(snapshots_dir, start_date, end_date)
    equity = compute_equity_curve(snapshots)
    returns = compute_returns(equity)
    executions = load_executions_for_performance(executions_dir, start_date, end_date)
    trade_pnl = compute_trade_pnl(executions)
    return {
        "snapshots": snapshots,
        "equity": equity,
        "returns": returns,
        "total_return": total_return(equity),
        "annualized_return": annualized_return(equity),
        "volatility": annualized_volatility(returns),
        "sharpe": sharpe_ratio(returns),
        "max_drawdown": max_drawdown(equity),
        "max_dd_duration": max_drawdown_duration(equity),
        "num_trades": len(trade_pnl),
        "win_rate": win_rate(trade_pnl),
        "profit_factor": profit_factor(trade_pnl),
        "trade_pnl": trade_pnl,
        "num_snapshots": len(snapshots),
    }


def format_performance_report(metrics: dict) -> str:
    eq = metrics.get("equity")
    if eq is None or len(eq) == 0:
        return "No snapshots found. Run 'python main.py seed' or 'python main.py snapshot' first."
    lines = [
        "PERFORMANCE REPORT",
        "=" * 60,
        f"Period: {eq.index[0].date()} to {eq.index[-1].date()} ({len(eq)} snapshots)",
        f"Start Equity: ${eq.iloc[0]:,.2f}",
        f"End Equity:   ${eq.iloc[-1]:,.2f}",
        f"Total Return: {metrics['total_return']*100:+.2f}%",
        f"Ann. Return:  {metrics['annualized_return']*100:+.2f}%",
        f"Ann. Vol:     {metrics['volatility']*100:.2f}%",
        f"Sharpe:       {metrics['sharpe']:.2f} (rf=0%)",
        f"Max DD:       {metrics['max_drawdown']*100:.2f}%",
        f"Max DD Days:  {metrics['max_dd_duration']}",
        f"Trades:       {metrics['num_trades']}",
        f"Win Rate:     {metrics['win_rate']*100:.1f}%",
        f"Profit Factor:{metrics['profit_factor']:.2f}" if metrics['profit_factor'] != float('inf') else "Profit Factor: inf",
        "",
        "Data source: Yahoo Finance (paper) — simulated fills",
        "",
    ]
    return "\n".join(lines)
