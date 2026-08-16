from unittest.mock import Mock
from utilization_llm_fallback import call_fallback, validate_response

HTML = "<table><tr><td>2024</td><td>85.3</td><td>1,000</td></tr></table>"

def test_normal_json():
    result = validate_response('[{"사업연도":"2024","가동률":"85.3","생산실적":"1,000","confidence":"high"}]', HTML)
    assert result.status == "success" and len(result.rows) == 1

def test_bad_json(): assert validate_response("nope", HTML).status == "llm_bad_json"

def test_hallucinated_number_is_null_and_dropped_when_rate():
    result = validate_response('[{"사업연도":"2024","가동률":"99.9","생산실적":"777","confidence":"high"}]', HTML)
    assert result.rows == [] and result.status == "llm_uncertain"

def test_out_of_range_rate():
    assert validate_response('[{"사업연도":"2024","가동률":"201","confidence":"high"}]', HTML + "201").rows == []

def test_timeout_retries_then_skips():
    client = Mock(); client.chat.side_effect = TimeoutError()
    result = call_fallback(HTML, client=client, sleep=lambda _: None)
    assert result.status == "llm_error" and client.chat.call_count == 3
