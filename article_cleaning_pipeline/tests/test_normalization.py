from run_pipeline import parse_date


def test_parse_date_chinese_like():
    assert parse_date("2026-06-26") == "2026-06-26"
