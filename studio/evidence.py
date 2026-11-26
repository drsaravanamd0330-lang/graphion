"""Journal-level indexing evidence (DOAJ, Scopus, Web of Science).

Each index has a criteria file in ``studio/criteria/<index>.yaml`` that names
its official sources, when an editor last compared the list with them, and
whether that comparison has been done. Criteria are edited from the web UI or
the file; every saved edit bumps ``version`` and keeps the previous file under
``DATA_DIR/criteria-history/``.

Per journal, an editor records a status for each criterion with the URL that
evidences it. Studio adds what it can observe from the journal profile (an
ISSN entered and marked verified, a licence configured), labelled as an
observation: it never marks a criterion verified on its own. A status recorded
against an older criteria version is shown as needing review.
"""
from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import yaml

from . import config, store
from .profiles import Profile

INDEXES = ("doaj", "scopus", "wos")
STATUSES = ("unverified", "verified", "not-met", "not-applicable")


class CriteriaError(ValueError):
    pass


def criteria_path(index: str) -> Path:
    if index not in INDEXES:
        raise CriteriaError(f"unknown index {index!r}")
    return config.CRITERIA_DIR / f"{index}.yaml"


def load_criteria(index: str) -> dict:
    return yaml.safe_load(criteria_path(index).read_text(encoding="utf-8"))


def validate_criteria(data: dict, index: str) -> list[str]:
    errs = []
    if not isinstance(data, dict):
        return ["criteria file must be a mapping"]
    if data.get("index") != index:
        errs.append(f"index must be {index!r}")
    if not data.get("official_sources"):
        errs.append("official_sources must list at least one source with a url")
    for s in data.get("official_sources") or []:
        if not str(s.get("url", "")).startswith("https://"):
            errs.append(f"official source {s.get('label')!r} needs an https url")
    if data.get("verification") not in ("unverified", "verified"):
        errs.append("verification must be 'unverified' or 'verified'")
    if data.get("verification") == "verified" and not data.get("last_checked"):
        errs.append("a verified criteria list needs last_checked (the date it was compared with the sources)")
    if data.get("last_checked"):
        try:
            dt.date.fromisoformat(str(data["last_checked"]))
        except ValueError:
            errs.append("last_checked must be an ISO date (YYYY-MM-DD)")
    ids = [c.get("id") for c in data.get("criteria") or []]
    if not ids:
        errs.append("criteria list is empty")
    if len(ids) != len(set(ids)):
        errs.append("criterion ids must be unique")
    for c in data.get("criteria") or []:
        if not c.get("id") or not c.get("text"):
            errs.append("every criterion needs an id and text")
    return errs


def save_criteria(index: str, text: str, by: str) -> dict:
    """Validate and save an edited criteria file as the next version."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CriteriaError(f"not valid YAML: {exc}") from exc
    errs = validate_criteria(data, index)
    if errs:
        raise CriteriaError("; ".join(errs))
    path = criteria_path(index)
    old = load_criteria(index)
    hist = config.DATA_DIR / "criteria-history"
    hist.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, hist / f"{index}-v{old.get('version', 1)}.yaml")
    data["version"] = int(old.get("version", 1)) + 1
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=10_000)
    path.write_text(f"# Edited by {by} on {dt.date.today().isoformat()}; previous version kept in criteria-history.\n" + body,
                    encoding="utf-8")
    store.log(None, "criteria-saved", {"index": index, "version": data["version"], "by": by})
    return data


def history(index: str) -> list[Path]:
    hist = config.DATA_DIR / "criteria-history"
    return sorted(hist.glob(f"{index}-v*.yaml")) if hist.is_dir() else []


def observations(profile: Profile) -> dict[str, str]:
    """What the journal profile shows for criteria marked ``auto``. These are
    hints for the editor, never a verified status."""
    j = profile.journal
    out = {}
    issns = [v for v in (j.get("issn_print"), j.get("issn_online")) if isinstance(v, dict) and v.get("value")]
    if any(v.get("verified") for v in issns):
        out["issn"] = "Profile has an ISSN marked verified: " + ", ".join(v["value"] for v in issns if v.get("verified"))
    elif issns:
        out["issn"] = "Profile has an ISSN that is not marked verified."
    else:
        out["issn"] = "Profile has no ISSN."
    lic = (profile.data.get("license") or {}).get("default") or {}
    if lic.get("url"):
        verified = (profile.data.get("license") or {}).get("verified")
        out["licence"] = f"Profile licence: {lic.get('type')} ({lic['url']})" + ("" if verified else ", policy not marked verified.")
        out["copyright"] = f"Profile copyright holder: {lic.get('copyright_holder') or 'not set'}."
    else:
        out["licence"] = "Profile has no default licence."
    oa = (profile.data.get("policies") or {}).get("open_access")
    out["open_access"] = ("Profile marks the journal as open access." if oa
                          else "Profile does not state open access." if oa is None else "Profile marks the journal as not open access.")
    out["english_abstracts"] = "Studio blocks approval of any article without an English title and abstract."
    return out


def journal_status(journal_id: str, index: str) -> dict[str, dict]:
    with store.db() as con:
        rows = con.execute("SELECT * FROM evidence_status WHERE journal_id=? AND index_name=?", (journal_id, index)).fetchall()
    return {r["criterion_id"]: dict(r) for r in rows}


def set_status(journal_id: str, index: str, criterion_id: str, status: str, evidence_url: str, note: str,
               checked_on: str, by: str) -> None:
    if status not in STATUSES:
        raise CriteriaError(f"status must be one of {', '.join(STATUSES)}")
    crit = load_criteria(index)
    if criterion_id not in {c["id"] for c in crit["criteria"]}:
        raise CriteriaError(f"{index} has no criterion {criterion_id!r}")
    if status == "verified" and not evidence_url.startswith(("https://", "http://")):
        raise CriteriaError("a verified criterion needs the URL of the page that evidences it")
    if checked_on:
        dt.date.fromisoformat(checked_on)
    with store.db() as con:
        con.execute("""INSERT INTO evidence_status(journal_id, index_name, criterion_id, status, evidence_url, note,
                       checked_on, verified_by, criteria_version) VALUES (?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(journal_id, index_name, criterion_id) DO UPDATE SET status=excluded.status,
                       evidence_url=excluded.evidence_url, note=excluded.note, checked_on=excluded.checked_on,
                       verified_by=excluded.verified_by, criteria_version=excluded.criteria_version""",
                    (journal_id, index, criterion_id, status, evidence_url or None, note or None, checked_on or None, by,
                     str(crit.get("version", 1))))
    store.log(None, "evidence-set", {"journal": journal_id, "index": index, "criterion": criterion_id, "status": status})


def view(profile: Profile, index: str) -> dict:
    """Everything the evidence page shows for one journal and one index."""
    crit = load_criteria(index)
    status = journal_status(profile.id, index)
    obs = observations(profile)
    version = str(crit.get("version", 1))
    rows = []
    counts = {s: 0 for s in STATUSES}
    counts["needs review"] = 0
    for c in crit["criteria"]:
        st = status.get(c["id"])
        stale = bool(st and st.get("criteria_version") != version)
        shown = "needs review" if stale else (st["status"] if st else "unverified")
        counts[shown] += 1
        rows.append({**c, "record": st, "status": shown, "stale": stale, "observation": obs.get(c.get("auto", ""))})
    return {"index": index, "criteria": crit, "rows": rows, "counts": counts, "history": [p.name for p in history(index)]}
