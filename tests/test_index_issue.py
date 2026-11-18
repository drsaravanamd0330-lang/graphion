"""Indexing an issue that reached the volume as files.

A deploy seeds `content/` onto the volume no-clobber, and nothing then put
it in the database: Writing Spaces 7 arrived in production as 33 article
directories no page could see.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import conversion
import db
import index_issue


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(conversion, "CONTENT_DIR", tmp_path / "content")
    db.init_db()
    db.execute("INSERT INTO journals (slug, name, template_path) VALUES "
               "('writing-spaces', 'Writing Spaces', 'x')")
    issue = tmp_path / "content" / "journals" / "writing-spaces" / "issues" / "v7-n1-2027"
    (issue / "articles").mkdir(parents=True)
    (issue / "_issue.yaml").write_text(
        "volume: 7\nissue: 1\nyear: 2027\ntitle: Writing Spaces 7\n", encoding="utf-8")

    def article(slug, **fm):
        d = issue / "articles" / slug
        d.mkdir()
        lines = ["---"] + [f"{k}: {json.dumps(v)}" for k, v in fm.items()] + ["---", "", "Body.", ""]
        (d / "article.md").write_text("\n".join(lines), encoding="utf-8")

    article("contributors-volume-7", title="Contributors")
    article("10-clinnin", title="Reading Filters", **{"chapter-number": 10,
            "toc-section": "Source-Based Writing"})
    article("2-catchmark", title="This Changes Everything", **{"chapter-number": 2,
            "toc-section": "Rhetoric and Writing Studies"})
    return tmp_path


def test_an_issue_on_disk_is_indexed_in_order(store):
    summary = index_issue.index_issue("writing-spaces", "v7-n1-2027")

    assert summary["issue_created"]
    rows = db.query_all("SELECT slug, order_in_issue, section, title FROM articles "
                        "ORDER BY order_in_issue")
    assert [r["slug"] for r in rows] == ["2-catchmark", "10-clinnin",
                                         "contributors-volume-7"]
    assert rows[0]["section"] == "Rhetoric and Writing Studies"
    assert rows[2]["section"] == index_issue.DEFAULT_SECTION
    issue = db.query_one("SELECT * FROM issues")
    assert (issue["volume"], issue["year"], issue["title"]) == (7, 2027, "Writing Spaces 7")


def test_the_journal_gains_the_issue_s_sections_after_its_own(store):
    index_issue.index_issue("writing-spaces", "v7-n1-2027")
    listed = json.loads(db.query_one("SELECT toc_sections_json FROM journals")
                        ["toc_sections_json"])
    assert listed[0] == index_issue.DEFAULT_SECTION
    assert listed[1:3] == ["Rhetoric and Writing Studies", "Source-Based Writing"]


def test_running_it_twice_changes_nothing(store):
    index_issue.index_issue("writing-spaces", "v7-n1-2027")
    again = index_issue.index_issue("writing-spaces", "v7-n1-2027")
    assert not again["issue_created"] and again["added"] == []
    assert len(again["already_indexed"]) == 3
    assert db.query_one("SELECT COUNT(*) AS n FROM articles")["n"] == 3
    assert db.query_one("SELECT COUNT(*) AS n FROM issues")["n"] == 1


def test_a_missing_issue_file_is_refused(store):
    (conversion.issue_dir("writing-spaces", "v7-n1-2027") / "_issue.yaml").unlink()
    with pytest.raises(ValueError):
        index_issue.index_issue("writing-spaces", "v7-n1-2027")
