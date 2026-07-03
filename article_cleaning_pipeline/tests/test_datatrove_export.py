from src.main_stages import quality_for


def test_quality_flags_short_text():
    label, reasons = quality_for({"content": {"clean_text": "短"}})
    assert label == "needs_review"
    assert "short_clean_text" in reasons
