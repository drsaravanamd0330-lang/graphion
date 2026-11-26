"""Paths and tool locations for Journal Typesetting Studio.

Everything is overridable from the environment so the same code runs from a
Windows checkout, a CI runner, or a test's temporary directory.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

STUDIO_VERSION = "0.1.0"

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_DIR = PACKAGE_DIR.parent

# Version-controlled inputs.
PROFILES_DIR = Path(os.environ.get("STUDIO_PROFILES_DIR", PACKAGE_DIR / "profiles"))
TYPST_DIR = PACKAGE_DIR / "typst"
CSL_DIR = PACKAGE_DIR / "csl"
CRITERIA_DIR = Path(os.environ.get("STUDIO_CRITERIA_DIR", PACKAGE_DIR / "criteria"))
SCHEMAS_DIR = PACKAGE_DIR / "schemas"
TEMPLATES_DIR = PACKAGE_DIR / "templates"

# Working data (gitignored): imported manuscripts, records, proofs, the DB.
DATA_DIR = Path(os.environ.get("STUDIO_DATA_DIR", REPO_DIR / "studio_data"))


def articles_dir() -> Path:
    return DATA_DIR / "articles"


def db_path() -> Path:
    return DATA_DIR / "studio.db"


def pandoc_path() -> str | None:
    """The Pandoc executable, or None when it cannot be found."""
    explicit = os.environ.get("PANDOC_PATH")
    if explicit:
        return explicit if Path(explicit).exists() or shutil.which(explicit) else None
    return shutil.which("pandoc")
