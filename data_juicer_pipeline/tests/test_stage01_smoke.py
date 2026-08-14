from pathlib import Path

from stage01_pipeline import StageOptions, run_stage01


FIXTURE = Path(__file__).parent / "fixtures" / "sample_web_documents.jsonl"


def test_dry_run_never_creates_formal_output(tmp_path):
    output = tmp_path / "must_not_exist.jsonl"
    result = run_stage01(
        StageOptions(
            input_path=FIXTURE,
            output_path=output,
            work_dir=tmp_path / "work",
            report_dir=tmp_path / "reports",
            dry_run=True,
            frequency_threshold=3,
            allow_small_group=True,
            sample_count=5,
        ),
        command=["pytest", "dry-run"],
    )
    assert result["status"] == "dry_run_complete"
    assert not output.exists()


def test_formal_smoke_without_data_juicer_preserves_structure(tmp_path):
    output = tmp_path / "cleaned.jsonl"
    result = run_stage01(
        StageOptions(
            input_path=FIXTURE,
            output_path=output,
            work_dir=tmp_path / "work",
            report_dir=tmp_path / "reports",
            skip_data_juicer=True,
            sample_count=5,
        ),
        command=["pytest", "formal-smoke"],
    )
    assert result["status"] == "success"
    assert result["input_document_count"] == result["output_document_count"] == 22
    assert output.exists()
