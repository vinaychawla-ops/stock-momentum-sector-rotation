import json
from src.execution.signal_logger import SignalLogger


def _signal():
    return {
        "signal_date": "2026-10-29",
        "ranked": [],
        "top_sectors": ["XLK"],
        "target_weights": {"XLK": 0.333},
    }


def test_two_runs_on_the_same_day_are_both_kept(tmp_path):
    logger = SignalLogger(str(tmp_path), retention_days=0)
    first = logger.log_signal(_signal(), [], "dry_run")
    second = logger.log_signal(_signal(), [], "live")
    assert first != second
    history = logger.get_signals_history()
    assert len(history) == 2
    assert history[0]["execution_mode"] == "live"


def test_fill_metadata_comes_from_the_live_config(tmp_path):
    logger = SignalLogger(str(tmp_path), retention_days=0, fill_model="close", slippage_bps=7)
    path = logger.log_signal(_signal(), [], "dry_run")
    payload = json.loads(path.read_text())
    assert payload["fill_model"] == "close"
    assert payload["slippage_bps"] == 7


def test_retention_prunes_old_files(tmp_path):
    old = tmp_path / "2020-01-01_120000_live.json"
    old.write_text("{}")
    logger = SignalLogger(str(tmp_path), retention_days=30)
    logger.log_signal(_signal(), [], "dry_run")
    assert not old.exists()


def test_history_reads_legacy_list_files(tmp_path):
    legacy = tmp_path / "2026-01-02.json"
    legacy.write_text(json.dumps([{"execution_mode": "dry_run"}, {"execution_mode": "live"}]))
    logger = SignalLogger(str(tmp_path), retention_days=0)
    history = logger.get_signals_history()
    assert [h["execution_mode"] for h in history] == ["dry_run", "live"]
