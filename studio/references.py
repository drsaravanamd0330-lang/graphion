"""Reference parsing and CSL formatting.

A reference arrives as the text an author typed. Studio keeps that text
verbatim in ``raw`` whatever else happens. Where the entry parses with
confidence, or where the DOCX carried structured citation data (Zotero or
Mendeley fields), a CSL-JSON item is attached and the entry is formatted with
the journal's CSL style through Pandoc's citeproc. Anything else is printed as
typed and flagged for the editor; nothing is guessed.

Formatting is accepted only if the formatted text still carries every number
and every author surname of the parsed item. A style that drops a page range
or a DOI would otherwise change the reference silently.
"""
from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

import yaml

from . import config

LEADING_LABEL_RE = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)[.)])\s+")
DOI_RE = re.compile(r"(?:doi:\s*|https?://(?:dx\.)?doi\.org/)(10\.\d{4,9}/[^\s]+?)(?=[.,;]?\s*$|[.,;]?\s)", re.I)

VANCOUVER_RE = re.compile(
    r"^(?P<authors>[^.]+?)\.\s+"
    r"(?P<title>.+?[.?!])\s+"
    r"(?P<journal>[A-Z][^.;]*?(?:\.[^.;0-9]*?)*?)\.?\s+"
    r"(?P<year>(?:19|20)\d{2})(?:\s+[A-Z][a-z]{2}(?:\s+\d{1,2})?)?"
    r"(?:;\s*(?P<volume>\d+)(?:\((?P<issue>[^)]+)\))?)?"
    r"(?::\s*(?P<pages>[A-Za-z]?\d+(?:[-–][A-Za-z]?\d+)?))?\.?"
    r"(?:\s*(?:doi:\s*|https?://(?:dx\.)?doi\.org/)(?P<doi>10\.\d{4,9}/\S+?))?\.?\s*$"
)
AUTHOR_RE = re.compile(r"^(?P<family>[A-Z][\w'’\-]+(?:\s[A-Z][\w'’\-]+)*?)\s+(?P<initials>[A-Z]{1,4})$")


def split_label(text: str) -> tuple[str | None, str]:
    """Separate a leading '1.' or '[1]' from the reference text."""
    m = LEADING_LABEL_RE.match(text)
    if not m:
        return None, text.strip()
    return (m.group(1) or m.group(2)), text[m.end():].strip()


def parse_reference(raw: str) -> dict[str, Any]:
    """Try to read a typed Vancouver/AMA-style journal reference.

    Returns ``{"status": "parsed" | "partial" | "unparsed", "csl": {...} | None,
    "doi": str | None, "reason": str}``. Only "parsed" items are reformatted.
    """
    doi_m = DOI_RE.search(raw + " ")
    doi = doi_m.group(1).rstrip(".") if doi_m else None
    m = VANCOUVER_RE.match(raw.strip())
    if not m:
        return {"status": "unparsed", "csl": None, "doi": doi,
                "reason": "does not follow a recognisable journal-article pattern"}
    authors_txt = m.group("authors")
    et_al = bool(re.search(r",?\s*et al\.?$", authors_txt))
    authors_txt = re.sub(r",?\s*et al\.?$", "", authors_txt)
    authors = []
    for a in [x.strip() for x in authors_txt.split(",") if x.strip()]:
        am = AUTHOR_RE.match(a)
        if not am:
            return {"status": "partial", "csl": None, "doi": doi,
                    "reason": f"author {a!r} is not in 'Surname Initials' form"}
        authors.append({"family": am.group("family"), "given": " ".join(am.group("initials"))})
    item: dict[str, Any] = {
        "type": "article-journal",
        "author": authors,
        "title": m.group("title").rstrip(".").strip(),
        "container-title-short": m.group("journal").strip(),
        "container-title": m.group("journal").strip(),
        "issued": {"date-parts": [[int(m.group("year"))]]},
    }
    for key, grp in (("volume", "volume"), ("issue", "issue"), ("page", "pages")):
        if m.group(grp):
            item[key] = m.group(grp).replace("–", "-")
    if m.group("doi"):
        item["DOI"] = m.group("doi").rstrip(".")
    if et_al:
        return {"status": "partial", "csl": item, "doi": doi,
                "reason": "lists 'et al.'; the full author list is not in the source, so the typed text is kept"}
    return {"status": "parsed", "csl": item, "doi": item.get("DOI") or doi, "reason": ""}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s)


