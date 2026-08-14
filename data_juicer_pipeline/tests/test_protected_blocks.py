from protected_blocks import protect_text, restore_text


def test_table_long_line_clause_and_link_round_trip_exactly():
    text = "一、监测结果\n| 疾病 | 发病 | 死亡 |\n| --- | --- | --- |\n| 鼠疫 | 0 | 0 |\n这是一条长度明显超过五十个字符并且属于合法公共卫生正文的完整句子，它绝对不能交给跨文档高频短行删除算子处理。\n[附件](https://x/a.pdf)"
    protected = protect_text(text, doc_key="doc-1", title="测试")
    assert "| 鼠疫" not in protected.text
    assert protected.stats["markdown_table_count"] == 1
    assert protected.stats["clause_line_count"] == 1
    restored = restore_text(protected.text, protected)
    assert restored.success
    assert restored.text == text
    assert protected.stats["hash_equal"] is True


def test_missing_placeholder_fails_closed():
    protected = protect_text("# 标题\n正文", doc_key="doc-2")
    restored = restore_text("正文", protected)
    assert not restored.success
    assert restored.errors
