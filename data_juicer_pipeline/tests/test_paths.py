from pathlib import Path

import paths


def _redirect_data_roots(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(paths, "DATA_DIR", root)
    monkeypatch.setattr(paths, "RUNS_DIR", root / "runs")
    monkeypatch.setattr(paths, "INTERMEDIATE_DIR", root / "intermediate")
    monkeypatch.setattr(paths, "OUTPUT_DIR", root / "output")
    monkeypatch.setattr(paths, "REPORT_DIR", root / "reports")
    monkeypatch.setattr(paths, "QUARANTINE_DIR", root / "quarantine")
    monkeypatch.setattr(paths, "LOG_DIR", root / "logs")


def test_run_directories_are_self_contained(monkeypatch, tmp_path):
    _redirect_data_roots(monkeypatch, tmp_path)

    result = paths.run_directories("20260807_120000_000001")

    run_root = tmp_path / "runs" / "20260807_120000_000001"
    assert result["root"] == run_root
    for name in ("intermediate", "output", "reports", "quarantine", "logs"):
        assert result[name] == run_root / name
        assert result[name].is_dir()


def test_resolve_existing_run_prefers_new_layout(monkeypatch, tmp_path):
    _redirect_data_roots(monkeypatch, tmp_path)
    run_id = "20260807_120000_000002"
    new_paths = paths.run_directories(run_id)
    (tmp_path / "logs" / "runs" / run_id).mkdir(parents=True)

    assert paths.resolve_existing_run_paths(run_id) == new_paths


def test_resolve_existing_run_supports_legacy_layout(monkeypatch, tmp_path):
    _redirect_data_roots(monkeypatch, tmp_path)
    run_id = "20260806_120000_000003"
    legacy_log = tmp_path / "logs" / "runs" / run_id
    legacy_log.mkdir(parents=True)

    result = paths.resolve_existing_run_paths(run_id)

    assert result["logs"] == legacy_log
    assert result["output"] == tmp_path / "output"
    assert result["quarantine"] == tmp_path / "quarantine" / "runs" / run_id
