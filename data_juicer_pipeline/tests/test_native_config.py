from pathlib import Path

from data_juicer_runner import build_native_config


def test_native_config_injects_structured_windows_safe_dataset(tmp_path):
    template = tmp_path / "template.yaml"
    template.write_text(
        "process:\n  - document_line_deduplicator:\n      frequency_threshold: 6\n",
        encoding="utf-8",
    )
    config = build_native_config(
        template,
        tmp_path / "in.jsonl",
        tmp_path / "out.jsonl",
        tmp_path / "work",
        project_name="test",
        line_frequency_threshold=77,
    )
    assert config["dataset_path"] == ""
    assert config["np"] == 1
    assert config["dataset"]["configs"][0]["type"] == "local"
    assert config["process"][0]["document_line_deduplicator"]["frequency_threshold"] == 77


def test_native_config_applies_named_operator_overrides(tmp_path):
    template = tmp_path / "quality.yaml"
    template.write_text(
        "process:\n  - text_length_filter:\n      min_len: 10\n      max_len: 100\n",
        encoding="utf-8",
    )
    config = build_native_config(
        template,
        tmp_path / "in.jsonl",
        tmp_path / "out.jsonl",
        tmp_path / "work",
        project_name="quality-test",
        operator_overrides={"text_length_filter": {"min_len": 50}},
    )
    assert config["process"][0]["text_length_filter"] == {"min_len": 50, "max_len": 100}


def test_native_config_accepts_llm_worker_count(tmp_path):
    template = tmp_path / "llm.yaml"
    template.write_text(
        "process:\n  - llm_quality_score_filter:\n      api_or_hf_model: test\n",
        encoding="utf-8",
    )
    config = build_native_config(
        template,
        tmp_path / "in.jsonl",
        tmp_path / "out.jsonl",
        tmp_path / "work",
        project_name="llm-test",
        num_proc=16,
    )
    assert config["np"] == 16
