import json

from jsonl_io import iter_jsonl
from llm_policy import (
    apply_local_noise_cleanup,
    clean_validated_noise_lines,
    partition_topic_annotations,
)
from paths import LLM_TAG_LABEL_CONFIG


def _write_rows(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _scored(doc_id, score, **analysis):
    return {
        "doc_id": doc_id,
        "title": doc_id,
        "text": analysis.pop("text", "公共卫生正文内容，包含可复用的严谨信息。"),
        "__dj__stats__": {
            "llm_quality_score": score,
            "llm_quality_record": {
                "public_health_relevance": 5,
                "substantive_public_health_content": True,
                "topic_tags": ["public_health_policy"],
                "content_type": "policy_document",
                "exclusion_tags": [],
                "noise_segments": [],
                "repairable_flags": [],
                "topic_decision": "keep",
                "topic_confidence": 0.98,
                "training_use": ["both"],
                "rationale": "公共卫生政策正文。",
                **analysis,
            },
        },
    }


def test_partition_topics_localizes_tags_and_quarantines_uncertain_rows(tmp_path):
    scored = tmp_path / "scored.jsonl"
    annotated = tmp_path / "annotated.jsonl"
    candidate = tmp_path / "candidate.jsonl"
    excluded = tmp_path / "excluded.jsonl"
    low = tmp_path / "low.jsonl"
    retry = tmp_path / "retry.jsonl"
    review = tmp_path / "review.jsonl"
    report = tmp_path / "report.json"
    _write_rows(
        scored,
        [
            _scored("keep", 0.9),
            _scored(
                "recruitment",
                0.9,
                public_health_relevance=1,
                substantive_public_health_content=False,
                topic_tags=[],
                content_type="recruitment",
                exclusion_tags=["recruitment"],
                topic_decision="exclude",
                topic_confidence=0.98,
                training_use=["none"],
            ),
            _scored("low", 0.4),
            {
                "doc_id": "failed",
                "text": "调用失败",
                "__dj__stats__": {"llm_quality_score": 0.0},
            },
            _scored("review", 0.9, topic_decision="review", topic_confidence=0.7),
        ],
    )

    summary = partition_topic_annotations(
        scored,
        annotated,
        candidate,
        excluded,
        low,
        retry,
        review,
        report,
        LLM_TAG_LABEL_CONFIG,
        min_quality_score=0.6,
        min_topic_confidence=0.9,
        min_public_health_relevance=4.0,
        provider_name="company",
    )

    assert summary["input_document_count"] == 5
    assert summary["candidate_keep_count"] == 1
    assert summary["topic_excluded_count"] == 1
    assert summary["low_quality_excluded_count"] == 1
    assert summary["llm_retry_required_count"] == 1
    assert summary["manual_review_required_count"] == 1
    annotated_rows = [row for _, row in iter_jsonl(annotated)]
    assert len(annotated_rows) == 5
    keep = next(row for row in annotated_rows if row["doc_id"] == "keep")
    assert keep["llm_topic_tags"] == ["public_health_policy"]
    assert keep["llm_topic_tags_zh"] == ["公共卫生政策"]
    recruitment = list(iter_jsonl(excluded))[0][1]
    assert recruitment["llm_exclusion_tags_zh"] == ["招聘或人才引进"]
    assert recruitment["llm_policy_status_zh"] == "高置信度无关主题，已隔离"


def test_local_cleanup_deletes_only_validated_unique_full_lines(tmp_path):
    candidate = tmp_path / "candidate.jsonl"
    kept = tmp_path / "kept.jsonl"
    audit = tmp_path / "audit.jsonl"
    review = tmp_path / "review.jsonl"
    report = tmp_path / "report.json"
    _write_rows(
        candidate,
        [
            {
                "doc_id": "nav",
                "text": "正文第一段。\n时政要闻\n医保新闻\n正文第二段。",
                "llm_noise_segments": [
                    {
                        "noise_type": "navigation_menu",
                        "noise_type_zh": "栏目导航菜单",
                        "start_line": 2,
                        "end_line": 3,
                        "exact_lines": ["时政要闻", "医保新闻"],
                        "confidence": 0.99,
                    }
                ],
            }
        ],
    )

    summary = apply_local_noise_cleanup(
        candidate,
        kept,
        audit,
        review,
        report,
        LLM_TAG_LABEL_CONFIG,
        min_noise_confidence=0.9,
        max_removed_ratio=0.5,
        min_remaining_characters=5,
    )

    assert summary["kept_document_count"] == 1
    row = list(iter_jsonl(kept))[0][1]
    assert row["text"] == "正文第一段。\n正文第二段。"
    assert row["llm_noise_removed_types_zh"] == ["栏目导航菜单"]


def test_local_cleanup_protects_table_structure():
    text = "| 栏目 | 数值 |\n|---|---|\n| 时政要闻 | 1 |\n正文。"
    result = clean_validated_noise_lines(
        text,
        [
            {
                "noise_type": "navigation_menu",
                "exact_lines": ["| 时政要闻 | 1 |"],
                "start_line": 3,
                "end_line": 3,
                "confidence": 0.99,
            }
        ],
        allowed_noise_types={"navigation_menu"},
        min_confidence=0.9,
        max_removed_ratio=0.5,
        min_remaining_characters=1,
    )

    assert result.status == "review_required"
    assert result.text == text
    assert "segment_0:table_structure_protected" in result.issues
