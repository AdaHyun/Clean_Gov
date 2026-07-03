from run_pipeline import validate_record


def test_validation_missing_required():
    status, errors, warnings = validate_record({"url": "https://example.com"})
    assert status == "invalid_repairable"
    assert errors
