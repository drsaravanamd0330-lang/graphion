"""The typeset article as a Word file, for authors and editors who correct
proofs in Word.

It is built from the same revision as the PDF: the front matter comes from
the Typst view (so the "[Insert ...]" placeholders, header and masthead are
the ones the PDF prints) and the body, declarations and references from the
revision's full-text HTML, converted by Pandoc. The layout follows the
profile: A4, its margins, its body font, one column for the front matter and
the profile's column count for the body.
"""
from __future__ import annotations

import copy
import datetime as dt
import subprocess
import tempfile
from pathlib import Path

from lxml import html as lhtml

from . import config
from .model import plain

PT_PER_MM = 72 / 25.4


def _mm(v, default: float) -> float:
    """"20mm", "2cm", "56pt", or a number of points (the Typst view's unit)."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v) / PT_PER_MM
    v = str(v).strip()
    for unit, f in (("mm", 1), ("cm", 10), ("pt", 1 / PT_PER_MM), ("in", 25.4)):
        if v.endswith(unit):
            return float(v[: -len(unit)]) * f
    return float(v)


def _pandoc() -> str:
    p = config.pandoc_path()
    if not p:
        raise RuntimeError("Pandoc is needed for the Word export")
    return p


def _hex(c: str):
    from docx.shared import RGBColor
    c = (c or "#000000").lstrip("#")
    return RGBColor(int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def _reference_docx(path: Path, view: dict) -> None:
    """Pandoc's default reference document restyled for the journal."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    data = subprocess.run([_pandoc(), "--print-default-data-file", "reference.docx"], capture_output=True, check=True).stdout
    path.write_bytes(data)
    doc = Document(str(path))
    by_name = {s.name: s for s in doc.styles}   # python-docx's own lookup mistranslates "Heading 1"
    st = view["style"]
    body_font, head_font = st["fonts"]["body"], st["fonts"]["heading"]
    size = float(st.get("size", 10))
    head_color = _hex(st["colors"].get(st["house"].get("heading_color", "primary"), "#000000"))
    indent = float(st["house"].get("first_line_indent", 1.0))
    for name in ("Normal", "Body Text", "First Paragraph", "Compact", "Block Text"):
        s = by_name.get(name)
        if s is None:
            continue
        s.font.name, s.font.size = body_font, Pt(size)
        pf = s.paragraph_format
        pf.space_before, pf.space_after = Pt(0), Pt(0)
        pf.line_spacing = 1.0
        if name in ("Body Text", "First Paragraph"):
            pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        if name == "Body Text":
            pf.first_line_indent = Pt(size * indent)
    # The full-text page sets sections as h2 and subsections as h3.
    for lvl, sz, italic in ((1, size + 2, False), (2, size + 2, False), (3, size, True), (4, size, True)):
        s = by_name[f"Heading {lvl}"]
        s.font.size, s.font.bold, s.font.italic = Pt(sz), True, italic
        _set_font(s, head_font)
        s.font.color.rgb = head_color
        s.paragraph_format.space_before, s.paragraph_format.space_after = Pt(8 if lvl <= 2 else 5), Pt(2)
        s.paragraph_format.keep_with_next = True
    for name in ("Normal", "Body Text", "First Paragraph", "Compact", "Block Text", "Bibliography", "Footnote Text"):
        if name in by_name:
            _set_font(by_name[name], body_font)
    if "Hyperlink" in by_name:
        h = by_name["Hyperlink"]
        h.font.color.rgb = _hex(st["colors"].get("link") or st["colors"].get("primary"))
        h.font.underline = False
    for name in ("Table", "Table Caption", "Image Caption", "Caption"):
        s = by_name.get(name)
        if s is None:
            continue
        if s.type == 1 or name == "Table":
            try:
                s.font.name, s.font.size = body_font, Pt(size - 2)
            except AttributeError:
                pass
    doc.save(str(path))


