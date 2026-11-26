"""JATS XML (Journal Publishing 1.3) from the article record.

Everything maps from the same record the PDF and HTML were built from. Where
JATS has no faithful place for something, the exporter says so in the
returned ``losses`` list and the record keeps the information; nothing is
squeezed into a field it does not belong in.
"""
from __future__ import annotations

from pathlib import Path

from lxml import etree

from . import mathconv
from .model import DECLARATION_LABELS, find, plain
from .profiles import Profile
from .typst_render import cite_parts, locator
from .xmlutil import date_parts, schema

SCHEMA_FILE = "JATS-journalpublishing1-3d2-mathml3-nonamespace.xsd"
XLINK = "http://www.w3.org/1999/xlink"
MML = "http://www.w3.org/1998/Math/MathML"
ALI = "http://www.niso.org/schemas/ali/1.0/"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NSMAP = {"xlink": XLINK, "mml": MML, "ali": ALI}
ARTICLE_TYPES = {"original-research": "research-article", "review": "review-article",
                 "case-report": "case-report", "editorial": "editorial"}
NOTES_TYPE = {"funding": "funding-statement", "conflicts": "COI-statement", "contributions": "author-contribution",
              "data_availability": "data-availability", "ethics": "ethics-statement", "consent": "consent-statement",
              "trial_registration": "trial-registration"}


def E(tag, *children, **attrs):
    el = etree.Element(tag, {k.replace("__", ":").replace("_", "-") if not k.startswith("{") else k: str(v)
                             for k, v in attrs.items() if v is not None})
    for c in children:
        if c is None:
            continue
        if isinstance(c, str):
            if len(el):
                el[-1].tail = (el[-1].tail or "") + c
            else:
                el.text = (el.text or "") + c
        else:
            el.append(c)
    return el


