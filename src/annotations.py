"""Trade annotations — pre/post thesis notes (unchanged from reference, USD)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def list_annotations(annotations_dir: str) -> list[dict]:
    p = Path(annotations_dir)
    if not p.exists():
        return []
    out = []
    for jf in p.glob("*.json"):
        if jf.name.startswith("monthly"):
            continue
        try:
            data = json.loads(jf.read_text())
            if "trade_id" in data:
                out.append(data)
        except Exception:
            continue
    return sorted(out, key=lambda x: x.get("created_at", ""), reverse=True)


def format_annotation_list(annotations: list[dict]) -> str:
    if not annotations:
        return "No annotations yet. Run 'python main.py annotate new --pre'"
    lines = ["ANNOTATIONS", "=" * 60]
    for a in annotations:
        tid = a.get("trade_id", "")
        created = a.get("created_at", "")
        pre = a.get("pre_trade", {})
        post = a.get("post_trade", {})
        lines.append(f"{tid} | {created} | {pre.get('symbol','')} | thesis: {pre.get('thesis','')[:50]}")
        if post:
            lines.append(f"  post: {post.get('outcome','')} | lesson: {post.get('lesson','')}")
    return "\n".join(lines)


def interactive_annotate(annotations_dir: str, trade_id: Optional[str] = None, pre_trade: bool = True, post_trade: bool = False) -> None:
    p = Path(annotations_dir)
    p.mkdir(parents=True, exist_ok=True)
    if trade_id is None:
        trade_id = str(uuid.uuid4())
        print(f"Creating new annotation: {trade_id}")
    file_path = p / f"{trade_id}.json"
    existing = {}
    if file_path.exists():
        try:
            existing = json.loads(file_path.read_text())
        except Exception:
            existing = {"trade_id": trade_id}

    if not existing:
        existing = {"trade_id": trade_id, "created_at": _now_iso()}

    if pre_trade:
        print("Pre-trade annotation:")
        symbol = input("Symbol (e.g. XLK): ").strip().upper() or existing.get("pre_trade", {}).get("symbol", "")
        thesis = input("Thesis: ").strip() or existing.get("pre_trade", {}).get("thesis", "")
        intended = input("Intended entry price: ").strip() or str(existing.get("pre_trade", {}).get("intended_entry", ""))
        rationale = input("Position size rationale: ").strip() or existing.get("pre_trade", {}).get("position_size_rationale", "")
        exit_plan = input("Exit plan: ").strip() or existing.get("pre_trade", {}).get("exit_plan", "")
        try:
            intended_val: Optional[float] = float(intended) if intended else None
        except Exception:
            intended_val = intended  # type: ignore
        existing["pre_trade"] = {"symbol": symbol, "thesis": thesis, "intended_entry": intended_val, "position_size_rationale": rationale, "exit_plan": exit_plan}
        if "created_at" not in existing:
            existing["created_at"] = _now_iso()

    if post_trade:
        print("Post-trade annotation:")
        outcome = input("Outcome: ").strip()
        matched = input("Matched expectation? (yes/no): ").strip().lower()
        lesson = input("Lesson: ").strip()
        existing["post_trade"] = {"outcome": outcome, "matched_expectation": matched == "yes", "lesson": lesson}
        existing["updated_at"] = _now_iso()

    file_path.write_text(json.dumps(existing, indent=2))
    print(f"Saved annotation to {file_path}")
