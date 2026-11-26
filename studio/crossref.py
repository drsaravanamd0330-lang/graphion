"""Crossref deposit XML (schema 5.3.1) for one journal article.

The deposit is generated only when every value Crossref needs is present and
was entered by an editor: a DOI under the journal's configured prefix, a
depositor, a verified publication date and a live landing page URL. Nothing is
filled in to make the file validate. ``readiness`` lists what is missing, and
the export records that list instead of a file.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

from .model import plain
from .profiles import Profile
from .xmlutil import date_parts, validate_file

SCHEMA_NAME = "crossref5.3.1.xsd"
CR = "http://www.crossref.org/schema/5.3.1"
JATS = "http://www.ncbi.nlm.nih.gov/JATS1"
AI = "http://www.crossref.org/AccessIndicators.xsd"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
NSMAP = {None: CR, "jats": JATS, "ai": AI, "xsi": XSI}


def _cfg(profile: Profile) -> dict:
    return profile.data.get("crossref") or {}


def readiness(record: dict, profile: Profile) -> list[str]:
    cfg = _cfg(profile)
    pub = record["publication"]
    problems = []
    prefix = (cfg.get("doi_prefix") or "").strip()
    if not prefix:
        problems.append("Journal profile has no Crossref DOI prefix (crossref.doi_prefix).")
    if not (cfg.get("depositor_name") and cfg.get("depositor_email")):
        problems.append("Journal profile has no Crossref depositor name and email.")
    doi = (pub.get("doi") or "").strip()
    if not doi:
        problems.append("Article has no DOI. Enter the DOI assigned by the journal; it is never generated.")
    elif prefix and not doi.lower().startswith(prefix.lower() + "/"):
        problems.append(f"Article DOI {doi} is not under the journal prefix {prefix}.")
    published = record["dates"].get("published") or {}
    if not (published.get("value") and published.get("verified")):
        problems.append("Publication date is missing or not confirmed by an editor.")
    if not (record.get("urls") or {}).get("landing_live"):
        problems.append("Landing page URL is not set as live (Crossref resolves the DOI to it).")
    j = record["journal"]
    if (j.get("issn_print") and not j.get("issn_print_verified")) or (j.get("issn_online") and not j.get("issn_online_verified")):
        problems.append("An ISSN in the journal profile is not marked verified; verify it or remove it before depositing.")
    if not record["title"].get("en"):
        problems.append("Article has no English title.")
    if not record["authors"]:
        problems.append("Article has no authors.")
    return problems


def _date(parent, iso: str, media_type: str = "online"):
    y, m, d = date_parts(iso)
    el = etree.SubElement(parent, f"{{{CR}}}publication_date", {"media_type": media_type})
    etree.SubElement(el, f"{{{CR}}}month").text = m
    etree.SubElement(el, f"{{{CR}}}day").text = d
    etree.SubElement(el, f"{{{CR}}}year").text = y
    return el


def S(parent, tag, text=None, **attrs):
    el = etree.SubElement(parent, f"{{{CR}}}{tag}", {k: str(v) for k, v in attrs.items()})
    if text is not None:
        el.text = str(text)
    return el


def build(record: dict, profile: Profile, timestamp: str | None = None) -> bytes:
    """Build the deposit. ``timestamp`` (ISO) fixes the batch timestamp so a
    rebuild of the same revision gives the same bytes."""
    problems = readiness(record, profile)
    if problems:
        raise ValueError("Crossref deposit not ready: " + " ".join(problems))
    cfg = _cfg(profile)
    pub = record["publication"]
    j = record["journal"]
    when = datetime.fromisoformat(timestamp) if timestamp else datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    stamp = when.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")

    root = etree.Element(f"{{{CR}}}doi_batch", nsmap=NSMAP)
    root.set("version", "5.3.1")
    root.set(f"{{{XSI}}}schemaLocation", f"{CR} https://www.crossref.org/schemas/crossref5.3.1.xsd")
    head = S(root, "head")
    S(head, "doi_batch_id", f"{record['article_id']}-{stamp}")
    S(head, "timestamp", stamp)
    dep = S(head, "depositor")
    S(dep, "depositor_name", cfg["depositor_name"])
    S(dep, "email_address", cfg["depositor_email"])
    S(head, "registrant", cfg.get("registrant") or j.get("publisher") or cfg["depositor_name"])

    body = S(root, "body")
    journal = S(body, "journal")
    jm = S(journal, "journal_metadata", language=record.get("language", "en"))
    S(jm, "full_title", j["title"])
    if j.get("abbreviation"):
        S(jm, "abbrev_title", j["abbreviation"])
    if j.get("issn_print") and j.get("issn_print_verified"):
        S(jm, "issn", j["issn_print"], media_type="print")
    if j.get("issn_online") and j.get("issn_online_verified"):
        S(jm, "issn", j["issn_online"], media_type="electronic")

    published = record["dates"]["published"]["value"]
    if pub.get("volume") or pub.get("issue"):
        ji = S(journal, "journal_issue")
        _date(ji, published)
        if pub.get("volume"):
            jv = S(ji, "journal_volume")
            S(jv, "volume", pub["volume"])
        if pub.get("issue"):
            S(ji, "issue", pub["issue"])

    ja = S(journal, "journal_article", publication_type="full_text", language=record.get("language", "en"))
    titles = S(ja, "titles")
    S(titles, "title", plain(record["title"]["en"]))
    contribs = S(ja, "contributors")
    aff_by_id = {a["id"]: a for a in record["affiliations"]}
    for i, a in enumerate(record["authors"]):
        pn = S(contribs, "person_name", sequence="first" if i == 0 else "additional", contributor_role="author")
        if a.get("given"):
            S(pn, "given_name", a["given"])
        S(pn, "surname", a.get("family") or a["full_name"])
        affs = [aff_by_id[x] for x in a.get("affiliations", []) if x in aff_by_id]
        if affs:
            ae = S(pn, "affiliations")
            for af in affs:
                inst = S(ae, "institution")
                S(inst, "institution_name", af.get("institution") or af["raw"])
                if af.get("country"):
                    S(inst, "institution_place", af["country"])
        if a.get("orcid") and a.get("orcid_verified"):
            S(pn, "ORCID", f"https://orcid.org/{a['orcid']}", authenticated="false")
    if record["abstract"]["sections"]:
        ab = etree.SubElement(ja, f"{{{JATS}}}abstract")
        for s in record["abstract"]["sections"]:
            p = etree.SubElement(ab, f"{{{JATS}}}p")
            p.text = (f"{s['label']}: " if s.get("label") else "") + plain(s["c"])
    _date(ja, published)
    if pub.get("first_page"):
        pages = S(ja, "pages")
        S(pages, "first_page", pub["first_page"])
        if pub.get("last_page"):
            S(pages, "last_page", pub["last_page"])
    if pub.get("article_number"):
        pi = S(ja, "publisher_item")
        S(pi, "item_number", pub["article_number"], item_number_type="article_number")
    lic = record.get("license") or {}
    if lic.get("url"):
        prog = etree.SubElement(ja, f"{{{AI}}}program", {"name": "AccessIndicators"})
        if pub.get("open_access"):
            etree.SubElement(prog, f"{{{AI}}}free_to_read")
        etree.SubElement(prog, f"{{{AI}}}license_ref", {"applies_to": "vor"}).text = lic["url"]
    dd = S(ja, "doi_data")
    S(dd, "doi", pub["doi"])
    S(dd, "resource", record["urls"]["landing_live"])
    if record.get("references"):
        cl = S(ja, "citation_list")
        for ref in record["references"]:
            c = S(cl, "citation", key=ref["id"])
            if ref.get("doi"):
                S(c, "doi", ref["doi"])
            S(c, "unstructured_citation", ref["raw"])
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def validate(path: Path) -> dict:
    return validate_file(path, SCHEMA_NAME)
