"""Production checks. Each returns findings with a status, an explanation and
a link to the field or output it concerns.

Statuses:

* ``pass``: checked and fine.
* ``warning``: worth knowing; does not stop approval.
* ``blocked``: stops approval. A diagnostic draft can still be rendered.
* ``na``: does not apply to this article (the reason is given).
* ``manual``: a person has to look. Approval waits until the editor
  acknowledges each one, so a manual finding is never silently waved through.

Record checks read ``record.json`` and the profile. Output checks read the
files a revision produced, which is the only way to know the outputs agree.
Live checks touch the network and run only when asked, and report apart from
the local proof results.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from .model import DATE_KEYS, DECLARATION_LABELS, blocks_plain, find, object_plain, plain, walk_inlines
from .profiles import Profile, issn_checksum_ok

PASS, WARNING, BLOCKED, NA, MANUAL = "pass", "warning", "blocked", "na", "manual"
STATUS_LABEL = {PASS: "Pass", WARNING: "Warning", BLOCKED: "Blocked", NA: "Not Applicable", MANUAL: "Needs Manual Review"}
STATUS_ORDER = {BLOCKED: 0, MANUAL: 1, WARNING: 2, PASS: 3, NA: 4}

PLACEHOLDER_RE = re.compile(
    r"\b(XXX+|TBD|TODO|INSERT\s+\w+|lorem ipsum|Error! Reference source not found|\[\[[^\]]*\]\]|\?\?+|"
    r"\[(?:citation|ref|reference)\s+needed\]|\[(?:to\s+be\s+(?:completed|added|confirmed|inserted|supplied)|authors?\s+to\s+|insert\s|add\s)[^\]]*\]|<<[^>]*>>|\{(?:year|volume|issue|doi|copyright_holder|publisher|article_number)\})", re.I)
NON_LATIN_RE = re.compile(r"[Ͱ-ϿЀ-ӿ֐-׿؀-ۿऀ-෿฀-๿"
                          r"ᄀ-ᇿ぀-ヿ㐀-鿿가-힯]")
DOI_FORMAT_RE = re.compile(r"^10\.\d{4,9}/\S+$")
LICENSE_URLS = {
    "CC BY 4.0": "/licenses/by/4.0", "CC BY-SA 4.0": "/licenses/by-sa/4.0", "CC BY-NC 4.0": "/licenses/by-nc/4.0",
    "CC BY-NC-SA 4.0": "/licenses/by-nc-sa/4.0", "CC BY-ND 4.0": "/licenses/by-nd/4.0",
    "CC BY-NC-ND 4.0": "/licenses/by-nc-nd/4.0", "CC0 1.0": "/publicdomain/zero/1.0",
}


@dataclass
class Finding:
    check: str
    status: str
    title: str
    explanation: str
    field: str = ""          # record field the editor should open
    output: str = "record"   # record | pdf | html | json | jats | crossref | live
    group: str = "article"
    fingerprint: str = ""

    def __post_init__(self):
        if not self.fingerprint:
            self.fingerprint = hashlib.sha256(f"{self.check}|{self.field}|{self.explanation}".encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status_label"] = STATUS_LABEL[self.status]
        return d


def summarise(findings: Iterable[Finding | dict]) -> dict:
    counts = Counter((f.status if isinstance(f, Finding) else f["status"]) for f in findings)
    return {s: counts.get(s, 0) for s in (BLOCKED, MANUAL, WARNING, PASS, NA)}


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (STATUS_ORDER[f.status], f.output, f.check))


# ====================================================================== record

def record_checks(record: dict, profile: Profile) -> list[Finding]:
    out: list[Finding] = []
    atype_key = record.get("article_type")
    atype = profile.article_type(atype_key)
    F = out.append

    if not atype:
        F(Finding("article-type", BLOCKED, "Article type", f"The journal profile has no article type '{atype_key}'.", "article_type"))

    # ---- import findings carried forward
    sev = {"blocked": BLOCKED, "warning": WARNING, "manual": MANUAL}
    for fl in record.get("flags", []):
        if fl["severity"] in sev:
            F(Finding(f"import:{fl['code']}", sev[fl["severity"]], "Import: " + fl["code"].replace("-", " "),
                      fl["message"], fl.get("field") or "source", group="import"))

    # ---- title
    title = plain(record["title"]["en"]).strip()
    if not title:
        F(Finding("title", BLOCKED, "English title", "The English title is missing.", "title"))
    else:
        F(Finding("title", PASS, "English title", f"Present ({len(title.split())} words).", "title"))
    if record["title"].get("original") and not record["title"].get("original_lang"):
        F(Finding("title-original-lang", BLOCKED, "Original-language title",
                  "An original-language title is given without its language code.", "title"))

    # ---- abstract
    abs_cfg = atype.get("abstract", {}) if atype else {}
    sections = record["abstract"]["sections"]
    abs_words = sum(len(plain(s["c"]).split()) for s in sections)
    if abs_cfg.get("required", True):
        if not sections:
            F(Finding("abstract", BLOCKED, "Abstract", f"A {atype.get('label', atype_key)} requires an abstract; none was found.", "abstract"))
        else:
            F(Finding("abstract", PASS, "Abstract", f"Present, {abs_words} words.", "abstract"))
            want = abs_cfg.get("sections")
            if abs_cfg.get("structured"):
                got = [s.get("label") for s in sections]
                if not all(got):
                    F(Finding("abstract-structure", BLOCKED, "Structured abstract",
                              f"This article type needs a structured abstract ({', '.join(want or [])}); "
                              "some paragraphs carry no heading.", "abstract"))
                elif want and [g.lower() for g in got] != [w.lower() for w in want]:
                    F(Finding("abstract-structure", WARNING, "Structured abstract",
                              f"Headings are {', '.join(got)}; the journal expects {', '.join(want)}.", "abstract"))
                else:
                    F(Finding("abstract-structure", PASS, "Structured abstract", "Headings match the journal's structure.", "abstract"))
            elif any(s.get("label") for s in sections):
                F(Finding("abstract-structure", WARNING, "Abstract structure",
                          "The abstract is structured but this article type expects a single paragraph.", "abstract"))
            mx = abs_cfg.get("max_words")
            if mx and abs_words > mx:
                F(Finding("abstract-length", WARNING, "Abstract length", f"{abs_words} words; the limit is {mx}.", "abstract"))
    else:
        F(Finding("abstract", NA, "Abstract", f"Not required for {atype.get('label', atype_key)}.", "abstract"))

    kw = record.get("keywords") or []
    kcfg = atype.get("keywords", {}) if atype else {}
    if kcfg.get("min", 0) and len(kw) < kcfg["min"]:
        F(Finding("keywords", BLOCKED, "Keywords", f"{len(kw)} keyword(s); at least {kcfg['min']} required.", "keywords"))
    elif kcfg.get("max") and len(kw) > kcfg["max"]:
        F(Finding("keywords", WARNING, "Keywords", f"{len(kw)} keywords; the journal allows {kcfg['max']}.", "keywords"))
    else:
        F(Finding("keywords", PASS if kw or not kcfg.get("min") else NA, "Keywords", f"{len(kw)} keyword(s).", "keywords"))

    # ---- authors and affiliations
    authors, affs = record["authors"], record["affiliations"]
    aff_ids = {a["id"] for a in affs}
    if not authors:
        F(Finding("authors", BLOCKED, "Authors", "No authors.", "authors"))
    for a in authors:
        name = a["full_name"]
        mapped = [x for x in a.get("affiliations", []) if x in aff_ids]
        if not mapped:
            F(Finding("author-affiliation", BLOCKED, "Author affiliation", f"{name} is not mapped to an institution.", f"authors.{a['id']}"))
        if NON_LATIN_RE.search(name) and not a.get("name_roman"):
            F(Finding("author-roman", BLOCKED, "Roman-script name", f"{name} is written in a non-Latin script; add the Roman-script form.", f"authors.{a['id']}"))
        if not a.get("family"):
            F(Finding("author-family", MANUAL, "Family name", f"No family name recorded for {name}.", f"authors.{a['id']}"))
        if a.get("orcid"):
            from .docx_import import orcid_checksum_ok
            if not orcid_checksum_ok(a["orcid"]):
                F(Finding("orcid", BLOCKED, "ORCID iD", f"{name}: {a['orcid']} fails the ORCID check digit.", f"authors.{a['id']}"))
            elif not a.get("orcid_verified"):
                F(Finding("orcid", MANUAL, "ORCID iD",
                          f"{name}: {a['orcid']} is well formed but not verified, so it is not printed. Confirm it belongs to this author.",
                          f"authors.{a['id']}"))
            else:
                F(Finding("orcid", PASS, "ORCID iD", f"{name}: verified.", f"authors.{a['id']}"))
    if authors and all(any(x in aff_ids for x in a.get("affiliations", [])) for a in authors):
        F(Finding("author-affiliation", PASS, "Author affiliations", f"All {len(authors)} authors map to an institution.", "authors"))
    for af in affs:
        if not af.get("country"):
            F(Finding("affiliation-country", BLOCKED, "Affiliation country", f"Affiliation {af['label']} has no country or region.", f"affiliations.{af['id']}"))
        if not af.get("institution"):
            F(Finding("affiliation-institution", BLOCKED, "Affiliation institution", f"Affiliation {af['label']} names no institution.", f"affiliations.{af['id']}"))
        if NON_LATIN_RE.search(af.get("raw", "")) and not af.get("roman"):
            F(Finding("affiliation-roman", BLOCKED, "Roman-script affiliation", f"Affiliation {af['label']} needs a Roman-script form.", f"affiliations.{af['id']}"))
        if not any(af["id"] in a.get("affiliations", []) for a in authors):
            F(Finding("affiliation-unused", WARNING, "Unused affiliation", f"No author is mapped to affiliation {af['label']}.", f"affiliations.{af['id']}"))
    corr = [a for a in authors if a.get("corresponding")]
    if not corr:
        F(Finding("corresponding", BLOCKED, "Corresponding author", "No corresponding author is marked.", "authors"))
    elif not all(a.get("email") for a in corr):
        F(Finding("corresponding", BLOCKED, "Corresponding author", "The corresponding author has no email address.", "authors"))
    else:
        F(Finding("corresponding", PASS, "Corresponding author", ", ".join(a["full_name"] for a in corr), "authors"))

    # ---- dates
    d = record["dates"]
    for k in DATE_KEYS:
        v = d.get(k) or {}
        if v.get("value") and not v.get("verified"):
            F(Finding("date-verify", MANUAL, f"{k.capitalize()} date",
                      f"{v['value']} comes from the manuscript and is not verified, so it is not printed. Confirm it.", f"dates.{k}"))
    seq = [(k, (d.get(k) or {}).get("value")) for k in DATE_KEYS if (d.get(k) or {}).get("value")]
    for (k1, v1), (k2, v2) in zip(seq, seq[1:]):
        if v1 > v2:
            F(Finding("date-order", BLOCKED, "Date order", f"{k1.capitalize()} ({v1}) is after {k2} ({v2}).", "dates"))
    if not (d.get("accepted") or {}).get("value"):
        F(Finding("date-accepted", WARNING, "Accepted date", "No accepted date is recorded.", "dates.accepted"))

    # ---- publication metadata
    pub = record["publication"]
    model = (profile.data.get("numbering") or {}).get("model", "article-number")
    missing = [k for k in ("year", "volume") if not pub.get(k)]
    if model == "article-number" and not pub.get("article_number"):
        missing.append("article_number")
    if model == "pages" and not pub.get("first_page"):
        missing.append("first_page")
    if missing:
        F(Finding("publication", WARNING, "Publication details",
                  f"Not yet set: {', '.join(missing)}. The proof prints what is known; Crossref export needs all of them.", "publication"))
    else:
        F(Finding("publication", PASS, "Publication details", "Year, volume and locator are set.", "publication"))
    if pub.get("first_page") and pub.get("last_page"):
        try:
            if int(pub["last_page"]) < int(pub["first_page"]):
                F(Finding("pages", BLOCKED, "Page range", "The last page is before the first page.", "publication"))
        except ValueError:
            pass

    doi = pub.get("doi")
    prefix = (profile.data.get("crossref") or {}).get("doi_prefix") or ""
    if not doi:
        F(Finding("doi", WARNING, "DOI", "No DOI is assigned; none is printed. Studio never generates one.", "publication.doi"))
    elif not DOI_FORMAT_RE.match(doi):
        F(Finding("doi", BLOCKED, "DOI", f"{doi} is not a well-formed DOI.", "publication.doi"))
    elif prefix and not doi.startswith(prefix + "/"):
        F(Finding("doi", BLOCKED, "DOI", f"{doi} does not use the journal's prefix {prefix}.", "publication.doi"))
    else:
        F(Finding("doi", PASS, "DOI", f"{doi} ({'registered' if pub.get('doi_registered') else 'not yet registered'}).", "publication.doi"))

    # ---- journal identity (journal-level, shown on every article)
    j = record["journal"]
    for key, label in (("issn_print", "Print ISSN"), ("issn_online", "Online ISSN")):
        v = j.get(key)
        if not v:
            F(Finding(f"journal-{key}", WARNING, label, f"No {label} is configured; the masthead omits it.", f"profile.journal.{key}", group="journal"))
        elif not issn_checksum_ok(v):
            F(Finding(f"journal-{key}", BLOCKED, label, f"{v} fails the ISSN check digit.", f"profile.journal.{key}", group="journal"))
        elif not j.get(f"{key}_verified"):
            F(Finding(f"journal-{key}", MANUAL, label, f"{v} is not marked verified in the journal profile.", f"profile.journal.{key}", group="journal"))
        else:
            F(Finding(f"journal-{key}", PASS, label, v, f"profile.journal.{key}", group="journal"))

    # ---- licence
    lic = record.get("license")
    if not lic:
        F(Finding("license", BLOCKED, "Licence", "No licence is configured for the journal or the article. Studio never assumes one.", "license"))
    else:
        if lic.get("statement_error"):
            F(Finding("license", BLOCKED, "Licence statement", lic["statement_error"], "license"))
        elif re.search(r"\{\w+\}", lic.get("statement", "")):
            F(Finding("license", BLOCKED, "Licence statement",
                      "The statement still has an unfilled " + ", ".join(re.findall(r"\{\w+\}", lic["statement"])) +
                      "; set the publication year (or the article's published date).", "publication.year"))
        exp = LICENSE_URLS.get(lic.get("type", ""))
        if exp and lic.get("url") and exp not in lic["url"].rstrip("/") + "/":
            F(Finding("license-url", BLOCKED, "Licence URL", f"{lic['type']} does not match {lic['url']}.", "license"))
        if lic.get("exception") and lic.get("exception_reason_missing"):
            F(Finding("license-exception", BLOCKED, "Licence exception", "An article-level licence differs from the journal default without a recorded reason.", "license"))
        if not lic.get("policy_verified"):
            F(Finding("license-policy", MANUAL, "Licence wording",
                      "The journal profile's licence and copyright wording is not marked verified. Confirm it is the journal's actual statement.",
                      "profile.license", group="journal"))
        else:
            F(Finding("license-policy", PASS, "Licence wording", f"{lic.get('type')} from the {lic['source']}.", "license"))

    # ---- declarations
    required = atype.get("required_declarations", []) if atype else []
    for key in DECLARATION_LABELS:
        content = record["declarations"].get(key)
        if key in required and not content:
            F(Finding(f"declaration-{key}", BLOCKED, DECLARATION_LABELS[key], f"Required for this article type and missing.", f"declarations.{key}"))
        elif content:
            F(Finding(f"declaration-{key}", PASS, DECLARATION_LABELS[key], "Present.", f"declarations.{key}"))
    text_all = (plain(record["title"]["en"]) + " " + " ".join(plain(s["c"]) for s in sections)).lower()
    if atype_key == "original-research" and re.search(r"\btrial\b", text_all) and not record["declarations"].get("trial_registration"):
        F(Finding("declaration-trial_registration", MANUAL, "Clinical trial registration",
                  "The title or abstract describes a trial and no registration statement is present. Confirm whether one is required; "
                  "Studio will not supply a registration number.", "declarations.trial_registration"))

    # ---- figures, tables, equations, citations
    cited = Counter()
    xrefs = Counter()
    for n in walk_inlines(record):
        if n.get("t") == "cite":
            for r in n["refs"]:
                cited[r] += 1
        elif n.get("t") == "xref":
            xrefs[n["target"]] += 1
    ref_ids = [r["id"] for r in record.get("references", [])]
    bad_cites = sorted(set(cited) - set(ref_ids))
    if bad_cites:
        F(Finding("citations-targets", BLOCKED, "Citation targets",
                  f"Cited but not in the reference list: {', '.join(r.replace('ref', '') for r in bad_cites)}.", "references"))
    elif cited:
        F(Finding("citations-targets", PASS, "Citation targets", f"All {sum(cited.values())} citations resolve.", "references"))
    uncited = [r for r in ref_ids if r not in cited]
    if uncited and cited:
        F(Finding("references-uncited", WARNING, "Uncited references",
                  f"Never cited in the text: {', '.join(r.replace('ref', '') for r in uncited)}.", "references"))
    first_seen = []
    for n in walk_inlines(record):
        if n.get("t") == "cite":
            for r in n["refs"]:
                if r not in first_seen and r in ref_ids:
                    first_seen.append(r)
    if first_seen and (profile.data.get("references") or {}).get("in_text") in ("bracket", "superscript"):
        expected = [r for r in ref_ids if r in first_seen]
        if first_seen != expected:
            F(Finding("citation-order", WARNING, "Citation order",
                      "References are not numbered in order of first citation, which numeric styles expect.", "references"))
    bad_x = sorted(t for t in xrefs if not any(t == o["id"] for o in record["figures"] + record["tables"] + record["equations"]))
    if bad_x:
        F(Finding("xref-targets", BLOCKED, "Cross-references", f"Text refers to items that do not exist: {', '.join(bad_x)}.", "body"))
    elif xrefs:
        F(Finding("xref-targets", PASS, "Cross-references", f"All {sum(xrefs.values())} references to figures, tables and equations resolve.", "body"))
    for f in record["figures"]:
        fld = f"figures.{f['id']}"
        if not f.get("src"):
            F(Finding("figure-media", BLOCKED, f"{f['label']} image", "The image file is missing.", fld))
        if not plain(f.get("caption")):
            F(Finding("figure-caption", BLOCKED, f"{f['label']} caption", "No caption.", fld))
        if not f.get("alt"):
            F(Finding("figure-alt", MANUAL, f"{f['label']} alt text",
                      "No alt text. Write a description of what the figure shows; Studio does not generate one.", fld))
        else:
            F(Finding("figure-alt", PASS, f"{f['label']} alt text", f"{len(f['alt'].split())} words.", fld))
        if not xrefs.get(f["id"]):
            F(Finding("figure-cited", WARNING, f"{f['label']} citation", "The figure is never referred to in the text.", fld))
    for t in record["tables"]:
        fld = f"tables.{t['id']}"
        if not t["head"]:
            F(Finding("table-header", BLOCKED, f"{t['label']} header", "No header row.", fld))
        if not plain(t.get("caption")):
            F(Finding("table-caption", BLOCKED, f"{t['label']} caption", "No caption.", fld))
        if not xrefs.get(t["id"]):
            F(Finding("table-cited", WARNING, f"{t['label']} citation", "The table is never referred to in the text.", fld))
        widths = {len(r) for r in t["head"] + t["rows"]}
        if len(widths) > 1:
            F(Finding("table-shape", MANUAL, f"{t['label']} shape", "Rows have different numbers of cells; check the proof against the source.", fld))
    for e in record["equations"]:
        if not xrefs.get(e["id"]):
            F(Finding("equation-cited", NA, f"Equation {e['label']}", "Not referred to in the text (allowed).", f"equations.{e['id']}"))

    # ---- references
    for r in record.get("references", []):
        if r.get("parse_status") in ("unparsed", "partial"):
            F(Finding("reference-parse", MANUAL, f"Reference {r['label']}",
                      f"Could not be parsed reliably ({r.get('parse_reason')}). It is printed exactly as typed: check it.",
                      f"references.{r['id']}"))
        if r.get("doi") and not DOI_FORMAT_RE.match(r["doi"]):
            F(Finding("reference-doi", WARNING, f"Reference {r['label']} DOI", f"{r['doi']} is not a well-formed DOI.", f"references.{r['id']}"))

    # ---- supplementary
    for s in record.get("supplementary", []):
        if not re.match(r"^https?://", s.get("url", "")):
            F(Finding("supplementary-url", BLOCKED, s.get("label", "Supplementary file"), f"{s.get('url')} is not a web address.", "supplementary"))

    # ---- placeholders
    hits = placeholder_hits(record)
    if hits:
        F(Finding("placeholders", BLOCKED, "Unresolved placeholders", "Found: " + "; ".join(hits[:8]), "body"))
    else:
        F(Finding("placeholders", PASS, "Unresolved placeholders", "None found.", "body"))

    # ---- URLs
    urls = record.get("urls") or {}
    if urls.get("landing_live"):
        F(Finding("landing-url", PASS, "Article landing URL", urls["landing_live"], "urls"))
    else:
        F(Finding("landing-url", WARNING, "Article landing URL", "Live landing URL not yet resolvable: " + str(urls.get("landing_live_missing")), "urls"))

    # ---- short title for running heads
    if not record.get("short_title") and len(title) > 55:
        F(Finding("short-title", WARNING, "Running-head title", "No short title; the running head uses the title cut at 55 characters.", "short_title"))

    # ---- wording kept from the manuscript
    out.extend(preservation_checks(record))
    return out


def placeholder_hits(record: dict) -> list[str]:
    hits = []
    texts = [plain(record["title"]["en"])] + [plain(s["c"]) for s in record["abstract"]["sections"]]
    texts.append(blocks_plain(record["body"], record))
    for v in (record.get("declarations") or {}).values():
        if v:
            texts.append(blocks_plain(v, record))
    texts += [r["raw"] for r in record.get("references", [])]
    for t in texts:
        for m in PLACEHOLDER_RE.finditer(t):
            hits.append(m.group(0))
    return hits


# ====================================================================== preservation

NUM_RE = re.compile(r"(?<![\w.])[−-]?\d+(?:[.,]\d+)*%?")
WORD_RE = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return s.replace("−", "-").replace("–", "-").replace("—", "-")


def record_text(record: dict) -> str:
    parts = [plain(record["title"]["en"]), plain(record["title"].get("original")), plain(record.get("subtitle"))]
    labels = {a["id"]: a["label"] for a in record["affiliations"]}
    parts += [a["full_name"] + " " + " ".join(labels.get(x, "") for x in a.get("affiliations", [])) for a in record["authors"]]
    parts += [f"{a['label']} {a['raw']}" for a in record["affiliations"]]
    for s in record["abstract"]["sections"]:
        parts.append((s.get("label") or "") + " " + plain(s["c"]))
    parts.append(" ".join(record.get("keywords") or []))
    # Body text without the objects placed in it; captions and cells follow
    # once each. Alt text and equations are left out on both sides: Word keeps
    # alt text outside the paragraph text and math in its own markup.
    parts.append(blocks_plain(record["body"]))
    for o in record["figures"]:
        parts.append(object_plain("figure", o))
    for o in record["tables"]:
        parts.append(object_plain("table", o))
    for k, v in (record.get("declarations") or {}).items():
        if v:
            parts.append((record.get("declaration_headings") or {}).get(k, "") + " " + blocks_plain(v, record))
    parts += [f"{r['label']}. {r['raw']}" for r in record.get("references", [])]
    parts += [plain(f["c"]) for f in record.get("footnotes", [])]
    parts += [s.get("url", "") for s in record.get("supplementary", [])]
    return _norm("\n".join(p for p in parts if p))


def preservation_checks(record: dict) -> list[Finding]:
    """Compare the manuscript's own text with what the record will print.

    The manuscript text is read from the DOCX XML at import, independently of
    Pandoc, so a word Pandoc dropped shows up here. Front-matter lines that
    were consumed into structured fields (correspondence, ORCID, dates) are
    excluded, since their content lives in fields checked elsewhere.
    """
    src_pars = (record.get("provenance") or {}).get("source_paragraphs")
    if not src_pars:
        return [Finding("preservation", NA, "Manuscript wording preserved", "No source text was recorded at import.", "source")]
    consumed_re = re.compile(r"^\s*([*†]?\s*correspond|orcid|received|abstract$|references$|keywords?\s*:)", re.I)
    unparsed = set(record.get("front_matter_unparsed") or [])
    src = _norm("\n".join(p for p in src_pars if not consumed_re.match(p) and p.strip() not in unparsed))
    rec = record_text(record)
    src_nums, rec_nums = Counter(NUM_RE.findall(src)), Counter(NUM_RE.findall(rec))
    src_words = Counter(w.lower() for w in WORD_RE.findall(src))
    rec_words = Counter(w.lower() for w in WORD_RE.findall(rec))
    lost_nums = src_nums - rec_nums
    ignore = {"keywords", "abstract", "references", "table", "figure"}
    lost_words = Counter({w: c for w, c in (src_words - rec_words).items() if w not in ignore})
    added_nums = rec_nums - src_nums
    out = []
    if lost_nums:
        out.append(Finding("preservation-numbers", BLOCKED, "Numbers preserved",
                           "Numbers in the manuscript that the record does not carry: " +
                           ", ".join(f"{k}×{v}" if v > 1 else k for k, v in list(lost_nums.items())[:20]), "body"))
    else:
        out.append(Finding("preservation-numbers", PASS, "Numbers preserved",
                           f"All {sum(src_nums.values())} numbers in the manuscript text are carried.", "body"))
    if added_nums:
        # Labels Studio adds (reference numbers, equation numbers) are expected.
        unexpected = {k: v for k, v in added_nums.items() if not re.fullmatch(r"\d{1,3}", k)}
        if unexpected:
            out.append(Finding("preservation-added-numbers", MANUAL, "Numbers added",
                               "Numbers in the record that are not in the manuscript (editor edits?): " +
                               ", ".join(list(unexpected)[:20]), "body"))
    if lost_words:
        out.append(Finding("preservation-words", WARNING if sum(lost_words.values()) < 5 else BLOCKED, "Wording preserved",
                           "Words in the manuscript the record does not carry: " +
                           ", ".join(list(lost_words)[:25]), "body"))
    else:
        out.append(Finding("preservation-words", PASS, "Wording preserved",
                           f"All {sum(src_words.values())} words of the manuscript text are carried.", "body"))
    return out


# ====================================================================== outputs

def pdf_checks(pdf_path: Path, record: dict, compile_msgs: list[str]) -> list[Finding]:
    out: list[Finding] = []
    F = out.append
    if not pdf_path.is_file():
        F(Finding("pdf-render", BLOCKED, "PDF rendered", "Typst did not produce a PDF: " + "; ".join(compile_msgs[:4]), "", "pdf"))
        return out
    F(Finding("pdf-render", PASS, "PDF rendered", "Typst compiled the article.", "", "pdf"))
    for m in compile_msgs:
        F(Finding("pdf-warning", WARNING, "Rendering warning", m, "", "pdf"))
    import pdfplumber
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    unembedded = set()
    for page in reader.pages:
        res = page.get("/Resources") or {}
        fonts = (res.get("/Font") or {}) if hasattr(res, "get") else {}
        for _, ref in (fonts.items() if hasattr(fonts, "items") else []):
            font = ref.get_object()
            desc = font.get("/FontDescriptor")
            if font.get("/Subtype") == "/Type0":
                desc = font["/DescendantFonts"][0].get_object().get("/FontDescriptor")
            if desc is None:
                continue
            desc = desc.get_object()
            if not any(k in desc for k in ("/FontFile", "/FontFile2", "/FontFile3")):
                unembedded.add(str(font.get("/BaseFont")))
    if unembedded:
        F(Finding("pdf-fonts", BLOCKED, "Fonts embedded", "Not embedded: " + ", ".join(sorted(unembedded)), "", "pdf"))
    else:
        F(Finding("pdf-fonts", PASS, "Fonts embedded", "Every font in the PDF is embedded.", "", "pdf"))

    # Text in content-stream order, which Typst writes column by column; a
    # layout-based extraction would interleave the two columns line by line.
    page_texts = [page.extract_text() or "" for page in reader.pages]
    overflow = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            w, h = page.width, page.height
            outside = [c for c in page.chars if c["x0"] < 5 or c["x1"] > w - 5 or c["top"] < 5 or c["bottom"] > h - 5]
            if outside:
                overflow.append(f"page {i} ({len(outside)} characters at the page edge)")
    full = _norm("\n".join(page_texts))
    if not full.strip():
        F(Finding("pdf-text", BLOCKED, "Selectable text", "No text could be extracted from the PDF.", "", "pdf"))
    else:
        F(Finding("pdf-text", PASS, "Selectable text", f"{len(page_texts)} pages, text extracts from every page.", "", "pdf"))
    if overflow:
        F(Finding("pdf-overflow", WARNING, "Overflow or clipping", "Text reaches the page edge on " + "; ".join(overflow), "", "pdf"))
    else:
        F(Finding("pdf-overflow", PASS, "Overflow or clipping", "No text reaches the page edge.", "", "pdf"))

    flat = re.sub(r"-\s*\n\s*", "", full)          # rejoin words hyphenated at line ends
    flat_cmp = re.sub(r"[\s\-‐]+", "", flat).lower()

    def present(s: str) -> bool:
        return re.sub(r"[\s\-‐]+", "", _norm(s)).lower() in flat_cmp

    title = plain(record["title"]["en"])
    F(Finding("pdf-title", PASS if present(title) else BLOCKED, "Title in PDF",
              "The title prints as recorded." if present(title) else "The recorded title was not found in the PDF text.", "title", "pdf"))
    first = _norm(page_texts[0]) if page_texts else ""
    first_cmp = re.sub(r"[\s\-‐]+", "", first).lower()
    positions = [first_cmp.find(re.sub(r"[\s\-‐]+", "", _norm(a["full_name"])).lower()) for a in record["authors"]]
    if not record["authors"]:
        F(Finding("pdf-author-order", NA, "Author order in PDF", "The record has no authors yet; see the author findings.", "authors", "pdf"))
    elif all(p >= 0 for p in positions) and positions == sorted(positions):
        F(Finding("pdf-author-order", PASS, "Author order in PDF", "All authors print on page 1 in the recorded order.", "authors", "pdf"))
    else:
        F(Finding("pdf-author-order", BLOCKED, "Author order in PDF", "Authors are missing from page 1 or print in a different order.", "authors", "pdf"))
    body_txt = _norm("\n".join([plain(s["c"]) for s in record["abstract"]["sections"]] + [blocks_plain(record["body"])]
                               + [object_plain("table", t) for t in record["tables"]]))
    nums = [n for n in NUM_RE.findall(body_txt)]
    missing = sorted({n for n in nums if re.sub(r"[\s\-‐]+", "", n) not in flat_cmp})
    if missing:
        F(Finding("pdf-numbers", BLOCKED, "Values in PDF", "Numbers from the record not found in the PDF text: " + ", ".join(missing[:20]), "body", "pdf"))
    else:
        F(Finding("pdf-numbers", PASS, "Values in PDF", f"All {len(nums)} numeric values of the abstract, body and tables appear in the PDF.", "body", "pdf"))
    words = [w for w in WORD_RE.findall(body_txt)]
    lost = sorted({w for w in words if re.sub(r"[\-‐']", "", w).lower() not in flat_cmp})
    if lost:
        F(Finding("pdf-words", BLOCKED if len(lost) > 3 else WARNING, "Wording in PDF", "Words not found in the PDF text: " + ", ".join(lost[:20]), "body", "pdf"))
    else:
        F(Finding("pdf-words", PASS, "Wording in PDF", f"All {len(words)} words of the abstract, body and tables appear in the PDF.", "body", "pdf"))
    lic = record.get("license")
    if lic:
        ok = present(lic["statement"])
        F(Finding("pdf-license", PASS if ok else BLOCKED, "Licence in PDF",
                  "The licence statement prints in full." if ok else "The licence statement was not found in the PDF.", "license", "pdf"))
    hits = PLACEHOLDER_RE.findall(full)
    if hits:
        F(Finding("pdf-placeholders", BLOCKED, "Placeholders in PDF", "Found: " + ", ".join(sorted(set(hits))[:8]), "", "pdf"))
    meta = reader.metadata or {}
    if (meta.get("/Title") or "").strip() != title.strip():
        F(Finding("pdf-metadata-title", BLOCKED, "PDF document title", f"Document properties carry “{meta.get('/Title')}”.", "title", "pdf"))
    else:
        F(Finding("pdf-metadata-title", PASS, "PDF document title", "Matches the record.", "title", "pdf"))
    return out


def html_checks(pages: dict[str, Path], record: dict) -> list[Finding]:
    from lxml import html as lhtml
    out: list[Finding] = []
    F = out.append
    title = plain(record["title"]["en"])
    for name, path in pages.items():
        doc = lhtml.fromstring(path.read_bytes())
        meta = {}
        for m in doc.iter("meta"):
            if m.get("name"):
                meta.setdefault(m.get("name"), []).append(m.get("content", ""))
        if (meta.get("citation_title") or [""])[0] != title:
            F(Finding("html-meta-title", BLOCKED, f"{name}: citation title", "citation_title does not match the record.", "title", "html"))
        authors = meta.get("citation_author", [])
        expect = [f"{a['family']}, {a['given']}".strip(", ") if a.get("family") else a["full_name"] for a in record["authors"]]
        if authors != expect:
            F(Finding("html-meta-authors", BLOCKED, f"{name}: author metadata", f"Authors in metadata ({len(authors)}) differ from the record.", "authors", "html"))
        doi = record["publication"].get("doi")
        if doi and meta.get("citation_doi", [None])[0] != doi:
            F(Finding("html-meta-doi", BLOCKED, f"{name}: DOI metadata", "citation_doi does not match.", "publication.doi", "html"))
        imgs_no_alt = [i for i in doc.iter("img") if not (i.get("alt") or "").strip()]
        if imgs_no_alt:
            F(Finding("html-alt", MANUAL, f"{name}: image alt text", f"{len(imgs_no_alt)} image(s) without alt text.", "figures", "html"))
        levels = [int(h.tag[1]) for h in doc.iter() if isinstance(h.tag, str) and re.fullmatch(r"h[1-6]", h.tag)]
        skips = [f"h{a}→h{b}" for a, b in zip(levels, levels[1:]) if b > a + 1]
        if skips:
            F(Finding("html-headings", WARNING, f"{name}: heading levels", "Skipped heading levels: " + ", ".join(skips), "body", "html"))
        for tb in doc.iter("table"):
            if not tb.findall(".//th"):
                F(Finding("html-table-headers", BLOCKED, f"{name}: table headers", "A table has no header cells.", "tables", "html"))
        live = record.get("urls", {}).get("landing_live")
        canon = [l.get("href") for l in doc.iter("link") if l.get("rel") == "canonical"]
        if live and canon != [live]:
            F(Finding("html-canonical", BLOCKED, f"{name}: canonical URL", "The canonical link is not the live landing URL.", "urls", "html"))
        lic = record.get("license")
        if lic:
            txt = re.sub(r"\s+", " ", doc.text_content())
            if lic["statement"] not in txt:
                F(Finding("html-license", BLOCKED, f"{name}: licence", "The licence statement is not on the page.", "license", "html"))
    if not any(f.status in (BLOCKED, MANUAL) for f in out):
        F(Finding("html-pages", PASS, "HTML pages", "Landing and full-text pages carry matching metadata, alt text and table headers.", "", "html"))
    return out


def agreement_checks(record: dict, pdf_path: Path, pages: dict[str, Path], json_path: Path,
                     xml_paths: dict[str, Path] | None = None) -> list[Finding]:
    """Do title, author order, DOI, volume, issue, locator and licence agree everywhere?"""
    import json as _json
    from lxml import etree
    from lxml import html as lhtml
    from pypdf import PdfReader

    out: list[Finding] = []
    rec = _json.loads(json_path.read_text(encoding="utf-8"))
    facts = {
        "title": plain(record["title"]["en"]),
        "authors": [a["full_name"] for a in record["authors"]],
        "doi": record["publication"].get("doi"),
        "volume": record["publication"].get("volume"),
        "issue": record["publication"].get("issue"),
        "license_url": (record.get("license") or {}).get("url"),
    }
    seen: dict[str, dict] = {"record.json": {
        "title": plain(rec["title"]["en"]), "authors": [a["full_name"] for a in rec["authors"]],
        "doi": rec["publication"].get("doi"), "volume": rec["publication"].get("volume"),
        "issue": rec["publication"].get("issue"), "license_url": (rec.get("license") or {}).get("url")}}
    if pdf_path.is_file():
        info = PdfReader(str(pdf_path)).metadata or {}
        seen["article.pdf"] = {"title": info.get("/Title"),
                               "authors": [a.strip() for a in (info.get("/Author") or "").split(",") if a.strip()] or None}
    for name, path in pages.items():
        doc = lhtml.fromstring(path.read_bytes())
        meta = {}
        for m in doc.iter("meta"):
            if m.get("name"):
                meta.setdefault(m.get("name"), []).append(m.get("content", ""))
        seen[name] = {"title": (meta.get("citation_title") or [None])[0],
                      "doi": (meta.get("citation_doi") or [None])[0],
                      "volume": (meta.get("citation_volume") or [None])[0],
                      "issue": (meta.get("citation_issue") or [None])[0],
                      "license_url": (meta.get("DC.rights") or [None])[0]}
    for name, path in (xml_paths or {}).items():
        x = etree.parse(str(path))
        r = x.getroot()
        if name.endswith("jats.xml"):
            ns = {"xlink": "http://www.w3.org/1999/xlink", "ali": "http://www.niso.org/schemas/ali/1.0/"}
            seen[name] = {
                "title": "".join(x.find(".//article-meta/title-group/article-title").itertext()),
                "authors": [" ".join(filter(None, [c.findtext("name/given-names"), c.findtext("name/surname")]))
                            for c in x.findall(".//article-meta/contrib-group/contrib[@contrib-type='author']")],
                "doi": x.findtext(".//article-meta/article-id[@pub-id-type='doi']"),
                "volume": x.findtext(".//article-meta/volume"), "issue": x.findtext(".//article-meta/issue"),
                "license_url": (x.find(".//license", ns).get("{http://www.w3.org/1999/xlink}href")
                                if x.find(".//license", ns) is not None else None),
            }
        elif name.endswith("crossref.xml"):
            cr = {"c": r.nsmap.get(None)}
            ja = r.find(".//c:journal_article", cr)
            seen[name] = {
                "title": "".join(ja.find("c:titles/c:title", cr).itertext()) if ja is not None else None,
                "doi": r.findtext(".//c:journal_article/c:doi_data/c:doi", namespaces=cr),
                "volume": r.findtext(".//c:journal_issue/c:journal_volume/c:volume", namespaces=cr),
                "issue": r.findtext(".//c:journal_issue/c:issue", namespaces=cr),
            }
    disagreements = []
    for key, expected in facts.items():
        for name, got in seen.items():
            if key not in got or got[key] is None and expected is None:
                continue
            g = got[key]
            if key == "authors" and name == "article.pdf" and g is not None:
                g = g
            if key == "authors" and name.endswith("jats.xml"):
                expected_cmp = [" ".join(filter(None, [a.get("given"), a.get("family")])) for a in record["authors"]]
                if g != expected_cmp:
                    disagreements.append(f"{key} in {name}")
                continue
            if (g or None) != (str(expected) if expected is not None and key in ("volume", "issue") else (expected or None)):
                disagreements.append(f"{key} in {name}")
    if disagreements:
        out.append(Finding("agreement", BLOCKED, "Metadata agrees across outputs", "Differs: " + ", ".join(disagreements), "", "json"))
    else:
        out.append(Finding("agreement", PASS, "Metadata agrees across outputs",
                           "Title, author order, DOI, volume, issue and licence match in " + ", ".join(seen), "", "json"))
    return out


# ====================================================================== live

def live_checks(record: dict, timeout: float = 15.0) -> list[Finding]:
    """Network checks against the live site. Reported apart from the proof."""
    import requests
    out: list[Finding] = []
    pub, urls = record["publication"], record.get("urls") or {}
    if pub.get("doi") and pub.get("doi_registered"):
        try:
            r = requests.head(f"https://doi.org/{pub['doi']}", allow_redirects=False, timeout=timeout)
            loc = r.headers.get("Location", "")
            if r.status_code in (301, 302, 303, 307, 308) and urls.get("landing_live") and loc.rstrip("/") == urls["landing_live"].rstrip("/"):
                out.append(Finding("live-doi", PASS, "DOI resolves", f"doi.org redirects to {loc}.", "publication.doi", "live", "live"))
            elif r.status_code in (301, 302, 303, 307, 308):
                out.append(Finding("live-doi", WARNING, "DOI resolves", f"doi.org redirects to {loc}, not the landing URL.", "publication.doi", "live", "live"))
            else:
                out.append(Finding("live-doi", BLOCKED, "DOI resolves", f"doi.org answered {r.status_code}.", "publication.doi", "live", "live"))
        except requests.RequestException as exc:
            out.append(Finding("live-doi", WARNING, "DOI resolves", f"Could not reach doi.org: {exc.__class__.__name__}.", "publication.doi", "live", "live"))
    else:
        out.append(Finding("live-doi", NA, "DOI resolves", "Only checked for a DOI marked as registered.", "publication.doi", "live", "live"))
    if pub.get("open_access", True) and urls.get("fulltext_live"):
        for key in ("landing_live", "fulltext_live", "pdf_live"):
            u = urls.get(key)
            if not u:
                continue
            try:
                r = requests.get(u, timeout=timeout, allow_redirects=True)
                login = re.search(r"/(login|signin|sign-in|auth)\b", r.url, re.I)
                if r.status_code == 200 and not login:
                    out.append(Finding(f"live-{key}", PASS, f"Open access: {key}", f"{u} is reachable without signing in.", "urls", "live", "live"))
                else:
                    out.append(Finding(f"live-{key}", BLOCKED, f"Open access: {key}", f"{u} answered {r.status_code} at {r.url}.", "urls", "live", "live"))
            except requests.RequestException as exc:
                out.append(Finding(f"live-{key}", WARNING, f"Open access: {key}", f"Could not reach {u}: {exc.__class__.__name__}.", "urls", "live", "live"))
    else:
        out.append(Finding("live-access", NA, "Open-access full text", "No live full-text URL exists yet.", "urls", "live", "live"))
    return out
