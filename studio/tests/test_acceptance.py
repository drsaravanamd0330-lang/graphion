"""Acceptance tests for Journal Typesetting Studio.

They run the real pipeline on the synthetic fixture manuscript: DOCX import
through Pandoc, Typst PDF, HTML, checks, revisions, export and XML validation.
Pandoc must be on PATH (Typst comes from the typst pip package); the tests
skip without it.

Two values in the test-only profile copy are not real registrations and are
used only so the XML exports have something to validate: ISSN 2049-3630 is
the example ISSN the ISSN International Centre publishes, and 10.5555 is
Crossref's test prefix. Neither appears in the shipped profiles.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import pytest
import yaml

from studio import config

pytestmark = pytest.mark.skipif(config.pandoc_path() is None, reason="Pandoc is not on PATH")

MCMED = "mcmed-international/sample-clinical-research"
PI = "pharma-intelligence/sample-pharmaceutical-sciences"
TESTJ = "mcmed-international/xml-test-journal"
META = config.PACKAGE_DIR / "fixtures" / "sample-metadata.yaml"
EXAMPLE_ISSN = "2049-3630"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("studio")
    data, profiles, criteria = root / "data", root / "profiles", root / "criteria"
    shutil.copytree(config.PROFILES_DIR, profiles)
    shutil.copytree(config.CRITERIA_DIR, criteria)
    # Test-only journal whose profile carries the values the XML schemas need.
    src = profiles / MCMED
    dst = profiles / TESTJ
    shutil.copytree(src, dst)
    p = yaml.safe_load((dst / "profile.yaml").read_text(encoding="utf-8"))
    p["journal"]["title"] = "XML Test Journal (test fixture only)"
    p["journal"]["issn_online"] = {"value": EXAMPLE_ISSN, "verified": True}
    p["crossref"].update(doi_prefix="10.5555", depositor_name="Test Depositor", depositor_email="depositor@example.org")
    (dst / "profile.yaml").write_text(yaml.safe_dump(p, sort_keys=False, allow_unicode=True, width=10_000), encoding="utf-8")

    saved = (config.DATA_DIR, config.PROFILES_DIR, config.CRITERIA_DIR)
    config.DATA_DIR, config.PROFILES_DIR, config.CRITERIA_DIR = data, profiles, criteria
    from studio.fixtures.make_sample import build
    docx = build(root / "sample.docx")
    tracked = build(root / "tracked.docx", tracked_change=True)
    yield {"root": root, "docx": docx, "tracked": tracked}
    config.DATA_DIR, config.PROFILES_DIR, config.CRITERIA_DIR = saved


def _ack_all(aid):
    from studio import pipeline, store
    acks = store.acknowledgements(aid)
    for f in pipeline.validate(aid):
        if f.status == "manual" and f.fingerprint not in acks:
            pipeline.acknowledge(aid, f.fingerprint, f.check, "Test Editor", "checked in test")


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader
    return " ".join((pg.extract_text() or "") for pg in PdfReader(str(path)).pages)


def _norm(s: str) -> str:
    return " ".join(s.replace("-\n", "").split())


@pytest.fixture(scope="module")
def approved(env):
    """Sample imported with metadata into the McMed profile, rendered, every
    manual finding acknowledged, and revision 1 approved."""
    from studio import pipeline
    aid = pipeline.import_article(env["docx"], MCMED, "original-research", META)
    res = pipeline.render(aid)
    _ack_all(aid)
    pipeline.approve(aid, res["number"], "Test Editor")
    return aid


# ------------------------------------------------------------------ import

def test_fixture_is_labelled_synthetic_and_imports_completely(env):
    from studio.docx_import import import_docx
    rec = import_docx(env["docx"], env["root"] / "media-check")
    assert any("SYNTHETIC" in line for line in rec["front_matter_unparsed"])
    assert [a["full_name"] for a in rec["authors"]] == ["Asha R. Menon", "Daniel K. Osei", "María José Núñez-Ibáñez"]
    assert rec["authors"][0]["affiliations"] == ["aff1", "aff2"]
    assert all(af["country"] for af in rec["affiliations"])
    assert len(rec["tables"]) == 1 and rec["tables"][0]["cols"] == 8
    assert len(rec["figures"]) == 1 and rec["figures"][0]["alt"]
    assert len(rec["equations"]) == 1
    assert len(rec["references"]) == 5
    assert rec["references"][4]["parse_status"] == "unparsed"


def test_tracked_changes_block(env):
    from studio import pipeline
    aid = pipeline.import_article(env["tracked"], MCMED, "original-research", META)
    blocked = [f for f in pipeline.validate(aid) if f.status == "blocked"]
    assert any("tracked" in (f.title + f.explanation).lower() for f in blocked)


# ------------------------------------------------------------------ missing metadata

def test_missing_metadata_blocks_approval_but_draft_renders(env):
    from studio import pipeline, store
    aid = pipeline.import_article(env["docx"], MCMED, "original-research")
    findings = pipeline.validate(aid)
    blocked = {f.check for f in findings if f.status == "blocked"}
    assert blocked, "an article without publication metadata must have blocked findings"
    res = pipeline.render(aid)
    assert res["status"] == "diagnostic"
    rdir = store.revision_dir(aid, res["number"])
    assert (rdir / "article.pdf").is_file() and (rdir / "index.html").is_file()
    assert "NOT FOR PUBLICATION" in _pdf_text(rdir / "article.pdf")
    with pytest.raises(pipeline.WorkflowError):
        pipeline.approve(aid, res["number"], "Test Editor")


# ------------------------------------------------------------------ proof outputs

def test_proof_outputs_exist_and_preserve_content(env, approved):
    from studio import pipeline, store
    rdir = store.revision_dir(approved, 1)
    for name in ("article.pdf", "index.html", "fulltext.html", "record.json", "report.json", "report.html", "inputs.json"):
        assert (rdir / name).is_file(), name
    report = pipeline.revision_report(approved, 1)
    by_check = {f["check"]: f for f in report["findings"]}
    for check in ("pdf-render", "pdf-fonts", "pdf-title", "pdf-author-order", "pdf-words", "pdf-numbers",
                  "pdf-license", "pdf-overflow", "html-pages", "agreement"):
        assert by_check[check]["status"] == "pass", (check, by_check[check]["explanation"])
    text = _norm(_pdf_text(rdir / "article.pdf"))
    pos = [text.find(n) for n in ("Menon", "Osei", "Núñez-Ibáñez")]
    assert all(p >= 0 for p in pos) and pos == sorted(pos)
    record = store.read_json(rdir / "record.json")
    html = (rdir / "fulltext.html").read_text(encoding="utf-8")
    assert record["title"]["en"] and "Menon" in html
    assert store.read_json(rdir / "inputs.json")["profile_version"]
    # The Word copy carries the same article: authors in order, every
    # reference, and the body text word for word.
    import re
    from collections import Counter
    from docx import Document
    from studio.model import blocks_plain
    doc = Document(str(rdir / "article.docx"))
    dtext = " ".join(p.text for p in doc.paragraphs) + " " + " ".join(
        c.text for t in doc.tables for row in t.rows for c in row.cells)
    dpos = [dtext.find(n) for n in ("Menon", "Osei", "Núñez-Ibáñez")]
    assert all(p >= 0 for p in dpos) and dpos == sorted(dpos)
    words = lambda s: Counter(re.findall(r"\w+", s))
    body, got = words(blocks_plain(record["body"], record)), words(dtext)
    math = words(" ".join(e["tex"] for e in record["equations"]))   # Word holds equations as OMML, not text
    assert not {w: n for w, n in body.items() if got[w] < n and w not in math}
    assert "m:oMath" in __import__("zipfile").ZipFile(rdir / "article.docx").read("word/document.xml").decode()


def test_approval_records_versions_and_rerender_reuses(env, approved):
    from studio import pipeline, store
    rev = store.revision(approved, 1)
    assert rev["status"] == "approved" and rev["approved_by"] == "Test Editor"
    approval = store.read_json(store.revision_dir(approved, 1) / "approval.json")
    assert approval["input_key"] == rev["input_key"]
    again = pipeline.render(approved)
    assert again["reproduced"] and again["number"] == 1


def test_export_is_reproducible_and_never_overwrites(env, approved):
    from studio import pipeline
    first = pipeline.export(approved)
    digest = hashlib.sha256(first["path"].read_bytes()).hexdigest()
    second = pipeline.export(approved)
    assert second["reproduced"] and second["path"] == first["path"]
    assert hashlib.sha256(second["path"].read_bytes()).hexdigest() == digest
    with zipfile.ZipFile(first["path"]) as z:
        names = set(z.namelist())
        manifest = json.loads(z.read("manifest.json"))
    assert {"article.pdf", "index.html", "fulltext.html", "record.json", "report.html"} <= names
    assert manifest["kind"] == "approved"


def test_edit_after_approval_makes_new_unapproved_revision(env, approved):
    from studio import pipeline, store
    r1 = store.revision_dir(approved, 1)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in r1.iterdir() if p.is_file()}
    pipeline.update_editor(approved, {"short_title": "Pharmacist review and blood pressure"})
    res = pipeline.render(approved)
    assert res["number"] == 2 and not res["reproduced"]
    assert store.revision(approved, 2)["status"] != "approved"
    assert store.revision(approved, 1)["status"] == "approved"
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in r1.iterdir() if p.is_file()}
    assert {k: v for k, v in after.items() if k in before} == before
    with pytest.raises(pipeline.WorkflowError):
        pipeline.approve(approved, 1, "Test Editor")


# ------------------------------------------------------------------ second profile

def test_second_profile_changes_branding_layout_and_reference_style(env):
    from pypdf import PdfReader
    from studio import pipeline, store
    a = pipeline.import_article(env["docx"], MCMED, "original-research", META)
    b = pipeline.import_article(env["docx"], PI, "original-research", META)
    ra, rb = pipeline.render(a), pipeline.render(b)
    da, db_ = store.revision_dir(a, ra["number"]), store.revision_dir(b, rb["number"])
    ta, tb = _pdf_text(da / "article.pdf"), _pdf_text(db_ / "article.pdf")
    assert "Sample Journal of Clinical Research" in ta and "Sample Reports in Pharmaceutical Sciences" in tb

    def fonts(p):
        out = set()
        for pg in PdfReader(str(p)).pages:
            for f in (pg["/Resources"].get("/Font") or {}).values():
                out.add(str(f.get_object()["/BaseFont"]).split("+")[-1])
        return out
    assert fonts(da / "article.pdf") != fonts(db_ / "article.pdf")
    # Vancouver (McMed sample) abbreviates page ranges; AMA (PI sample) prints them in full.
    ha = (da / "fulltext.html").read_text(encoding="utf-8")
    hb = (db_ / "fulltext.html").read_text(encoding="utf-8")
    assert "2019;8(2):101–9." in ha and "2019;8(2):101-109." in hb
    # Same manuscript wording in both: the record body is identical.
    body_a = store.read_json(da / "record.json")["body"]
    body_b = store.read_json(db_ / "record.json")["body"]
    assert body_a == body_b


# ------------------------------------------------------------------ XML

def test_xml_exports_validate_when_metadata_allows(env):
    from studio import pipeline
    aid = pipeline.import_article(env["docx"], TESTJ, "original-research", META)
    pipeline.update_editor(aid, {"publication": {"doi": "10.5555/xmltest.2026.1024"}})
    res = pipeline.render(aid)
    out = pipeline.export(aid, res["number"], jats=True, crossref=True)
    assert out["kind"] == "proof"
    assert out["xml"]["jats"]["valid"], out["xml"]["jats"]["errors"]
    assert out["xml"]["crossref"]["valid"], out["xml"]["crossref"]["errors"]
    with zipfile.ZipFile(out["path"]) as z:
        assert {"article.jats.xml", "article.crossref.xml"} <= {Path(n).name for n in z.namelist()}


def test_xml_exports_report_missing_metadata_instead_of_inventing(env, approved):
    from studio import pipeline
    out = pipeline.export(approved, 1, jats=True, crossref=True)
    assert not out["xml"]["jats"]["valid"]
    assert any("ISSN" in n for n in out["xml"]["jats"]["mapping_notes"])
    cr = out["xml"]["crossref"]
    assert not cr["valid"] and cr["note"] == "deposit not generated"
    assert any("DOI" in e for e in cr["errors"])


# ------------------------------------------------------------------ evidence

def test_evidence_status_and_criteria_versioning(env):
    from studio import evidence
    from studio.profiles import load_profile
    prof = load_profile(MCMED)
    v = evidence.view(prof, "doaj")
    assert v["criteria"]["official_sources"][0]["url"].startswith("https://")
    assert all(r["status"] == "unverified" for r in v["rows"])
    with pytest.raises(evidence.CriteriaError):
        evidence.set_status(prof.id, "doaj", "issn", "verified", "", "", "", "Test Editor")
    evidence.set_status(prof.id, "doaj", "issn", "not-met", "", "no ISSN yet", "2026-11-26", "Test Editor")
    assert evidence.view(prof, "doaj")["counts"]["not-met"] == 1
    text = evidence.criteria_path("doaj").read_text(encoding="utf-8")
    saved = evidence.save_criteria("doaj", text, "Test Editor")
    assert saved["version"] == 2
    row = next(r for r in evidence.view(prof, "doaj")["rows"] if r["id"] == "issn")
    assert row["stale"] and row["status"] == "needs review"


# ------------------------------------------------------------------ interfaces

def test_web_pages_render(env, approved):
    from studio.web import create_app
    c = create_app().test_client()
    for url in ("/", f"/a/{approved}", f"/a/{approved}/r/1", f"/a/{approved}/r/1/file/index.html",
                f"/a/{approved}/r/1/page/1", "/journals", f"/journals/{MCMED}/evidence/scopus", "/criteria/wos"):
        assert c.get(url).status_code == 200, url
    assert c.get(f"/a/{approved}/r/1/file/../../editor.json").status_code == 404


def test_cli_uses_the_same_pipeline(env, approved, capsys):
    from studio.cli import main
    assert main(["validate", approved]) == 0
    assert "Ready for editorial review" in capsys.readouterr().out
    assert main(["export", approved, "--revision", "1"]) == 0
    assert "reproduced" in capsys.readouterr().out
