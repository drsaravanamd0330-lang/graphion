"""Local web interface. Binds to 127.0.0.1 by default and has no login: it is
a single-desk tool. Every action calls the pipeline functions the CLI uses.
"""
from __future__ import annotations

import json
import secrets
import tempfile
from pathlib import Path

from flask import (Flask, abort, flash, redirect, render_template, request, send_file, session,
                   url_for)

from . import config, evidence, pipeline, store
from .model import ARTICLE_TYPES, plain
from .pipeline import WorkflowError
from .profiles import ProfileError, create_journal, list_profiles, load_profile, save_profile_yaml

REVISION_FILES = {"logo.svg", "article.pdf", "index.html", "fulltext.html", "record.json", "report.json", "report.html",
                  "inputs.json", "approval.json", "data.json"}


def create_app() -> Flask:
    app = Flask(__name__, template_folder=str(config.TEMPLATES_DIR / "ui"))
    key_file = config.DATA_DIR / ".secret_key"
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not key_file.exists():
        key_file.write_text(secrets.token_hex(32), encoding="utf-8")
    app.secret_key = key_file.read_text(encoding="utf-8").strip()
    app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
    app.jinja_env.globals.update(plain=plain, ARTICLE_TYPES={k: k.replace("-", " ").title() for k in ARTICLE_TYPES})

    def editor_name() -> str:
        name = (request.form.get("editor") or session.get("editor") or "").strip()
        if not name:
            raise WorkflowError("enter your name in the box at the top of the page first; it is recorded with the action")
        session["editor"] = name
        return name

    @app.context_processor
    def inject():
        return {"editor": session.get("editor", "")}

    @app.errorhandler(WorkflowError)
    @app.errorhandler(ProfileError)
    @app.errorhandler(evidence.CriteriaError)
    def workflow_error(exc):
        flash(str(exc), "error")
        return redirect(request.referrer or url_for("index"))

    @app.post("/editor")
    def set_editor():
        session["editor"] = request.form.get("editor", "").strip()
        return redirect(request.referrer or url_for("index"))

    # ---------------------------------------------------------------- articles

    @app.get("/")
    def index():
        return render_template("index.html", articles=store.list_articles(), profiles=list_profiles())

    @app.post("/import")
    def import_article():
        f = request.files.get("docx")
        if not f or not f.filename.lower().endswith(".docx"):
            raise WorkflowError("choose a .docx manuscript")
        with tempfile.TemporaryDirectory() as tmp:
            docx = Path(tmp) / Path(f.filename).name
            f.save(docx)
            meta = None
            mf = request.files.get("metadata")
            if mf and mf.filename:
                meta = Path(tmp) / "metadata.yaml"
                mf.save(meta)
            aid = pipeline.import_article(docx, request.form["journal"], request.form["article_type"], meta)
        flash(f"Imported as {aid}.", "ok")
        return redirect(url_for("article", aid=aid))

    def _article(aid):
        try:
            row = store.get_article(aid)
        except ValueError:
            abort(404)
        if row is None:
            abort(404)
        return row

    @app.get("/a/<aid>")
    def article(aid):
        row = _article(aid)
        _, profile = pipeline.article_profile(aid)
        record = pipeline.load_record(aid)
        findings = [f.to_dict() for f in pipeline.validate(aid)]
        acks = store.acknowledgements(aid)
        state = pipeline.readiness(findings, acks)
        revs = []
        for r in store.revisions(aid):
            rdir = store.revision_dir(aid, r["number"])
            revs.append({**dict(r), "summary": json.loads(r["summary"]) if r["summary"] else {},
                         "packages": sorted(p.name for p in rdir.glob("*.zip"))})
        editor_json = json.dumps(store.read_json(store.article_dir(aid) / "editor.json", {}), indent=2, ensure_ascii=False)
        return render_template("article.html", row=row, record=record, profile=profile, findings=findings,
                               acks=acks, state=state, revisions=revs, editor_json=editor_json)

    @app.post("/a/<aid>/metadata")
    def metadata(aid):
        _article(aid)
        form = request.form
        pub = {k: form.get(k, "").strip() or None for k in ("volume", "issue", "article_number", "first_page", "last_page", "doi")}
        patch = {"publication": {**pub, "doi_registered": form.get("doi_registered") == "on"},
                 "dates": {k: form.get(f"date_{k}") for k in ("received", "revised", "accepted", "published")
                           if form.get(f"date_{k}")}}
        if form.get("short_title", "").strip():
            patch["short_title"] = form["short_title"].strip()
        authors = {}
        for a in pipeline.load_record(aid)["authors"]:
            authors[a["id"]] = {"orcid_verified": form.get(f"orcid_verified_{a['id']}") == "on",
                                "corresponding": form.get(f"corresponding_{a['id']}") == "on"}
            for fld in ("given", "family", "email"):
                v = form.get(f"{fld}_{a['id']}")
                if v is not None and v.strip():
                    authors[a["id"]][fld] = v.strip()
        patch["authors_patch"] = authors
        pipeline.update_editor(aid, patch)
        store.log(aid, "metadata-form", {"by": editor_name()})
        flash("Metadata saved. Render a new revision to see it in the outputs.", "ok")
        return redirect(url_for("article", aid=aid) + "#metadata")

    @app.post("/a/<aid>/editor-json")
    def editor_json(aid):
        _article(aid)
        try:
            data = json.loads(request.form["editor_json"] or "{}")
        except json.JSONDecodeError as exc:
            raise WorkflowError(f"editor metadata is not valid JSON: {exc}")
        if not isinstance(data, dict):
            raise WorkflowError("editor metadata must be a JSON object")
        editor_name()
        store.write_json(store.article_dir(aid) / "editor.json", {})
        pipeline.update_editor(aid, data)
        flash("Editor metadata replaced.", "ok")
        return redirect(url_for("article", aid=aid) + "#metadata")

    @app.post("/a/<aid>/reimport")
    def reimport(aid):
        _article(aid)
        f = request.files.get("docx")
        if not f or not f.filename.lower().endswith(".docx"):
            raise WorkflowError("choose a .docx manuscript")
        editor_name()
        with tempfile.TemporaryDirectory() as tmp:
            docx = Path(tmp) / Path(f.filename).name
            f.save(docx)
            pipeline.reimport(aid, docx)
        flash("Manuscript replaced. Editor metadata was kept.", "ok")
        return redirect(url_for("article", aid=aid))

    @app.post("/a/<aid>/ack")
    def ack(aid):
        _article(aid)
        pipeline.acknowledge(aid, request.form["fingerprint"], request.form["check"], editor_name(), request.form.get("note", ""))
        return redirect(url_for("article", aid=aid) + "#findings")

    @app.post("/a/<aid>/render")
    def render(aid):
        _article(aid)
        res = pipeline.render(aid)
        flash(f"Revision {res['number']}: {res['status']}" + (" (inputs unchanged, existing revision reused)" if res["reproduced"] else "."), "ok")
        return redirect(url_for("revision", aid=aid, n=res["number"]))

    @app.get("/a/<aid>/r/<int:n>")
    def revision(aid, n):
        row = _article(aid)
        rev = store.revision(aid, n)
        if rev is None:
            abort(404)
        report = pipeline.revision_report(aid, n)
        rdir = store.revision_dir(aid, n)
        pages = len(pipeline.page_images(aid, n)) if (rdir / "article.pdf").is_file() else 0
        acks = store.acknowledgements(aid)
        current = pipeline.readiness(pipeline.validate(aid), acks)
        xml = {p.parent.name: store.read_json(p) for p in rdir.glob("xml*/validation.json")}
        return render_template("revision.html", row=row, rev=rev, report=report, pages=pages, current=current,
                               packages=sorted(p.name for p in rdir.glob("*.zip")), xml=xml)

    @app.get("/a/<aid>/r/<int:n>/file/<path:name>")
    def revision_file(aid, n, name):
        """Files of one revision. The HTML pages link to each other, to
        article.pdf and to media/ by relative path, so they work here as they
        will on the journal site."""
        _article(aid)
        rdir = store.revision_dir(aid, n).resolve()
        if name.startswith("media/"):
            path = (rdir / name).resolve()
            if (rdir / "media") not in path.parents:
                abort(404)
        elif name in REVISION_FILES or (name.endswith(".zip") and "/" not in name and "\\" not in name):
            path = rdir / name
        else:
            abort(404)
        if not path.is_file():
            abort(404)
        return send_file(path, as_attachment=name.endswith(".zip"))

    @app.get("/a/<aid>/r/<int:n>/page/<int:p>")
    def page_image(aid, n, p):
        _article(aid)
        imgs = pipeline.page_images(aid, n)
        if not 1 <= p <= len(imgs):
            abort(404)
        return send_file(imgs[p - 1], mimetype="image/png")

    @app.post("/a/<aid>/r/<int:n>/approve")
    def approve(aid, n):
        _article(aid)
        pipeline.approve(aid, n, editor_name())
        flash(f"Revision {n} approved.", "ok")
        return redirect(url_for("revision", aid=aid, n=n))

    @app.post("/a/<aid>/r/<int:n>/export")
    def export(aid, n):
        _article(aid)
        editor_name()
        res = pipeline.export(aid, n, jats=request.form.get("jats") == "on", crossref=request.form.get("crossref") == "on")
        msg = f"{res['path'].name}: " + ("an identical package already existed and was reused." if res["reproduced"] else "package built.")
        flash(msg, "ok")
        return redirect(url_for("revision", aid=aid, n=n) + "#export")

    # ---------------------------------------------------------------- journals

    @app.get("/journals")
    def journals():
        return render_template("journals.html", profiles=list_profiles())

    @app.get("/journals/<path:pid>/edit")
    def journal_edit(pid):
        prof = load_profile(pid)
        return render_template("journal_edit.html", profile=prof, text=(prof.path / "profile.yaml").read_text(encoding="utf-8"))

    @app.post("/journals/<path:pid>/edit")
    def journal_save(pid):
        editor_name()
        save_profile_yaml(pid, request.form["text"])
        store.log(None, "profile-saved", {"profile": pid, "by": session.get("editor")})
        flash("Profile saved. Articles in this journal will render a new revision next time.", "ok")
        return redirect(url_for("journal_edit", pid=pid))

    @app.post("/journals/new")
    def journal_new():
        editor_name()
        prof = create_journal(request.form["publisher"].strip(), request.form["slug"].strip(), request.form["title"].strip(),
                              request.form["clone_from"])
        flash(f"Created {prof.id}. Fill in and verify its details before using it.", "ok")
        return redirect(url_for("journal_edit", pid=prof.id))

    @app.get("/journals/<path:pid>/evidence/<index>")
    def journal_evidence(pid, index):
        prof = load_profile(pid)
        return render_template("evidence.html", profile=prof, v=evidence.view(prof, index), indexes=evidence.INDEXES,
                               statuses=evidence.STATUSES)

    @app.post("/journals/<path:pid>/evidence/<index>")
    def journal_evidence_set(pid, index):
        prof = load_profile(pid)
        f = request.form
        evidence.set_status(prof.id, index, f["criterion"], f["status"], f.get("evidence_url", "").strip(),
                            f.get("note", "").strip(), f.get("checked_on", "").strip(), editor_name())
        return redirect(url_for("journal_evidence", pid=pid, index=index) + f"#c-{f['criterion']}")

    @app.get("/criteria/<index>")
    def criteria_edit(index):
        return render_template("criteria_edit.html", index=index, text=evidence.criteria_path(index).read_text(encoding="utf-8"),
                               history=evidence.history(index), profiles=list_profiles())

    @app.post("/criteria/<index>")
    def criteria_save(index):
        data = evidence.save_criteria(index, request.form["text"], editor_name())
        flash(f"Saved as version {data['version']}. Statuses recorded against the old version now show as needing review.", "ok")
        return redirect(url_for("criteria_edit", index=index))

    return app
