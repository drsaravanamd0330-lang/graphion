"""TeX math (what Pandoc reads out of Word's OMML) to Typst math and MathML.

Both conversions are Pandoc's, run once per render over every expression in
the article, so the PDF and the HTML show the same formula from the same
source. An expression Pandoc cannot convert is reported, never approximated.
"""
from __future__ import annotations

import json
import subprocess

from . import config

MARK = "STUDIOMATHSEP"


def _pandoc_math(exprs: list[str], to: str, extra: list[str]) -> list[str | None]:
    exe = config.pandoc_path()
    if not exe or not exprs:
        return [None] * len(exprs)
    blocks = []
    for e in exprs:
        blocks.append({"t": "Para", "c": [{"t": "Math", "c": [{"t": "DisplayMath"}, e]}]})
        blocks.append({"t": "Para", "c": [{"t": "Str", "c": MARK}]})
    meta = json.loads(subprocess.run([exe, "-f", "markdown", "-t", "json"], input=b"x",
                                     capture_output=True, timeout=60).stdout)
    doc = {"pandoc-api-version": meta["pandoc-api-version"], "meta": {}, "blocks": blocks}
    proc = subprocess.run([exe, "-f", "json", "-t", to, *extra], input=json.dumps(doc).encode(),
                          capture_output=True, timeout=120)
    if proc.returncode != 0:
        return [None] * len(exprs)
    out = proc.stdout.decode("utf-8")
    parts = out.split(MARK)
    results: list[str | None] = []
    for p in parts[: len(exprs)]:
        results.append(p.strip() or None)
    while len(results) < len(exprs):
        results.append(None)
    return results


def to_typst(exprs: list[str]) -> list[str | None]:
    """Typst math source for each expression, without the surrounding $."""
    out = []
    for r in _pandoc_math(exprs, "typst", []):
        if r is None:
            out.append(None)
            continue
        r = r.strip()
        # Pandoc writes a display equation as "$ ... $", possibly with a label.
        if r.startswith("$") and r.rstrip().endswith("$"):
            r = r.strip()[1:-1].strip()
        out.append(r)
    return out


def to_mathml(exprs: list[str]) -> list[str | None]:
    out = []
    for r in _pandoc_math(exprs, "html", ["--mathml"]):
        if r is None:
            out.append(None)
            continue
        start, end = r.find("<math"), r.rfind("</math>")
        out.append(r[start:end + 7] if start >= 0 and end > start else None)
    return out