def _set_font(style, name: str) -> None:
    """Set every script slot and drop theme fonts, which would win otherwise."""
    from docx.oxml.ns import qn
    rpr = style.element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = rpr.makeelement(qn("w:rFonts"), {})
        rpr.insert(0, rf)
    for k in list(rf.attrib):
        if k.endswith("Theme"):
            del rf.attrib[k]
    for slot in ("ascii", "hAnsi", "cs", "eastAsia"):
        rf.set(qn(f"w:{slot}"), name)
    col = rpr.find(qn("w:color"))
    if col is not None:
        for k in list(col.attrib):
            if "theme" in k.lower():
                del col.attrib[k]


def _body_html(fulltext: Path) -> str:
    """The part of the full-text page that is article content."""
    tree = lhtml.fromstring(fulltext.read_text(encoding="utf-8"))
    # A table figure becomes caption, table, note, in that order, with the
    # caption and note in their own styles; Pandoc's Figure output would put
    # the caption under the table.
    for fig in tree.xpath('//figure[contains(@class,"table")]'):
        new = []
        for cap in fig.xpath("./figcaption"):
            d = lhtml.Element("div", {"data-custom-style": "Table Caption"})
            p = lhtml.Element("p")
            d.append(p)
            p.text, p[:] = cap.text, list(cap)
            new.append(d)
        new += fig.xpath(".//table")
        for note in fig.xpath('.//p[contains(@class,"table-note")]'):
            d = lhtml.Element("div", {"data-custom-style": "Table Note"})
            d.append(note)
            new.append(d)
        parent, i = fig.getparent(), fig.getparent().index(fig)
        tail = fig.tail
        parent.remove(fig)
        for k, el in enumerate(new):
            parent.insert(i + k, el)
        if new:
            new[-1].tail = (new[-1].tail or "") + (tail or "")
    parts = []
    for xp in ('//div[@id="body"]', '//section[contains(@class,"declarations")]',
               '//section[@role="doc-bibliography"]', '//section[@role="doc-endnotes"]'):
        for el in tree.xpath(xp):
            for a in el.xpath('.//a[@role="doc-backlink" and not(contains(@class,"backref"))]'):
                a.drop_tree()   # footnote back-links; a reference's back-link is kept
            parts.append(lhtml.tostring(el, encoding="unicode"))
    return "<html><body>" + "\n".join(parts) + "</body></html>"


def _sect_cols(sect_pr, n: int, gap_mm: float):
    from docx.oxml.ns import qn
    cols = sect_pr.find(qn("w:cols"))
    if cols is None:
        cols = sect_pr.makeelement(qn("w:cols"), {})
        sect_pr.append(cols)
    cols.set(qn("w:num"), str(n))
    cols.set(qn("w:space"), str(int(gap_mm * PT_PER_MM * 20)))


