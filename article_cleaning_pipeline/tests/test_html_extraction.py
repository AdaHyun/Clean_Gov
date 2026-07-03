from src.utils import choose_article_html, html_to_text


def test_choose_article_html():
    html = "<html><body><nav>首页</nav><div class='article'>正文内容" + "很多" * 40 + "</div></body></html>"
    clean_html, selector, confidence = choose_article_html(html, [".article"])
    assert "正文内容" in html_to_text(clean_html)
    assert selector == ".article"
