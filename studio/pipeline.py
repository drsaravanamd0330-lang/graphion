"""The production workflow, shared by the CLI and the web interface.

import -> review/edit metadata -> validate -> render revision -> approve -> export

A revision is identified by a key hashed from everything that shapes its
outputs: the manuscript, the extracted record, the editor metadata, the
journal profile version, the templates and CSL style, and the Studio version.
Rendering with an unchanged key returns the existing revision; any change
makes a new one, which needs its own approval. Nothing in an existing
revision directory is ever rewritten.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

from . import checks, config, store
from .checks import BLOCKED, MANUAL, Finding
from .docx_import import import_docx
from .model import plain, sha256_bytes, sha256_json
from .profiles import Profile, git_commit, load_profile
from .records import load_metadata_file, merge, normalise_editor

DIAGNOSTIC_MARK = "DIAGNOSTIC DRAFT · NOT FOR PUBLICATION"


class WorkflowError(RuntimeError):
    pass


# ------------------------------------------------------------------ import

def import_article(docx: Path, journal_id: str, article_type: str, metadata: Path | dict | None = None) -> str:
    profile = load_profile(journal_id)
    if not profile.article_type(article_type):
        raise WorkflowError(f"{journal_id} has no article type {article_type!r}")
    editor = {}
    if isinstance(metadata, dict):
        editor = normalise_editor(metadata)
    elif metadata:
        editor = load_metadata_file(Path(metadata))
    aid = store.new_article_id()
    adir = store.article_dir(aid)
    adir.mkdir(parents=True)
    try:
        shutil.copyfile(docx, adir / "source.docx")
        extracted = import_docx(adir / "source.docx", adir / "media")
        extracted["provenance"]["source_file"] = Path(docx).name
    except Exception:
        shutil.rmtree(adir, ignore_errors=True)
        raise
    store.write_json(adir / "extracted.json", extracted)
    store.write_json(adir / "editor.json", editor)
    now = store.now_iso()
    with store.db() as con:
        con.execute("INSERT INTO articles(id, journal_id, article_type, title, source_name, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (aid, journal_id, article_type, plain(extracted["title"]["en"])[:300], Path(docx).name, now, now))
    rebuild_record(aid)
    store.log(aid, "imported", {"source": Path(docx).name, "journal": journal_id})
    return aid


def reimport(article_id: str, docx: Path) -> None:
    """Replace the manuscript. Editor metadata is kept; revisions are kept."""
    adir = store.article_dir(article_id)
    shutil.copyfile(docx, adir / "source.docx")
    media = adir / "media"
    if media.exists():
        shutil.rmtree(media)
    extracted = import_docx(adir / "source.docx", media)
    extracted["provenance"]["source_file"] = Path(docx).name
    store.write_json(adir / "extracted.json", extracted)
    rebuild_record(article_id)
    store.log(article_id, "reimported", {"source": Path(docx).name})


# ------------------------------------------------------------------ record

def article_profile(article_id: str) -> tuple[Any, Profile]:
    row = store.get_article(article_id)
    if row is None:
        raise WorkflowError(f"no article {article_id}")
    return row, load_profile(row["journal_id"])


def rebuild_record(article_id: str) -> dict:
    row, profile = article_profile(article_id)
    adir = store.article_dir(article_id)
    extracted = store.read_json(adir / "extracted.json")
    editor = store.read_json(adir / "editor.json", {})
    record = merge(extracted, editor, profile, article_id, row["article_type"])
    store.write_json(adir / "record.json", record)
    with store.db() as con:
        con.execute("UPDATE articles SET title=?, article_type=?, updated_at=? WHERE id=?",
                    (plain(record["title"]["en"])[:300], record["article_type"], store.now_iso(), article_id))
    return record


def load_record(article_id: str) -> dict:
    return rebuild_record(article_id)


def update_editor(article_id: str, patch: dict) -> dict:
    """Merge an edit into editor.json (patch maps merge; other keys replace)."""
    adir = store.article_dir(article_id)
    editor = store.read_json(adir / "editor.json", {})
    patch = normalise_editor(patch)
    for k, v in patch.items():
        if k.endswith("_patch") and isinstance(v, dict):
            cur = editor.setdefault(k, {})
            for item_id, fields in v.items():
                cur.setdefault(item_id, {}).update(fields)
        elif isinstance(v, dict) and isinstance(editor.get(k), dict) and k not in ("license",):
            editor[k] = {**editor[k], **v}
        else:
            editor[k] = v
    store.write_json(adir / "editor.json", editor)
    store.log(article_id, "metadata-edited", {"keys": sorted(patch)})
    return rebuild_record(article_id)


def validate(article_id: str) -> list[Finding]:
    record = load_record(article_id)
    _, profile = article_profile(article_id)
    return checks.sort_findings(checks.record_checks(record, profile))


def acknowledge(article_id: str, fingerprint: str, check_id: str, by: str, note: str = "") -> None:
    if not by.strip():
        raise WorkflowError("an acknowledgement needs the editor's name")
    with store.db() as con:
        con.execute("INSERT OR REPLACE INTO acknowledgements VALUES (?,?,?,?,?,?)",
                    (article_id, fingerprint, check_id, note, by.strip(), store.now_iso()))
    store.log(article_id, "acknowledged", {"check": check_id, "fingerprint": fingerprint, "by": by})


def readiness(findings: list[Finding | dict], acks: dict) -> dict:
    fs = [f.to_dict() if isinstance(f, Finding) else f for f in findings]
    blocked = [f for f in fs if f["status"] == BLOCKED]
    manual_open = [f for f in fs if f["status"] == MANUAL and f["fingerprint"] not in acks]
    ready = not blocked and not manual_open
    return {"ready": ready, "blocked": len(blocked), "manual_open": len(manual_open),
            "label": "Ready for editorial review" if ready else
            f"Not ready: {len(blocked)} blocked, {len(manual_open)} awaiting manual review"}


# ------------------------------------------------------------------ inputs and keys

RENDER_MODULES = ("typst_render.py", "html_render.py", "references.py")


def template_hash(profile: Profile) -> str:
    h = hashlib.sha256()
    files = [config.TYPST_DIR / "lib.typ", profile.csl_path()]
    files += sorted((config.PACKAGE_DIR / "fonts").glob("*.[ot]tf"))
    files += sorted((profile.path / "fonts").glob("*.[ot]tf")) if (profile.path / "fonts").is_dir() else []
    files += sorted((config.TEMPLATES_DIR / "output").glob("*.j2"))
    # The code that turns a record into a page is part of the template: a
    # change to it must make a new revision rather than reuse an old one.
    files += [config.PACKAGE_DIR / n for n in RENDER_MODULES]
    for f in files:
        h.update(f.name.encode() + b"\0" + f.read_bytes())
    return h.hexdigest()[:16]


def revision_inputs(article_id: str, record: dict, profile: Profile, mode: str) -> dict:
    adir = store.article_dir(article_id)
    extracted = store.read_json(adir / "extracted.json")
    editor = store.read_json(adir / "editor.json", {})
    return {
        "manuscript_sha256": extracted["provenance"]["source_sha256"],
        "extracted_sha256": sha256_json(extracted),
        "editor_metadata_sha256": sha256_json(editor),
        "record_sha256": sha256_json(record),
        "profile_id": profile.id,
        "profile_version": profile.version(),
        "template_version": template_hash(profile),
        "csl_style": profile.csl_path().name,
        "studio_version": config.STUDIO_VERSION,
        "mode": mode,
    }


def input_key(inputs: dict) -> str:
    return sha256_json(inputs)[:20]


# ------------------------------------------------------------------ render

def render(article_id: str) -> dict:
    """Render a proof revision, or return the existing one for unchanged inputs."""
    from . import html_render, typst_render

    record = load_record(article_id)
    row, profile = article_profile(article_id)
    rec_findings = checks.record_checks(record, profile)
    mode = "diagnostic" if any(f.status == BLOCKED for f in rec_findings) else "proof"
    inputs = revision_inputs(article_id, record, profile, mode)
    key = input_key(inputs)
    with store.db() as con:
        existing = con.execute("SELECT * FROM revisions WHERE article_id=? AND input_key=?", (article_id, key)).fetchone()
    if existing:
        store.log(article_id, "render-reproduced", {"revision": existing["number"]})
        return {"number": existing["number"], "reproduced": True, "status": existing["status"]}

    with store.db() as con:
        n = (con.execute("SELECT MAX(number) FROM revisions WHERE article_id=?", (article_id,)).fetchone()[0] or 0) + 1
        created = store.now_iso()
        con.execute("INSERT INTO revisions(article_id, number, input_key, status, created_at) VALUES (?,?,?,?,?)",
                    (article_id, n, key, "rendering", created))
    rdir = store.revision_dir(article_id, n)
    if rdir.exists():
        raise WorkflowError(f"revision directory {rdir} already exists; refusing to overwrite it")
    rdir.mkdir(parents=True)
    watermark = DIAGNOSTIC_MARK if mode == "diagnostic" else None
    ts = dt.datetime.fromisoformat(created)

    store.write_json(rdir / "record.json", record)
    inputs["profile_git_commit"] = git_commit(profile.path / "profile.yaml")
    inputs["key"] = key
    inputs["created_at"] = created
    store.write_json(rdir / "inputs.json", inputs)

    view, facts = typst_render.build_view(record, profile, watermark)
    typst_render.write_sources(view, profile, store.article_dir(article_id) / "media", rdir)
    ok, msgs = typst_render.compile_pdf(rdir, ts, typst_render.font_dirs_for(profile))
    pages = html_render.render_pages(record, profile, facts["references"], watermark)
    for name, html in pages.items():
        (rdir / name).write_text(html, encoding="utf-8")

    out_findings = checks.pdf_checks(rdir / "article.pdf", record, msgs)
    page_paths = {name: rdir / name for name in pages}
    out_findings += checks.html_checks(page_paths, record)
    out_findings += checks.agreement_checks(record, rdir / "article.pdf", page_paths, rdir / "record.json")
    for r in facts["references"]:
        if r["note"]:
            out_findings.append(Finding("reference-format", checks.WARNING, f"Reference {r['label']} formatting", r["note"],
                                        f"references.{r['id']}", "pdf"))
    missing_fonts = sorted({m.split("unknown font family:")[1].strip() for m in msgs if "unknown font family:" in m})
    if missing_fonts:
        out_findings.append(Finding("pdf-font-family", checks.WARNING, "Font family not found",
                                    f"Typst could not find {', '.join(missing_fonts)} and used a fallback. Add the font files to "
                                    "the profile's fonts/ folder or change the profile.", "profile.branding.fonts", "pdf"))
    for e in facts["math_failed"]:
        out_findings.append(Finding("math-convert", BLOCKED, "Equation conversion", f"Pandoc could not convert: {e}", "equations", "pdf"))
    for note in facts["notes"]:
        out_findings.append(Finding("table-width", checks.WARNING, "Wide table", note, "tables", "pdf"))
    csl_n = sum(1 for r in facts["references"] if r["mode"] == "csl")
    out_findings.append(Finding("reference-style", checks.PASS if csl_n else checks.NA, "Reference style",
                                f"{csl_n} of {len(facts['references'])} references formatted with {profile.csl_path().name}; "
                                "the rest print as typed.", "references", "pdf"))

    findings = checks.sort_findings(rec_findings + out_findings)
    if not ok:
        status = "failed"
    elif mode == "diagnostic":
        status = "diagnostic"
    elif any(f.status == BLOCKED for f in out_findings):
        status = "blocked"
    else:
        status = "proof"
    report = {
        "article_id": article_id, "revision": n, "status": status, "mode": mode, "created_at": created,
        "inputs": inputs, "summary": checks.summarise(findings),
        "findings": [f.to_dict() for f in findings],
        "compile_messages": msgs,
        "references": [{k: r[k] for k in ("id", "label", "mode", "note", "raw")} for r in facts["references"]],
        "tables": facts["tables"],
        "urls": {"live": record["urls"], "staged": {"landing": "index.html", "fulltext": "fulltext.html", "pdf": "article.pdf"}},
    }
    store.write_json(rdir / "report.json", report)
    write_report_html(rdir, report, record, profile)
    with store.db() as con:
        con.execute("UPDATE revisions SET status=?, summary=? WHERE article_id=? AND number=?",
                    (status, json.dumps(report["summary"]), article_id, n))
    store.log(article_id, "rendered", {"revision": n, "status": status})
    return {"number": n, "reproduced": False, "status": status}


def write_report_html(rdir: Path, report: dict, record: dict, profile: Profile, extra: dict | None = None) -> None:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    env = Environment(loader=FileSystemLoader(str(config.TEMPLATES_DIR / "output")), autoescape=select_autoescape(["html", "j2"]))
    html = env.get_template("report.html.j2").render(report=report, record=record, title=plain(record["title"]["en"]),
                                                     journal=profile.title, extra=extra or {},
                                                     status_label=checks.STATUS_LABEL)
    (rdir / "report.html").write_text(html, encoding="utf-8")


def revision_report(article_id: str, number: int) -> dict:
    return store.read_json(store.revision_dir(article_id, number) / "report.json", {})


# ------------------------------------------------------------------ approval

def approve(article_id: str, number: int, by: str) -> None:
    rev = store.revision(article_id, number)
    if rev is None:
        raise WorkflowError(f"no revision {number}")
    if rev["status"] == "approved":
        raise WorkflowError(f"revision {number} is already approved")
    if rev["status"] != "proof":
        raise WorkflowError(f"revision {number} is {rev['status']}; only a proof with no blocking findings can be approved")
    if not by.strip():
        raise WorkflowError("approval needs the editor's name")
    # The approval must be for the inputs as they stand now.
    record = load_record(article_id)
    _, profile = article_profile(article_id)
    if input_key(revision_inputs(article_id, record, profile, "proof")) != rev["input_key"]:
        raise WorkflowError("the manuscript, metadata, profile or template changed after this revision; render a new revision")
    report = revision_report(article_id, number)
    state = readiness(report["findings"], store.acknowledgements(article_id))
    if not state["ready"]:
        raise WorkflowError(state["label"])
    at = store.now_iso()
    rdir = store.revision_dir(article_id, number)
    approval = {"article_id": article_id, "revision": number, "input_key": rev["input_key"], "approved_by": by.strip(),
                "approved_at": at, "files": {p.name: sha256_bytes(p.read_bytes()) for p in sorted(rdir.iterdir()) if p.is_file()},
                "acknowledged": [dict(a) for a in store.acknowledgements(article_id).values()]}
    store.write_json(rdir / "approval.json", approval)
    with store.db() as con:
        con.execute("UPDATE revisions SET status='approved', approved_by=?, approved_at=? WHERE article_id=? AND number=?",
                    (by.strip(), at, article_id, number))
    store.log(article_id, "approved", {"revision": number, "by": by})


# ------------------------------------------------------------------ export

ZIP_EPOCH = (2020, 1, 1, 0, 0, 0)


def export(article_id: str, number: int | None = None, jats: bool = False, crossref: bool = False) -> dict:
    """Build the publication package for a revision (latest approved by default).

    The ZIP is deterministic: the same revision and options produce the same
    bytes. An existing package is never rewritten; a repeat export verifies
    it reproduces and returns it.
    """
    revs = store.revisions(article_id)
    if number is None:
        approved = [r for r in revs if r["status"] == "approved"]
        if not approved:
            raise WorkflowError("no approved revision; pass a revision number to export a proof package")
        number = approved[-1]["number"]
    rev = store.revision(article_id, number)
    if rev is None:
        raise WorkflowError(f"no revision {number}")
    if rev["status"] in ("failed", "rendering"):
        raise WorkflowError(f"revision {number} is {rev['status']} and cannot be exported")
    rdir = store.revision_dir(article_id, number)
    record = store.read_json(rdir / "record.json")
    _, profile = article_profile(article_id)
    kind = "approved" if rev["status"] == "approved" else "proof"
    variant = "".join(["-jats" if jats else "", "-crossref" if crossref else ""])
    xml_dir = rdir / f"xml{variant}" if variant else None
    xml_results: dict[str, Any] = {}
    if variant:
        xml_dir.mkdir(exist_ok=True)
        if jats:
            from . import jats as jats_mod
            path = xml_dir / "article.jats.xml"
            if not path.exists():
                path.write_bytes(jats_mod.build(record, profile))
            xml_results["jats"] = jats_mod.validate(path)
            xml_results["jats"]["mapping_notes"] = jats_mod.mapping_losses(record, profile)
        if crossref:
            from . import crossref as cr_mod
            problems = cr_mod.readiness(record, profile)
            path = xml_dir / "article.crossref.xml"
            if problems:
                xml_results["crossref"] = {"valid": False, "schema": cr_mod.SCHEMA_NAME, "errors": problems,
                                           "note": "deposit not generated"}
            else:
                if not path.exists():
                    path.write_bytes(cr_mod.build(record, profile, timestamp=rev["created_at"]))
                xml_results["crossref"] = cr_mod.validate(path)
        store.write_json(xml_dir / "validation.json", xml_results)
        xml_paths = {p.name: p for p in xml_dir.glob("*.xml")}
        agreement = checks.agreement_checks(record, rdir / "article.pdf", {n: rdir / n for n in ("index.html", "fulltext.html")},
                                            rdir / "record.json", xml_paths)
        store.write_json(xml_dir / "agreement.json", [f.to_dict() for f in agreement])
    members = _package_members(rdir, xml_dir)
    manifest = {
        "article_id": article_id, "revision": number, "kind": kind, "input_key": rev["input_key"],
        "approved_by": rev["approved_by"], "approved_at": rev["approved_at"],
        "files": {name: sha256_bytes(p.read_bytes()) for name, p in members},
        "xml_validation": xml_results,
        "note": None if kind == "approved" else "Proof package: this revision has not been approved for publication.",
    }
    data = _zip_bytes(members, manifest)
    out = rdir / f"{article_id}-r{number:03d}-{kind}{variant}.zip"
    if out.exists():
        if out.read_bytes() != data:
            raise WorkflowError(f"{out.name} exists and a rebuild differs from it; the existing package was left untouched")
        store.log(article_id, "export-reproduced", {"revision": number, "file": out.name})
        return {"path": out, "reproduced": True, "kind": kind, "xml": xml_results}
    out.write_bytes(data)
    store.log(article_id, "exported", {"revision": number, "file": out.name})
    return {"path": out, "reproduced": False, "kind": kind, "xml": xml_results}


def _package_members(rdir: Path, xml_dir: Path | None) -> list[tuple[str, Path]]:
    names = ["article.pdf", "index.html", "fulltext.html", "record.json", "report.json", "report.html", "inputs.json",
             "approval.json"]
    members = [(n, rdir / n) for n in names if (rdir / n).is_file()]
    media = rdir / "media"
    if media.is_dir():
        members += [(f"media/{p.name}", p) for p in sorted(media.iterdir()) if p.is_file()]
    if xml_dir is not None:
        members += [(f"xml/{p.name}", p) for p in sorted(xml_dir.iterdir()) if p.is_file()]
    return sorted(members)


def _zip_bytes(members: list[tuple[str, Path]], manifest: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, path in members:
            info = zipfile.ZipInfo(name, ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, path.read_bytes())
        info = zipfile.ZipInfo("manifest.json", ZIP_EPOCH)
        info.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(info, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return buf.getvalue()


def page_images(article_id: str, number: int, scale: float = 1.6) -> list[Path]:
    """PNG of every PDF page, for page-by-page review. Cached beside the revision."""
    import pypdfium2 as pdfium
    rdir = store.revision_dir(article_id, number)
    pdf_path = rdir / "article.pdf"
    if not pdf_path.is_file():
        return []
    cache = config.DATA_DIR / "cache" / article_id / f"r{number:03d}"
    cache.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    out = []
    for i in range(len(pdf)):
        p = cache / f"page-{i + 1:03d}.png"
        if not p.exists():
            pdf[i].render(scale=scale).to_pil().save(p)
        out.append(p)
    return out