def formatted_keeps_content(item: dict, formatted: str) -> tuple[bool, str]:
    """Check a formatted entry still carries the parsed item's facts."""
    f = _norm(formatted).lower()
    for a in item.get("author", []):
        if a.get("family", "").lower() not in f:
            return False, f"author {a.get('family')} missing from formatted text"
    for key in ("volume", "issue"):
        v = item.get(key)
        if v:
            for num in re.findall(r"\d+", str(v)):
                if num not in f:
                    return False, f"{key} {v} missing from formatted text"
    page = item.get("page")
    if page:
        nums = re.findall(r"\d+", str(page))
        if nums and nums[0] not in f:
            return False, f"page {page} missing from formatted text"
        if len(nums) > 1:
            # A style may abbreviate a range (101-109 as 101-9); the end page
            # must still be there, whole or as its trailing digits.
            start, end = nums[0], nums[-1]
            m = re.search(re.escape(start) + r"\s*-\s*(\d+)", f)
            if not m or not end.endswith(m.group(1)):
                return False, f"page range {page} not carried by formatted text"
    year = item.get("issued", {}).get("date-parts", [[None]])[0][0]
    if year and str(year) not in f:
        return False, "year missing from formatted text"
    doi = item.get("DOI")
    if doi and doi.lower() not in f:
        return False, "DOI missing from formatted text"
    return True, ""


def format_with_csl(items: list[dict], csl_path: Path) -> dict[str, list]:
    """Format CSL items with Pandoc citeproc; returns id -> Pandoc inlines.

    The bibliography's own numbering is stripped: Studio numbers references
    itself, from the manuscript's order, so citations and list always agree.
    """
    if not items:
        return {}
    pandoc = config.pandoc_path()
    if not pandoc:
        return {}
    doc = "---\n" + yaml.safe_dump({"nocite": "@*", "references": items}, allow_unicode=True) + "---\n"
    proc = subprocess.run(
        [pandoc, "-f", "markdown", "-t", "json", "--citeproc", "--csl", str(csl_path)],
        input=doc.encode("utf-8"), capture_output=True, timeout=120,
    )
    if proc.returncode != 0:
        return {}
    ast = json.loads(proc.stdout)
    out: dict[str, list] = {}

    def visit(blocks):
        for b in blocks:
            if b.get("t") == "Div":
                (ident, classes, _), children = b["c"]
                if ident.startswith("ref-"):
                    inl = []
                    for child in children:
                        if child.get("t") in ("Para", "Plain"):
                            inl.extend(child["c"])
                    out[ident[4:]] = _strip_left_margin(inl)
                else:
                    visit(children)

    visit(ast.get("blocks", []))
    return out


def _strip_left_margin(inlines: list) -> list:
    result = []
    for n in inlines:
        if n.get("t") == "Span":
            (_, classes, _), content = n["c"]
            if "csl-left-margin" in classes:
                continue
            if "csl-right-inline" in classes:
                result.extend(content)
                continue
        result.append(n)
    while result and result[0].get("t") in ("Space", "SoftBreak"):
        result.pop(0)
    return result


# ------------------------------------------------- structured citation fields

ZOTERO_RE = re.compile(r"ADDIN\s+(?:ZOTERO_ITEM\s+)?CSL_CITATION\s+(\{.*\})\s*$", re.S)


def items_from_field_codes(instr_texts: list[str]) -> list[dict]:
    """CSL items embedded by Zotero or Mendeley citation fields, de-duplicated."""
    seen: dict[str, dict] = {}
    for instr in instr_texts:
        m = ZOTERO_RE.search(instr.strip())
        if not m:
            continue
        try:
            payload = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        for ci in payload.get("citationItems", []):
            data = ci.get("itemData")
            if isinstance(data, dict):
                key = str(data.get("id") or data.get("title"))
                seen.setdefault(key, data)
    return list(seen.values())


def match_structured(raw: str, items: list[dict]) -> dict | None:
    """The structured item whose title appears in a typed reference."""
    r = _norm(raw).lower()
    for item in items:
        title = _norm(str(item.get("title", ""))).lower().rstrip(".")
        if len(title) > 12 and title in r:
            return item
    return None
