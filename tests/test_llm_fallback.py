from unittest.mock import Mock
from utilization_llm_fallback import call_fallback, validate_framework_response, validate_response, validate_structure_response

HTML = "<table><tr><td>2024</td><td>85.3</td><td>1,000</td></tr></table>"
STRUCTURE = {
    "quarters": [{"col": 6, "label": "1Q26"}],
    "rows": [{"row": 18, "section": "가동률", "division": "소재산업", "item": "MDF", "site": "", "unit": "(%)"}],
}


def test_normal_json():
    result = validate_response('[{"사업연도":"2024","가동률":"85.3","생산실적":"1,000","confidence":"high"}]', HTML)
    assert result.status == "success" and len(result.rows) == 1


def test_bad_json(): assert validate_response("nope", HTML).status == "llm_bad_json"


def test_hallucinated_number_is_null_and_dropped_when_rate():
    result = validate_response('[{"사업연도":"2024","가동률":"99.9","생산실적":"777","confidence":"high"}]', HTML)
    assert result.rows == [] and result.status == "llm_uncertain"


def test_out_of_range_rate():
    assert validate_response('[{"사업연도":"2024","가동률":"201","confidence":"high"}]', HTML + "201").rows == []


def test_structure_response_targets_known_sheet_row():
    raw = '[{"target_row":18,"section":"가동률","division":"소재산업","item":"MDF","quarter":"1Q26","value":"85.3","confidence":"high"}]'
    result = validate_structure_response(raw, HTML, STRUCTURE, "1Q26")
    assert result.status == "success"
    assert result.updates == [{
        "target_row": 18,
        "section": "가동률",
        "division": "소재산업",
        "item": "MDF",
        "site": "",
        "unit": "(%)",
        "quarter": "1Q26",
        "value": "85.3",
        "source_label": "",
        "confidence": "high",
    }]


def test_structure_response_skips_hallucinated_values():
    raw = '[{"target_row":18,"section":"가동률","item":"MDF","quarter":"1Q26","value":"99.9","confidence":"high"}]'
    result = validate_structure_response(raw, HTML, STRUCTURE, "1Q26")
    assert result.status == "llm_uncertain"
    assert result.updates == []


def test_structure_response_resets_unknown_row_for_new_structure_row():
    raw = '[{"target_row":999,"section":"생산실적","item":"MDF","quarter":"1Q26","value":"1,000","confidence":"high"}]'
    result = validate_structure_response(raw, HTML, STRUCTURE, "1Q26")
    assert result.status == "success"
    assert result.updates[0]["target_row"] is None
    assert result.updates[0]["section"] == "생산실적"


def test_framework_response_creates_reusable_selectors_without_values():
    raw = '[{"target_row":18,"section":"가동률","division":"소재산업","item":"MDF","source_aliases":["MDF"],"value":"85.3","confidence":"high"}]'
    result = validate_framework_response(raw, STRUCTURE)
    assert result.status == "success"
    assert result.selectors == [{
        "target_row": 18,
        "section": "가동률",
        "division": "소재산업",
        "item": "MDF",
        "site": "",
        "unit": "(%)",
        "source_aliases": ["MDF"],
        "confidence": "high",
    }]
    assert "value" not in result.selectors[0]


def test_framework_response_resets_unknown_row_for_new_selector():
    raw = '[{"target_row":999,"section":"생산실적","item":"MDF","source_aliases":["MDF"],"confidence":"high"}]'
    result = validate_framework_response(raw, STRUCTURE)
    assert result.status == "success"
    assert result.selectors[0]["target_row"] is None
    assert result.selectors[0]["section"] == "생산실적"


def test_timeout_retries_then_skips():
    client = Mock(); client.chat.side_effect = TimeoutError()
    result = call_fallback(HTML, client=client, sleep=lambda _: None)
    assert result.status == "llm_error" and client.chat.call_count == 3