def build_docx(view: dict, fulltext: Path, logo: Path | None, out: Path, timestamp: dt.datetime) -> None:
    from docx import Document
    from docx.enum.section import WD_SECTION
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt

    st = view["style"]
    H = st["house"]
    size = float(st.get("size", 10))
    accent = _hex(st["colors"].get("accent", "#000000"))
    ink = _hex(st["colors"].get("ink", "#000000"))
    with tempfile.TemporaryDirectory() as tmp:
        ref = Path(tmp) / "reference.docx"
        _reference_docx(ref, view)
        src = Path(tmp) / "body.html"
        src.write_text(_body_html(fulltext), encoding="utf-8")
        body = Path(tmp) / "body.docx"
        subprocess.run([_pandoc(), str(src), "-f", "html", "-t", "docx", "--reference-doc", str(ref),
                        "--resource-path", str(fulltext.parent), "-o", str(body)], check=True, capture_output=True)
        doc = Document(str(body))

    sec = doc.sections[0]
    m = st.get("margins") or {}
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.top_margin, sec.bottom_margin = Mm(_mm(m.get("top"), 20)), Mm(_mm(m.get("bottom"), 20))
    sec.left_margin, sec.right_margin = Mm(_mm(m.get("inside"), 16)), Mm(_mm(m.get("outside"), 16))
    text_w = sec.page_width - sec.left_margin - sec.right_margin

    # Tables: smaller type, full text width, header row repeated. A table the
    # PDF sets across both columns sits in a one-column section of its own.
    wide = [bool(v.get("wide")) for v in (view.get("tables") or {}).values()]
    for n, t in enumerate(doc.tables):
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        tw = t._tbl.tblPr.find(qn("w:tblW"))
        if tw is None:
            tw = OxmlElement("w:tblW")
            t._tbl.tblPr.append(tw)
        tw.set(qn("w:type"), "pct"), tw.set(qn("w:w"), "5000")
        if n < len(wide) and wide[n] and int(st.get("columns", 2)) > 1:
            _isolate_wide(t._tbl, sec._sectPr, int(st.get("columns", 2)), _mm(st.get("column_gap"), 6))
        for i, row in enumerate(t.rows):
            if i == 0:
                trpr = row._tr.get_or_add_trPr()
                trpr.append(OxmlElement("w:tblHeader"))
            for cell in row.cells:
                for p in cell.paragraphs:
                    p.paragraph_format.first_line_indent = Pt(0)
                    for r_el in p._p.iter(qn("w:r")):   # includes runs inside links
                        rpr = r_el.get_or_add_rPr()
                        for tag in ("w:sz", "w:szCs"):
                            e = rpr.find(qn(tag))
                            if e is None:
                                e = OxmlElement(tag)
                                rpr.append(e)
                            e.set(qn("w:val"), str(int(float(st.get("table_size", size - 2)) * 2)))
                        if i == 0 and rpr.find(qn("w:b")) is None:
                            rpr.append(OxmlElement("w:b"))

    # References: ragged right and a size smaller, as in the PDF.
    # Each reference and table caption gets the bookmark its in-text links
    # point at (#ref3, #tab1), which Pandoc drops from list items.
    ref_ids = [r["id"] for r in view.get("references") or []]
    tab_ids = list((view.get("tables") or {}).keys())
    in_refs, bm = False, [1000]
    for p in doc.paragraphs:
        if p.style.name.startswith("Heading"):
            in_refs = p.text.strip().lower() == "references"
            continue
        if in_refs and p.text.strip():
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT   # the list keeps its own hanging indent
            _run_size(p._p, size - 1.5)
            if ref_ids:
                _bookmark(p._p, ref_ids.pop(0), bm)
        elif "TableCaption" in _pstyle(p._p) and tab_ids:
            _bookmark(p._p, tab_ids.pop(0), bm)

    # ---- front matter, inserted before the first body paragraph
    anchor = doc.paragraphs[0]
    sans = st["fonts"].get("sans") or st["fonts"]["body"]

    def para(text="", *, bold=False, italic=False, sz=None, color=None, align=None, font=None, upper=False, after=2):
        p = anchor.insert_paragraph_before()
        p.paragraph_format.first_line_indent = Pt(0)
        p.paragraph_format.space_after = Pt(after)
        if align:
            p.alignment = align
        if text:
            r = p.add_run(text.upper() if upper else text)
            r.bold, r.italic = bold, italic
            r.font.size = Pt(sz or size)
            if color is not None:
                r.font.color.rgb = color
            if font:
                r.font.name = font
        return p

    def run(p, text, *, bold=False, italic=False, sz=None, sup=False, color=None, font=None):
        r = p.add_run(text)
        r.bold, r.italic = bold, italic
        r.font.size = Pt(sz or size)
        r.font.superscript = sup
        if color is not None:
            r.font.color.rgb = color
        if font:
            r.font.name = font
        return r

    if view.get("watermark"):
        para(view["watermark"] + ": not for publication", bold=True, sz=9, color=_hex("#c00000"),
             align=WD_ALIGN_PARAGRAPH.CENTER, font=sans, after=4)

    # Masthead: logo | journal name, URL and ISSNs, in one ruled box.
    mh = view["masthead"]
    tbl = doc.add_table(rows=1, cols=2 if logo else 1)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl.autofit = False
    from docx.shared import Emu
    widths = [Mm(24), Emu(text_w - Mm(24))] if logo else [Emu(text_w)]
    for col, w in zip(tbl.columns, widths):
        col.width = w
    grid = tbl._tbl.tblGrid
    for gc, w in zip(grid.findall(qn("w:gridCol")), widths):
        gc.set(qn("w:w"), str(int(w.twips)))
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideV"):
        e = OxmlElement(f"w:{edge}")
        e.set(qn("w:val"), "single"), e.set(qn("w:sz"), "8"), e.set(qn("w:color"), "000000")
        borders.append(e)
    tbl._tbl.tblPr.append(borders)
    cells = tbl.rows[0].cells
    if logo:
        cells[0].width = Mm(24)
        lp = cells[0].paragraphs[0]
        lp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        lp.add_run().add_picture(_raster_logo(logo), width=Mm(19))
    c = cells[-1]
    c.width = text_w - (Mm(24) if logo else 0)
    title = mh["journal_title"]
    pre, main = ("International Journal of", title[25:]) if title.startswith("International Journal of ") else (None, title)
    p = c.paragraphs[0]
    if pre:
        run(p, pre, sz=8, font=sans, color=ink)
        p = c.add_paragraph()
    run(p, main, sz=20, font=sans, color=ink)
    p = c.add_paragraph()
    run(p, (mh.get("url") or "").replace("https://", "").replace("http://", ""), sz=7.5, font=sans)
    for line in mh.get("issn_lines") or []:
        p2 = c.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        run(p2, line, sz=7.5, font=sans)
    for p in c.paragraphs:
        p.paragraph_format.first_line_indent = Pt(0)
    anchor._p.addprevious(tbl._tbl)

    p = para(after=0)
    p.paragraph_format.tab_stops.add_tab_stop(text_w, WD_TAB_ALIGNMENT.RIGHT)
    run(p, view["article_type_label"].upper(), bold=True, sz=8, color=accent, font=sans)
    run(p, "\t" + view.get("article_line", ""), sz=7.5, font=sans)
    if view.get("doi_url") or view.get("doi_placeholder"):
        para(view.get("doi_url") or f"DOI: {view['doi_placeholder']}", sz=7.5, font=sans, align=WD_ALIGN_PARAGRAPH.RIGHT)

    title_text = plain_inlines(view["title"])
    para(title_text, bold=True, sz=H.get("title_size", 17), upper=H.get("title_case") == "upper",
         color=_hex(st["colors"].get(H.get("heading_color", "primary"), "#000000")),
         align=WD_ALIGN_PARAGRAPH.JUSTIFY if H.get("title_align") == "justify" else None, after=6)
    p = para(after=2)
    for i, a in enumerate(view["authors"]):
        if i:
            run(p, ", ", sz=size + 0.5)
        run(p, a["name"], sz=size + 0.5)
        marks = (a.get("sup") or "") + ("*" if a.get("corresponding") else "")
        if marks:
            run(p, marks, sz=size + 0.5, sup=True)
    for af in view["affiliations"]:
        p = para(after=0)
        run(p, af["label"], sz=7.5, sup=True)
        run(p, af["text"], sz=7.5)
    if view.get("correspondence"):
        p = para(after=0)
        run(p, "*Correspondence: ", sz=7.5, bold=True)
        run(p, view["correspondence"], sz=7.5)
    if view.get("dates"):
        para(" · ".join(f"{k}: {v}" for k, v in view["dates"]), sz=7.5, after=6)

    ab = view.get("abstract")
    if ab:
        para(H.get("abstract_heading") or ab["heading"], bold=True, sz=9, after=2)
        for s in ab["sections"]:
            p = para(after=1, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
            if s.get("label"):
                run(p, s["label"] + ": ", bold=True, sz=size - 0.8)
            add_inlines(p, s["c"], size - 0.8)
        if view.get("keywords"):
            p = para(after=6, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
            run(p, H.get("keywords_label", "Keywords") + ": ", bold=True, sz=size - 0.8)
            run(p, view["keywords"], sz=size - 0.8)
        # Frame the abstract like the PDF: a box around its paragraphs.
        _frame_abstract(doc, anchor, accent)

    # Section break: front matter in one column, body in the profile's columns.
    brk = para(after=0)
    sp = copy.deepcopy(sec._sectPr)
    _sect_cols(sp, 1, 0)
    for tag in ("w:headerReference", "w:footerReference"):
        for e in sp.findall(qn(tag)):
            sp.remove(e)
    brk._p.get_or_add_pPr().append(sp)
    cols = int(st.get("columns", 2))
    _sect_cols(sec._sectPr, cols, _mm(st.get("column_gap"), 6))
    sec.start_type = WD_SECTION.CONTINUOUS

    # Header and footer on every page, as in the PDF.
    first = doc.sections[0]   # the body section after it is linked to it, so it repeats
    hp = first.header.paragraphs[0]
    hp.paragraph_format.tab_stops.add_tab_stop(text_w, WD_TAB_ALIGNMENT.RIGHT)
    run(hp, view["header"]["odd"], italic=H.get("header_style") == "left-italic", sz=7.5)
    run(hp, "\t", sz=7.5)
    _page_field(hp, size=7.5)
    fp = first.footer.paragraphs[0]
    lic = view.get("license")
    run(fp, (lic["statement"] + (" " + lic["url"] if lic.get("url") else "")) if lic else "No licence configured", sz=7, font=sans)

    cp = doc.core_properties
    cp.title = view.get("document_title") or title_text
    cp.author = "; ".join(view.get("meta_authors") or [])
    cp.keywords = view.get("keywords") or ""
    cp.created = cp.modified = timestamp.replace(tzinfo=None)
    cp.last_modified_by = "Journal Typesetting Studio"
    doc.save(str(out))


def add_inlines(p, nodes, sz, bold=False, italic=False, sup=False, sub=False):
    """Inline nodes as runs, keeping bold, italic, super- and subscript."""
    from docx.shared import Pt
    for n in nodes or []:
        t = n.get("t")
        if t in ("strong", "emph", "sup", "sub", "smallcaps", "span", "link", "underline"):
            add_inlines(p, n.get("c"), sz, bold or t == "strong", italic or t == "emph",
                        sup or t == "sup", sub or t == "sub")
            continue
        txt = plain([n])
        if not txt:
            continue
        r = p.add_run(txt)
        r.font.size = Pt(sz)
        r.bold, r.italic = bold or None, italic or None
        r.font.superscript, r.font.subscript = sup or None, sub or None


def plain_inlines(nodes) -> str:
    try:
        return plain(nodes)
    except Exception:
        return str(nodes or "")


def _isolate_wide(tbl, body_sect, cols: int, gap_mm: float) -> None:
    """Continuous section breaks around a table (with its caption above and
    note below) so it spans the page while the text keeps its columns."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    first, last = tbl, tbl
    prev, nxt = tbl.getprevious(), tbl.getnext()
    if prev is not None and prev.tag == qn("w:p") and "TableCaption" in _pstyle(prev):
        first = prev
    if nxt is not None and nxt.tag == qn("w:p") and "TableNote" in _pstyle(nxt):
        last = nxt

    def brk(n_cols):
        p = OxmlElement("w:p")
        ppr = OxmlElement("w:pPr")
        rpr = OxmlElement("w:rPr")
        sz = OxmlElement("w:sz")
        sz.set(qn("w:val"), "2")
        rpr.append(sz)
        sp = copy.deepcopy(body_sect)
        for e in list(sp):
            if e.tag in (qn("w:headerReference"), qn("w:footerReference"), qn("w:type")):
                sp.remove(e)
        t = OxmlElement("w:type")
        t.set(qn("w:val"), "continuous")
        sp.insert(0, t)
        _sect_cols(sp, n_cols, gap_mm)
        ppr.append(sp)
        ppr.append(rpr)
        p.append(ppr)
        return p

    first.addprevious(brk(cols))   # ends the columned text before the table
    last.addnext(brk(1))           # ends the one-column table section


def _bookmark(p_el, name: str, counter: list) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    counter[0] += 1
    start, end = OxmlElement("w:bookmarkStart"), OxmlElement("w:bookmarkEnd")
    start.set(qn("w:id"), str(counter[0])), start.set(qn("w:name"), name)
    end.set(qn("w:id"), str(counter[0]))
    ppr = p_el.find(qn("w:pPr"))
    if ppr is not None:
        ppr.addnext(start)
    else:
        p_el.insert(0, start)
    p_el.append(end)


def _run_size(p_el, pt: float) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    for r_el in p_el.iter(qn("w:r")):
        rpr = r_el.get_or_add_rPr()
        for tag in ("w:sz", "w:szCs"):
            e = rpr.find(qn(tag))
            if e is None:
                e = OxmlElement(tag)
                rpr.append(e)
            e.set(qn("w:val"), str(int(pt * 2)))


def _pstyle(p) -> str:
    from docx.oxml.ns import qn
    ps = p.find(qn("w:pPr") + "/" + qn("w:pStyle"))
    return ps.get(qn("w:val")) if ps is not None else ""


def _raster_logo(logo: Path):
    """Word takes PNG and JPEG; an SVG logo is drawn to PNG by Typst."""
    import io
    if logo.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".bmp"):
        return str(logo)
    import typst
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / logo.name).write_bytes(logo.read_bytes())
        (t / "logo.typ").write_text(f'#set page(width: auto, height: auto, margin: 0pt)\n#image("{logo.name}", width: 6cm)\n',
                                    encoding="utf-8")
        png = typst.compile(str(t / "logo.typ"), format="png", ppi=300)
    return io.BytesIO(png[0] if isinstance(png, list) else png)


def _page_field(p, size):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    for kind, text in (("begin", None), (None, " PAGE "), ("end", None)):
        r = p.add_run()
        r.font.size = Pt(size)
        if kind:
            f = OxmlElement("w:fldChar")
            f.set(qn("w:fldCharType"), kind)
            r._r.append(f)
        else:
            t = OxmlElement("w:instrText")
            t.set(qn("xml:space"), "preserve")
            t.text = text
            r._r.append(t)


def _frame_abstract(doc, anchor, color):
    """Put a double border round the paragraphs from the abstract heading
    down to the keywords (Word joins bordered neighbours into one box)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    body = anchor._p.getparent()
    idx = list(body).index(anchor._p)
    hexc = "%02X%02X%02X" % (color[0], color[1], color[2])
    ps = []
    for el in reversed(list(body)[:idx]):
        if el.tag != qn("w:p"):
            break
        ps.append(el)
        txt = "".join(t.text or "" for t in el.iter(qn("w:t")))
        if txt.strip().lower() in ("abstract",) or txt.strip().isupper() and txt.strip().startswith("ABSTRACT"):
            break
    for el in ps:
        ppr = el.get_or_add_pPr()
        bdr = OxmlElement("w:pBdr")
        for edge in ("top", "left", "bottom", "right"):
            e = OxmlElement(f"w:{edge}")
            e.set(qn("w:val"), "double"), e.set(qn("w:sz"), "12"), e.set(qn("w:space"), "4"), e.set(qn("w:color"), hexc)
            bdr.append(e)
        ppr.append(bdr)
