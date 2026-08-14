import pytest

from src.normalizer import normalize_markdown
from src.path_mirror import mirror_document_dir, sanitize_component


def test_chinese_nested_mirror_and_same_name_separation(tmp_path):
    root = tmp_path / "documents"
    first = mirror_document_dir(
        root,
        "国家卫健委/政策法规/子栏目/文章标题/文件 A.pdf",
        "abcdef123456789",
        "mirror",
    )
    second = mirror_document_dir(
        root,
        "疾控中心/政策法规/子栏目/文章标题/文件 A.pdf",
        "abcdef123456789",
        "mirror",
    )
    assert first != second
    assert "国家卫健委" in str(first)
    assert first.relative_to(root).as_posix() == (
        "国家卫健委/政策法规/子栏目/文章标题/文件 A.pdf"
    )
    assert first.name == "文件 A.pdf"


def test_readable_layout_keeps_request_id_suffix(tmp_path):
    result = mirror_document_dir(
        tmp_path,
        "机构/文章/文件.pdf",
        "abcdef123456789",
        "readable",
    )
    assert result is not None
    assert result.name == "文件__abcdef123456"


@pytest.mark.parametrize("value", ["../x.pdf", "/etc/passwd", "a/../../x.pdf"])
def test_path_traversal_is_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        mirror_document_dir(tmp_path, value, "r1", "mirror")


def test_special_and_long_component_is_safe():
    assert sanitize_component('a<>:"/\\|?* b') == "a_b"
    value = sanitize_component("很长" * 100)
    assert len(value) <= 100


def test_markdown_normalization_preserves_structure():
    raw = "\ufeff# 标题  \r\n\r\n- 项目  \r\n\r\n\r\n|A|B|\r\n|-|-|\r\n"
    clean, stats = normalize_markdown(raw)
    assert clean.startswith("# 标题\n")
    assert "- 项目\n" in clean
    assert "|A|B|" in clean
    assert clean.endswith("\n")
    assert stats["clean_character_count"] == len(clean)
