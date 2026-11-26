"""Read an accepted Word manuscript into an article record.

Pandoc reads the DOCX (``docx+styles``, so paragraph styles survive) into its
JSON AST and extracts the media. The DOCX XML is also read directly, for two
things Pandoc cannot report: what it silently resolved (tracked changes are
accepted by default) and what it silently dropped (text boxes, OLE objects,
charts, SmartArt). The importer then walks the AST once, splitting front
matter, abstract, keywords, body, declarations and references.

Every judgement the importer makes about structure is recorded as a flag on
the record, so the editor sees it. Wording, numbers, author order and Unicode
are carried through untouched; the importer moves text between fields but
never edits it.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from lxml import etree

from . import config, references
from .countries import looks_like_country
from .model import (DATE_KEYS, DECLARATION_KEYS, empty_record, plain, sha256_bytes, text)

IMPORTER_VERSION = "1"

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {
    "w": W,
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
}

DECLARATION_PATTERNS = [
    ("trial_registration", r"(clinical\s+)?trial\s+registration|registration\s+number"),
    ("funding", r"funding|financial\s+support|grant\s+support"),
    ("conflicts", r"conflicts?\s+of\s+interest|competing\s+interests?|declaration\s+of\s+interests?|disclosures?"),
    ("contributions", r"(authors?['’]?\s+)?contributions?|credit\s+authorship"),
    ("data_availability", r"data\s+(availability|sharing|accessibility)(\s+statement)?|availability\s+of\s+data"),
    ("acknowledgements", r"acknowledge?ments?"),
    ("ethics", r"ethic(s|al)(\s+(approval|statement|considerations))?|institutional\s+review\s+board"),
    ("consent", r"(informed\s+|patient\s+)?consent(\s+(for\s+publication|to\s+participate))?"),
]
REFERENCES_HEAD_RE = re.compile(r"^\s*(references|bibliography|literature\s+cited|works\s+cited)\s*$", re.I)
ABSTRACT_HEAD_RE = re.compile(r"^\s*(abstract|summary)\s*:?\s*$", re.I)
KEYWORDS_RE = re.compile(r"^\s*(key\s*words?|index\s+terms)\s*[:.]\s*", re.I)
CAPTION_RE = re.compile(r"^\s*(Figure|Fig\.?|Table)\s+(\d+)\s*[.:|–-]\s*", re.I)
NOTE_AFTER_TABLE_RE = re.compile(r"^\s*(notes?|abbreviations?|source)\s*[:.]|^[A-Z][\w\-/]{0,12}, [a-z].*;", re.I)
ORCID_RE = re.compile(r"(?:https?://orcid\.org/)?(\d{4}-\d{4}-\d{4}-\d{3}[\dX])")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
CORRESP_RE = re.compile(r"^\s*[*†]?\s*(corresponding\s+author|correspondence|address\s+for\s+correspondence)\s*[:.]?\s*", re.I)
DATE_LINE_RE = re.compile(r"(received|revised|accepted|published(?:\s+online)?)\s*[:\-]?\s*([^;|]+)", re.I)
CITE_BRACKET_RE = re.compile(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]")
XREF_RE = re.compile(r"\b(Figures?|Figs?\.?|Tables?|Equations?|Eqs?\.?)\s*\(?(\d+)\)?(?![\d.]*\d)")
LABEL_LINE_RE = re.compile(r"^[A-Z][A-Za-z ]{2,40}:\s")
NAME_PARTICLES = {"van", "von", "de", "da", "del", "della", "di", "du", "la", "le", "dos", "das", "ter", "ten", "bin", "al", "el"}


class ImportError_(RuntimeError):
    pass


def flag(code: str, severity: str, message: str, field: str = "", **detail) -> dict:
    out = {"code": code, "severity": severity, "message": message, "field": field}
    if detail:
        out["detail"] = detail
    return out


# ============================================================ DOCX XML scan

def scan_docx(path: Path) -> dict[str, Any]:
    """Facts about the DOCX that Pandoc does not report."""
    facts: dict[str, Any] = {"paragraphs": [], "instr": [], "counts": Counter(), "linked_images": 0}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "word/document.xml" not in names:
            raise ImportError_("not a Word document: word/document.xml is missing")
        doc = etree.fromstring(z.read("word/document.xml"))
        facts["has_comments"] = "word/comments.xml" in names and bool(
            etree.fromstring(z.read("word/comments.xml")).findall(".//w:comment", NS))
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8", "replace") if "word/_rels/document.xml.rels" in names else ""
        facts["external_image_rels"] = len(re.findall(r'Type="[^"]+/image"[^>]*TargetMode="External"', rels))
        fn_xml = z.read("word/footnotes.xml") if "word/footnotes.xml" in names else None
    c = facts["counts"]
    c["insertions"] = len(doc.findall(".//w:ins", NS))
    c["deletions"] = len(doc.findall(".//w:del", NS))
    c["moves"] = len(doc.findall(".//w:moveFrom", NS)) + len(doc.findall(".//w:moveTo", NS))
    c["format_changes"] = len(doc.findall(".//w:rPrChange", NS)) + len(doc.findall(".//w:pPrChange", NS))
    c["ole_objects"] = len(doc.findall(".//w:object", NS)) + len(doc.findall(".//o:OLEObject", NS))
    c["charts"] = len(doc.findall(".//c:chart", NS))
    c["smartart"] = len(doc.findall(".//dgm:relIds", NS))
    c["text_boxes"] = len(doc.findall(".//wps:txbx", NS)) + len(doc.findall(".//v:textbox", NS))
    c["linked_images"] = len([b for b in doc.findall(".//a:blip", NS) if b.get(f"{{{NS['r']}}}link")])
    c["fields"] = len(doc.findall(".//w:instrText", NS))
    facts["instr"] = ["".join(t.text or "" for t in p.findall(".//w:instrText", NS))
                      for p in doc.findall(".//w:p", NS) if p.findall(".//w:instrText", NS)]
    # Paragraph text as Word holds it, insertions accepted and deletions
    # dropped (Pandoc's default), so the two readings can be compared.
    for p in doc.iter(f"{{{W}}}p"):
        parts = []
        for node in p.iter():
            tag = etree.QName(node).localname
            if tag == "t" and node.text and etree.QName(node).namespace == W:
                parts.append(node.text)
            elif tag == "tab" and etree.QName(node).namespace == W:
                parts.append(" ")
        t = "".join(parts).strip()
        if t:
            facts["paragraphs"].append(t)
    if fn_xml is not None:
        fdoc = etree.fromstring(fn_xml)
        for fn in fdoc.findall("w:footnote", NS):
            if fn.get(f"{{{W}}}type") in ("separator", "continuationSeparator"):
                continue
            t = "".join(x.text or "" for x in fn.iter(f"{{{W}}}t")).strip()
            if t:
                facts["paragraphs"].append(t)
    return facts


def docx_flags(facts: dict) -> list[dict]:
    c = facts["counts"]
    out = []
    tracked = c["insertions"] + c["deletions"] + c["moves"]
    if tracked:
        out.append(flag("tracked-changes", "blocked",
                        f"The manuscript holds {tracked} unresolved tracked change(s) "
                        f"({c['insertions']} insertion, {c['deletions']} deletion, {c['moves']} move). "
                        "The import shows the text with changes accepted; resolve them in Word and re-import "
                        "so the accepted wording is certain.", "source"))
    if c["format_changes"]:
        out.append(flag("tracked-formatting", "warning",
                        f"{c['format_changes']} tracked formatting change(s) remain in the manuscript.", "source"))
    if facts.get("has_comments"):
        out.append(flag("comments", "warning", "The manuscript contains Word comments; they are not imported.", "source"))
    for key, label in (("ole_objects", "embedded OLE object(s)"), ("charts", "native Word chart(s)"),
                       ("smartart", "SmartArt graphic(s)"), ("text_boxes", "text box(es)")):
        if c[key]:
            out.append(flag(f"unsupported-{key.replace('_', '-')}", "blocked",
                            f"The manuscript contains {c[key]} {label}. Pandoc cannot convert these reliably, so their "
                            "content may be missing. Supply them as images or editable text and re-import.", "source"))
    if c["linked_images"] or facts.get("external_image_rels"):
        out.append(flag("linked-images", "blocked",
                        "One or more images are linked to files outside the document rather than embedded; "
                        "they cannot be imported. Embed them in Word and re-import.", "figures"))
    return out


# ============================================================ Pandoc

def pandoc_version() -> str | None:
    exe = config.pandoc_path()
    if not exe:
        return None
    out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)
    first = out.stdout.splitlines()[0] if out.stdout else ""
    return first.split()[-1] if first else None


def run_pandoc(docx: Path, media_dir: Path) -> tuple[dict, list[str]]:
    exe = config.pandoc_path()
    if not exe:
        raise ImportError_("Pandoc was not found. Install Pandoc 3.1.3 or newer and put it on PATH, "
                           "or set PANDOC_PATH to the executable.")
    proc = subprocess.run(
        [exe, "-f", "docx+styles", "-t", "json", f"--extract-media={media_dir}", str(docx)],
        capture_output=True, timeout=300,
    )
    if proc.returncode != 0:
        raise ImportError_("Pandoc could not read the manuscript: " + proc.stderr.decode("utf-8", "replace")[:800])
    warnings = [l for l in proc.stderr.decode("utf-8", "replace").splitlines() if l.strip()]
    return json.loads(proc.stdout), warnings


# ============================================================ AST -> nodes

class Ctx:
    def __init__(self):
        self.footnotes: list[dict] = []
        self.flags: list[dict] = []
        self.images: list[dict] = []   # images met inline, for flagging


def _merge(nodes: list[dict]) -> list[dict]:
    out: list[dict] = []
    for n in nodes:
        if n["t"] == "text" and out and out[-1]["t"] == "text":
            out[-1] = text(out[-1]["v"] + n["v"])
        elif n["t"] == "text" and n["v"] == "":
            continue
        else:
            out.append(n)
    return out


def conv_inlines(inl: list, ctx: Ctx) -> list[dict]:
    out: list[dict] = []
    for n in inl or []:
        t, c = n.get("t"), n.get("c")
        if t == "Str":
            out.append(text(c))
        elif t in ("Space", "SoftBreak"):
            out.append(text(" "))
        elif t == "LineBreak":
            out.append({"t": "br"})
        elif t in ("Emph", "Strong", "Superscript", "Subscript", "SmallCaps"):
            key = {"Emph": "emph", "Strong": "strong", "Superscript": "sup", "Subscript": "sub", "SmallCaps": "smallcaps"}[t]
            out.append({"t": key, "c": conv_inlines(c, ctx)})
        elif t == "Underline":
            out.extend(conv_inlines(c, ctx))
        elif t == "Strikeout":
            ctx.flags.append(flag("strikethrough", "manual",
                                  f"Struck-through text kept as plain text: “{plain(conv_inlines(c, Ctx()))[:80]}”. "
                                  "Strikethrough often marks a deletion; confirm it belongs in the article.", "body"))
            out.extend(conv_inlines(c, ctx))
        elif t == "Quoted":
            q_open, q_close = ("“", "”") if c[0]["t"] == "DoubleQuote" else ("‘", "’")
            out.append(text(q_open))
            out.extend(conv_inlines(c[1], ctx))
            out.append(text(q_close))
        elif t == "Link":
            out.append({"t": "link", "url": c[2][0], "c": conv_inlines(c[1], ctx)})
        elif t == "Math":
            out.append({"t": "math", "tex": c[1], "display": c[0]["t"] == "DisplayMath"})
        elif t == "Note":
            fid = f"fn{len(ctx.footnotes) + 1}"
            body = []
            for blk in c:
                if blk.get("t") in ("Para", "Plain"):
                    if body:
                        body.append({"t": "br"})
                    body.extend(conv_inlines(blk["c"], ctx))
            ctx.footnotes.append({"id": fid, "c": _merge(body)})
            out.append({"t": "fnref", "id": fid})
        elif t == "Span":
            out.extend(conv_inlines(c[1], ctx))
        elif t == "Image":
            ctx.images.append(n)
            out.append({"t": "image", "src": c[2][0], "alt": plain(conv_inlines(c[1], Ctx()))})
        elif t == "Cite":
            out.extend(conv_inlines(c[1], ctx))
        elif t == "Code":
            out.append(text(c[1]))
        elif t == "RawInline":
            ctx.flags.append(flag("raw-inline", "warning", f"Unconverted {c[0]} content dropped: {c[1][:60]!r}", "body"))
        else:
            ctx.flags.append(flag("unknown-inline", "warning", f"Unhandled inline element {t}", "body"))
    return _merge(out)


def unwrap(blocks: list) -> list[tuple[str | None, dict]]:
    """Flatten Divs (paragraph-style wrappers from docx+styles) to (style, block)."""
    out = []
    for b in blocks:
        if b.get("t") == "Div":
            (ident, classes, attrs), children = b["c"]
            style = dict(attrs).get("custom-style")
            for s, child in unwrap(children):
                out.append((s or style, child))
        else:
            out.append((None, b))
    return out


def block_inlines(b: dict) -> list | None:
    if b.get("t") in ("Para", "Plain"):
        return b["c"]
    return None


# ============================================================ helpers

def _cell_inlines(cell_blocks: list, ctx: Ctx) -> list[dict]:
    out: list[dict] = []
    for _, blk in unwrap(cell_blocks):
        inl = block_inlines(blk)
        if inl is None:
            if blk.get("t") in ("BulletList", "OrderedList"):
                for item in blk["c"] if blk["t"] == "BulletList" else blk["c"][1]:
                    for _, ib in unwrap(item):
                        if block_inlines(ib) is not None:
                            if out:
                                out.append({"t": "br"})
                            out.append(text("• "))
                            out.extend(conv_inlines(block_inlines(ib), ctx))
            continue
        if out:
            out.append({"t": "br"})
        out.extend(conv_inlines(inl, ctx))
    return _merge(out)


def _table_rows(rows: list, ctx: Ctx) -> list[list[list[dict]]]:
    out = []
    for row in rows:
        cells = row[1]
        r = []
        for cell in cells:
            # cell = [attr, align, rowspan, colspan, blocks]
            content = _cell_inlines(cell[4], ctx)
            node = content
            rs, cs = cell[2], cell[3]
            if rs != 1 or cs != 1:
                ctx.flags.append(flag("merged-cells", "manual",
                                      "A table has merged cells; check the proof keeps the source layout.", "tables"))
            r.append(node)
        out.append(r)
    return out


def _split_label_text(inlines: list[dict]) -> tuple[str, list[dict]]:
    """Separate 'Table 1.' from the caption text that follows it."""
    s = plain(inlines)
    m = CAPTION_RE.match(s)
    if not m:
        return "", inlines
    cut = m.end()
    out, consumed = [], 0
    for n in inlines:
        if consumed >= cut:
            out.append(n)
            continue
        if n["t"] == "text":
            remaining = cut - consumed
            if len(n["v"]) <= remaining:
                consumed += len(n["v"])
            else:
                out.append(text(n["v"][remaining:]))
                consumed = cut
        else:
            ln = len(plain([n]))
            if consumed + ln <= cut:
                consumed += ln
            else:
                out.append(n)
                consumed = cut
    kind = m.group(1).lower()
    word = "Table" if kind.startswith("tab") else "Figure"
    return f"{word} {m.group(2)}", _merge(out)


def _parse_date(s: str) -> str | None:
    s = s.strip().strip(".")
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def split_name(full: str) -> tuple[str, str]:
    parts = full.split()
    if len(parts) == 1:
        return "", parts[0]
    i = len(parts) - 1
    while i > 1 and parts[i - 1].lower() in NAME_PARTICLES:
        i -= 1
    return " ".join(parts[:i]), " ".join(parts[i:])


def parse_author_line(inlines: list[dict]) -> list[dict]:
    """Authors and their affiliation markers from a byline.

    A superscript after a name holds its affiliation labels and any
    corresponding-author mark. Names are split at commas, semicolons, 'and'
    and '&'. Order is the order on the page, always.
    """
    tokens: list[tuple[str, str]] = []
    for n in inlines:
        if n["t"] == "sup":
            tokens.append(("sup", plain(n["c"])))
        elif n["t"] in ("text", "emph", "strong"):
            tokens.append(("text", plain([n])))
        elif n["t"] == "link":
            tokens.append(("text", plain(n["c"])))
    authors: list[dict] = []
    buf = ""

    def push(name, marks=""):
        name = re.sub(r"^\s*(and|&)\s+", "", name.strip().strip(",;").strip())
        if not name:
            return
        labels = [m for m in re.split(r"[,\s]+", re.sub(r"[*†‡§#]", " ", marks)) if m]
        authors.append({"full_name": name, "labels": labels,
                        "corresponding": any(ch in marks for ch in "*†")})

    has_sup = any(k == "sup" for k, _ in tokens)
    for kind, val in tokens:
        if kind == "sup":
            push(buf, val)
            buf = ""
        else:
            if has_sup:
                buf += val
                # text after a superscript that begins with a separator closes nothing
                # by itself; the next superscript or a separator inside closes a name
                pieces = re.split(r"\s*(?:,|;|\band\b|&)\s*", buf)
                if len(pieces) > 1:
                    for p in pieces[:-1]:
                        if p.strip():
                            push(p)
                    buf = pieces[-1]
            else:
                buf += val
    if buf.strip():
        if has_sup:
            push(buf)
        else:
            for p in re.split(r"\s*(?:,|;|\band\b|&)\s*", buf):
                push(p)
    return authors


def parse_affiliation(raw: str) -> dict:
    segs = [s.strip() for s in raw.split(",") if s.strip()]
    country = segs[-1] if segs else ""
    city = segs[-2] if len(segs) >= 3 else ""
    inst_segs = segs[:-2] if len(segs) >= 3 else segs[:-1]
    dept = ""
    if len(inst_segs) >= 2 and re.search(r"department|dept\.?|school|faculty|division|unit|unidad|centre|center|laboratory|section|program", inst_segs[0], re.I):
        dept = inst_segs[0]
        inst_segs = inst_segs[1:]
    return {"raw": raw, "department": dept, "institution": ", ".join(inst_segs), "city": city,
            "country": country, "country_recognised": looks_like_country(country)}


def _strip_strong_lead(inlines: list[dict]) -> tuple[str | None, list[dict]]:
    """'**Background:** text' -> ('Background', text inlines)."""
    if inlines and inlines[0]["t"] == "strong":
        label = plain(inlines[0]["c"]).strip()
        if label.endswith(":") or label.endswith("."):
            rest = inlines[1:]
            if rest and rest[0]["t"] == "text":
                rest = [text(rest[0]["v"].lstrip())] + rest[1:]
            return label.rstrip(":.").strip(), _merge(rest)
    s = plain(inlines)
    m = re.match(r"^(Background|Objectives?|Aims?|Introduction|Methods?|Materials and Methods|Design|Setting|"
                 r"Participants|Interventions?|Results?|Findings|Conclusions?|Discussion|Case Presentation|"
                 r"Case Description)\s*:\s*", s)
    if m:
        cut = m.end()
        first = inlines[0]
        if first["t"] == "text" and len(first["v"]) >= cut:
            return m.group(1), _merge([text(first["v"][cut:])] + inlines[1:])
    return None, inlines


def _strip_prefix(inlines: list[dict], pattern: re.Pattern) -> list[dict]:
    s = plain(inlines)
    m = pattern.match(s)
    if not m:
        return inlines
    out, consumed, cut = [], 0, m.end()
    for n in inlines:
        ln = len(plain([n]))
        if consumed >= cut:
            out.append(n)
        elif n["t"] == "text" and consumed + ln > cut:
            out.append(text(n["v"][cut - consumed:]))
            consumed = cut
        elif n["t"] == "strong" and consumed + ln > cut:
            out.append({"t": "strong", "c": _strip_prefix(n["c"], re.compile(".{%d}" % (cut - consumed), re.S))})
            consumed = cut
        else:
            consumed += ln
    return _merge(out)


def declaration_key(heading: str) -> str | None:
    h = re.sub(r"^\s*[\dIVX]+(\.\d+)*\.?\s+", "", heading).strip().rstrip(":").lower()
    for key, pat in DECLARATION_PATTERNS:
        if re.fullmatch(pat, h, re.I):
            return key
    return None


# ============================================================ in-text links

def expand_cites(spec: str) -> list[int]:
    nums: list[int] = []
    for part in re.split(r"\s*,\s*", spec):
        m = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if 0 < b - a < 100:
                nums.extend(range(a, b + 1))
                continue
        if part.strip().isdigit():
            nums.append(int(part))
    return nums


def link_text(nodes: list[dict], cite_style_superscript: bool) -> list[dict]:
    """Turn '[1,2]' into citations and 'Figure 1' into cross-references."""
    out: list[dict] = []
    for i, n in enumerate(nodes):
        if n["t"] == "text":
            out.extend(_split_text(n["v"]))
        elif n["t"] == "sup" and cite_style_superscript:
            s = plain(n["c"]).strip()
            prev = out[-1]["v"] if out and out[-1]["t"] == "text" else ""
            if re.fullmatch(r"\d+(\s*[-–,]\s*\d+)*", s) and not re.search(r"\d$", prev):
                out.append({"t": "cite", "refs": [f"ref{k}" for k in expand_cites(s)], "v": s, "form": "sup"})
            else:
                out.append(n)
        elif n["t"] in ("emph", "strong", "sup", "sub", "smallcaps"):
            out.append({"t": n["t"], "c": link_text(n["c"], cite_style_superscript)})
        else:
            out.append(n)
    return _merge(out)


def _split_text(s: str) -> list[dict]:
    out: list[dict] = []
    pos = 0
    pattern = re.compile(f"{CITE_BRACKET_RE.pattern}|{XREF_RE.pattern}")
    for m in pattern.finditer(s):
        if m.start() > pos:
            out.append(text(s[pos:m.start()]))
        if m.group(1):
            out.append({"t": "cite", "refs": [f"ref{k}" for k in expand_cites(m.group(1))], "v": m.group(0), "form": "bracket"})
        else:
            word = m.group(2).lower()
            prefix = "fig" if word.startswith("fig") else "tab" if word.startswith("tab") else "eq"
            out.append({"t": "xref", "target": f"{prefix}{m.group(3)}", "v": m.group(0)})
        pos = m.end()
    if pos < len(s):
        out.append(text(s[pos:]))
    return out


# ============================================================ main import

def import_docx(docx_path: Path, media_out: Path, cite_superscript_source: bool = False) -> dict:
    """Read a DOCX into an extracted record. Writes media into ``media_out``."""
    docx_path = Path(docx_path)
    data = docx_path.read_bytes()
    facts = scan_docx(docx_path)
    record = empty_record()
    record["flags"].extend(docx_flags(facts))

    with tempfile.TemporaryDirectory() as tmp:
        ast, warnings = run_pandoc(docx_path, Path(tmp))
        for w in warnings:
            record["flags"].append(flag("pandoc-warning", "warning", f"Pandoc: {w}", "source"))
        ctx = Ctx()
        blocks = unwrap(ast.get("blocks", []))
        _assemble(record, blocks, ctx, Path(tmp), Path(media_out), cite_superscript_source)
    record["footnotes"] = ctx.footnotes
    record["flags"].extend(ctx.flags)

    # structured citation data carried by Zotero/Mendeley fields
    structured = references.items_from_field_codes(facts["instr"])
    if structured:
        for ref in record["references"]:
            item = references.match_structured(ref["raw"], structured)
            if item:
                ref["csl"] = {k: v for k, v in item.items() if k != "id"}
                ref["parse_status"] = "structured"
                ref["parse_reason"] = "from citation manager field data in the DOCX"

    record["provenance"] = {
        "source_file": docx_path.name,
        "source_sha256": sha256_bytes(data),
        "imported_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "pandoc_version": pandoc_version(),
        "importer_version": IMPORTER_VERSION,
        "source_paragraphs": facts["paragraphs"],
    }
    return record


def _assemble(record: dict, blocks: list[tuple[str | None, dict]], ctx: Ctx, tmp: Path,
              media_out: Path, cite_sup: bool) -> None:
    flags = record["flags"]
    n = len(blocks)
    texts = [plain(conv_inlines(block_inlines(b) or [], Ctx())) if block_inlines(b) is not None
             else plain(conv_inlines(b["c"][2], Ctx())) if b.get("t") == "Header" else "" for _, b in blocks]

    def is_header(i):
        return blocks[i][1].get("t") == "Header"

    # ---------------------------------------------------------- landmarks
    abstract_i = next((i for i in range(n) if is_header(i) and ABSTRACT_HEAD_RE.match(texts[i])), None)
    if abstract_i is None:
        abstract_i = next((i for i in range(n) if blocks[i][1].get("t") in ("Para", "Plain")
                           and re.match(r"^\s*abstract\s*[:.]", texts[i], re.I)), None)
    refs_i = next((i for i in range(n) if is_header(i) and REFERENCES_HEAD_RE.match(texts[i])), None)
    if refs_i is None:
        refs_i = next((i for i in range(n) if REFERENCES_HEAD_RE.match(texts[i]) and len(texts[i]) < 30), None)
    first_body_head = next((i for i in range(n) if is_header(i) and i > (abstract_i if abstract_i is not None else -1)
                            and not ABSTRACT_HEAD_RE.match(texts[i])), None)
    if abstract_i is None:
        flags.append(flag("no-abstract-heading", "manual",
                          "No 'Abstract' heading was found; front matter and body were split at the first heading.",
                          "abstract"))
        front_end = first_body_head if first_body_head is not None else 0
    else:
        front_end = abstract_i
    if refs_i is None:
        flags.append(flag("no-references-heading", "manual", "No 'References' heading was found.", "references"))

    # ---------------------------------------------------------- front matter
    _front_matter(record, blocks[:front_end], ctx, flags)

    # ---------------------------------------------------------- abstract + keywords
    body_start = first_body_head if first_body_head is not None else front_end
    if abstract_i is not None:
        start = abstract_i + 1 if is_header(abstract_i) else abstract_i
        sections = []
        for i in range(start, body_start):
            _, b = blocks[i]
            inl = block_inlines(b)
            if inl is None:
                continue
            nodes = conv_inlines(inl, ctx)
            if KEYWORDS_RE.match(plain(nodes)):
                rest = plain(_strip_prefix(nodes, KEYWORDS_RE))
                record["keywords"] = [k.strip().rstrip(".") for k in re.split(r"[;,]", rest) if k.strip()]
                continue
            if i == abstract_i:  # 'Abstract: text' paragraph form
                nodes = _strip_prefix(nodes, re.compile(r"^\s*abstract\s*[:.]\s*", re.I))
            label, rest = _strip_strong_lead(nodes)
            sections.append({"label": label, "c": link_text(rest, cite_sup)})
        record["abstract"] = {"structured": any(s["label"] for s in sections), "sections": sections}
    if not record["keywords"]:
        for i in range(0, body_start):
            if KEYWORDS_RE.match(texts[i]):
                rest = KEYWORDS_RE.sub("", texts[i])
                record["keywords"] = [k.strip().rstrip(".") for k in re.split(r"[;,]", rest) if k.strip()]

    # ---------------------------------------------------------- body / declarations / references
    end = refs_i if refs_i is not None else n
    _body(record, blocks[body_start:end], ctx, tmp, media_out, cite_sup)
    if refs_i is not None:
        _references(record, blocks[refs_i + 1:], ctx)


def _front_matter(record: dict, blocks: list, ctx: Ctx, flags: list) -> None:
    paras = []
    for style, b in blocks:
        inl = block_inlines(b) if b.get("t") != "Header" else b["c"][2]
        if inl is None:
            continue
        nodes = conv_inlines(inl, ctx)
        if plain(nodes).strip():
            paras.append((style, nodes))
    consumed = set()
    title_i = next((i for i, (s, _) in enumerate(paras) if s and s.lower() == "title"), None)
    if title_i is None:
        for i, (s, nodes) in enumerate(paras):
            t = plain(nodes)
            if any(x["t"] == "sup" for x in nodes) or LABEL_LINE_RE.match(t) or len(t.split()) < 4:
                continue
            title_i = i
            break
    if title_i is None:
        flags.append(flag("no-title", "blocked", "No article title was found in the manuscript.", "title"))
    else:
        record["title"]["en"] = paras[title_i][1]
        consumed.add(title_i)
        flags.append(flag("title-detected", "info", "Title taken from "
                          + ("the Word 'Title' style." if paras[title_i][0] == "Title" else
                             "the first full-length line of the front matter; confirm it."), "title"))
    # subtitle / original-language title
    if title_i is not None and title_i + 1 < len(paras) and (paras[title_i + 1][0] or "").lower() == "subtitle":
        record["subtitle"] = paras[title_i + 1][1]
        consumed.add(title_i + 1)

    # byline: the first paragraph after the title holding superscripts or styled Author
    author_i = None
    for i, (s, nodes) in enumerate(paras):
        if i in consumed or (title_i is not None and i < title_i):
            continue
        if (s and s.lower() in ("author", "authors")) or (
                any(x["t"] == "sup" for x in nodes) and not (nodes and nodes[0]["t"] == "sup")):
            author_i = i
            break
    if author_i is None and title_i is not None and title_i + 1 < len(paras):
        author_i = title_i + 1
        flags.append(flag("byline-guessed", "manual", "The author line was taken as the line after the title.", "authors"))
    authors_raw = parse_author_line(paras[author_i][1]) if author_i is not None else []
    if author_i is not None:
        consumed.add(author_i)

    # affiliations
    affs = []
    for i, (s, nodes) in enumerate(paras):
        if i in consumed:
            continue
        t = plain(nodes).strip()
        if nodes and nodes[0]["t"] == "sup":
            label = plain(nodes[0]["c"]).strip()
            raw = plain(nodes[1:]).strip()
        else:
            m = re.match(r"^([0-9]{1,2}|[a-z])[.)]?\s+(.{8,})$", t)
            if not (m and (s or "").lower() in ("affiliation", "address") or (m and re.search(
                    r"universit|hospital|institut|college|school|department|centre|center|faculty|laborator", t, re.I))):
                continue
            label, raw = m.group(1), m.group(2)
        if CORRESP_RE.match(t) or t.lower().startswith("orcid"):
            continue
        a = parse_affiliation(raw)
        a["id"] = f"aff{len(affs) + 1}"
        a["label"] = label
        affs.append(a)
        consumed.add(i)
    if not affs:
        for i, (s, nodes) in enumerate(paras):
            if i not in consumed and (s or "").lower() in ("affiliation", "address"):
                a = parse_affiliation(plain(nodes).strip())
                a.update(id=f"aff{len(affs) + 1}", label=str(len(affs) + 1))
                affs.append(a)
                consumed.add(i)
    record["affiliations"] = affs
    for a in affs:
        if not a["country_recognised"]:
            flags.append(flag("affiliation-country", "manual",
                              f"Affiliation {a['label']} ends with “{a['country']}”, which is not a recognised country "
                              "or region; confirm the country.", "affiliations"))

    # authors -> affiliation ids
    by_label = {a["label"]: a["id"] for a in affs}
    authors = []
    for k, ar in enumerate(authors_raw, start=1):
        given, family = split_name(ar["full_name"])
        aff_ids = [by_label[l] for l in ar["labels"] if l in by_label]
        missing = [l for l in ar["labels"] if l not in by_label]
        if missing:
            flags.append(flag("author-affiliation-label", "blocked",
                              f"{ar['full_name']} is marked with affiliation label(s) {', '.join(missing)}, "
                              "which match no affiliation line.", "authors"))
        if not aff_ids:
            if len(affs) == 1:
                aff_ids = [affs[0]["id"]]
                flags.append(flag("author-affiliation-assumed", "manual",
                                  f"{ar['full_name']} carries no affiliation marker; mapped to the only affiliation.",
                                  "authors"))
            else:
                flags.append(flag("author-affiliation-uncertain", "blocked",
                                  f"Cannot tell which affiliation belongs to {ar['full_name']}.", "authors"))
        authors.append({
            "id": f"au{k}", "full_name": ar["full_name"], "given": given, "family": family,
            "name_roman": None, "affiliations": aff_ids, "corresponding": ar["corresponding"],
            "email": None, "orcid": None, "orcid_verified": False,
        })
    if authors:
        flags.append(flag("name-split", "manual",
                          "Given and family names were split automatically (" +
                          "; ".join(f"{a['family']}, {a['given']}" for a in authors) +
                          "). Confirm them; they drive citation metadata.", "authors"))

    # corresponding author, ORCID, dates, other lines
    for i, (s, nodes) in enumerate(paras):
        if i in consumed:
            continue
        t = plain(nodes).strip()
        if CORRESP_RE.match(t):
            consumed.add(i)
            emails = EMAIL_RE.findall(t) + [x["url"][7:] for x in nodes if x["t"] == "link" and x["url"].startswith("mailto:")]
            target = None
            for a in authors:
                if a["full_name"].lower() in t.lower() or (a["family"] and a["family"].lower() in t.lower()):
                    target = a
                    break
            if target is None:
                target = next((a for a in authors if a["corresponding"]), None)
            if target is None:
                flags.append(flag("corresponding-unmatched", "blocked",
                                  f"The correspondence line names no author on the byline: “{t[:100]}”.", "authors"))
            else:
                target["corresponding"] = True
                if emails:
                    target["email"] = emails[0]
        elif ORCID_RE.search(t) and "orcid" in t.lower():
            consumed.add(i)
            for chunk in re.split(r";|\n", t):
                m = ORCID_RE.search(chunk)
                if not m:
                    continue
                who = next((a for a in authors if a["full_name"].lower() in chunk.lower()
                            or (a["family"] and a["family"].lower() in chunk.lower())), None)
                if who is None:
                    flags.append(flag("orcid-unmatched", "manual", f"ORCID {m.group(1)} could not be matched to an author.", "authors"))
                    continue
                who["orcid"] = m.group(1)
                if not orcid_checksum_ok(m.group(1)):
                    flags.append(flag("orcid-checksum", "blocked",
                                      f"ORCID {m.group(1)} for {who['full_name']} fails its check digit.", "authors"))
        elif DATE_LINE_RE.search(t) and re.match(r"^\s*(received|revised|accepted|published)", t, re.I):
            consumed.add(i)
            for m in DATE_LINE_RE.finditer(t):
                key = m.group(1).lower().split()[0]
                iso = _parse_date(m.group(2))
                if key in DATE_KEYS:
                    if iso:
                        record["dates"][key] = {"value": iso, "verified": False, "source": "manuscript"}
                    else:
                        flags.append(flag("date-unparsed", "manual", f"Could not read the {key} date “{m.group(2).strip()}”.", "dates"))
    leftovers = [plain(nodes).strip() for i, (s, nodes) in enumerate(paras) if i not in consumed]
    if leftovers:
        record["front_matter_unparsed"] = leftovers
        flags.append(flag("front-matter-unparsed", "manual",
                          f"{len(leftovers)} front-matter line(s) were not recognised and are not printed: " +
                          " | ".join(x[:70] for x in leftovers), "front_matter"))
    record["authors"] = authors
    if not authors:
        flags.append(flag("no-authors", "blocked", "No authors were found.", "authors"))


def orcid_checksum_ok(orcid: str) -> bool:
    digits = orcid.replace("-", "")
    if not re.fullmatch(r"\d{15}[\dX]", digits):
        return False
    total = 0
    for ch in digits[:15]:
        total = (total + int(ch)) * 2
    check = (12 - total % 11) % 11
    return digits[15] == ("X" if check == 10 else str(check))


def _body(record: dict, blocks: list, ctx: Ctx, tmp: Path, media_out: Path, cite_sup: bool) -> None:
    body: list[dict] = []
    decl_target: list | None = None
    decl_level = 0
    pending_table_caption: tuple[str, list] | None = None
    last_figure: dict | None = None
    last_table: dict | None = None
    fig_auto = tab_auto = eq_auto = 0
    flags = record["flags"]
    media_out.mkdir(parents=True, exist_ok=True)

    def emit(block):
        (decl_target if decl_target is not None else body).append(block)

    i = 0
    while i < len(blocks):
        style, b = blocks[i]
        t = b.get("t")
        if t == "Header":
            level, attr, inl = b["c"]
            nodes = conv_inlines(inl, ctx)
            key = declaration_key(plain(nodes))
            if key:
                record["declarations"][key] = record["declarations"].get(key) or []
                decl_target, decl_level = record["declarations"][key], level
                record.setdefault("declaration_headings", {})[key] = plain(nodes).strip()
            else:
                if decl_target is not None and level <= decl_level:
                    decl_target = None
                if decl_target is not None:
                    emit({"t": "heading", "level": level, "id": attr[0], "c": nodes})
                else:
                    body.append({"t": "heading", "level": level, "id": attr[0], "c": nodes})
            last_figure = last_table = None
            i += 1
            continue

        if t == "Table":
            tab_auto += 1
            caption_inl = conv_inlines(_pandoc_caption(b), ctx)
            label, cap = _split_label_text(caption_inl) if caption_inl else ("", [])
            if not label and pending_table_caption:
                label, cap = pending_table_caption
                if body and body[-1].get("_caption_para"):
                    body.pop()
            pending_table_caption = None
            num = re.search(r"\d+", label).group(0) if label else str(tab_auto)
            head_rows = b["c"][3][1]
            bodies = b["c"][4]
            head = _table_rows(head_rows, ctx)
            rows = []
            for tb in bodies:
                rows.extend(_table_rows(tb[2], ctx))
                rows.extend(_table_rows(tb[3], ctx))
            if not head and rows:
                head, rows = [rows[0]], rows[1:]
                flags.append(flag("table-header-assumed", "info",
                                  f"Table {num}: the first row was taken as the header row (Word marked none).", "tables"))
            tid = f"tab{num}"
            if any(x["id"] == tid for x in record["tables"]):
                flags.append(flag("duplicate-table", "blocked", f"Two tables are labelled Table {num}.", "tables"))
                tid = f"tab{num}-{tab_auto}"
            table = {"id": tid, "label": label or f"Table {num}", "caption": link_text(cap, cite_sup),
                     "head": [[link_text(c, cite_sup) for c in r] for r in head],
                     "rows": [[link_text(c, cite_sup) for c in r] for r in rows],
                     "cols": max((len(r) for r in head + rows), default=0), "note": []}
            if not label:
                flags.append(flag("table-caption-missing", "blocked", f"Table {num} has no caption.", "tables"))
            record["tables"].append(table)
            body.append({"t": "table", "id": tid})
            last_table, last_figure = table, None
            i += 1
            continue

        if t == "Figure":  # Pandoc 3 figure block
            img = _first_image(b["c"][2])
            cap = conv_inlines(_figure_caption(b), ctx)
            fig_auto += 1
            fig = _make_figure(record, img, cap, fig_auto, tmp, media_out, flags, cite_sup)
            if fig:
                body.append({"t": "figure", "id": fig["id"]})
                last_figure, last_table = fig, None
            i += 1
            continue

        if t in ("Para", "Plain"):
            inl = b["c"]
            # display equation
            if len(inl) == 1 and inl[0].get("t") == "Math" and inl[0]["c"][0]["t"] == "DisplayMath":
                eq_auto += 1
                eid = f"eq{eq_auto}"
                record["equations"].append({"id": eid, "label": f"({eq_auto})", "tex": inl[0]["c"][1]})
                emit({"t": "equation", "id": eid})
                i += 1
                continue
            images = [x for x in inl if x.get("t") == "Image"]
            others = [x for x in inl if x.get("t") not in ("Image", "Space", "SoftBreak", "LineBreak")]
            if images and not others:
                for img in images:
                    fig_auto += 1
                    cap: list = []
                    # caption is usually the next paragraph
                    if i + 1 < len(blocks) and block_inlines(blocks[i + 1][1]) is not None:
                        nxt = conv_inlines(block_inlines(blocks[i + 1][1]), ctx)
                        if CAPTION_RE.match(plain(nxt)) and plain(nxt).lower().startswith("fig"):
                            cap = nxt
                            i += 1
                    fig = _make_figure(record, img, cap, fig_auto, tmp, media_out, flags, cite_sup)
                    if fig:
                        body.append({"t": "figure", "id": fig["id"]})
                        last_figure, last_table = fig, None
                i += 1
                continue
            nodes = conv_inlines(inl, ctx)
            s = plain(nodes)
            cm = CAPTION_RE.match(s)
            if cm and cm.group(1).lower().startswith("tab"):
                nxt_t = blocks[i + 1][1].get("t") if i + 1 < len(blocks) else None
                if nxt_t == "Table":
                    pending_table_caption = _split_label_text(nodes)
                    i += 1
                    continue
            if cm and cm.group(1).lower().startswith("fig") and last_figure is not None and not last_figure["caption"]:
                lab, cap = _split_label_text(nodes)
                last_figure["label"], last_figure["caption"] = lab, link_text(cap, cite_sup)
                i += 1
                continue
            if last_table is not None and not last_table["note"] and NOTE_AFTER_TABLE_RE.match(s):
                last_table["note"] = link_text(nodes, cite_sup)
                i += 1
                continue
            if any(x["t"] == "image" for x in nodes):
                flags.append(flag("inline-image", "manual",
                                  "An image sits inside a text paragraph; it is not placed as a figure.", "figures"))
                nodes = [x for x in nodes if x["t"] != "image"]
            emit({"t": "para", "c": link_text(nodes, cite_sup)})
            last_figure = last_table = None
            i += 1
            continue

        if t in ("BulletList", "OrderedList"):
            items = b["c"] if t == "BulletList" else b["c"][1]
            emit({"t": "list", "ordered": t == "OrderedList",
                  "items": [_simple_blocks(item, ctx, cite_sup) for item in items]})
        elif t == "BlockQuote":
            emit({"t": "quote", "blocks": _simple_blocks(b["c"], ctx, cite_sup)})
        elif t == "LineBlock":
            for line in b["c"]:
                emit({"t": "para", "c": link_text(conv_inlines(line, ctx), cite_sup)})
        elif t == "CodeBlock":
            emit({"t": "para", "c": [text(b["c"][1])]})
            flags.append(flag("code-block", "manual", "A preformatted block was converted to a paragraph.", "body"))
        elif t == "HorizontalRule":
            pass
        elif t == "RawBlock":
            flags.append(flag("raw-block", "warning", f"Unconverted {b['c'][0]} block dropped.", "body"))
        else:
            flags.append(flag("unknown-block", "warning", f"Unhandled block {t}.", "body"))
        i += 1

    record["body"] = body
    # supplementary links
    for node in _iter_nodes(body):
        if node.get("t") == "link" and re.search(r"supplement", node["url"] + plain(node.get("c")), re.I):
            if not any(s["url"] == node["url"] for s in record["supplementary"]):
                record["supplementary"].append({"label": f"Supplementary file {len(record['supplementary']) + 1}",
                                                "url": node["url"], "description": ""})


def _iter_nodes(blocks):
    for b in blocks:
        for n in b.get("c", []) if b.get("t") in ("para", "heading") else []:
            yield n
            yield from _iter_inner(n)
        if b.get("t") == "list":
            for item in b["items"]:
                yield from _iter_nodes(item)
        if b.get("t") == "quote":
            yield from _iter_nodes(b["blocks"])


def _iter_inner(n):
    for c in n.get("c", []) if isinstance(n.get("c"), list) else []:
        yield c
        yield from _iter_inner(c)


def _simple_blocks(blocks: list, ctx: Ctx, cite_sup: bool) -> list[dict]:
    out = []
    for _, b in unwrap(blocks):
        inl = block_inlines(b)
        if inl is not None:
            out.append({"t": "para", "c": link_text(conv_inlines(inl, ctx), cite_sup)})
        elif b.get("t") in ("BulletList", "OrderedList"):
            items = b["c"] if b["t"] == "BulletList" else b["c"][1]
            out.append({"t": "list", "ordered": b["t"] == "OrderedList",
                        "items": [_simple_blocks(it, ctx, cite_sup) for it in items]})
    return out


def _pandoc_caption(table_block: dict) -> list:
    cap = table_block["c"][1]  # [short, blocks]
    inl = []
    for blk in cap[1] or []:
        if blk.get("t") in ("Para", "Plain"):
            inl.extend(blk["c"])
    return inl


def _figure_caption(fig_block: dict) -> list:
    cap = fig_block["c"][1]
    inl = []
    for blk in cap[1] or []:
        if blk.get("t") in ("Para", "Plain"):
            inl.extend(blk["c"])
    return inl


def _first_image(blocks: list) -> dict | None:
    for _, b in unwrap(blocks):
        for x in block_inlines(b) or []:
            if x.get("t") == "Image":
                return x
    return None


def _make_figure(record, img, cap_nodes, auto_n, tmp: Path, media_out: Path, flags, cite_sup) -> dict | None:
    if img is None:
        return None
    attr, alt_inl, (src, _title) = img["c"]
    alt = plain(conv_inlines(alt_inl, Ctx())).strip()
    label, cap = _split_label_text(cap_nodes) if cap_nodes else ("", [])
    num = re.search(r"\d+", label).group(0) if label else str(auto_n)
    fid = f"fig{num}"
    if any(f["id"] == fid for f in record["figures"]):
        flags.append(flag("duplicate-figure", "blocked", f"Two figures are labelled Figure {num}.", "figures"))
        fid = f"fig{num}-{auto_n}"
    src_path = Path(src)
    if not src_path.is_absolute():
        src_path = tmp / src
    dest_name = None
    if src_path.is_file():
        dest_name = f"{fid}{src_path.suffix.lower()}"
        shutil.copyfile(src_path, media_out / dest_name)
        if src_path.suffix.lower() in (".emf", ".wmf", ".tif", ".tiff"):
            flags.append(flag("figure-format", "blocked",
                              f"Figure {num} is a {src_path.suffix} file, which neither PDF nor HTML output can show. "
                              "Supply a PNG, JPEG or SVG.", "figures"))
    else:
        flags.append(flag("missing-media", "blocked", f"The image for Figure {num} is missing from the manuscript.", "figures"))
    width_attr = dict(attr[2]).get("width", "")
    wide = False
    m = re.match(r"([\d.]+)in", width_attr)
    if m and float(m.group(1)) > 4.0:
        wide = True
    if alt and re.fullmatch(r"(image|picture|figure)?\s*\d*(\.\w{3,4})?", alt, re.I):
        alt = ""
    fig = {"id": fid, "label": label or f"Figure {num}", "caption": link_text(cap, cite_sup), "alt": alt,
           "src": f"media/{dest_name}" if dest_name else None, "wide": wide, "alt_source": "manuscript" if alt else None}
    if not label:
        flags.append(flag("figure-caption-missing", "blocked", f"Figure {num} has no caption.", "figures"))
    record["figures"].append(fig)
    return fig


def _references(record: dict, blocks: list, ctx: Ctx) -> None:
    refs = []
    items: list[list] = []
    for _, b in blocks:
        t = b.get("t")
        if t == "Header":
            break
        if t in ("Para", "Plain"):
            items.append(b["c"])
        elif t == "OrderedList":
            for it in b["c"][1]:
                for _, ib in unwrap(it):
                    if block_inlines(ib) is not None:
                        items.append(block_inlines(ib))
        elif t == "BulletList":
            for it in b["c"]:
                for _, ib in unwrap(it):
                    if block_inlines(ib) is not None:
                        items.append(block_inlines(ib))
    for k, inl in enumerate(items, start=1):
        nodes = conv_inlines(inl, ctx)
        raw_full = plain(nodes).strip()
        if not raw_full:
            continue
        label, raw = references.split_label(raw_full)
        if label and int(label) != len(refs) + 1:
            record["flags"].append(flag("reference-numbering", "blocked",
                                        f"Reference numbered {label} appears in position {len(refs) + 1}.", "references"))
        parsed = references.parse_reference(raw)
        ref = {"id": f"ref{len(refs) + 1}", "label": str(len(refs) + 1), "raw": raw,
               "parse_status": parsed["status"], "parse_reason": parsed["reason"],
               "csl": parsed["csl"] if parsed["status"] == "parsed" else None,
               "csl_candidate": parsed["csl"] if parsed["status"] == "partial" else None,
               "doi": parsed["doi"]}
        refs.append(ref)
    record["references"] = refs
