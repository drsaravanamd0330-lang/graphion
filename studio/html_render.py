"""Semantic HTML: the article landing page and the full-text page.

Both pages are built from the same record and the same reference formatting
the PDF used, so they print the same words. In-page links are relative, so a
proof package works wherever it is unpacked (a staging server, a laptop).
Citation metadata (``citation_*``, Dublin Core, JSON-LD) only ever carries the
*live* URLs from the record; a staging address never reaches a crawler.
"""
from __future__ import annotations

import json
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from . import config, mathconv
from .model import DECLARATION_LABELS, plain
from .profiles import Profile
from .typst_render import INSERT, cite_parts, fmt_date, insert_dates, insert_mode, locator, short_title

_env = Environment(loader=FileSystemLoader(str(config.TEMPLATES_DIR / "output")),
                   autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)


class Ctx:
    def __init__(self, record: dict, mathml: dict[str, str], cite_form: str):
        self.labels = {r["id"]: r["label"] for r in record.get("references", [])}
        self.targets = {f["id"] for f in record["figures"]} | {t["id"] for t in record["tables"]} | {
            e["id"] for e in record["equations"]}
        self.footnotes = {f["id"]: f for f in record.get("footnotes", [])}
        self.mathml = mathml
        self.cite_form = cite_form
        self.fn_order: list[str] = []
        self.ref_href = "#"


def inl(nodes: list[dict] | None, ctx: Ctx) -> Markup:
    out: list[str] = []
    for n in nodes or []:
        t = n["t"]
        if t == "text":
            out.append(str(escape(n["v"])))
        elif t in ("emph", "strong", "sup", "sub"):
            tag = {"emph": "em", "strong": "strong", "sup": "sup", "sub": "sub"}[t]
            out.append(f"<{tag}>{inl(n['c'], ctx)}</{tag}>")
        elif t == "smallcaps":
            out.append(f'<span class="sc">{inl(n["c"], ctx)}</span>')
        elif t == "link":
            out.append(f'<a href="{escape(n["url"])}">{inl(n["c"], ctx)}</a>')
        elif t == "br":
            out.append("<br>")
        elif t == "cite":
            parts = "".join(
                f'<a href="{ctx.ref_href}{escape(rid)}" role="doc-biblioref">{escape(txt)}</a>' if rid else str(escape(txt))
                for txt, rid in cite_parts(n["refs"], ctx.labels))
            if ctx.cite_form == "superscript":
                if out and out[-1].endswith(" "):
                    out[-1] = out[-1].rstrip(" ")
                out.append(f'<sup class="cite">{parts}</sup>')
            else:
                out.append(f'<span class="cite">[{parts}]</span>')
        elif t == "xref":
            if n["target"] in ctx.targets:
                out.append(f'<a href="#{escape(n["target"])}" class="xref">{escape(n["v"])}</a>')
            else:
                out.append(str(escape(n["v"])))
        elif t == "math":
            mm = ctx.mathml.get(n["tex"])
            out.append(mm.replace('display="block"', 'display="inline"') if mm else f'<code class="tex">{escape(n["tex"])}</code>')
        elif t == "fnref":
            if n["id"] not in ctx.fn_order:
                ctx.fn_order.append(n["id"])
            k = ctx.fn_order.index(n["id"]) + 1
            out.append(f'<sup><a href="#{escape(n["id"])}" id="{escape(n["id"])}-ref" role="doc-noteref">{k}</a></sup>')
    return Markup("".join(out))


