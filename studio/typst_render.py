"""Build the Typst view of a record and compile it to PDF.

``build_view`` turns the record and profile into ``data.json``: every string
the page prints, already decided (which dates are verified enough to print,
how citations group into ranges, which references were formatted by CSL).
``lib.typ`` only lays that out. The revision directory gets a copy of the
library, the logo and the media, so a proof can be recompiled from its own
directory alone.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path
from typing import Any

import typst

from . import config, mathconv, references
from .docx_import import conv_inlines, Ctx
from .model import DECLARATION_LABELS, plain, text
from .profiles import Profile

LONG_TABLE_ROWS = 28


def fmt_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B')} {d.year}"


def cite_parts(refs: list[str], labels: dict[str, str]) -> list[list]:
    """Group cited reference numbers into ranges, each linked to its entry."""
    nums = []
    for r in refs:
        if r in labels:
            nums.append((int(labels[r]), r))
        else:
            nums.append((None, r))
    parts: list[list] = []
    known = sorted({n for n in nums if n[0] is not None})
    unknown = [n for n in nums if n[0] is None]
    i = 0
    while i < len(known):
        j = i
        while j + 1 < len(known) and known[j + 1][0] == known[j][0] + 1:
            j += 1
        if parts:
            parts.append([",", None])
        if j - i >= 2:
            parts.append([f"{known[i][0]}", known[i][1]])
            parts.append(["–", None])
            parts.append([f"{known[j][0]}", known[j][1]])
        else:
            for k in range(i, j + 1):
                if k > i:
                    parts.append([",", None])
                parts.append([f"{known[k][0]}", known[k][1]])
        i = j + 1
    for _, rid in unknown:
        if parts:
            parts.append([",", None])
        parts.append([f"?{rid}", None])
    return parts


def _prepare_inlines(nodes: list[dict], ctx: dict) -> list[dict]:
    out = []
    for n in nodes or []:
        n = dict(n)
        t = n["t"]
        if t == "cite":
            n["parts"] = cite_parts(n["refs"], ctx["labels"])
            # A superscript citation sits against the word it follows; the
            # space before a bracketed citation is the only thing removed.
            if ctx.get("cite_form") == "superscript" and out and out[-1]["t"] == "text":
                out[-1] = dict(out[-1], v=out[-1]["v"].rstrip(" "))
        elif t == "xref":
            n["ok"] = n["target"] in ctx["targets"]
        elif t == "math":
            n["typst"] = ctx["math"].get(n["tex"])
        elif t == "fnref":
            pass
        if "c" in n and isinstance(n["c"], list):
            n["c"] = _prepare_inlines(n["c"], ctx)
        out.append(n)
    return out


def _prepare_blocks(blocks: list[dict], ctx: dict) -> list[dict]:
    out = []
    for b in blocks or []:
        b = dict(b)
        if b["t"] in ("heading", "para"):
            b["c"] = _prepare_inlines(b["c"], ctx)
        elif b["t"] == "list":
            b["items"] = [_prepare_blocks(it, ctx) for it in b["items"]]
        elif b["t"] == "quote":
            b["blocks"] = _prepare_blocks(b["blocks"], ctx)
        out.append(b)
    return out


def _all_math(record: dict) -> list[str]:
    from .model import walk_inlines
    exprs = [e["tex"] for e in record.get("equations", [])]
    exprs += [n["tex"] for n in walk_inlines(record) if n.get("t") == "math"]
    seen, out = set(), []
    for e in exprs:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def epub_date(iso: str) -> str:
    """2026-07-01 -> 2026 Jul 1 (NLM)."""
    d = dt.date.fromisoformat(str(iso))
    return f"{d.year} {d.strftime('%b')} {d.day}"


def format_references(record: dict, profile: Profile) -> list[dict]:
    """Each reference with the inlines to print and how they were produced."""
    items = []
    for r in record.get("references", []):
        if r.get("csl"):
            items.append(dict(r["csl"], id=r["id"]))
    formatted = references.format_with_csl(items, profile.csl_path()) if items else {}
    out = []
    for r in record.get("references", []):
        entry = {"id": r["id"], "label": r["label"], "raw": r["raw"], "mode": "raw", "note": ""}
        pand = formatted.get(r["id"])
        if r.get("csl") and pand:
            nodes = conv_inlines(pand, Ctx())
            ok, why = references.formatted_keeps_content(r["csl"], plain(nodes))
            if ok:
                entry.update(c=nodes, mode="csl")
            else:
                entry.update(c=[text(r["raw"])], note=f"CSL output rejected ({why}); typed text kept")
        else:
            entry["c"] = [text(r["raw"])]
            if r.get("csl"):
                entry["note"] = "CSL formatting unavailable (Pandoc citeproc did not run); typed text kept"
        if r.get("epub"):
            # The online date of an item whose volume carries a later year,
            # in the NLM form ("Epub 2026 Jul 1"). Set by an editor, with the
            # source they checked it against, never inferred.
            entry["c"] = list(entry["c"]) + [text(f" Epub {epub_date(r['epub'])}.")]
        out.append(entry)
    return out


def locator(record: dict, profile: Profile) -> str:
    pub = record["publication"]
    model = (profile.data.get("numbering") or {}).get("model", "article-number")
    fmt = (profile.data.get("numbering") or {}).get("locator_format")
    if model == "article-number":
        if not pub.get("article_number"):
            return ""
        return (fmt or "{article_number}").format(article_number=pub["article_number"])
    if pub.get("first_page"):
        if pub.get("last_page"):
            return (fmt or "{first_page}–{last_page}").format(first_page=pub["first_page"], last_page=pub["last_page"])
        return str(pub["first_page"])
    return ""


_SEGMENT_SEP = re.compile(r"(\s+[/|·]\s+)")


INSERT = "[Insert {}]"


def insert_mode(profile) -> bool:
    """layout.house.missing: insert prints "[Insert ...]" in the proof where a
    value is missing, for the editor and authors to fill in."""
    return ((profile.data.get("layout") or {}).get("house") or {}).get("missing") == "insert"


def insert_dates(record: dict, fmt_date) -> list:
    out = []
    for k in ("received", "revised", "accepted", "published"):
        v = record["dates"].get(k) or {}
        out.append((k.capitalize(), fmt_date(v["value"]) if v.get("value") and v.get("verified") else INSERT.format("date")))
    return out


def _template_fill(tpl: str, values: dict) -> str:
    """Fill {placeholders}. A template split by " / ", " | " or " · " is
    filled segment by segment, and a segment whose placeholders are all
    empty is dropped with its separator, so a header never prints
    "et al. / Vol / Issue / /" for an article that has no details yet."""
    parts = _SEGMENT_SEP.split(tpl or "")
    if len(parts) > 1:
        kept, seps = [], []
        for i in range(0, len(parts), 2):
            seg = parts[i]
            names = re.findall(r"\{(\w+)\}", seg)
            if names and all(values.get(n) in (None, "") for n in names):
                continue
            filled = _template_fill_one(seg, values)
            if filled:
                if kept:
                    seps.append(parts[i - 1])
                kept.append(filled)
        out = kept[0] if kept else ""
        for sep, seg in zip(seps, kept[1:]):
            out += sep + seg
        return out.strip()
    return _template_fill_one(tpl, values)


def _template_fill_one(tpl: str, values: dict) -> str:
    def rep(m):
        v = values.get(m.group(1))
        return "" if v in (None, "") else str(v)
    s = re.sub(r"\{(\w+)\}", rep, tpl or "")
    s = re.sub(r"\(\)|;\s*\(|\s{2,}", lambda m: " " if m.group(0).isspace() else "", s)
    s = re.sub(r"\s*[;·]\s*(?=[;·]|$)", "", s).strip(" ;·:")
    return s


def build_view(record: dict, profile: Profile, watermark: str | None) -> tuple[dict, dict]:
    """The Typst data model plus facts about the render for the report."""
    data = profile.data
    atype = profile.article_type(record["article_type"])
    variant = atype.get("template", "research")
    layout = data.get("layout") or {}
    branding = data.get("branding") or {}
    fonts = dict({"body": "Libertinus Serif", "heading": "Libertinus Serif", "sans": "DejaVu Sans", "size": 10},
                 **(branding.get("fonts") or {}))
    colors = dict({"primary": "#1f3b63", "accent": "#b5532a", "rule": "#9aa7b8", "muted": "#5b6573", "ink": "#000000"},
                  **(branding.get("colors") or {}))
    margins = layout.get("margins") or {}
    columns = atype.get("columns", layout.get("columns", 2))
    style = {
        "page": layout.get("page", "a4"), "columns": columns,
        "column_gap": layout.get("column_gap", "6mm"), "size": float(fonts.get("size", 10)),
        "fonts": fonts, "colors": colors, "table_size": float(layout.get("table_font_size", 8)),
        "margins": {k: margins.get(k, "20mm") for k in ("top", "bottom", "inside", "outside")},
        # House-style switches; every key is optional and defaults to the
        # standard Studio layout. See layout.house in a profile.
        "house": {"masthead": "band", "title_case": "as-is", "title_size": 17, "title_align": "left",
                  "abstract_box": "variant", "abstract_heading": None, "keywords_label": "Keywords",
                  "header_style": "split", "heading_color": "primary", "first_line_indent": 1.0,
                  **(layout.get("house") or {})},
    }
    for k in ("top", "bottom", "inside", "outside"):
        style["margins"][k] = _length(style["margins"][k])
    style["column_gap"] = _length(style["column_gap"])

    labels = {r["id"]: r["label"] for r in record.get("references", [])}
    targets = {f["id"] for f in record["figures"]} | {t["id"] for t in record["tables"]} | {e["id"] for e in record["equations"]}
    exprs = _all_math(record)
    converted = mathconv.to_typst(exprs)
    math = {e: c for e, c in zip(exprs, converted) if c is not None}
    ctx = {"labels": labels, "targets": targets, "math": math,
           "cite_form": (data.get("references") or {}).get("in_text", "bracket")}

    pub = record["publication"]
    j = record["journal"]
    issn_bits = []
    if j.get("issn_print"):
        issn_bits.append(f"ISSN {j['issn_print']} (Print)")
    if j.get("issn_online"):
        issn_bits.append(f"ISSN {j['issn_online']} (Online)")
    logo = profile.logo_path()
    loc = locator(record, profile)
    line_bits = [str(pub["year"]) if pub.get("year") else None,
                 f"Volume {pub['volume']}" if pub.get("volume") else None,
                 f"Issue {pub['issue']}" if pub.get("issue") else None,
                 (f"Article {loc}" if loc and (data.get("numbering") or {}).get("model") == "article-number"
                  else f"Pages {loc}" if loc else None)]
    doi_url = f"https://doi.org/{pub['doi']}" if pub.get("doi") else None
    insert = insert_mode(profile)
    if insert:
        line_bits = [str(pub["year"]) if pub.get("year") else INSERT.format("year"),
                     f"Volume {pub.get('volume') or INSERT.format('volume')}",
                     f"Issue {pub.get('issue') or INSERT.format('issue')}",
                     (f"Article {loc}" if (data.get("numbering") or {}).get("model") == "article-number" else f"Pages {loc}")
                     if loc else f"Pages {INSERT.format('pages')}"]

    authors = []
    aff_labels = {a["id"]: a["label"] for a in record["affiliations"]}
    for a in record["authors"]:
        authors.append({
            "name": a["full_name"],
            "sup": ",".join(aff_labels[x] for x in a.get("affiliations", []) if x in aff_labels),
            "corresponding": bool(a.get("corresponding")),
            "orcid": a.get("orcid") if a.get("orcid") and a.get("orcid_verified") else None,
        })
    corr = [a for a in record["authors"] if a.get("corresponding")]
    correspondence = "; ".join(f"{a['full_name']}" + (f", {a['email']}" if a.get("email") else "") for a in corr) or None
    dates = [(k.capitalize(), fmt_date(v["value"])) for k, v in record["dates"].items()
             if v and v.get("value") and v.get("verified")]
    affiliations = [{"label": a["label"], "text": a["raw"]} for a in record["affiliations"]]
    if insert:
        # Proof placeholders. They are display only: record.json, the XML and
        # the page metadata never carry them, and checks.PLACEHOLDER_RE keeps
        # the proof blocked while any is showing.
        if not authors:
            authors = [{"name": INSERT.format("author names"), "sup": "1", "corresponding": True, "orcid": None}]
        if not affiliations:
            affiliations = [{"label": "1", "text": INSERT.format("department, institution, city, country")}]
        correspondence = "; ".join(f"{a['full_name']}, " + (a.get("email") or INSERT.format("email")) for a in corr) \
            or INSERT.format("corresponding author name and email")
        dates = insert_dates(record, fmt_date)

    abstract = None
    if record["abstract"]["sections"]:
        abstract = {"heading": "Abstract", "sections": [
            {"label": s.get("label"), "c": _prepare_inlines(s["c"], ctx)} for s in record["abstract"]["sections"]]}

    figures = {}
    for f in record["figures"]:
        figures[f["id"]] = dict(f, caption=_prepare_inlines(f["caption"], ctx), wide=bool(f.get("wide")) and columns == 2,
                                alt=f.get("alt") or f"{f['label']} (no description supplied)")
    tables = {}
    render_notes = []
    for t in record["tables"]:
        wide = columns == 2 and (t["cols"] > 4 or _row_chars(t) > 70)
        long = len(t["rows"]) > LONG_TABLE_ROWS
        tables[t["id"]] = dict(t, wide=wide, long=long,
                               caption=_prepare_inlines(t["caption"], ctx),
                               head=[[_prepare_inlines(c, ctx) for c in r] for r in t["head"]],
                               rows=[[_prepare_inlines(c, ctx) for c in r] for r in _pad_rows(t)],
                               note=_prepare_inlines(t.get("note") or [], ctx))
        if t["cols"] > 10:
            render_notes.append(f"{t['label']} has {t['cols']} columns; check it stays legible at {style['table_size']}pt.")
    equations = {e["id"]: {"id": e["id"], "label": e["label"], "tex": e["tex"], "typst": math.get(e["tex"]),
                           "alt": e.get("alt") or f"Equation {e['label']}: {e['tex']}"} for e in record["equations"]}

    order = data.get("declarations_order") or list(DECLARATION_LABELS)
    heads = record.get("declaration_headings") or {}
    declarations = []
    for key in order:
        v = record["declarations"].get(key)
        if v:
            declarations.append({"heading": heads.get(key) or DECLARATION_LABELS[key], "blocks": _prepare_blocks(v, ctx)})

    refs = format_references(record, profile)
    hdr_values = {
        "journal_abbreviation": j.get("abbreviation"), "journal_title": j.get("title"), "year": pub.get("year"),
        "volume": pub.get("volume"), "issue": pub.get("issue"), "locator": loc, "doi_url": doi_url,
        "first_author_surname": record["authors"][0]["family"] if record["authors"] else "",
        "short_title": record.get("short_title") or short_title(plain(record["title"]["en"])),
    }
    if insert:
        for k, what in (("year", "year"), ("volume", "volume"), ("issue", "issue"), ("locator", "pages"),
                        ("first_author_surname", "first author")):
            if hdr_values.get(k) in (None, ""):
                hdr_values[k] = INSERT.format(what)
    lic = record.get("license")
    if insert and not lic:
        lic = {"statement": INSERT.format("licence statement"), "url": None, "type": None}
    view = {
        "lang": record.get("language", "en"),
        "watermark": watermark,
        "variant": variant,
        "style": style,
        "cite_form": (data.get("references") or {}).get("in_text", "bracket"),
        "masthead": {"journal_title": j.get("title") or "", "publisher": j.get("publisher") or "",
                     "issn_line": " · ".join(issn_bits), "url": j.get("homepage") or "",
                     "issn_lines": ([f"e-ISSN {j['issn_online']}"] if j.get("issn_online") else [])
                                   + ([f"Print ISSN {j['issn_print']}"] if j.get("issn_print") else []),
                     "logo": "logo" + logo.suffix if logo else None},
        "article_type_label": atype.get("label", record["article_type"]),
        "article_line": " · ".join(b for b in line_bits if b),
        "doi_url": doi_url,
        "document_title": plain(record["title"]["en"]),
        "title": _prepare_inlines(record["title"]["en"], ctx),
        "title_original": _prepare_inlines(record["title"].get("original"), ctx) or None,
        "title_original_lang": record["title"].get("original_lang") or "en",
        "authors": authors,
        "meta_authors": [a["full_name"] for a in record["authors"]],
        "affiliations": affiliations,
        "correspondence": correspondence,
        "dates": dates,
        "abstract": abstract if atype.get("abstract", {}).get("required", True) or abstract else None,
        "keywords": "; ".join(record.get("keywords") or []),
        "keyword_list": record.get("keywords") or [],
        "body": _prepare_blocks(record["body"], ctx),
        "figures": figures, "tables": tables, "equations": equations,
        "footnotes": {f["id"]: _prepare_inlines(f["c"], ctx) for f in record.get("footnotes", [])},
        "declarations": declarations,
        "references": [{"id": r["id"], "label": r["label"], "c": _prepare_inlines(r["c"], ctx)} for r in refs],
        "license": {"statement": lic["statement"], "url": lic.get("url"), "type": lic.get("type")} if lic else None,
        "header": {"odd": _template_fill(layout.get("running_header", "{journal_abbreviation}"), hdr_values)
                   or _template_fill("{journal_abbreviation}", hdr_values),
                   "even": _template_fill(layout.get("running_header_even", "{first_author_surname} et al."), hdr_values)
                   or _template_fill("{journal_abbreviation}", hdr_values)},
        "footer": _template_fill(layout.get("footer", "{doi_url}"), hdr_values),
        "supplementary": record.get("supplementary") or [],
        "doi_placeholder": INSERT.format("DOI") if insert and not doi_url else None,
    }
    facts = {"references": refs, "math_failed": [e for e in exprs if e not in math], "notes": render_notes,
             "tables": {k: {"wide": v["wide"], "long": v["long"]} for k, v in tables.items()}}
    return view, facts


def short_title(title: str, limit: int = 55) -> str:
    """A running-head fallback: the title cut at a word boundary, marked as cut."""
    if len(title) <= limit:
        return title
    cut = title[:limit].rsplit(" ", 1)[0].rstrip(":;,. ")
    return cut + "…"


def _pad_rows(t: dict) -> list:
    n = t["cols"]
    return [r + [[]] * (n - len(r)) for r in t["rows"]]


def _row_chars(t: dict) -> int:
    rows = t["head"] + t["rows"]
    if not rows:
        return 0
    return max(sum(len(plain(c)) for c in r) for r in rows)


_UNIT_PT = {"mm": 72 / 25.4, "cm": 72 / 2.54, "in": 72.0, "pt": 1.0}


def _length(v: Any) -> float:
    """A profile length ('18mm', '0.5in', 12) as points; bare numbers are mm."""
    s = str(v).strip()
    m = re.fullmatch(r"([\d.]+)\s*(mm|cm|pt|in)?", s)
    if not m:
        raise ValueError(f"not a length: {v!r}")
    return round(float(m.group(1)) * _UNIT_PT[m.group(2) or "mm"], 3)


def write_sources(view: dict, profile: Profile, record_media: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "data.json").write_text(json.dumps(view, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.copyfile(config.TYPST_DIR / "lib.typ", out_dir / "article.typ")
    logo = profile.logo_path()
    if logo:
        shutil.copyfile(logo, out_dir / ("logo" + logo.suffix))
    if record_media.is_dir():
        dest = out_dir / "media"
        dest.mkdir(exist_ok=True)
        for f in record_media.iterdir():
            if f.is_file():
                shutil.copyfile(f, dest / f.name)


def font_dirs_for(profile: Profile | None) -> list[Path]:
    """Studio's bundled fonts plus the profile's own ``fonts/`` folder."""
    dirs = [config.PACKAGE_DIR / "fonts"]
    if profile is not None and (profile.path / "fonts").is_dir():
        dirs.append(profile.path / "fonts")
    return dirs


def compile_pdf(out_dir: Path, timestamp: dt.datetime, font_dirs: list[Path] | None = None) -> tuple[bool, list[str]]:
    """Compile article.typ; returns (ok, messages). Fonts are embedded by Typst.

    System fonts are ignored so the same inputs give the same PDF on any
    machine; ``font_dirs`` adds Studio's and the profile's font folders."""
    src = out_dir / "article.typ"
    font_dirs = font_dirs if font_dirs is not None else font_dirs_for(None)
    try:
        pdf, warnings = typst.compile_with_warnings(
            str(src), root=str(out_dir), format="pdf",
            timestamp=int(timestamp.timestamp()),
            font_paths=[str(p) for p in font_dirs], ignore_system_fonts=True,
        )
    except typst.TypstError as exc:
        msgs = [f"error: {getattr(exc, 'message', str(exc))}"]
        msgs += [f"hint: {h}" for h in (getattr(exc, "hints", None) or [])]
        msgs += [f"trace: {t}" for t in (getattr(exc, "trace", None) or [])]
        return False, msgs
    (out_dir / "article.pdf").write_bytes(pdf)
    return True, [f"warning: {getattr(w, 'message', str(w))}" for w in warnings]
