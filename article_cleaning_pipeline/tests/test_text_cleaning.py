from src.utils import clean_text_basic, noise_hits


def test_clean_text_basic_repairs_chinese_date_spaces():
    assert clean_text_basic("2026 年 6 月 26 日") == "2026年6月26日"


def test_noise_hits_detects_navigation():
    assert "长者版" in noise_hits("长者版 无障碍 首页")