def blocks(bs: list[dict], record: dict, ctx: Ctx, base_level: int = 1) -> Markup:
    from .model import find
    out: list[str] = []
    for b in bs or []:
        t = b["t"]
        if t == "heading":
            lvl = min(6, b["level"] + base_level)
            ident = f' id="{escape(b["id"])}"' if b.get("id") else ""
            out.append(f"<h{lvl}{ident}>{inl(b['c'], ctx)}</h{lvl}>")
        elif t == "para":
            out.append(f"<p>{inl(b['c'], ctx)}</p>")
        elif t == "list":
            tag = "ol" if b["ordered"] else "ul"
            items = "".join(f"<li>{blocks(it, record, ctx, base_level)}</li>" for it in b["items"])
            out.append(f"<{tag}>{items}</{tag}>")
        elif t == "quote":
            out.append(f"<blockquote>{blocks(b['blocks'], record, ctx, base_level)}</blockquote>")
        elif t == "figure":
            f = find(record, "figure", b["id"])
            if f:
                img = (f'<img src="{escape(f["src"])}" alt="{escape(f.get("alt") or "")}" loading="lazy">'
                       if f.get("src") else '<p class="missing">Image missing</p>')
                out.append(f'<figure id="{escape(f["id"])}">{img}<figcaption><span class="label">'
                           f'{escape(f["label"])}.</span> {inl(f["caption"], ctx)}</figcaption></figure>')
        elif t == "table":
            tb = find(record, "table", b["id"])
            if tb:
                out.append(table_html(tb, ctx))
        elif t == "equation":
            e = find(record, "equation", b["id"])
            if e:
                mm = ctx.mathml.get(e["tex"]) or f'<code class="tex">{escape(e["tex"])}</code>'
                out.append(f'<div class="equation" id="{escape(e["id"])}">{mm}<span class="eqno">{escape(e["label"])}</span></div>')
    return Markup("\n".join(out))


