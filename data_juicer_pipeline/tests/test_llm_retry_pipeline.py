import json

import pytest

from jsonl_io import iter_jsonl
from llm_retry_pipeline import (
    _ensure_disjoint_doc_ids,
    _prepare_cleanup_candidates,
    build_llm_review_text,
    prepare_retry_input,
)


def _write_rows(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_review_text_keeps_short_text_and_samples_long_text():
    short = "公共卫生政策正文。"
    review, mode = build_llm_review_text(
        short, max_direct_chars=20, sample_chars_per_section=5
    )
    assert review == short
    assert mode == "full_text"

    long = "开头内容\n" + "甲" * 50 + "\n中部内容\n" + "乙" * 50 + "\n结尾内容"
    review, mode = build_llm_review_text(
        long, max_direct_chars=30, sample_chars_per_section=15
    )
    assert mode == "representative_sample"
    assert "【原文开头】" in review
    assert "【原文中部】" in review
    assert "【原文结尾】" in review
    assert "noise_segments必须输出空数组" in review
    assert review != long


def test_prepare_retry_input_removes_stale_llm_stats_and_status(tmp_path):
    source = tmp_path / "retry.jsonl"
    target = tmp_path / "prepared.jsonl"
    _write_rows(
        source,
        [
            {
                "doc_id": "web-1",
                "title": "标题",
                "text": "公共卫生正文。",
                "native_pipeline_lane": "web_normal",
                "__dj__stats__": {
                    "text_len": 8,
                    "llm_quality_score": 0.0,
                    "llm_quality_record": "",
                    "llm_quality_tags": "",
                },
                "llm_policy_status": "llm_api_or_response_failed_retry_required",
                "llm_policy_status_zh": "等待重试",
                "quarantine_reason": "llm_api_or_response_failed_retry_required",
                "quarantine_stage": "04_llm_topic_quality",
            }
        ],
    )

    summary = prepare_retry_input(
        source,
        target,
        source_run_id="20260806_152852_937068",
        round_number=1,
        max_direct_chars=20_000,
        sample_chars_per_section=6_000,
    )

    assert summary["input_document_count"] == 1
    assert summary["full_text_count"] == 1
    row = list(iter_jsonl(target))[0][1]
    assert row["__dj__stats__"] == {"text_len": 8}
    assert "llm_quality_score" not in row["__dj__stats__"]
    assert "quarantine_reason" not in row
    assert row["llm_review_text"] == row["text"]
    assert row["llm_retry_input_mode_zh"] == "完整正文重试"


def test_sampled_long_documents_defer_noise_deletion(tmp_path):
    source = tmp_path / "candidate.jsonl"
    target = tmp_path / "cleanup.jsonl"
    _write_rows(
        source,
        [
            {
                "doc_id": "attachment-1",
                "text": "完整附件正文",
                "llm_review_text": "抽样文本",
                "llm_retry_input_mode": "representative_sample",
                "llm_noise_segments": [
                    {
                        "noise_type": "navigation_menu",
                        "exact_lines": ["完整附件正文"],
                        "confidence": 1.0,
                    }
                ],
            }
        ],
    )

    counts = _prepare_cleanup_candidates(source, target)

    assert counts["sampled_noise_deferred"] == 1
    row = list(iter_jsonl(target))[0][1]
    assert "llm_review_text" not in row
    assert row["llm_noise_segments"] == []
    assert "不依据不完整抽样删除栏目噪声" in row["llm_retry_noise_policy_zh"]


def test_merge_guard_rejects_doc_id_already_in_base(tmp_path):
    base = tmp_path / "base.jsonl"
    recovered = tmp_path / "recovered.jsonl"
    _write_rows(base, [{"doc_id": "same", "text": "原结果"}])
    _write_rows(recovered, [{"doc_id": "same", "text": "重试结果"}])

    with pytest.raises(ValueError, match="已经存在于原正式结果"):
        _ensure_disjoint_doc_ids(base, recovered)
