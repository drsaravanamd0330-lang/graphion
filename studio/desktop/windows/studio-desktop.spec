# PyInstaller spec for the packaged Windows program. Build with build.ps1,
# which runs:  pyinstaller --noconfirm studio\desktop\windows\studio-desktop.spec
# from the repository root.
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

REPO = Path(SPECPATH).resolve().parents[2]
STUDIO = REPO / "studio"

datas = []
# Version-controlled inputs the Studio reads at run time.
for sub in ("profiles", "typst", "csl", "criteria", "schemas", "templates", "fonts", "desktop/branding"):
    datas.append((str(STUDIO / sub), f"studio/{sub}"))
datas.append((str(STUDIO / "fixtures" / "sample-metadata.yaml"), "studio/fixtures"))
# The render code is hashed into each proof's template version, so the
# source files have to be present beside the compiled ones.
for name in ("typst_render.py", "html_render.py", "references.py", "docx_export.py"):
    datas.append((str(STUDIO / name), "studio"))

binaries = []
hiddenimports = collect_submodules("studio")
for pkg in ("typst", "docx", "pypdfium2", "pypdfium2_raw", "webview", "pdfplumber", "pdfminer", "lxml"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(STUDIO / "desktop" / "windows" / "entry.py")],
    pathex=[str(REPO)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "tkinter.test"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Journal Typesetting Studio",
    icon=str(STUDIO / "desktop" / "branding" / "icon.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Journal Typesetting Studio", upx=False)