class Builder:
    def __init__(self, record: dict, profile: Profile):
        self.r = record
        self.prof = profile
        self.losses: list[str] = []
        from .typst_render import _all_math
        exprs = _all_math(record)
        self.mathml = {e: m for e, m in zip(exprs, mathconv.to_mathml(exprs)) if m}
        self.labels = {x["id"]: x["label"] for x in record.get("references", [])}
        self.kinds = {**{f["id"]: "fig" for f in record["figures"]}, **{t["id"]: "table" for t in record["tables"]},
                      **{e["id"]: "disp-formula" for e in record["equations"]}}

    # -------------------------------------------------------------- inline
    def inline_into(self, parent, nodes, in_p=True):
        for n in nodes or []:
            t = n["t"]
            if t == "text":
                self._text(parent, n["v"])
            elif t in ("emph", "strong", "sup", "sub", "smallcaps"):
                tag = {"emph": "italic", "strong": "bold", "sup": "sup", "sub": "sub", "smallcaps": "sc"}[t]
                child = etree.SubElement(parent, tag)
                self.inline_into(child, n["c"], in_p)
            elif t == "link":
                child = etree.SubElement(parent, "ext-link", {"ext-link-type": "uri", f"{{{XLINK}}}href": n["url"]})
                self.inline_into(child, n["c"], in_p)
            elif t == "br":
                self._text(parent, " ")
            elif t == "cite":
                sup = (self.prof.data.get("references") or {}).get("in_text", "bracket") == "superscript"
                host = etree.SubElement(parent, "sup") if sup else parent
                if not sup:
                    self._text(host, "[")
                for txt, rid in cite_parts(n["refs"], self.labels):
                    if rid:
                        etree.SubElement(host, "xref", {"ref-type": "bibr", "rid": rid}).text = txt
                    else:
                        self._text(host, txt)
                if not sup:
                    self._text(host, "]")
            elif t == "xref":
                if n["target"] in self.kinds:
                    etree.SubElement(parent, "xref", {"ref-type": self.kinds[n["target"]], "rid": n["target"]}).text = n["v"]
                else:
                    self._text(parent, n["v"])
            elif t == "math":
                f = etree.SubElement(parent, "inline-formula")
                self._formula(f, n["tex"])
            elif t == "fnref":
                k = [x["id"] for x in self.r.get("footnotes", [])].index(n["id"]) + 1
                etree.SubElement(parent, "xref", {"ref-type": "fn", "rid": n["id"]}).text = str(k)

    @staticmethod
    def _text(parent, s):
        if len(parent):
            parent[-1].tail = (parent[-1].tail or "") + s
        else:
            parent.text = (parent.text or "") + s

    def _formula(self, parent, tex):
        alts = etree.SubElement(parent, "alternatives")
        etree.SubElement(alts, "tex-math").text = tex
        mm = self.mathml.get(tex)
        if mm:
            m = etree.fromstring(mm)
            alts.append(m)

    def p(self, nodes):
        el = etree.Element("p")
        self.inline_into(el, nodes)
        return el

    # -------------------------------------------------------------- blocks
    def blocks(self, parent, blocks):
        """Nest flat headings into <sec> elements."""
        stack = [(0, parent)]
        for b in blocks or []:
            t = b["t"]
            if t == "heading":
                while stack[-1][0] >= b["level"]:
                    stack.pop()
                sec = etree.SubElement(stack[-1][1], "sec", {"id": f"sec-{b['id']}"} if b.get("id") else {})
                title = etree.SubElement(sec, "title")
                self.inline_into(title, b["c"])
                stack.append((b["level"], sec))
                continue
            target = stack[-1][1]
            el = self.block(b)
            if el is not None:
                target.append(el)

    def block(self, b):
        t = b["t"]
        if t == "para":
            return self.p(b["c"])
        if t == "list":
            lst = etree.Element("list", {"list-type": "order" if b["ordered"] else "bullet"})
            for item in b["items"]:
                li = etree.SubElement(lst, "list-item")
                for sub in item:
                    el = self.block(sub)
                    if el is not None:
                        li.append(el)
                if not len(li):
                    etree.SubElement(li, "p")
            return lst
        if t == "quote":
            q = etree.Element("disp-quote")
            for sub in b["blocks"]:
                el = self.block(sub)
                if el is not None:
                    q.append(el)
            return q
        if t == "figure":
            f = find(self.r, "figure", b["id"])
            fig = E("fig", E("label", f["label"]), id=f["id"])
            cap = etree.SubElement(fig, "caption")
            cap.append(self.p(f["caption"]))
            if f.get("src"):
                g = etree.SubElement(fig, "graphic", {f"{{{XLINK}}}href": f["src"]})
                if f.get("alt"):
                    etree.SubElement(g, "alt-text").text = f["alt"]
            else:
                self.losses.append(f"{f['label']}: no image file, so no <graphic>")
            return fig
        if t == "table":
            tb = find(self.r, "table", b["id"])
            wrap = E("table-wrap", E("label", tb["label"]), id=tb["id"])
            cap = etree.SubElement(wrap, "caption")
            cap.append(self.p(tb["caption"]))
            table = etree.SubElement(wrap, "table")
            thead = etree.SubElement(table, "thead")
            for row in tb["head"]:
                tr = etree.SubElement(thead, "tr")
                for c in row:
                    self.inline_into(etree.SubElement(tr, "th"), c)
            tbody = etree.SubElement(table, "tbody")
            for row in tb["rows"]:
                tr = etree.SubElement(tbody, "tr")
                for c in row:
                    self.inline_into(etree.SubElement(tr, "td"), c)
            if tb.get("note"):
                foot = etree.SubElement(wrap, "table-wrap-foot")
                foot.append(self.p(tb["note"]))
            return wrap
        if t == "equation":
            e = find(self.r, "equation", b["id"])
            df = E("disp-formula", E("label", e["label"]), id=e["id"])
            self._formula(df, e["tex"])
            return df
        return None

    # -------------------------------------------------------------- document
    def build(self) -> etree._Element:
        r, p = self.r, self.prof
        art = etree.Element("article", nsmap=NSMAP)
        art.set("article-type", ARTICLE_TYPES.get(r["article_type"], "other"))
        art.set("dtd-version", "1.3")
        art.set(f"{{{XML_NS}}}lang", r.get("language", "en"))
        front = etree.SubElement(art, "front")
        j = r["journal"]
        jm = etree.SubElement(front, "journal-meta")
        etree.SubElement(jm, "journal-id", {"journal-id-type": "publisher-id"}).text = j.get("abbreviation") or p.id
        tg = etree.SubElement(jm, "journal-title-group")
        etree.SubElement(tg, "journal-title").text = j.get("title")
        if j.get("abbreviation"):
            etree.SubElement(tg, "abbrev-journal-title").text = j["abbreviation"]
        exported_issn = False
        for key, fmt in (("issn_print", "print"), ("issn_online", "electronic")):
            if j.get(key) and j.get(f"{key}_verified"):
                etree.SubElement(jm, "issn", {"publication-format": fmt}).text = j[key]
                exported_issn = True
            elif j.get(key):
                self.losses.append(f"{fmt} ISSN {j[key]} is not marked verified in the profile, so it is not exported")
        if not exported_issn:
            self.losses.append("no ISSN configured: JATS Journal Publishing requires one, so the file cannot validate until a verified ISSN is entered in the journal profile")
        pub_el = etree.SubElement(jm, "publisher")
        etree.SubElement(pub_el, "publisher-name").text = j.get("publisher") or ""
        loc = (p.data.get("jats") or {}).get("publisher_loc")
        if loc:
            etree.SubElement(pub_el, "publisher-loc").text = loc

        am = etree.SubElement(front, "article-meta")
        pub = r["publication"]
        if pub.get("doi"):
            etree.SubElement(am, "article-id", {"pub-id-type": "doi"}).text = pub["doi"]
        etree.SubElement(am, "article-id", {"pub-id-type": "publisher-id"}).text = r["article_id"]
        cats = etree.SubElement(am, "article-categories")
        sg = etree.SubElement(cats, "subj-group", {"subj-group-type": "heading"})
        etree.SubElement(sg, "subject").text = p.article_type(r["article_type"]).get("label", r["article_type"])
        tgrp = etree.SubElement(am, "title-group")
        self.inline_into(etree.SubElement(tgrp, "article-title"), r["title"]["en"])
        if r["title"].get("original"):
            ttg = etree.SubElement(tgrp, "trans-title-group", {f"{{{XML_NS}}}lang": r["title"].get("original_lang") or "und"})
            self.inline_into(etree.SubElement(ttg, "trans-title"), r["title"]["original"])

        cg = etree.SubElement(am, "contrib-group")
        aff_label = {a["id"]: a["label"] for a in r["affiliations"]}
        corr_ids = []
        for i, a in enumerate(r["authors"]):
            c = etree.SubElement(cg, "contrib", {"contrib-type": "author"})
            if a.get("corresponding"):
                c.set("corresp", "yes")
            if a.get("orcid") and a.get("orcid_verified"):
                etree.SubElement(c, "contrib-id", {"contrib-id-type": "orcid", "authenticated": "false"}).text = f"https://orcid.org/{a['orcid']}"
            elif a.get("orcid"):
                self.losses.append(f"{a['full_name']}: ORCID not verified, so not exported")
            if a.get("name_roman"):
                na = etree.SubElement(c, "name-alternatives")
                nm = etree.SubElement(na, "name", {"name-style": "western"})
                etree.SubElement(nm, "surname").text = a["name_roman"].get("family", "") if isinstance(a["name_roman"], dict) else a["name_roman"]
                etree.SubElement(na, "string-name", {f"{{{XML_NS}}}lang": r.get("language", "en")}).text = a["full_name"]
            else:
                nm = etree.SubElement(c, "name")
                etree.SubElement(nm, "surname").text = a.get("family") or a["full_name"]
                if a.get("given"):
                    etree.SubElement(nm, "given-names").text = a["given"]
            for x in a.get("affiliations", []):
                etree.SubElement(c, "xref", {"ref-type": "aff", "rid": x}).text = aff_label.get(x, "")
            if a.get("corresponding"):
                cid = f"cor{len(corr_ids) + 1}"
                corr_ids.append((cid, a))
                etree.SubElement(c, "xref", {"ref-type": "corresp", "rid": cid}).text = "*"
        for af in r["affiliations"]:
            ael = etree.SubElement(cg, "aff", {"id": af["id"]})
            etree.SubElement(ael, "label").text = af["label"]
            parts = []
            if af.get("department"):
                parts.append(("institution", {"content-type": "dept"}, af["department"]))
            parts.append(("institution", {}, af.get("institution") or af["raw"]))
            if af.get("city"):
                parts.append(("addr-line", {"content-type": "city"}, af["city"]))
            if af.get("country"):
                parts.append(("country", {}, af["country"]))
            for i, (tag, attrs, val) in enumerate(parts):
                el = etree.SubElement(ael, tag, attrs)
                el.text = val
                if i < len(parts) - 1:
                    el.tail = ", "
        if corr_ids:
            an = etree.SubElement(am, "author-notes")
            for cid, a in corr_ids:
                corr = etree.SubElement(an, "corresp", {"id": cid})
                corr.text = f"* {a['full_name']}" + (", " if a.get("email") else "")
                if a.get("email"):
                    etree.SubElement(corr, "email").text = a["email"]

        dates = r["dates"]
        published = dates.get("published") or {}
        if published.get("value") and published.get("verified"):
            y, m, d = date_parts(published["value"])
            pd = etree.SubElement(am, "pub-date", {"publication-format": "electronic", "date-type": "pub"})
            etree.SubElement(pd, "day").text = d
            etree.SubElement(pd, "month").text = m
            etree.SubElement(pd, "year").text = y
        elif pub.get("year"):
            pd = etree.SubElement(am, "pub-date", {"publication-format": "electronic", "date-type": "pub"})
            etree.SubElement(pd, "year").text = str(pub["year"])
        if pub.get("volume"):
            etree.SubElement(am, "volume").text = str(pub["volume"])
        if pub.get("issue"):
            etree.SubElement(am, "issue").text = str(pub["issue"])
        if pub.get("first_page"):
            etree.SubElement(am, "fpage").text = str(pub["first_page"])
            if pub.get("last_page"):
                etree.SubElement(am, "lpage").text = str(pub["last_page"])
        elif pub.get("article_number"):
            etree.SubElement(am, "elocation-id").text = locator(r, p) or str(pub["article_number"])
        for s in r.get("supplementary") or []:
            sm = etree.SubElement(am, "supplementary-material", {f"{{{XLINK}}}href": s["url"], "content-type": "local-data"})
            etree.SubElement(sm, "label").text = s["label"]
            if s.get("description"):
                cap = etree.SubElement(sm, "caption")
                etree.SubElement(cap, "p").text = s["description"]
        hist = [(k, v) for k, v in dates.items() if k in ("received", "revised", "accepted") and v and v.get("value") and v.get("verified")]
        if hist:
            h = etree.SubElement(am, "history")
            for k, v in hist:
                y, m, d = date_parts(v["value"])
                de = etree.SubElement(h, "date", {"date-type": "rev-recd" if k == "revised" else k})
                etree.SubElement(de, "day").text = d
                etree.SubElement(de, "month").text = m
                etree.SubElement(de, "year").text = y
        lic = r.get("license")
        if lic:
            perm = etree.SubElement(am, "permissions")
            if lic.get("copyright_holder"):
                if lic.get("copyright_year"):
                    etree.SubElement(perm, "copyright-year").text = str(lic["copyright_year"])
                etree.SubElement(perm, "copyright-holder").text = lic["copyright_holder"]
            le = etree.SubElement(perm, "license", {f"{{{XLINK}}}href": lic["url"]} if lic.get("url") else {})
            if lic.get("url"):
                etree.SubElement(le, f"{{{ALI}}}license_ref").text = lic["url"]
            etree.SubElement(le, "license-p").text = lic["statement"]
        urls = r.get("urls") or {}
        if urls.get("pdf_live"):
            etree.SubElement(am, "self-uri", {f"{{{XLINK}}}href": urls["pdf_live"], "content-type": "pdf"})
        if r["abstract"]["sections"]:
            ab = etree.SubElement(am, "abstract")
            for s in r["abstract"]["sections"]:
                if s.get("label"):
                    sec = etree.SubElement(ab, "sec")
                    etree.SubElement(sec, "title").text = s["label"]
                    sec.append(self.p(s["c"]))
                else:
                    ab.append(self.p(s["c"]))
        if r.get("keywords"):
            kg = etree.SubElement(am, "kwd-group", {"kwd-group-type": "author"})
            for k in r["keywords"]:
                etree.SubElement(kg, "kwd").text = k
        if r["declarations"].get("funding"):
            fg = etree.SubElement(am, "funding-group")
            fs = etree.SubElement(fg, "funding-statement")
            fs.text = " ".join(plain(b.get("c")) for b in r["declarations"]["funding"] if b["t"] == "para")
        body = etree.SubElement(art, "body")
        self.blocks(body, r["body"])
        if not len(body):
            etree.SubElement(body, "p")

        back = etree.SubElement(art, "back")
        decl = r["declarations"]
        heads = r.get("declaration_headings") or {}
        if decl.get("acknowledgements"):
            ack = etree.SubElement(back, "ack")
            etree.SubElement(ack, "title").text = heads.get("acknowledgements", "Acknowledgements")
            self.blocks(ack, decl["acknowledgements"])
        for key in DECLARATION_LABELS:
            if key == "acknowledgements" or not decl.get(key):
                continue
            n = etree.SubElement(back, "notes", {"notes-type": NOTES_TYPE[key]})
            etree.SubElement(n, "title").text = heads.get(key) or DECLARATION_LABELS[key]
            self.blocks(n, decl[key])
        if r.get("footnotes"):
            fg = etree.SubElement(back, "fn-group")
            for i, f in enumerate(r["footnotes"], start=1):
                fn = etree.SubElement(fg, "fn", {"id": f["id"]})
                etree.SubElement(fn, "label").text = str(i)
                fn.append(self.p(f["c"]))
        if r.get("references"):
            rl = etree.SubElement(back, "ref-list")
            etree.SubElement(rl, "title").text = "References"
            from .typst_render import epub_date
            for ref in r["references"]:
                re_ = etree.SubElement(rl, "ref", {"id": ref["id"]})
                typed = ref["raw"] + (f" Epub {epub_date(ref['epub'])}." if ref.get("epub") else "")
                etree.SubElement(re_, "label").text = ref["label"]
                csl = ref.get("csl")
                if csl and csl.get("type") == "article-journal":
                    alt = etree.SubElement(re_, "citation-alternatives")
                    alt.append(self.element_citation(csl))
                    etree.SubElement(alt, "mixed-citation", {"publication-type": "journal"}).text = typed
                else:
                    mc = etree.SubElement(re_, "mixed-citation")
                    mc.text = typed
        if not len(back):
            art.remove(back)
        return art

    def element_citation(self, csl: dict):
        ec = etree.Element("element-citation", {"publication-type": "journal"})
        pg = etree.SubElement(ec, "person-group", {"person-group-type": "author"})
        for a in csl.get("author", []):
            nm = etree.SubElement(pg, "name")
            etree.SubElement(nm, "surname").text = a.get("family", "")
            if a.get("given"):
                etree.SubElement(nm, "given-names").text = a["given"].replace(" ", "")
        etree.SubElement(ec, "article-title").text = csl.get("title", "")
        etree.SubElement(ec, "source").text = csl.get("container-title", "")
        year = (csl.get("issued", {}).get("date-parts") or [[None]])[0][0]
        if year:
            etree.SubElement(ec, "year").text = str(year)
        if csl.get("volume"):
            etree.SubElement(ec, "volume").text = str(csl["volume"])
        if csl.get("issue"):
            etree.SubElement(ec, "issue").text = str(csl["issue"])
        if csl.get("page"):
            parts = str(csl["page"]).split("-")
            etree.SubElement(ec, "fpage").text = parts[0]
            if len(parts) > 1:
                etree.SubElement(ec, "lpage").text = parts[-1]
        if csl.get("DOI"):
            etree.SubElement(ec, "pub-id", {"pub-id-type": "doi"}).text = csl["DOI"]
        return ec