def table_html(tb: dict, ctx: Ctx) -> str:
    head = "".join("<tr>" + "".join(f'<th scope="col">{inl(c, ctx)}</th>' for c in row) + "</tr>" for row in tb["head"])
    rows = []
    for row in tb["rows"]:
        cells = []
        for i, c in enumerate(row):
            cells.append(f'<th scope="row">{inl(c, ctx)}</th>' if i == 0 else f"<td>{inl(c, ctx)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    note = f'<p class="table-note">{inl(tb["note"], ctx)}</p>' if tb.get("note") else ""
    return (f'<figure class="table" id="{escape(tb["id"])}"><figcaption><span class="label">{escape(tb["label"])}.</span> '
            f'{inl(tb["caption"], ctx)}</figcaption><div class="table-scroll" tabindex="0" role="region" '
            f'aria-label="{escape(tb["label"])}"><table><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table></div>{note}</figure>')


def _math_map(record: dict) -> dict[str, str]:
    from .typst_render import _all_math
    exprs = _all_math(record)
    return {e: m for e, m in zip(exprs, mathconv.to_mathml(exprs)) if m}


def meta_tags(record: dict, profile: Profile) -> list[tuple[str, str]]:
    """Highwire Press citation_* and Dublin Core tags, live URLs only."""
    pub, j, urls = record["publication"], record["journal"], record["urls"]
    tags: list[tuple[str, str]] = [("citation_title", plain(record["title"]["en"]))]
    aff = {a["id"]: a for a in record["affiliations"]}
    for a in record["authors"]:
        tags.append(("citation_author", f"{a['family']}, {a['given']}".strip(", ") if a.get("family") else a["full_name"]))
        for x in a.get("affiliations", []):
            if x in aff:
                tags.append(("citation_author_institution", aff[x]["raw"]))
        if a.get("orcid") and a.get("orcid_verified"):
            tags.append(("citation_author_orcid", f"https://orcid.org/{a['orcid']}"))
        if a.get("corresponding") and a.get("email"):
            tags.append(("citation_author_email", a["email"]))
    tags += [("citation_journal_title", j.get("title") or ""), ("citation_journal_abbrev", j.get("abbreviation") or ""),
             ("citation_publisher", j.get("publisher") or "")]
    for key in ("issn_print", "issn_online"):
        if j.get(key):
            tags.append(("citation_issn", j[key]))
    published = record["dates"].get("published") or {}
    if published.get("value") and published.get("verified"):
        tags.append(("citation_publication_date", published["value"].replace("-", "/")))
    elif pub.get("year"):
        tags.append(("citation_publication_date", str(pub["year"])))
    for k in ("volume", "issue"):
        if pub.get(k):
            tags.append((f"citation_{k}", str(pub[k])))
    if pub.get("first_page"):
        tags.append(("citation_firstpage", str(pub["first_page"])))
        if pub.get("last_page"):
            tags.append(("citation_lastpage", str(pub["last_page"])))
    elif pub.get("article_number"):
        tags.append(("citation_firstpage", str(pub["article_number"])))
    if pub.get("doi"):
        tags.append(("citation_doi", pub["doi"]))
    for key, tag in (("landing_live", "citation_abstract_html_url"), ("fulltext_live", "citation_fulltext_html_url"),
                     ("pdf_live", "citation_pdf_url")):
        if urls.get(key):
            tags.append((tag, urls[key]))
    tags.append(("citation_language", record.get("language", "en")))
    if record.get("keywords"):
        tags.append(("citation_keywords", "; ".join(record["keywords"])))
    for r in record.get("references", []):
        tags.append(("citation_reference", r["raw"]))
    tags += [("DC.title", plain(record["title"]["en"]))]
    tags += [("DC.creator", a["full_name"]) for a in record["authors"]]
    if pub.get("doi"):
        tags.append(("DC.identifier", f"doi:{pub['doi']}"))
    if record.get("license") and record["license"].get("url"):
        tags.append(("DC.rights", record["license"]["url"]))
    tags.append(("DC.publisher", j.get("publisher") or ""))
    return [(k, v) for k, v in tags if v]


def json_ld(record: dict) -> str:
    pub, j = record["publication"], record["journal"]
    data: dict[str, Any] = {
        "@context": "https://schema.org", "@type": "ScholarlyArticle",
        "headline": plain(record["title"]["en"]),
        "author": [{"@type": "Person", "name": a["full_name"],
                    **({"identifier": f"https://orcid.org/{a['orcid']}"} if a.get("orcid") and a.get("orcid_verified") else {})}
                   for a in record["authors"]],
        "isPartOf": {"@type": "Periodical", "name": j.get("title"),
                     **({"issn": [x for x in (j.get("issn_print"), j.get("issn_online")) if x]} if j.get("issn_print") or j.get("issn_online") else {})},
        "publisher": {"@type": "Organization", "name": j.get("publisher")},
        "keywords": record.get("keywords") or [],
    }
    if pub.get("doi"):
        data["identifier"] = f"https://doi.org/{pub['doi']}"
    if record.get("license") and record["license"].get("url"):
        data["license"] = record["license"]["url"]
    if record["urls"].get("landing_live"):
        data["url"] = record["urls"]["landing_live"]
    return json.dumps(data, ensure_ascii=False, indent=1).replace("</", "<\\/")


def article_csl(record: dict) -> dict:
    """The article itself as a CSL item, for 'How to cite' in the journal's style."""
    pub, j = record["publication"], record["journal"]
    item: dict[str, Any] = {
        "id": "this-article", "type": "article-journal", "title": plain(record["title"]["en"]),
        "author": [{"family": a.get("family") or a["full_name"], "given": a.get("given") or ""} for a in record["authors"]],
        "container-title": j.get("title"), "container-title-short": j.get("abbreviation"),
    }
    if pub.get("year"):
        item["issued"] = {"date-parts": [[int(pub["year"])]]}
    for k, ck in (("volume", "volume"), ("issue", "issue")):
        if pub.get(k):
            item[ck] = str(pub[k])
    if pub.get("first_page"):
        item["page"] = f"{pub['first_page']}-{pub['last_page']}" if pub.get("last_page") else str(pub["first_page"])
    elif pub.get("article_number"):
        item["number"] = str(pub["article_number"])
        item["page"] = str(pub["article_number"])
    if pub.get("doi"):
        item["DOI"] = pub["doi"]
    return item


def how_to_cite(record: dict, profile: Profile, ctx: "Ctx") -> Markup:
    from . import references
    from .docx_import import conv_inlines, Ctx as ICtx
    out = references.format_with_csl([article_csl(record)], profile.csl_path())
    if "this-article" in out:
        return inl(conv_inlines(out["this-article"], ICtx()), ctx)
    return Markup(escape(plain(record["title"]["en"])))


def render_pages(record: dict, profile: Profile, refs_rendered: list[dict], watermark: str | None) -> dict[str, str]:
    """Return {'index.html': landing, 'fulltext.html': full text}."""
    cite_form = (profile.data.get("references") or {}).get("in_text", "bracket")
    mathml = _math_map(record)
    ctx = Ctx(record, mathml, cite_form)
    colors = dict({"primary": "#1f3b63", "accent": "#b5532a", "rule": "#9aa7b8", "muted": "#5b6573"},
                  **((profile.data.get("branding") or {}).get("colors") or {}))
    colors.setdefault("link", colors["primary"])
    pub = record["publication"]
    atype = profile.article_type(record["article_type"])
    aff_labels = {a["id"]: a["label"] for a in record["affiliations"]}
    authors = [{"name": a["full_name"], "sup": ",".join(aff_labels.get(x, "") for x in a.get("affiliations", [])),
                "corresponding": a.get("corresponding"), "email": a.get("email") if a.get("corresponding") else None,
                "orcid": a.get("orcid") if a.get("orcid_verified") else None} for a in record["authors"]]
    loc = locator(record, profile)
    order = profile.data.get("declarations_order") or list(DECLARATION_LABELS)
    heads = record.get("declaration_headings") or {}
    abstract = [{"label": s.get("label"), "html": inl(s["c"], ctx)} for s in record["abstract"]["sections"]]
    body_ctx = ctx
    body_ctx.ref_href = "#"
    body = blocks(record["body"], record, body_ctx)
    decls = [{"heading": heads.get(k) or DECLARATION_LABELS[k], "html": blocks(record["declarations"][k], record, ctx, 2)}
             for k in order if record["declarations"].get(k)]
    refs = [{"id": r["id"], "label": r["label"], "html": inl(r["c"], ctx)} for r in refs_rendered]
    footnotes = [{"id": fid, "n": i + 1, "html": inl(ctx.footnotes[fid]["c"], ctx)} for i, fid in enumerate(ctx.fn_order)]
    dates = [(k.capitalize(), fmt_date(v["value"])) for k, v in record["dates"].items() if v and v.get("value") and v.get("verified")]
    affiliations = record["affiliations"]
    lic = record.get("license")
    placeholders = {}
    if insert_mode(profile):
        # Display only, as in the PDF: meta tags and JSON-LD read the record.
        if not authors:
            authors = [{"name": INSERT.format("author names"), "sup": "1", "corresponding": True,
                        "email": INSERT.format("corresponding author email"), "orcid": None}]
        else:
            for a in authors:
                if a["corresponding"] and not a["email"]:
                    a["email"] = INSERT.format("email")
        if not affiliations:
            affiliations = [{"label": "1", "raw": INSERT.format("department, institution, city, country")}]
        dates = insert_dates(record, fmt_date)
        if not lic:
            lic = {"statement": INSERT.format("licence statement"), "url": None}
        placeholders = {k: INSERT.format(w) for k, w in (("year", "year"), ("volume", "volume"), ("issue", "issue"),
                        ("locator", "pages"), ("doi", "DOI")) if not (loc if k == "locator" else pub.get(k))}
    common = dict(
        record=record, journal=record["journal"], pub=pub, colors=colors, authors=authors,
        affiliations=affiliations, placeholders=placeholders, title=inl(record["title"]["en"], ctx),
        title_text=plain(record["title"]["en"]), title_original=inl(record["title"].get("original"), ctx),
        title_original_lang=record["title"].get("original_lang"),
        article_type=atype.get("label", record["article_type"]), locator=loc,
        doi_url=f"https://doi.org/{pub['doi']}" if pub.get("doi") else None, dates=dates,
        abstract=abstract, keywords=record.get("keywords") or [], license=lic,
        meta=meta_tags(record, profile), jsonld=Markup(json_ld(record)), urls=record["urls"],
        supplementary=record.get("supplementary") or [], watermark=watermark,
        lang=record.get("language", "en"), short_title=record.get("short_title") or short_title(plain(record["title"]["en"])),
    )
    full = _env.get_template("fulltext.html.j2").render(body=body, declarations=decls, references=refs,
                                                       footnotes=footnotes, **common)
    landing = _env.get_template("landing.html.j2").render(references=refs, how_to_cite=how_to_cite(record, profile, ctx), **common)
    return {"index.html": landing, "fulltext.html": full}
