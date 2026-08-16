"""Deterministic parsers for DART production/utilization tables."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from typing import Iterable

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


def _number(value: str) -> float | None:
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", value)
    return float(match.group().replace(",", "")) if match else None


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

    aliases = {
        "business_year": ("사업연도", "연도", "기간", "구분"),
        "division": ("사업부문", "부문"), "item": ("품목", "제품"),
        "site": ("사업소", "공장"), "capacity": ("생산능력",),
        "production": ("생산실적", "생산량"), "utilization": ("가동률",),
        "numerator": ("실제가동시간", "가동시간"),
        "denominator": ("가동가능시간", "가능시간"), "unit": ("단위",),
    }

    def parse(self, html: str, business_year: int | str | None = None) -> list[dict[str, object]]:
        reader = _TableReader(); reader.feed(str(html))
        results: list[dict[str, object]] = []
        for table in reader.tables:
            rows = self._parse_table(table, business_year)
            results.extend(row.as_korean_dict() for row in rows)
        if results:
            return results
        # Pattern 3 is intentionally last and only produces real text values.
        for text in reader.paragraphs:
            if "가동률" not in text:
                continue
            match = re.search(r"가동률[^\d]{0,30}(\d+(?:\.\d+)?)\s*%", text)
            if match:
                results.append(UtilizationRow(str(business_year) if business_year else None,
                                              utilization=match.group(1), raw_table=json.dumps({"text": text}, ensure_ascii=False)).as_korean_dict())
                break
        return results

    def _parse_table(self, table, business_year) -> list[UtilizationRow]:
        matrix = table
        if len(matrix) < 2:
            return []
        headers = matrix[0]
        indexes: dict[str, int] = {}
        for field, aliases in self.aliases.items():
            for index, header in enumerate(headers):
                if any(alias in header.replace(" ", "") for alias in aliases):
                    indexes[field] = index
                    break
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
