"""Journal profiles: one YAML file per journal, kept in version control.

Layout::

    profiles/<publisher-slug>/publisher.yaml
    profiles/<publisher-slug>/<journal-slug>/profile.yaml
    profiles/<publisher-slug>/<journal-slug>/logo.svg       (optional)
    profiles/<publisher-slug>/<journal-slug>/evidence.yaml  (journal-level indexing evidence)

A profile id is ``<publisher-slug>/<journal-slug>``. Its *version* is a hash
of every file in its directory plus the publisher file, so a proof can say
exactly which configuration produced it, whether or not git is present.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import config
from .model import ARTICLE_TYPES, DECLARATION_KEYS, sha256_bytes

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")
ISSN_RE = re.compile(r"^\d{4}-\d{3}[\dX]$")


class ProfileError(ValueError):
    pass


@dataclass
class Profile:
    id: str
    path: Path
    data: dict[str, Any]
    publisher: dict[str, Any] = field(default_factory=dict)

    # convenience accessors -------------------------------------------------
    @property
    def journal(self) -> dict:
        return self.data.get("journal", {})

    @property
    def title(self) -> str:
        return self.journal.get("title", self.id)

    @property
    def publisher_name(self) -> str:
        return self.publisher.get("name", "")

    def article_type(self, key: str) -> dict:
        return (self.data.get("article_types") or {}).get(key, {})

    def logo_path(self) -> Path | None:
        name = (self.data.get("branding") or {}).get("logo")
        if not name:
            return None
        p = (self.path / name).resolve()
        return p if p.is_file() and self.path.resolve() in p.parents else None

    def csl_path(self) -> Path:
        name = (self.data.get("references") or {}).get("csl", "vancouver-nlm.csl")
        local = self.path / name
        return local if local.is_file() else config.CSL_DIR / name

    def version(self) -> str:
        return profile_version(self)


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - message passes through
        raise ProfileError(f"{path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError(f"{path.name} must hold a mapping")
    return data


def list_profiles(root: Path | None = None) -> list[Profile]:
    root = root or config.PROFILES_DIR
    out = []
    for pfile in sorted(root.glob("*/*/profile.yaml")):
        try:
            out.append(load_profile(f"{pfile.parent.parent.name}/{pfile.parent.name}", root))
        except ProfileError:
            continue
    return out


def load_profile(profile_id: str, root: Path | None = None) -> Profile:
    root = root or config.PROFILES_DIR
    parts = profile_id.split("/")
    if len(parts) != 2 or not all(SLUG_RE.match(p) for p in parts):
        raise ProfileError(f"not a profile id: {profile_id!r}")
    pdir = root / parts[0] / parts[1]
    pfile = pdir / "profile.yaml"
    if not pfile.is_file():
        raise ProfileError(f"no profile at {pfile}")
    pub_file = pdir.parent / "publisher.yaml"
    publisher = _read_yaml(pub_file) if pub_file.is_file() else {"name": parts[0]}
    return Profile(id=profile_id, path=pdir, data=_read_yaml(pfile), publisher=publisher)


def profile_version(profile: Profile) -> str:
    h = []
    files = sorted(p for p in profile.path.rglob("*") if p.is_file())
    pub = profile.path.parent / "publisher.yaml"
    if pub.is_file():
        files.append(pub)
    for p in files:
        h.append(f"{p.relative_to(profile.path.parent.parent).as_posix()}:{sha256_bytes(p.read_bytes())}")
    return sha256_bytes("\n".join(h).encode())[:16]


def git_commit(path: Path) -> str | None:
    """The commit a file was last changed in, when the checkout has git."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", str(path)],
            cwd=path if path.is_dir() else path.parent,
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha or None


def validate_profile_data(data: dict) -> list[str]:
    """Structural problems that would stop rendering. Empty means usable."""
    problems = []
    j = data.get("journal") or {}
    if not j.get("title"):
        problems.append("journal.title is required")
    for key in ("issn_print", "issn_online"):
        v = (j.get(key) or {}).get("value", "")
        if v and not ISSN_RE.match(v):
            problems.append(f"journal.{key} {v!r} is not in ISSN form NNNN-NNNC")
        elif v and not issn_checksum_ok(v):
            problems.append(f"journal.{key} {v!r} fails the ISSN check digit")
    types = data.get("article_types") or {}
    if not types:
        problems.append("article_types must list at least one type")
    for k, t in types.items():
        if k not in ARTICLE_TYPES:
            problems.append(f"article type {k!r} is not one of {', '.join(ARTICLE_TYPES)}")
        for d in (t or {}).get("required_declarations", []):
            if d not in DECLARATION_KEYS:
                problems.append(f"{k}: unknown declaration {d!r}")
    layout = data.get("layout") or {}
    if layout.get("columns") not in (None, 1, 2):
        problems.append("layout.columns must be 1 or 2")
    refs = data.get("references") or {}
    if refs.get("in_text") not in (None, "bracket", "superscript"):
        problems.append("references.in_text must be bracket or superscript")
    model = (data.get("numbering") or {}).get("model")
    if model not in (None, "article-number", "pages"):
        problems.append("numbering.model must be article-number or pages")
    return problems


def issn_checksum_ok(issn: str) -> bool:
    digits = issn.replace("-", "")
    if len(digits) != 8:
        return False
    total = sum(int(d) * w for d, w in zip(digits[:7], range(8, 1, -1)))
    check = (11 - total % 11) % 11
    return digits[7] == ("X" if check == 10 else str(check))


def save_profile_yaml(profile_id: str, text: str, root: Path | None = None) -> Profile:
    """Write edited YAML back to disk after it parses and validates."""
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ProfileError(f"YAML does not parse: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError("profile must be a mapping")
    problems = validate_profile_data(data)
    if problems:
        raise ProfileError("; ".join(problems))
    prof = load_profile(profile_id, root)
    (prof.path / "profile.yaml").write_text(text, encoding="utf-8")
    return load_profile(profile_id, root)


def create_journal(publisher_slug: str, journal_slug: str, title: str,
                   clone_from: str, root: Path | None = None) -> Profile:
    """Start a new journal by cloning an existing profile's files."""
    root = root or config.PROFILES_DIR
    if not SLUG_RE.match(publisher_slug) or not SLUG_RE.match(journal_slug):
        raise ProfileError("slugs use lowercase letters, digits and hyphens")
    src = load_profile(clone_from, root)
    dest = root / publisher_slug / journal_slug
    if dest.exists():
        raise ProfileError(f"{publisher_slug}/{journal_slug} already exists")
    pub_file = root / publisher_slug / "publisher.yaml"
    dest.mkdir(parents=True)
    if not pub_file.exists():
        pub_file.write_text(f"name: {publisher_slug}\nshort_name: ''\nurl: ''\ncountry: ''\n", encoding="utf-8")
    for f in src.path.iterdir():
        if f.is_file() and f.name != "evidence.yaml":
            (dest / f.name).write_bytes(f.read_bytes())
    data = dict(src.data)
    data["journal"] = dict(src.journal, title=title,
                           issn_print={"value": "", "verified": False},
                           issn_online={"value": "", "verified": False})
    (dest / "profile.yaml").write_text(
        "# Cloned from " + clone_from + ". Verify every value before publication.\n"
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=10_000),
        encoding="utf-8",
    )
    return load_profile(f"{publisher_slug}/{journal_slug}", root)
