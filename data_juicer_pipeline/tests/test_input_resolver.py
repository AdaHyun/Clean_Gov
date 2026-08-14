from pathlib import Path

import input_resolver as resolver
from paths import CLEAN_GOV_ROOT, COMPONENT_ROOT, resolve_from_clean_gov


def test_roots_are_clean_gov_and_component():
    assert CLEAN_GOV_ROOT.name == "Clean_Gov"
    assert COMPONENT_ROOT == CLEAN_GOV_ROOT / "data_juicer_pipeline"
    assert resolve_from_clean_gov("text_clean") == (CLEAN_GOV_ROOT / "text_clean").resolve()


def test_default_web_input_selects_latest_timestamped_corpus(tmp_path, monkeypatch):
    older = tmp_path / "gov_corpus_clean_20260722_165348.jsonl"
    newer = tmp_path / "gov_corpus_clean_20260804_101500.jsonl"
    ignored = tmp_path / "gov_corpus_clean_latest.jsonl"
    for path in (older, newer, ignored):
        path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(resolver, "WEB_CORPUS_DIR", tmp_path)
    assert resolver.resolve_input(None) == newer.resolve()


def test_explicit_relative_input_is_clean_gov_relative():
    relative = Path("data_juicer_pipeline/tests/fixtures/sample_web_documents.jsonl")
    assert resolver.resolve_input(relative) == (CLEAN_GOV_ROOT / relative).resolve()
