from deterministic_cleaning import clean_text
from site_rule_engine import SiteRule


def test_images_entities_duplicates_and_attachment_are_conservative():
    text = "标题\n标题\n&nbsp;正文&amp;说明\n![](data:image/png;base64,AAAA)\n![](x.gif)[附件](https://x/a.pdf)\n重复\n重复"
    result = clean_text(text, title="标题", doc_id="1")
    assert result.text.count("标题") == 1
    assert "data:image" not in result.text
    assert "![](x.gif)" not in result.text
    assert "[附件](https://x/a.pdf)" in result.text
    assert "&nbsp;" not in result.text
    assert "正文&说明" in result.text
    assert result.text.count("重复") == 1


def test_literal_newline_requires_explicit_switch():
    original = clean_text("第一段\\n第二段").text
    restored = clean_text("第一段\\n第二段", restore_literal_newlines=True).text
    assert "\\n" in original
    assert restored == "第一段\n第二段"


def test_site_rule_is_position_and_domain_scoped():
    rule = SiteRule("x", "test", "exact_line", "首页", domain="example.gov.cn", max_position_ratio=0.2)
    text = "首页\n正文一\n正文二\n正文三\n首页"
    result = clean_text(text, domain="example.gov.cn", site_rules=[rule])
    assert result.text.splitlines()[-1] == "首页"
    assert result.text.splitlines()[0] == "正文一"
