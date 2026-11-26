"""Merge extracted content, editor-approved metadata and the journal profile.

The result, ``record.json``, is what every output is built from. Profile
values that belong to the article (licence, live URLs) are resolved into it
here, so a profile change shows up as a record change and therefore as a new
proof revision, never as a silent difference between two outputs.

Editor metadata (``editor.json``) uses the record's own keys, plus
``<collection>_patch`` maps for per-item edits (``authors_patch: {au1: {...}}``)
so an ORCID can be confirmed without restating the author list. A list given
whole (``authors: [...]``) replaces the extracted one; the validation report
then shows that author order or wording differs from the manuscript.
"""
from __future__ import annotations

import copy
import datetime as dt
import re
from pathlib import Path
from typing import Any

import yaml

from .model import DATE_KEYS, deep_merge, plain
from .profiles import Profile

PATCHABLE = ("authors", "affiliations", "figures", "tables", "equations", "references", "footnotes")


class MetadataError(ValueError):
    pass


def load_metadata_file(path: Path) -> dict:
    """Read the editor-approved metadata sidecar (YAML or JSON) given at import."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise MetadataError(f"metadata file does not parse: {exc}") from exc
    if not isinstance(data, dict):
        raise MetadataError("metadata file must hold a mapping")
    return normalise_editor(data)


def normalise_editor(data: dict) -> dict:
    """Bring hand-written metadata into the stored shape.

    Dates given by the editor count as verified. ``title`` may be a string.
    Values are only reshaped, never invented.
    """
    data = copy.deepcopy(data)
    if "dates" in data and isinstance(data["dates"], dict):
        out = {}
        for k, v in data["dates"].items():
            if k not in DATE_KEYS:
                raise MetadataError(f"unknown date {k!r}; use one of {', '.join(DATE_KEYS)}")
            if isinstance(v, dict):
                out[k] = {"value": _iso(v.get("value")), "verified": bool(v.get("verified", True)),
                          "source": v.get("source", "editor")}
            elif v in (None, ""):
                out[k] = {"value": None, "verified": False, "source": None}
            else:
                out[k] = {"value": _iso(v), "verified": True, "source": "editor"}
        data["dates"] = out
    if isinstance(data.get("title"), str):
        data["title"] = {"en": [{"t": "text", "v": data["title"]}]}
    if isinstance(data.get("title_original"), str):
        data.setdefault("title", {})
        data["title"]["original"] = [{"t": "text", "v": data.pop("title_original")}]
        if "title_original_lang" in data:
            data["title"]["original_lang"] = data.pop("title_original_lang")
    pub = data.get("publication")
    if isinstance(pub, dict):
        for k in ("volume", "issue", "article_number", "first_page", "last_page"):
            if pub.get(k) is not None:
                pub[k] = str(pub[k])
        if pub.get("doi"):
            pub["doi"] = str(pub["doi"]).strip()
            pub["doi"] = re.sub(r"^https?://(dx\.)?doi\.org/", "", pub["doi"])
    for coll in PATCHABLE:
        key = f"{coll}_patch"
        if key in data and not isinstance(data[key], dict):
            raise MetadataError(f"{key} must map item ids to fields")
    return data


def _iso(v: Any) -> str | None:
    if v in (None, ""):
        return None
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()[:10]
    s = str(v).strip()
    try:
        return dt.date.fromisoformat(s).isoformat()
    except ValueError as exc:
        raise MetadataError(f"date {s!r} is not YYYY-MM-DD") from exc


def merge(extracted: dict, editor: dict, profile: Profile, article_id: str, article_type: str) -> dict:
    editor = editor or {}
    base = {k: v for k, v in editor.items() if not k.endswith("_patch")}
    rec = deep_merge(extracted, base)
    for coll in PATCHABLE:
        patch = editor.get(f"{coll}_patch") or {}
        for item in rec.get(coll, []):
            if item.get("id") in patch:
                item.update(copy.deepcopy(patch[item["id"]]))
    rec["article_id"] = article_id
    rec["journal_id"] = profile.id
    rec["article_type"] = editor.get("article_type") or article_type
    pub = rec["publication"]
    if not pub.get("year"):
        for key in ("published", "accepted"):
            v = (rec["dates"].get(key) or {}).get("value")
            if v and (rec["dates"][key].get("verified")):
                pub["year"] = int(v[:4])
                pub["year_source"] = f"{key} date"
                break
    rec["license"] = resolve_license(rec, editor, profile)
    rec["urls"] = resolve_urls(rec, editor, profile)
    rec["journal"] = journal_snapshot(profile)
    return rec


def journal_snapshot(profile: Profile) -> dict:
    j = profile.journal
    return {
        "profile_id": profile.id,
        "profile_version": profile.version(),
        "title": j.get("title"),
        "abbreviation": j.get("abbreviation"),
        "publisher": profile.publisher_name,
        "homepage": j.get("homepage"),
        "issn_print": (j.get("issn_print") or {}).get("value") or None,
        "issn_print_verified": bool((j.get("issn_print") or {}).get("verified")),
        "issn_online": (j.get("issn_online") or {}).get("value") or None,
        "issn_online_verified": bool((j.get("issn_online") or {}).get("verified")),
    }


def _fill(template: str, rec: dict, profile: Profile) -> str:
    pub = rec["publication"]
    values = {
        "year": pub.get("year"), "volume": pub.get("volume"), "issue": pub.get("issue"),
        "article_number": pub.get("article_number"), "first_page": pub.get("first_page"),
        "last_page": pub.get("last_page"), "doi": pub.get("doi"),
        "doi_suffix": (pub.get("doi") or "").split("/", 1)[-1] if pub.get("doi") else None,
        "publisher": profile.publisher_name, "article_id": rec.get("article_id"),
    }
    missing = [k for k in re.findall(r"\{(\w+)\}", template) if values.get(k) in (None, "")]
    if missing:
        raise KeyError(", ".join(missing))
    return template.format(**{k: ("" if v is None else v) for k, v in values.items()})


def resolve_urls(rec: dict, editor: dict, profile: Profile) -> dict:
    explicit = (editor or {}).get("urls") or {}
    patterns = profile.data.get("urls") or {}
    out = {}
    for key in ("landing_live", "pdf_live", "fulltext_live"):
        if explicit.get(key):
            out[key] = explicit[key]
            out[f"{key}_source"] = "editor"
            continue
        pat = patterns.get(key)
        if not pat:
            out[key] = None
            out[f"{key}_missing"] = "no URL pattern in the journal profile"
            continue
        try:
            out[key] = _fill(pat, rec, profile)
            out[f"{key}_source"] = "profile pattern"
        except KeyError as exc:
            out[key] = None
            out[f"{key}_missing"] = f"needs {exc.args[0]}"
    return out


def resolve_license(rec: dict, editor: dict, profile: Profile) -> dict | None:
    lic_cfg = profile.data.get("license") or {}
    default = lic_cfg.get("default")
    override = (editor or {}).get("license")
    year = rec["publication"].get("year")
    if override:
        lic = dict(default or {}, **override)
        lic["source"] = "article exception"
        lic["exception"] = True
        if not override.get("exception_reason"):
            lic["exception_reason_missing"] = True
    elif default:
        lic = dict(default)
        lic["source"] = "journal default"
        lic["exception"] = False
    else:
        return None
    lic["policy_verified"] = bool(lic_cfg.get("verified"))
    lic["copyright_year"] = lic.get("copyright_year") or year
    stmt = lic.get("statement") or ""
    try:
        lic["statement"] = stmt.format(year=lic["copyright_year"] or "{year}",
                                       copyright_holder=lic.get("copyright_holder") or "",
                                       publisher=profile.publisher_name)
    except (KeyError, IndexError):
        lic["statement_error"] = "statement has an unknown {placeholder}"
    lic["statement"] = re.sub(r"\s+", " ", lic["statement"]).strip()
    return lic


def first_author_surname(rec: dict) -> str:
    return rec["authors"][0]["family"] if rec.get("authors") else ""


def title_text(rec: dict) -> str:
    return plain(rec["title"]["en"])
