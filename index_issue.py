"""Register an issue that is on disk but not in the database.

Usage:
    python index_issue.py <journal-slug> <issue-slug> [--render] [--assemble]

    --render    render every article's galleys (HTML, PDF, EPUB) and record
                lint and the PDF's declaration in the accessibility register
    --assemble  bind the issue PDF afterwards, with continuous pagination

Content is the source of truth and the database is its index
(`CLAUDE.md`, "Filesystem-first"), but nothing could build that index for
an issue that arrived on disk some other way than through this app. A
deploy seeds `content/` onto the volume no-clobber, so an issue committed
from another machine reached production as files no page could see:
Writing Spaces 7 was ingested and remediated on a laptop, and its 33
articles were on the volume with no issue row and no article rows.

This reads the issue's `_issue.yaml` for the issue row and each article's
front matter for its title, and files them in order: chapters by their
`chapter-number`, then anything without one (a contributors list) by slug.
An article's `toc-section`, where its front matter carries one, becomes its
table-of-contents section, and any section the journal does not list yet is
appended to the journal's list after the ones it has.

It changes nothing that is already indexed. An article whose slug the
journal already holds is reported and left alone, so running this twice is
safe, and an issue row that exists is reused rather than duplicated.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

import conversion
import db


DEFAULT_SECTION = "ARTICLES"


def _order_key(item):
    number = item[1].get("chapter-number")
    try:
        return (0, int(number), item[0].name)
    except (TypeError, ValueError):
        return (1, 0, item[0].name)


def index_issue(journal_slug: str, issue_slug: str) -> dict:
    """Index one issue directory. Returns a summary for the caller to print."""
    db.init_db()
    journal = db.query_one("SELECT * FROM journals WHERE slug = ?", (journal_slug,))
    if not journal:
        raise ValueError(f"No journal with slug {journal_slug!r}.")
    journal = dict(journal)

    issue_path = conversion.issue_dir(journal_slug, issue_slug)
    meta_file = issue_path / "_issue.yaml"
    if not meta_file.exists():
        raise ValueError(f"{meta_file} is missing; nothing says what issue this is.")
    meta = yaml.safe_load(meta_file.read_text(encoding="utf-8")) or {}
    volume, number, year = (int(meta["volume"]), int(meta.get("issue") or 1),
                            int(meta["year"]))

    row = db.query_one(
        "SELECT id FROM issues WHERE journal_id = ? AND volume = ? AND issue_number = ?",
        (journal["id"], volume, number))
    if row:
        issue_id, created = row["id"], False
    else:
        issue_id = db.execute(
            "INSERT INTO issues (journal_id, volume, issue_number, year, title, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (journal["id"], volume, number, year, meta.get("title"),
             meta.get("status") or "draft"))
        created = True

    articles = []
    for article_dir in sorted((issue_path / "articles").glob("*")):
        if not (article_dir / "article.md").exists():
            continue
        fm, _ = conversion.read_article_metadata(article_dir)
        articles.append((article_dir, fm))
    articles.sort(key=_order_key)

    added, kept, sections = [], [], []
    taken = db.query_one(
        "SELECT COALESCE(MAX(order_in_issue), 0) AS m FROM articles WHERE issue_id = ?",
        (issue_id,))["m"] or 0
    for article_dir, fm in articles:
        slug = article_dir.name
        section = str(fm.get("toc-section") or "").strip() or DEFAULT_SECTION
        if section not in sections:
            sections.append(section)
        existing = db.query_one(
            "SELECT id FROM articles WHERE journal_id = ? AND slug = ?",
            (journal["id"], slug))
        if existing:
            kept.append(slug)
            continue
        taken += 1
        db.execute(
            "INSERT INTO articles (journal_id, issue_id, slug, title, short_title, "
            "project_path, order_in_issue, start_page, end_page, status, section) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)",
            (journal["id"], issue_id, slug,
             conversion.plain_text(fm.get("title")) or slug,
             fm.get("short-title"), str(article_dir), taken,
             fm.get("start-page"), fm.get("end-page"), section))
        added.append(slug)

    # The journal's section order gains this issue's sections after its own,
    # with the default first where the journal had none, so the issues it
    # already holds keep the order they print in.
    listed = []
    if journal.get("toc_sections_json"):
        try:
            listed = [str(s) for s in json.loads(journal["toc_sections_json"])]
        except (TypeError, ValueError):
            listed = []
    merged = list(listed) or [DEFAULT_SECTION]
    for section in sections:
        if section not in merged:
            merged.append(section)
    if merged != listed and sections != [DEFAULT_SECTION]:
        db.execute("UPDATE journals SET toc_sections_json = ? WHERE id = ?",
                   (json.dumps(merged), journal["id"]))

    return {"issue_id": issue_id, "issue_created": created,
            "added": added, "already_indexed": kept, "sections": merged}


def render_issue(journal_slug: str, issue_id: int) -> list:
    """Render every filed article's galleys and record what the register reads."""
    import lint
    import register

    problems = []
    for row in db.query_all(
            "SELECT a.*, j.slug AS journal_slug, j.name AS journal_name "
            "FROM articles a JOIN journals j ON a.journal_id = j.id "
            "WHERE a.issue_id = ? ORDER BY order_in_issue", (issue_id,)):
        article = dict(row)
        path = Path(article["project_path"])
        result = conversion.render_all(path, journal_slug, {"html", "pdf", "epub"})
        problems.extend(f"{article['slug']}: {e}" for e in (result.errors or []))
        register.record_lint(article["id"], path, lint.run(article),
                             content_hash=register.hash_for(path, "lint"))
        register.record_pdfua(article["id"], path)
    return problems


def main():
    parser = argparse.ArgumentParser(
        description="Register an issue that is on disk but not in the database.")
    parser.add_argument("journal", help="journal slug, e.g. writing-spaces")
    parser.add_argument("issue", help="issue directory, e.g. v7-n1-2027")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--assemble", action="store_true")
    args = parser.parse_args()

    try:
        summary = index_issue(args.journal, args.issue)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    print(f"Issue {summary['issue_id']} "
          f"({'created' if summary['issue_created'] else 'already indexed'}): "
          f"{len(summary['added'])} article(s) added, "
          f"{len(summary['already_indexed'])} already indexed.")
    if args.render:
        problems = render_issue(args.journal, summary["issue_id"])
        print(f"Rendered; {len(problems)} problem(s).")
        for problem in problems:
            print(f"  {problem}")
    if args.assemble:
        result = conversion.assemble_issue(summary["issue_id"])
        print(f"Bound {result.issue_pdf_path}.")
        for error in result.errors or []:
            print(f"  {error}")


if __name__ == "__main__":
    main()
