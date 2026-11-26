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
