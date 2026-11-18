"""The Pandoc the image installs is the Pandoc the filters were verified on.

The Dockerfile pinned 3.1.11 while the code and every document here were
checked against 3.11, a version string one character away. 3.1.11's Typst
writer emits no `alt:` for an image, so in that image every figure reached
Typst undescribed, Typst refused PDF/UA-1, and the galley shipped through
the unconstrained fallback with nothing but a log line to say so. The
filter tests could not catch it, because they run against whatever Pandoc
the developer's machine has.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]
FLOOR = (3, 11)


def _version(text: str) -> tuple:
    return tuple(int(part) for part in text.split("."))


def test_the_image_pins_a_pandoc_whose_typst_writer_carries_alt_text():
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    match = re.search(r"^ARG PANDOC_VERSION=(\S+)$", dockerfile, re.M)
    assert match, "the Dockerfile no longer pins a Pandoc version"
    assert _version(match.group(1)) >= FLOOR, (
        f"Pandoc {match.group(1)} drops image alt text in Typst; "
        f"the floor is {'.'.join(map(str, FLOOR))}")


def test_version_strings_compare_as_numbers_not_text():
    """3.1.11 sorts after 3.11 as text and before it as a version, which is
    the confusion the pin fell into."""
    assert _version("3.1.11") < _version("3.11") < _version("3.14")


pandoc = shutil.which("pandoc")


@pytest.mark.skipif(pandoc is None, reason="Pandoc is not on PATH")
def test_the_local_pandoc_writes_alt_text_into_typst():
    """Says out loud, on a developer's machine, what the image pin above
    guarantees in production. A failure here means renders on this machine
    lose every figure's description."""
    out = subprocess.run(
        [pandoc, "-f", "markdown", "-t", "typst"],
        input="![A described thing.](fig.png)\n", capture_output=True,
        text=True, encoding="utf-8", check=True).stdout
    assert 'alt: "A described thing."' in out
