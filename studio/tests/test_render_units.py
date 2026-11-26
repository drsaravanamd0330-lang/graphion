"""Small render rules that need no Pandoc."""
from studio.profiles import load_profile
from studio.typst_render import INSERT, _template_fill, insert_dates, insert_mode

HEADER = "{first_author_surname} et al. / Vol {volume} / Issue {issue} / {year} / {locator}"


def test_empty_header_segments_are_dropped():
    assert _template_fill(HEADER, {}) == ""
    assert _template_fill(HEADER, {"first_author_surname": "Kumar", "volume": 16, "year": 2026}) == "Kumar et al. / Vol 16 / 2026"


def test_insert_mode_is_opt_in_per_journal():
    assert insert_mode(load_profile("mcmed-international/ijidd"))
    assert not insert_mode(load_profile("mcmed-international/sample-clinical-research"))


def test_unverified_dates_print_as_insert():
    rec = {"dates": {"received": {"value": "2026-01-02", "verified": True}, "accepted": {"value": "2026-03-01"}}}
    got = dict(insert_dates(rec, lambda v: v))
    assert got["Received"] == "2026-01-02"
    assert got["Accepted"] == INSERT.format("date")   # an unverified date is never printed as fact
    assert got["Published"] == INSERT.format("date")


def test_doi_shown_as_doi_org_link_only_when_it_is_the_references_own():
    from studio.model import plain, text
    from studio.typst_render import link_doi
    typed = [text("J Pharm Sci. 2025;114:1. doi:10.1016/j.xphs.2025.103749")]
    out = link_doi(typed, "10.1016/j.xphs.2025.103749")
    assert out[-1] == {"t": "link", "url": "https://doi.org/10.1016/j.xphs.2025.103749",
                       "c": [text("https://doi.org/10.1016/j.xphs.2025.103749")]}
    assert "doi:" not in plain(out)
    assert link_doi(typed, "10.9999/other") == typed          # never swaps in a different DOI
    csl = [text("Talanta. 2027;311:130236. doi:"), {"t": "link", "url": "https://doi.org/10.1016/j.talanta.2026.130236",
                                                   "c": [text("10.1016/j.talanta.2026.130236")]}]
    out = link_doi(csl, "10.1016/j.talanta.2026.130236")
    assert plain(out) == "Talanta. 2027;311:130236. https://doi.org/10.1016/j.talanta.2026.130236"


def test_first_citation_of_each_reference_is_the_back_link_target():
    from studio.typst_render import with_cite_anchors
    rec = {"body": [{"t": "para", "c": [{"t": "cite", "refs": ["ref1", "ref2"]}]},
                    {"t": "para", "c": [{"t": "cite", "refs": ["ref2", "ref3"]}]}], "tables": [], "figures": []}
    out, cited = with_cite_anchors(rec)
    assert cited == {"ref1", "ref2", "ref3"}
    assert out["body"][0]["c"][0]["anchors"] == ["cite-ref1", "cite-ref2"]
    assert out["body"][1]["c"][0]["anchors"] == ["cite-ref3"]
    assert "anchors" not in rec["body"][0]["c"][0]          # the record itself is untouched
