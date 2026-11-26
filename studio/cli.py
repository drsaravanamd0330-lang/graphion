"""Command line interface. Every command calls the same pipeline functions
the web interface uses, so a proof made here is the proof the UI shows.

    python -m studio sample out.docx
    python -m studio import manuscript.docx --journal mcmed-international/sample-clinical-research --type original-research --metadata meta.yaml
    python -m studio validate ART_ID
    python -m studio render ART_ID
    python -m studio ack ART_ID FINGERPRINT --by "Editor Name" --note "checked"
    python -m studio approve ART_ID 1 --by "Editor Name"
    python -m studio export ART_ID [--revision N] [--jats] [--crossref]
    python -m studio serve
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STATUS_MARK = {"pass": "PASS", "warning": "WARN", "blocked": "BLOCK", "manual": "REVIEW", "na": "N/A"}


def _print_findings(findings, acks=None, show_pass=False):
    acks = acks or {}
    for f in findings:
        d = f.to_dict() if hasattr(f, "to_dict") else f
        if d["status"] == "pass" and not show_pass:
            continue
        mark = STATUS_MARK.get(d["status"], d["status"])
        if d["status"] == "manual" and d["fingerprint"] in acks:
            mark = "ACKED"
        where = f" [{d['field']}]" if d.get("field") else ""
        print(f"  {mark:6} {d['title']}{where}")
        if d.get("explanation"):
            print(f"         {d['explanation']}")
        if d["status"] == "manual" and d["fingerprint"] not in acks:
            print(f"         acknowledge with: ack <article> {d['fingerprint']} --by NAME")


def cmd_sample(a):
    from .fixtures.make_sample import build
    build(Path(a.out), tracked_change=a.tracked_change)
    print(f"wrote synthetic fixture {a.out}")


def cmd_profiles(a):
    from .profiles import list_profiles
    for p in list_profiles():
        print(f"{p.id:55} {p.title}  ({p.publisher_name})")


def cmd_import(a):
    from . import pipeline
    aid = pipeline.import_article(Path(a.docx), a.journal, a.type, Path(a.metadata) if a.metadata else None)
    print(aid)
    findings = pipeline.validate(aid)
    print(pipeline.readiness(findings, {})["label"], file=sys.stderr)


def cmd_validate(a):
    from . import pipeline, store
    findings = pipeline.validate(a.article)
    acks = store.acknowledgements(a.article)
    _print_findings(findings, acks, a.all)
    state = pipeline.readiness(findings, acks)
    print(state["label"])
    return 0 if state["ready"] else 2


def cmd_render(a):
    from . import pipeline
    res = pipeline.render(a.article)
    rep = pipeline.revision_report(a.article, res["number"])
    print(f"revision {res['number']}: {res['status']}" + (" (reproduced, nothing rewritten)" if res["reproduced"] else ""))
    print(f"  {rep.get('summary')}")
    from . import store
    print(f"  {store.revision_dir(a.article, res['number'])}")
    return 0 if res["status"] != "failed" else 1


def cmd_ack(a):
    from . import pipeline
    findings = {f.fingerprint: f for f in pipeline.validate(a.article)}
    f = findings.get(a.fingerprint)
    if f is None:
        print("no current finding has that fingerprint", file=sys.stderr)
        return 1
    pipeline.acknowledge(a.article, a.fingerprint, f.check, a.by, a.note or "")
    print(f"acknowledged: {f.title}")


def cmd_approve(a):
    from . import pipeline
    pipeline.approve(a.article, a.revision, a.by)
    print(f"revision {a.revision} approved by {a.by}")


def cmd_export(a):
    from . import pipeline
    res = pipeline.export(a.article, a.revision, jats=a.jats, crossref=a.crossref)
    print(res["path"])
    print("reproduced an existing package byte for byte" if res["reproduced"] else f"new {res['kind']} package")
    for name, v in res["xml"].items():
        verdict = "valid" if v["valid"] else ("NOT GENERATED" if v.get("note") == "deposit not generated" else "NOT VALID")
        print(f"  {name}: {verdict} ({v['schema']})")
        for e in v.get("errors", [])[:10]:
            print(f"    {e}")
        for n in v.get("mapping_notes", []):
            print(f"    note: {n}")


def cmd_live(a):
    from . import checks, pipeline
    _print_findings(checks.live_checks(pipeline.load_record(a.article)), show_pass=True)


def cmd_list(a):
    from . import store
    for r in store.list_articles():
        print(f"{r['id']}  {r['journal_id']:50} rev {r['latest_rev'] or '-'} {r['latest_status'] or ''}  {r['title'] or ''}")


def cmd_evidence(a):
    from . import evidence
    from .profiles import load_profile
    v = evidence.view(load_profile(a.journal), a.index)
    c = v["criteria"]
    print(f"{c['name']} criteria v{c.get('version')} ({c['verification']}, last checked {c.get('last_checked') or 'never'})")
    for s in c["official_sources"]:
        print(f"  source: {s['url']}")
    for r in v["rows"]:
        print(f"  {r['status']:15} {r['id']:22} {r['text']}")
        if r["observation"]:
            print(f"  {'':15} {'':22} observed: {r['observation']}")


def cmd_serve(a):
    from .web import create_app
    app = create_app()
    print(f"Journal Typesetting Studio on http://{a.host}:{a.port}")
    app.run(host=a.host, port=a.port, debug=False)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m studio", description="Journal Typesetting Studio")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample", help="write the synthetic fixture manuscript")
    s.add_argument("out")
    s.add_argument("--tracked-change", action="store_true", help="include an unresolved tracked insertion")
    s.set_defaults(fn=cmd_sample)
    sub.add_parser("profiles", help="list journal profiles").set_defaults(fn=cmd_profiles)
    sub.add_parser("list", help="list articles").set_defaults(fn=cmd_list)
    s = sub.add_parser("import", help="import an accepted DOCX")
    s.add_argument("docx")
    s.add_argument("--journal", required=True)
    s.add_argument("--type", default="original-research", choices=["original-research", "review", "case-report", "editorial"])
    s.add_argument("--metadata", help="editor-approved metadata (YAML or JSON)")
    s.set_defaults(fn=cmd_import)
    s = sub.add_parser("validate", help="run article checks")
    s.add_argument("article")
    s.add_argument("--all", action="store_true", help="show passing checks too")
    s.set_defaults(fn=cmd_validate)
    s = sub.add_parser("render", help="render a proof revision (PDF, HTML, report)")
    s.add_argument("article")
    s.set_defaults(fn=cmd_render)
    s = sub.add_parser("ack", help="acknowledge a finding that needs manual review")
    s.add_argument("article")
    s.add_argument("fingerprint")
    s.add_argument("--by", required=True)
    s.add_argument("--note")
    s.set_defaults(fn=cmd_ack)
    s = sub.add_parser("approve", help="approve a proof revision")
    s.add_argument("article")
    s.add_argument("revision", type=int)
    s.add_argument("--by", required=True)
    s.set_defaults(fn=cmd_approve)
    s = sub.add_parser("export", help="build the ZIP package")
    s.add_argument("article")
    s.add_argument("--revision", type=int)
    s.add_argument("--jats", action="store_true")
    s.add_argument("--crossref", action="store_true")
    s.set_defaults(fn=cmd_export)
    s = sub.add_parser("live", help="check registered DOI and live pages (network)")
    s.add_argument("article")
    s.set_defaults(fn=cmd_live)
    s = sub.add_parser("evidence", help="show journal indexing evidence")
    s.add_argument("journal")
    s.add_argument("index", choices=["doaj", "scopus", "wos"])
    s.set_defaults(fn=cmd_evidence)
    s = sub.add_parser("serve", help="run the local web interface")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=5070)
    s.set_defaults(fn=cmd_serve)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    from .pipeline import WorkflowError
    try:
        return a.fn(a) or 0
    except WorkflowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
