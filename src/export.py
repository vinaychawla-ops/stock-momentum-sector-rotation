"""Monthly PDF export — ReportLab, US/paper variant (USD, disclaimer)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, PageBreak

from src.performance import load_snapshots, compute_equity_curve, compute_returns, total_return, annualized_return, annualized_volatility, sharpe_ratio, max_drawdown

logger = logging.getLogger(__name__)


def get_current_week() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year}-W{now.isocalendar().week:02d}"


def generate_monthly_report(year_month: str, snapshots_dir: str, executions_dir: str, annotations_dir: str, output_dir: str) -> Path:
    """Generate monthly PDF for YYYY-MM (e.g. 2026-01)."""
    snapshots = load_snapshots(snapshots_dir)
    if snapshots.empty:
        raise ValueError("No snapshots found — run snapshot/seed first")

    # Filter to month
    year, month = year_month.split("-")
    month_start = f"{year_month}-01"
    # Use all snapshots for equity curve but highlight month

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    pdf_path = output_path / f"{year_month}-report.pdf"

    # Equity curve chart
    equity = compute_equity_curve(snapshots)
    plt.figure(figsize=(8, 3))
    plt.plot(equity.index, equity.values, color="#0f172a", linewidth=1.8)
    plt.title(f"Equity Curve — {year_month} (Yahoo Finance Paper)", fontsize=10)
    plt.xlabel("Date")
    plt.ylabel("Equity ($)")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    chart_path = output_path / f"{year_month}-chart.png"
    plt.savefig(chart_path, dpi=150)
    plt.close()

    # Metrics
    returns = compute_returns(equity)
    t_ret = total_return(equity) if len(equity) else 0
    a_ret = annualized_return(equity) if len(equity) else 0
    vol = annualized_volatility(returns) if len(returns) else 0
    sharpe = sharpe_ratio(returns) if len(returns) else 0
    mdd = max_drawdown(equity) if len(equity) else 0

    # Latest snapshot positions
    latest_snapshot = None
    snap_files = sorted(Path(snapshots_dir).glob("*.json"), reverse=True)
    if snap_files:
        latest_snapshot = json.loads(snap_files[0].read_text())

    # Executions in month
    exec_rows = []
    for csv_file in Path(executions_dir).glob("*.csv"):
        if csv_file.stem.startswith(year_month):
            try:
                df = pd.read_csv(csv_file)
                exec_rows.append(df)
            except Exception:
                pass
    executions_df = pd.concat(exec_rows, ignore_index=True) if exec_rows else pd.DataFrame()

    # Build PDF
    doc = SimpleDocTemplate(str(pdf_path), pagesize=letter, topMargin=0.6*inch, bottomMargin=0.6*inch)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title2", parent=styles["Title"], fontSize=18, textColor=colors.HexColor("#0f172a"))
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, textColor=colors.HexColor("#0f172a"), spaceBefore=12)
    normal = styles["Normal"]
    normal.fontSize = 9
    normal.leading = 12
    story = []

    story.append(Paragraph(f"Monthly Report — {year_month}", title_style))
    story.append(Spacer(1, 6))
    story.append(Paragraph("US Sector Momentum (12-1) · SPDR ETFs · Yahoo Finance (Paper Trading) · Not broker-verified — fills are simulated (next_open + slippage_bps).", normal))
    story.append(Spacer(1, 4))
    story.append(Paragraph(f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} | Data source: yfinance | Currency: USD", normal))
    story.append(Spacer(1, 12))

    # Key stats table
    stats_data = [
        ["Metric", "Value"],
        ["Period", f"{equity.index[0].date()} to {equity.index[-1].date()}" if len(equity) else "—"],
        ["Start / End Equity", f"${equity.iloc[0]:,.2f} → ${equity.iloc[-1]:,.2f}" if len(equity) else "—"],
        ["Total Return", f"{t_ret*100:+.2f}%"],
        ["Ann. Return", f"{a_ret*100:+.2f}%"],
        ["Ann. Vol", f"{vol*100:.2f}%"],
        ["Sharpe (rf=0)", f"{sharpe:.2f}"],
        ["Max Drawdown", f"{mdd*100:.2f}%"],
        ["Snapshots", str(len(equity))],
        ["Fills (month)", str(len(executions_df))],
    ]
    t = Table(stats_data, colWidths=[2.2*inch, 4.3*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#0f172a")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTSIZE", (0,0), (-1,-1), 8),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("BACKGROUND", (0,1), (-1,-1), colors.HexColor("#f8fafc")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(t)
    story.append(Spacer(1, 14))

    # Chart
    if chart_path.exists():
        story.append(RLImage(str(chart_path), width=6.5*inch, height=2.4*inch))
        story.append(Spacer(1, 12))

    # Positions
    story.append(Paragraph("Current Positions (latest snapshot)", h2))
    if latest_snapshot and latest_snapshot.get("positions"):
        pos_data = [["Symbol","Qty","Avg Cost","Mkt Price","Mkt Value","Unrealized"]]
        for pos in latest_snapshot["positions"]:
            pos_data.append([pos.get("symbol",""), str(pos.get("quantity","")), f"${pos.get('avg_cost',0):.2f}", f"${pos.get('market_price',0):.2f}", f"${pos.get('market_value',0):,.0f}", f"${pos.get('unrealized_pnl',0):,.0f}"])
        pt = Table(pos_data, colWidths=[1*inch, 0.8*inch, 1*inch, 1*inch, 1.2*inch, 1.2*inch])
        pt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0), colors.HexColor("#0f172a")),("TEXTCOLOR",(0,0),(-1,0), colors.white),("FONTSIZE",(0,0),(-1,-1),7),("GRID",(0,0),(-1,-1),0.5, colors.grey),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, colors.HexColor("#f8fafc")])]))
        story.append(pt)
    else:
        story.append(Paragraph("No positions (all cash or no snapshot).", normal))
    story.append(Spacer(1, 12))

    # Trades
    story.append(Paragraph(f"Trades — {year_month} (Paper Fills)", h2))
    if not executions_df.empty:
        # Show up to 20
        cols = ["timestamp","symbol","side","quantity","fill_price","slippage_bps","commission"]
        avail = [c for c in cols if c in executions_df.columns]
        head = executions_df[avail].head(20)
        trade_data = [avail]
        for _, r in head.iterrows():
            trade_data.append([str(r[c])[:19] if c=="timestamp" else str(r[c]) for c in avail])
        tt = Table(trade_data, repeatRows=1, colWidths=[1.6*inch, 0.7*inch, 0.6*inch, 0.7*inch, 0.8*inch, 0.9*inch, 0.8*inch][:len(avail)])
        tt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0), colors.HexColor("#0f172a")),("TEXTCOLOR",(0,0),(-1,0), colors.white),("FONTSIZE",(0,0),(-1,-1),6),("GRID",(0,0),(-1,-1),0.5, colors.grey),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, colors.HexColor("#f8fafc")])]))
        story.append(tt)
        if len(executions_df) > 20:
            story.append(Paragraph(f"... and {len(executions_df)-20} more fills (see data/executions/{year_month}*.csv)", normal))
    else:
        story.append(Paragraph("No fills recorded for this month.", normal))
    story.append(Spacer(1, 12))

    # Commentary
    monthly_md = Path(annotations_dir) / "monthly" / f"{year_month}.md"
    if monthly_md.exists():
        story.append(Paragraph("Monthly Commentary", h2))
        try:
            md_text = monthly_md.read_text()[:3000]
            # Strip markdown headers for PDF
            for line in md_text.splitlines()[:30]:
                story.append(Paragraph(line.replace("#","").strip() or "<br/>", normal))
        except Exception:
            pass
        story.append(Spacer(1, 12))

    # Disclaimer
    story.append(Paragraph("Disclaimer & Methodology", h2))
    story.append(Paragraph(
        "This report is for paper-trading illustration only. Prices are from Yahoo Finance (yfinance) and may be delayed or adjusted. "
        "Fills are simulated at next trading day open plus modeled slippage (slippage_bps in config, default 5 bps) and zero or configured commission. "
        "No brokerage execution occurred; results are not broker-verified. Past simulated performance does not guarantee future results. "
        "Not financial advice. Universe: US SPDR sector ETFs (XLK, XLF, XLE, XLV, XLY, XLP, XLI, XLB, XLU, XLC, XLRE) — configurable via config.yaml. "
        "Signal: 12-1 momentum (12-month cumulative return excluding most recent month), equal-weight top 3, monthly rebalance, NYSE calendar (America/New_York).",
        ParagraphStyle("Disclaimer", parent=normal, fontSize=7, leading=9, textColor=colors.HexColor("#475569"))
    ))

    doc.build(story)
    logger.info(f"Generated monthly report: {pdf_path}")
    # cleanup chart
    try:
        chart_path.unlink()
    except Exception:
        pass
    return pdf_path


def generate_weekly_report(year_week: str, snapshots_dir: str, executions_dir: str, annotations_dir: str, output_dir: str) -> Path:
    # Thin wrapper: reuse monthly report styling but filename by week
    snapshots = load_snapshots(snapshots_dir)
    if snapshots.empty:
        raise ValueError("No snapshots found")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    # Reuse monthly template with week label
    monthly_like = year_week.replace("-W", "-W")
    # Generate with same chart but label week
    equity = compute_equity_curve(snapshots)
    plt.figure(figsize=(8, 3))
    plt.plot(equity.index, equity.values, color="#0f172a", linewidth=1.8)
    plt.title(f"Equity Curve — {year_week} (Paper)", fontsize=10)
    plt.xlabel("Date")
    plt.ylabel("Equity ($)")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    chart_path = output_path / f"{year_week}-chart.png"
    plt.savefig(chart_path, dpi=150)
    plt.close()
    pdf_path = output_path / f"{year_week}-weekly-report.pdf"
    doc = SimpleDocTemplate(str(pdf_path), pagesize=letter)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title2", parent=styles["Title"], fontSize=16)
    normal = styles["Normal"]
    story: list = []
    story.append(Paragraph(f"Weekly Report — {year_week}", title_style))
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Equity: ${equity.iloc[-1]:,.2f} | Period: {equity.index[0].date()} to {equity.index[-1].date()}", normal))
    story.append(Spacer(1, 12))
    if chart_path.exists():
        story.append(RLImage(str(chart_path), width=6.5*inch, height=2.4*inch))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Paper trading via Yahoo Finance — simulated fills (next_open + slippage_bps).", ParagraphStyle("sm", parent=normal, fontSize=8, textColor=colors.HexColor("#475569"))))
    doc.build(story)
    try:
        chart_path.unlink()
    except Exception:
        pass
    return pdf_path
