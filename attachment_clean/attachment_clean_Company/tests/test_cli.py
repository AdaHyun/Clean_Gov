import sys

from src.cli import parse_args


def test_batch_submit_cli_default_max_in_flight_is_ten(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["company", "batch-submit"])
    assert parse_args().max_in_flight == 10


def test_batch_submit_cli_explicit_max_in_flight(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["company", "batch-submit", "--max-in-flight", "5"]
    )
    assert parse_args().max_in_flight == 5


def test_check_state_is_read_only_by_default(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["company", "check-state", "--batch-id", "b1"])
    args = parse_args()
    assert args.batch_id == "b1"
    assert args.repair_active_index is False


def test_audit_docx_accepts_parser_logs(monkeypatch, tmp_path):
    log = tmp_path / "parser.log"
    monkeypatch.setattr(
        sys,
        "argv",
        ["company", "audit-docx", "--filename-limit", "64", "--parser-log", str(log)],
    )
    args = parse_args()
    assert args.filename_limit == 64
    assert args.parser_log == [log]
