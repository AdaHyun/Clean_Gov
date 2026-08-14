import pytest
import yaml

from data_juicer.ops.mapper.chinese_convert_mapper import ChineseConvertMapper
from data_juicer.ops.mapper.fix_unicode_mapper import FixUnicodeMapper
from data_juicer.ops.mapper.remove_repeat_sentences_mapper import RemoveRepeatSentencesMapper
from data_juicer.ops.mapper.replace_content_mapper import ReplaceContentMapper
from data_juicer.ops.mapper.whitespace_normalization_mapper import WhitespaceNormalizationMapper


MAPPERS = {
    "fix_unicode_mapper": FixUnicodeMapper,
    "chinese_convert_mapper": ChineseConvertMapper,
    "whitespace_normalization_mapper": WhitespaceNormalizationMapper,
    "replace_content_mapper": ReplaceContentMapper,
    "remove_repeat_sentences_mapper": RemoveRepeatSentencesMapper,
}


def test_non_table_policy_normalizes_chinese_without_joining_english_words():
    from paths import NATIVE_CONFIG_DIR

    config = yaml.safe_load((NATIVE_CONFIG_DIR / "web_normal.yaml").read_text(encoding="utf-8"))
    data = {
        "text": [
            "① 2023 年 6 月 1 日 -6 月 30 日, 全国 31 个省。"
            "Public Health GB/T 19001\n繁體中文, 測試."
        ]
    }
    for step in config["process"]:
        name, args = next(iter(step.items()))
        data = MAPPERS[name](**args).process_batched(data)

    assert data["text"] == [
        "1 2023年6月1日-6月30日，全国31个省。"
        "Public Health GB/T 19001\n繁体中文，测试。"
    ]


@pytest.mark.parametrize(
    "config_name",
    ("web_normal.yaml", "web_multiline.yaml", "attachment_text.yaml"),
)
def test_non_table_policy_repairs_split_numbers_and_punctuation_spaces(config_name):
    from paths import NATIVE_CONFIG_DIR

    config = yaml.safe_load((NATIVE_CONFIG_DIR / config_name).read_text(encoding="utf-8"))
    replace_args = next(
        step["replace_content_mapper"]
        for step in config["process"]
        if "replace_content_mapper" in step
    )
    data = {
        "text": [
            "从2 026年1月1日的4 .3万人次上升至1月4日的5 .9万人次，随后。"
            "202 6年第1周( 2 025年1 2月29日）； ， 。"
            "分别为9 8.7 % 、4.3 %。Public Health, WHO GB/T 19001"
        ]
    }
    data = ReplaceContentMapper(**replace_args).process_batched(data)

    assert data["text"] == [
        "从2026年1月1日的4.3万人次上升至1月4日的5.9万人次，随后。"
        "2026年第1周（2025年12月29日）；，。"
        "分别为98.7%、4.3%。Public Health, WHO GB/T 19001"
    ]


def test_table_lanes_apply_text_normalization_before_table_extraction():
    from paths import NATIVE_CONFIG_DIR

    for config_name in ("web_table.yaml", "attachment_table.yaml"):
        config = yaml.safe_load((NATIVE_CONFIG_DIR / config_name).read_text(encoding="utf-8"))
        operator_names = {next(iter(step)) for step in config["process"]}
        assert "fix_unicode_mapper" in operator_names
        assert "whitespace_normalization_mapper" in operator_names
        assert "chinese_convert_mapper" in operator_names
        assert "replace_content_mapper" in operator_names
        assert "extract_tables_from_html_mapper" in operator_names


@pytest.mark.parametrize(
    "config_name",
    (
        "web_normal.yaml",
        "web_multiline.yaml",
        "web_table.yaml",
        "attachment_text.yaml",
        "attachment_table.yaml",
    ),
)
def test_all_lanes_remove_constrained_symbol_letter_spaces(config_name):
    from paths import NATIVE_CONFIG_DIR

    config = yaml.safe_load((NATIVE_CONFIG_DIR / config_name).read_text(encoding="utf-8"))
    replace_args = next(
        step["replace_content_mapper"]
        for step in config["process"]
        if "replace_content_mapper" in step
    )
    data = {
        "text": [
            "COVID - 19； ； A / B（ OpenAI ）API： OpenAI\n"
            "# Title\n- item\n  - nested\n* emphasis\n"
            "Note: text\nPublic Health, WHO\nSARS - CoV\nA + / B"
        ]
    }

    data = ReplaceContentMapper(**replace_args).process_batched(data)

    assert data["text"] == [
        "COVID-19；；A/B（OpenAI）API：OpenAI\n"
        "# Title\n- item\n  - nested\n* emphasis\n"
        "Note: text\nPublic Health, WHO\nSARS-CoV\nA+/B"
    ]
