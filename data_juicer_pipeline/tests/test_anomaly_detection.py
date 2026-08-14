from anomaly_detection import detect_anomalies


def test_list_page_is_flagged_but_not_deleted():
    text = "工作动态\n您现在所在位置：首页 > 工作动态\n标题一\n2026-06-15\n标题二\n2026-06-01\n标题三\n2026-05-20"
    flags = detect_anomalies(text, title="工作动态")
    assert "list_page_contamination" in flags
    assert "needs_recrawl" in flags


def test_attachment_page_is_flagged():
    flags = detect_anomalies("附件下载\n请查看附件\n附件：\n材料.pdf\n报表.xlsx", title="附件下载")
    assert "attachment_only" in flags
    assert "needs_attachment_merge" in flags


def test_boilerplate_shell_is_flagged():
    text = "首页\n机构\n新闻\n信息\n服务\n互动\n专题\n主办单位：某单位\n承办单位：某中心\n网站地图"
    flags = detect_anomalies(text, title="空壳")
    assert "boilerplate_only" in flags
    assert "body_missing" in flags
