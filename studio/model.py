"""The article record: the single source of truth every output is built from.

A record is plain JSON. Three files make it up on disk:

* ``extracted.json``: what the importer read out of the DOCX. Rewritten only
  by a re-import.
* ``editor.json``: editor-approved metadata, from the sidecar file given at
  import and from edits in the review screen. Values here count as verified.
* ``record.json``: the two merged. PDF, HTML, JSON, JATS and Crossref outputs
  are all generated from this one file, so they cannot disagree.

Inline content is a list of nodes::

    {"t": "text", "v": "..."}           plain text (spaces included)
    {"t": "emph" | "strong" | "sup" | "sub" | "smallcaps", "c": [...]}
    {"t": "link", "url": "...", "c": [...]}
    {"t": "cite", "refs": ["ref1", "ref2"], "v": "[1,2]"}   v = source text
    {"t": "xref", "target": "fig1", "v": "Figure 1"}
    {"t": "math", "tex": "..."}
    {"t": "fnref", "id": "fn1"}
    {"t": "br"}

Body blocks::

    {"t": "heading", "level": 1, "id": "...", "c": [inlines]}
    {"t": "para", "c": [inlines]}
    {"t": "list", "ordered": bool, "items": [[blocks], ...]}
    {"t": "quote", "blocks": [...]}
    {"t": "figure", "id": "fig1"}       placement of record["figures"][...]
    {"t": "table", "id": "tab1"}        placement of record["tables"][...]
    {"t": "equation", "id": "eq1"}      placement of record["equations"][...]
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Iterable

SCHEMA_VERSION = "1.0"

ARTICLE_TYPES = ("original-research", "review", "case-report", "editorial")

DECLARATION_KEYS = (
    "funding",
    "conflicts",
    "contributions",
    "data_availability",
    "acknowledgements",
    "ethics",
    "consent",
    "trial_registration",
)

DECLARATION_LABELS = {
    "funding": "Funding",
    "conflicts": "Conflicts of Interest",
    "contributions": "Authors' Contributions",
    "data_availability": "Data Availability",
    "acknowledgements": "Acknowledgements",
    "ethics": "Ethics Approval",
    "consent": "Consent",
    "trial_registration": "Clinical Trial Registration",
}

DATE_KEYS = ("received", "revised", "accepted", "published")


def empty_record() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "article_id": None,
        "journal_id": None,
        "article_type": "original-research",
        "language": "en",
        "title": {"en": [], "original": [], "original_lang": None},
        "short_title": "",
        "authors": [],
        "affiliations": [],
        "abstract": {"structured": False, "sections": []},
        "abstract_original": {"lang": None, "sections": []},
        "keywords": [],
        "dates": {k: {"value": None, "verified": False, "source": None} for k in DATE_KEYS},
        "publication": {
            "year": None,
            "volume": None,
            "issue": None,
            "article_number": None,
            "first_page": None,
            "last_page": None,
            "doi": None,
            "doi_registered": False,
            "open_access": True,
        },
        "urls": {"landing_live": None, "pdf_live": None, "fulltext_live": None},
        "license": None,
        "declarations": {k: None for k in DECLARATION_KEYS},
        "body": [],
        "figures": [],
        "tables": [],
        "equations": [],
        "footnotes": [],
        "references": [],
        "supplementary": [],
        "flags": [],
        "provenance": {},
    }


# --------------------------------------------------------------------- inlines

def text(v: str) -> dict:
    return {"t": "text", "v": v}


def plain(inlines: Iterable[dict] | None) -> str:
    """Flatten inline nodes to plain text (what a reader would see)."""
    out: list[str] = []
    for n in inlines or []:
        t = n.get("t")
        if t == "text":
            out.append(n["v"])
        elif t in ("cite", "xref"):
            out.append(n.get("v", ""))
        elif t == "math":
            out.append(n.get("tex", ""))
        elif t == "br":
            out.append(" ")
        elif t == "fnref":
            continue
        elif "c" in n:
            out.append(plain(n["c"]))
    return "".join(out)


def blocks_plain(blocks: Iterable[dict], record: dict | None = None) -> str:
    """Plain text of body blocks, including figure/table/equation captions."""
    parts: list[str] = []
    for b in blocks or []:
        t = b.get("t")
        if t in ("heading", "para"):
            parts.append(plain(b.get("c")))
        elif t == "list":
            for item in b.get("items", []):
                parts.append(blocks_plain(item, record))
        elif t == "quote":
            parts.append(blocks_plain(b.get("blocks", []), record))
        elif record is not None and t in ("figure", "table", "equation"):
            obj = find(record, t, b["id"])
            if obj:
                parts.append(object_plain(t, obj))
    return "\n".join(p for p in parts if p)


def object_plain(kind: str, obj: dict) -> str:
    if kind == "figure":
        return f"{obj.get('label', '')} {plain(obj.get('caption'))}"
    if kind == "table":
        cells = [plain(c) for row in obj.get("head", []) + obj.get("rows", []) for c in row]
        return " ".join([obj.get("label", ""), plain(obj.get("caption"))] + cells + [plain(obj.get("note"))])
    if kind == "equation":
        return obj.get("tex", "")
    return ""


def find(record: dict, kind: str, oid: str) -> dict | None:
    coll = {"figure": "figures", "table": "tables", "equation": "equations",
            "reference": "references", "footnote": "footnotes"}[kind]
    for obj in record.get(coll, []):
        if obj.get("id") == oid:
            return obj
    return None


def walk_inlines(record: dict):
    """Yield every inline node in the record's body, captions and notes."""
    def from_inlines(nodes):
        for n in nodes or []:
            yield n
            if "c" in n and isinstance(n["c"], list):
                yield from from_inlines(n["c"])

    def from_blocks(blocks):
        for b in blocks or []:
            if b.get("t") in ("heading", "para"):
                yield from from_inlines(b.get("c"))
            elif b.get("t") == "list":
                for item in b.get("items", []):
                    yield from from_blocks(item)
            elif b.get("t") == "quote":
                yield from from_blocks(b.get("blocks"))

    yield from from_blocks(record.get("body"))
    for f in record.get("figures", []):
        yield from from_inlines(f.get("caption"))
    for t in record.get("tables", []):
        yield from from_inlines(t.get("caption"))
        yield from from_inlines(t.get("note"))
        for row in t.get("head", []) + t.get("rows", []):
            for cell in row:
                yield from from_inlines(cell)
    for fn in record.get("footnotes", []):
        yield from from_inlines(fn.get("c"))
    for sec in record.get("abstract", {}).get("sections", []):
        yield from from_inlines(sec.get("c"))
    for v in (record.get("declarations") or {}).values():
        if v:
            yield from from_blocks(v)


# ---------------------------------------------------------------- merging

def deep_merge(base: Any, over: Any) -> Any:
    """Merge editor overrides onto extracted values.

    Dicts merge key by key; any other value in ``over`` replaces the base.
    A list is replaced whole, because author order and reference order are
    content and must never be interleaved by a merge.
    """
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = deep_merge(base.get(k), v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(over)


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(obj: Any) -> str:
    return sha256_bytes(canonical_json(obj).encode("utf-8"))