def build(record: dict, profile: Profile) -> bytes:
    b = Builder(record, profile)
    root = b.build()
    xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True,
                         doctype='<!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) Journal Publishing DTD v1.3 20210610//EN" '
                                 '"JATS-journalpublishing1-3.dtd">')
    return xml


def mapping_losses(record: dict, profile: Profile) -> list[str]:
    b = Builder(record, profile)
    b.build()
    return b.losses


def validate(path: Path) -> dict:
    """Validate against the vendored JATS 1.3d2 XSD.

    The file declares dtd-version="1.3", the published value. The only XSD
    that can be vendored offline is the 1.3d2 release, whose enumeration lists
    "1.3d2" but not "1.3", so that one attribute is compared as "1.3d2" in an
    in-memory copy. The note says so in every result.
    """
    try:
        doc = etree.parse(str(path))
    except etree.XMLSyntaxError as exc:
        return {"valid": False, "schema": SCHEMA_FILE, "errors": [f"not well-formed: {exc}"]}
    root = doc.getroot()
    if root.get("dtd-version") == "1.3":
        root.set("dtd-version", "1.3d2")
    xs = schema(SCHEMA_FILE)
    ok = xs.validate(doc)
    errors = [f"line {e.line}: {e.message}" for e in xs.error_log] if not ok else []
    return {"valid": ok, "schema": SCHEMA_FILE, "errors": errors[:50],
            "note": "dtd-version 1.3 checked as 1.3d2, the schema release vendored for offline use"}
