"""Deterministic parsers for DART production/utilization tables."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict

from html.parser import HTMLParser


class _TableReader(HTMLParser):
    def __init__(self):
        super().__init__(); self.tables = []; self.paragraphs = []; self.table = None; self.row = None; self.cell = None; self.p = None
    def handle_starttag(self, tag, attrs):
        if tag == "table": self.table = []
        elif tag == "tr" and self.table is not None: self.row = []
        elif tag in {"th", "td"} and self.row is not None: self.cell = ""
        elif tag in {"p", "div"}: self.p = ""
    def handle_data(self, data):
        if self.cell is not None: self.cell += data
        if self.p is not None: self.p += data
    def handle_endtag(self, tag):
        if tag in {"th", "td"} and self.cell is not None:
            self.row.append(_text(self.cell)); self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row: self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table); self.table = None
        elif tag in {"p", "div"} and self.p is not None:
            self.paragraphs.append(_text(self.p)); self.p = None


def _text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _number(value: str) -> float | None:
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", value)
    return float(match.group().replace(",", "")) if match else None


@dataclass
class ParsedDocument:
    tables: list[list[list[str]]]
    paragraphs: list[str]
    engine: str
    parser_error: str | None = None


@dataclass
class ParseResult:
    rows: list[dict[str, object]]
    status: str
    message: str = ""
    engine: str = ""
    table_count: int = 0
    candidate_table_count: int = 0
    header_table_count: int = 0

    @property
    def needs_llm(self) -> bool:
        return self.status in {
            "beautifulsoup_error",
            "html_parse_error",
            "table_structure_changed",
            "no_numeric_rows",
            "textual_structure_changed",
        }


@dataclass
class UtilizationRow:
    business_year: str | None = None
    division: str | None = None
    item: str | None = None
    site: str | None = None
    capacity: str | None = None
    production: str | None = None
    utilization: str | None = None
    unit: str | None = None
    formula: str | None = None
    raw_table: str | None = None

    def as_korean_dict(self) -> dict[str, object]:
        keys = ["사업연도", "사업부문", "품목", "사업소", "생산능력", "생산실적", "가동률(%)", "단위", "산출식", "원본표(JSON)"]
        return dict(zip(keys, asdict(self).values()))


class UtilizationParser:
    """Parse explicit rates, calculate time ratios, then inspect prose."""

    keywords = ("가동률", "평균가동률", "생산능력", "생산실적", "가동시간", "가동가능시간", "실제가동시간")
    aliases = {
        "business_year": ("사업연도", "연도", "기간", "구분"),
        "division": ("사업부문", "부문"), "item": ("품목", "제품"),
        "site": ("사업소", "공장"), "capacity": ("생산능력",),
        "production": ("생산실적", "생산량"), "utilization": ("가동률",),
        "numerator": ("실제가동시간", "가동시간"),
        "denominator": ("가동가능시간", "가능시간"), "unit": ("단위",),
    }

    def parse(self, html: str, business_year: int | str | None = None) -> list[dict[str, object]]:
        return self.parse_with_diagnostics(html, business_year).rows

    def parse_with_diagnostics(self, html: str, business_year: int | str | None = None) -> ParseResult:
        try:
            document = self._read_document(html)
        except Exception as exc:
            return ParseResult([], "html_parse_error", f"{type(exc).__name__}: {exc}")
        results: list[dict[str, object]] = []
        candidate_tables = header_tables = 0
        for table in document.tables:
            if self._table_has_utilization_signal(table):
                candidate_tables += 1
            if self._parseable_indexes(table):
                header_tables += 1
            rows = self._parse_table(table, business_year)
            results.extend(row.as_korean_dict() for row in rows)
        if results:
            return ParseResult(results, "success", engine=document.engine, table_count=len(document.tables),
                               candidate_table_count=candidate_tables, header_table_count=header_tables)
        # Pattern 3 is intentionally last and only produces real text values.
        for text in document.paragraphs:
            if "가동률" not in text:
                continue
            match = re.search(r"가동률[^\d]{0,30}(\d+(?:\.\d+)?)\s*%", text)
            if match:
                results.append(UtilizationRow(str(business_year) if business_year else None,
                                              utilization=match.group(1), raw_table=json.dumps({"text": text}, ensure_ascii=False)).as_korean_dict())
                break
        if results:
            return ParseResult(results, "success", engine=document.engine, table_count=len(document.tables),
                               candidate_table_count=candidate_tables, header_table_count=header_tables)
        if document.parser_error:
            return ParseResult([], "beautifulsoup_error", document.parser_error, document.engine,
                               len(document.tables), candidate_tables, header_tables)
        if candidate_tables and not header_tables:
            return ParseResult([], "table_structure_changed", "가동률/생산 관련 표는 있으나 기존 헤더 규칙과 맞지 않습니다.",
                               document.engine, len(document.tables), candidate_tables, header_tables)
        if header_tables:
            return ParseResult([], "no_numeric_rows", "기존 헤더는 인식했지만 숫자 가동률/가동시간 행을 찾지 못했습니다.",
                               document.engine, len(document.tables), candidate_tables, header_tables)
        if any("가동률" in text for text in document.paragraphs):
            return ParseResult([], "textual_structure_changed", "가동률 문장은 있으나 숫자 패턴을 찾지 못했습니다.",
                               document.engine, len(document.tables), candidate_tables, header_tables)
        if document.tables:
            return ParseResult([], "no_utilization_table", "표는 있으나 가동률/생산 관련 신호가 없습니다.",
                               document.engine, len(document.tables), candidate_tables, header_tables)
        return ParseResult([], "no_tables", "HTML에서 표를 찾지 못했습니다.", document.engine)

    def _read_document(self, html: str) -> ParsedDocument:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return self._read_with_html_parser(html, "htmlparser")
        try:
            soup = BeautifulSoup(str(html), "html.parser")
            tables = []
            for table_tag in soup.find_all("table"):
                table = []
                for row_tag in table_tag.find_all("tr"):
                    cells = [_text(cell.get_text(" ")) for cell in row_tag.find_all(["th", "td"])]
                    if cells:
                        table.append(cells)
                if table:
                    tables.append(table)
            paragraphs = []
            for tag in soup.find_all(["p", "div"]):
                text = _text(tag.get_text(" "))
                if text:
                    paragraphs.append(text)
            return ParsedDocument(tables, paragraphs, "beautifulsoup")
        except Exception as exc:
            fallback = self._read_with_html_parser(html, "htmlparser")
            fallback.parser_error = f"{type(exc).__name__}: {exc}"
            return fallback

    def _read_with_html_parser(self, html: str, engine: str) -> ParsedDocument:
        reader = _TableReader()
        reader.feed(str(html))
        return ParsedDocument(reader.tables, reader.paragraphs, engine)

    def _table_has_utilization_signal(self, table) -> bool:
        text = _compact(" ".join(" ".join(row) for row in table))
        return any(_compact(keyword) in text for keyword in self.keywords)

    def _parseable_indexes(self, table) -> bool:
        if len(table) < 2:
            return False
        indexes = self._header_indexes(table[0])
        return "utilization" in indexes or {"numerator", "denominator"} <= indexes.keys()

    def _header_indexes(self, headers) -> dict[str, int]:
        indexes: dict[str, int] = {}
        for field, aliases in self.aliases.items():
            for index, header in enumerate(headers):
                compact = _compact(header)
                if any(_compact(alias) in compact for alias in aliases):
                    indexes[field] = index
                    break
        return indexes

    def _parse_table(self, table, business_year) -> list[UtilizationRow]:
        matrix = table
        if len(matrix) < 2:
            return []
        headers = matrix[0]
        indexes = self._header_indexes(headers)
        if "utilization" not in indexes and not {"numerator", "denominator"} <= indexes.keys():
            return []
        raw = json.dumps(matrix, ensure_ascii=False)
        parsed: list[UtilizationRow] = []
        for values in matrix[1:]:
            get = lambda name: values[indexes[name]] if name in indexes and indexes[name] < len(values) else None
            utilization = get("utilization")
            formula = None
            if not utilization:
                numerator, denominator = _number(get("numerator") or ""), _number(get("denominator") or "")
                if numerator is not None and denominator:
                    utilization = f"{numerator / denominator * 100:.2f}".rstrip("0").rstrip(".")
                    formula = "가동시간/가동가능시간 * 100"
            if _number(utilization or "") is None:
                continue
            parsed.append(UtilizationRow(get("business_year") or (str(business_year) if business_year else None),
                                         get("division"), get("item"), get("site"), get("capacity"),
                                         get("production"), str(_number(utilization or "")), get("unit"), formula, raw))
        return parsed


def map_business_year(label: str | None, report_year: int) -> str:
    """Map relative period labels; absolute years pass through."""
    value = _text(label)
    match = re.search(r"(19|20)\d{2}", value)
    if match:
        return match.group()
    offsets = {"당기": 0, "전기": 1, "전전기": 2}
    return str(report_year - next((offset for key, offset in offsets.items() if key in value), 0))


def normalize_unit(unit: str | None) -> tuple[str | None, str | None]:
    original = _text(unit)
    compact = original.lower().replace(" ", "")
    if compact in {"톤", "t", "mt"}: return "톤", original
    if compact in {"천톤", "kt"}: return "천톤", original
    return (original or None), (original or None)
