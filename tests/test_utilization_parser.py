from utilization_parser import UtilizationParser

def test_explicit_utilization_column():
    html = "<table><tr><th>사업연도</th><th>품목</th><th>가동률(%)</th></tr><tr><td>2024</td><td>철강</td><td>85.3%</td></tr></table>"
    row = UtilizationParser().parse(html)[0]
    assert row["사업연도"] == "2024" and row["가동률(%)"] == "85.3"

def test_calculates_time_ratio():
    html = "<table><tr><th>사업소</th><th>가동시간</th><th>가동가능시간</th></tr><tr><td>서울</td><td>80</td><td>100</td></tr></table>"
    row = UtilizationParser().parse(html, 2024)[0]
    assert row["가동률(%)"] == "80.0" and row["산출식"].startswith("가동시간/")

def test_textual_rate_only_after_tables_fail():
    assert UtilizationParser().parse("<p>당분기 가동률은 91.2%입니다.</p>", 2025)[0]["가동률(%)"] == "91.2"

def test_no_sample_data_on_failure():
    assert UtilizationParser().parse("<table><tr><td>자료없음</td></tr></table>") == []

def test_diagnostics_flags_changed_table_structure_for_llm():
    html = "<table><tr><th>구분</th><th>1Q26</th></tr><tr><td>평균가동률</td><td>87%</td></tr></table>"
    result = UtilizationParser().parse_with_diagnostics(html, 2026)
    assert result.rows == []
    assert result.status == "table_structure_changed"
    assert result.needs_llm

def test_diagnostics_flags_parseable_header_without_numbers_for_llm():
    html = "<table><tr><th>품목</th><th>가동률</th></tr><tr><td>MDF</td><td>해당사항 없음</td></tr></table>"
    result = UtilizationParser().parse_with_diagnostics(html, 2026)
    assert result.rows == []
    assert result.status == "no_numeric_rows"
    assert result.needs_llm

def test_skips_out_of_range_utilization_values():
    html = "<table><tr><th>품목</th><th>가동률</th></tr><tr><td>MDF</td><td>607</td></tr></table>"
    assert UtilizationParser().parse(html, 2026) == []

def test_skips_ambiguous_multilevel_header_rows():
    html = "<table><tr><th>사업부문</th><th>품목</th><th>생산능력</th><th>평균가동률</th></tr><tr><td>발전</td><td>전기</td><td>26050</td><td>607</td><td>4700</td><td>120</td><td>73.1</td></tr></table>"
    assert UtilizationParser().parse(html, 2026) == []
