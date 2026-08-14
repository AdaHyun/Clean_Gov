import json
import time

from pipeline_timing import TimingRecorder, format_duration


def test_format_duration():
    assert format_duration(4.2) == "4.20 秒"
    assert format_duration(65.25) == "1 分 5.25 秒"


def test_timing_recorder_writes_counts_rates_and_activity_totals(tmp_path):
    output = tmp_path / "timing_summary.json"
    recorder = TimingRecorder()
    recorder.set_output_path(output)

    with recorder.measure(
        "01_normalize_web",
        stage="01",
        lane="web_normal",
        activity="data_juicer",
        input_document_count=20,
    ) as operation:
        time.sleep(0.002)
    operation.update_counts(
        output_document_count=18,
        input_character_count=200,
        output_character_count=180,
    )
    summary = recorder.finish(
        "success",
        input_document_count=20,
        output_document_count=18,
    )

    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted == summary
    assert summary["status"] == "success"
    assert summary["input_document_count"] == 20
    assert summary["output_document_count"] == 18
    assert summary["operations"][0]["status"] == "success"
    assert summary["operations"][0]["documents_per_second"] > 0
    assert summary["operations"][0]["input_characters_per_second"] > 0
    assert summary["duration_by_activity_seconds"]["data_juicer"] > 0


def test_failed_operation_persists_failed_snapshot(tmp_path):
    output = tmp_path / "timing_summary.json"
    recorder = TimingRecorder()
    recorder.set_output_path(output)

    try:
        with recorder.measure("04_dedup", stage="04", activity="data_juicer"):
            raise RuntimeError("test failure")
    except RuntimeError:
        pass

    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"
    assert persisted["operations"][0]["status"] == "failed"
    assert persisted["operations"][0]["error_type"] == "RuntimeError"
