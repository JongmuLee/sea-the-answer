"""Reviewed Port of Helsinki cruise tables; quay dimensions never become vessel limits."""
from collections import defaultdict
from html.parser import HTMLParser
import re

from scripts.prepare_navigation_references import listed_number

REVIEWED_BERTHS = {"LHB", "LHC", "LHD", "ERA", "EKL", "EPL", "EMA", "EO1"}


class CruiseTables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.rows, self.row, self.cell = [], None, None, None
        self.ignored = 0
        self.heading = None
        self.context = {}
        self.section = ""

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.ignored += 1
        if self.ignored:
            return
        if re.fullmatch(r"h[1-4]", tag):
            self.heading = (int(tag[1]), [])
        if tag == "table":
            if self.rows is not None:
                raise ValueError("Nested Helsinki table requires source review")
            self.rows = []
            self.section = " / ".join(self.context[k] for k in sorted(self.context))
        elif tag == "tr" and self.rows is not None:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            if any(k == "rowspan" and v not in (None, "1") for k, v in attrs):
                raise ValueError("Helsinki rowspan requires source review")
            self.cell = []
        elif tag in ("br", "p", "li") and self.cell is not None:
            self.cell.append(" ")

    def handle_data(self, data):
        if self.ignored:
            return
        if self.heading is not None:
            self.heading[1].append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.ignored = max(0, self.ignored - 1)
        if self.ignored:
            return
        if self.heading is not None and re.fullmatch(r"h[1-4]", tag):
            level, text = self.heading
            self.context = {k: v for k, v in self.context.items() if k < level}
            self.context[level] = " ".join("".join(text).split())
            self.heading = None
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.rows is not None:
            self.tables.append((self.section, self.rows))
            self.rows = None


def helsinki_berths(html, port_id):
    parser = CruiseTables()
    parser.feed(html)
    parser.close()
    if parser.rows is not None:
        raise ValueError("Incomplete Helsinki source table")
    grouped = defaultdict(list)
    for ordinal, (section, rows) in enumerate(parser.tables):
        # Ignore unrelated layout tables; never ignore an unrecognized limit table.
        if not any(row and row[0].casefold() == "maximum vessel draft" for row in rows):
            continue
        header = [cell for cell in rows[0] if cell] if rows else []
        if len(header) != 1 or header[0] not in REVIEWED_BERTHS:
            raise ValueError("Helsinki berth header changed; review the source")
        label = header[0]
        if any(len(row) != 2 for row in rows[1:] if any(row)):
            raise ValueError("Helsinki table columns changed; do not infer spans")
        text = ("Source scope: Cruise berth reference only; vessel/service suitability not assessed\n"
                + "Source section: " + section + "\n"
                + "\n".join(row[0] + ": " + row[1] for row in rows[1:] if any(row)))
        if len(text) > 6000:
            raise ValueError("Helsinki conditions exceed budget; do not truncate restrictions")
        drafts, lengths = [], []
        for key, value in (row for row in rows[1:] if any(row)):
            if key.casefold() == "maximum vessel draft":
                drafts.append(listed_number(value))
            elif key.casefold() == "maximum vessel length":
                lengths.append(listed_number(value))
            elif key.casefold() == "berth length (in metres)" and "max vessel length" in value.casefold():
                # LHD explicitly states a vessel limit alongside the separate quay lengths.
                match = re.fullmatch(r"\d+(?:[.,]\d+)?\s*m\s*\(\+\d+(?:[.,]\d+)?\s*m\)\s*"
                                     r"\(max vessel length\s+(\d+(?:[.,]\d+)?\s*m)\)", value, re.I)
                lengths.append(listed_number(match[1]) if match else None)
        ref = {"source_id": "helsinki_berths", "locator": f"table={ordinal}; berth={label}"}
        grouped[label].append({"heading": label, "text": text, "source_ref": ref,
            "max_loa_m": lengths[0] if len(lengths) == 1 else None,
            "max_draught_m": drafts[0] if len(drafts) == 1 else None})
    if not grouped:
        raise ValueError("No reviewed Helsinki cruise berth tables found")
    result = []
    for label, variants in grouped.items():
        item = {"berth_id": port_id + ":BERTH:" + label, "label": label, "variants": variants,
            "source_ref": variants[0]["source_ref"], "conditions": [v["text"] for v in variants],
            "issues": ["CRUISE_BERTH_SCOPE_REQUIRES_REVIEW"], "max_beam_m": None}
        if len(variants) > 1:
            item["issues"].append("DUPLICATE_BERTH_VARIANTS_REQUIRE_REVIEW")
        for field in ("max_loa_m", "max_draught_m"):
            item[field] = variants[0][field] if len(variants) == 1 else None
            if item[field] is None:
                item["issues"].append(field.upper() + "_UNKNOWN_OR_CONDITIONAL")
        result.append(item)
    return result
