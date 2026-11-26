# Journal Typesetting Studio

A local production desk for journals published by **McMed International** and
**Pharma Intelligence** (and any other publisher you add). It takes an accepted
Word manuscript plus editor-approved metadata and produces:

- a typeset A4 PDF (full-width masthead on page 1, two-column body or a
  one-column option, distinct layouts for original research, review, case
  report and editorial, declarations before references),
- an article landing page and a full-text HTML page,
- `record.json`, the structured record every other output is built from,
- optional JATS 1.3 XML and Crossref 5.3.1 deposit XML, each validated against
  the official schema (bundled, so validation works offline),
- a production quality report, and
- a downloadable ZIP package.

The Studio lives in the `studio/` folder of this repository and is separate
from the Graphion app beside it: it has its own requirements, data folder,
command line and web interface. It reuses nothing from Graphion at run time.

## What it will not do

- It never invents a DOI, ORCID iD, ISSN, date, ethics approval number, trial
  registration number or licence. A value the manuscript and the editor do not
  supply stays empty and is reported.
- It never rewrites the scientific text. Every word and number of the abstract,
  body and tables is checked to be present in the PDF.
- It flags, and does not silently resolve: tracked changes and comments,
  author to affiliation mappings it is unsure of, missing figure files,
  objects it cannot convert (OLE objects, charts, SmartArt, text boxes),
  conversion losses, and references it cannot parse (the typed text is kept
  and printed exactly as typed).

## Setup on Windows

You need three things: Python, Pandoc and this repository. Typst, the
typesetting engine, comes with the Python packages.

1. **Python 3.11 or newer.** Install from <https://www.python.org/downloads/windows/>
   and tick "Add python.exe to PATH" on the first screen. Check in a new
   PowerShell window:
   ```powershell
   python --version
   ```
2. **Pandoc 3.1.3 or newer.** Install the Windows `.msi` from
   <https://github.com/jgm/pandoc/releases> (the installer adds it to PATH).
   Check in a new PowerShell window:
   ```powershell
   pandoc --version
   ```
   If Pandoc is somewhere else, set `PANDOC_PATH` to the full path of
   `pandoc.exe`.
3. **Get the code and install the Python packages.**
   ```powershell
   git clone https://github.com/drsaravanamd0330-lang/graphion.git
   cd graphion
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r studio\requirements.txt
   ```
   If PowerShell refuses to run `Activate.ps1`, run
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use
   `.venv\Scripts\activate.bat` from Command Prompt.

PDF tools: none are needed. Typst writes the PDF, `pypdf` and `pdfplumber`
read it back for the checks, and `pypdfium2` draws the page images for the
review screen; all three are pip packages in the requirements. Fonts are
bundled (see `studio/fonts/README.md`), so the PDF comes out the same on
Windows, macOS and Linux.

macOS and Linux are the same steps with `python3`, `source .venv/bin/activate`
and Pandoc from Homebrew or your package manager.

## Your first proof (about five minutes)

From the `graphion` folder with the virtual environment active:

```powershell
# 1. Make the synthetic sample manuscript (3 authors, several affiliations,
#    a wide table, a figure, an equation, references). It is labelled as
#    synthetic test content inside the document.
python -m studio sample sample.docx

# 2. Import it with the sample editor-approved metadata.
python -m studio import sample.docx --journal mcmed-international/sample-clinical-research --type original-research --metadata studio\fixtures\sample-metadata.yaml
#    prints the article id, for example art-20261126-1a2b3c

# 3. See what needs attention.
python -m studio validate art-20261126-1a2b3c

# 4. Render a proof revision: PDF, landing page, full text, record.json, report.
python -m studio render art-20261126-1a2b3c
```

The files are in `studio_data\articles\<article id>\revisions\r001\`:
`article.pdf`, `index.html`, `fulltext.html`, `record.json`, `report.html`.

Or do the same in the browser:

```powershell
python -m studio serve
```

and open <http://127.0.0.1:5070>. Type your name in the box at the top (it is
recorded with approvals), import the manuscript from the Articles page, and
follow the article page from findings to revision to approval to export.

## The workflow

1. **Import** a DOCX into a journal profile, optionally with a metadata file
   (YAML or JSON, see `studio/fixtures/sample-metadata.yaml`).
2. **Review** findings. Each has a status (Pass, Warning, Blocked, Not
   Applicable, Needs Manual Review), an explanation, and the field it concerns.
   Edit metadata on the article page or replace the editor layer as JSON.
   Findings that need a person to look are acknowledged with a note.
3. **Render** a revision. If anything is Blocked the draft still renders, with
   a "DIAGNOSTIC DRAFT" watermark on every page, so you can see the layout;
   it cannot be approved.
4. **Approve** a proof revision when every required check passes and every
   manual finding is acknowledged ("Ready for editorial review"). A revision
   is tied to the exact manuscript, metadata, journal profile version,
   templates, CSL style and Studio version (`inputs.json`); approval is
   refused if any of them changed since it was rendered.
5. **Export** the ZIP. Exporting again returns the same file byte for byte and
   never overwrites a package. Any edit after approval makes a new revision
   that needs its own approval; the approved one is left untouched.

## Command line

Every command calls the same code as the web interface.

| Command | What it does |
| --- | --- |
| `python -m studio sample OUT.docx [--tracked-change]` | write the synthetic fixture |
| `python -m studio profiles` | list journal profiles |
| `python -m studio import DOCX --journal ID --type TYPE [--metadata FILE]` | import a manuscript |
| `python -m studio list` | list articles |
| `python -m studio validate ART [--all]` | article checks; exit code 2 when not ready |
| `python -m studio render ART` | render a revision, or reuse it if nothing changed |
| `python -m studio ack ART FINGERPRINT --by NAME [--note TEXT]` | acknowledge a manual finding |
| `python -m studio approve ART N --by NAME` | approve revision N |
| `python -m studio export ART [--revision N] [--jats] [--crossref]` | build the ZIP |
| `python -m studio live ART` | check a registered DOI and the live pages (network) |
| `python -m studio evidence JOURNAL doaj\|scopus\|wos` | indexing evidence for a journal |
| `python -m studio serve [--port 5070]` | the web interface on 127.0.0.1 |

## Journals and publishers

Profiles are folders: `studio/profiles/<publisher>/<journal>/profile.yaml`,
with `publisher.yaml` one level up, a logo, and the CSL style it names under
`studio/csl/`. Two sample journals ship, one per publisher, deliberately
different in branding, fonts, column layout and reference style:

- `mcmed-international/sample-clinical-research`: Vancouver references
  (bracketed numbers), Libertinus Serif, navy, article numbers.
- `pharma-intelligence/sample-pharmaceutical-sciences`: AMA references
  (superscript numbers), New Computer Modern, teal, page ranges.

They are configuration examples, not registered titles: their ISSNs, DOI
prefixes and Crossref depositor details are empty, and the licence wording is
marked unverified. Add a journal from the Journals page (it clones an existing
profile and clears the ISSNs) or by copying a folder. A profile is saved from
the web editor only if it parses and validates (ISSN check digits, colours,
licence fields). Its version is the hash of its files and is recorded in every
revision.

## Validation

Article checks: title and abstract as the article type requires; every author
mapped to an institution and country; author names and affiliations in Roman
script; every citation and figure, table and equation reference resolving;
title, author order, DOI, volume, issue and licence agreeing across
`record.json`, the PDF and both HTML pages (and the XML when exported);
declarations present; no placeholder text; licence statement agreeing with the
licence; fonts embedded; no text clipped at the page edge.

DOI resolution is only checked for a DOI marked as registered, and the live
page check is separate from the proof (`python -m studio live`), so a proof
never depends on the network.

**JATS** needs a verified ISSN in the profile: the Journal Publishing tag set
requires one, so the sample journals produce JATS that is reported as not
valid, with that reason, until one is entered. **Crossref** XML is only
generated when the journal has a DOI prefix and depositor and the article has
its DOI, a confirmed publication date and a live landing URL; otherwise the
report lists what is missing.

## Indexing evidence (DOAJ, Scopus, Web of Science)

Each journal has an evidence view per index (Journals page). Criteria lists
are in `studio/criteria/`, name their official source URL, the date an editor
last compared them with it, and whether that has been done. **The shipped
lists are working summaries written without access to the official pages and
are marked unverified;** check them against the sources and record the date.
Editing a list saves it as the next version and keeps the old one; statuses
recorded against an older version show as needing review. An editor records a
status per criterion with the URL that evidences it. Studio adds what it can
see in the profile (for example whether an ISSN is entered and verified) as an
observation; it never marks a criterion verified itself.

## Tests

```powershell
python -m pytest studio\tests -q
```

The tests run the real pipeline on the synthetic fixture and need Pandoc on
PATH (they skip without it). They cover the import, missing metadata blocking
approval while a draft still renders, PDF content preservation, approval and
reproducible export, a new revision after an edit, regenerating with the second
journal profile, XML validation, tracked changes, evidence versioning, the web
pages and the CLI.

## Folder layout

```
studio/
  cli.py, web.py          command line and web interface (same pipeline)
  pipeline.py             import, validate, render, approve, export
  docx_import.py          DOCX to record via Pandoc's JSON AST
  records.py              merge extracted record, editor layer and profile
  checks.py               every finding and its status
  typst_render.py, typst/ PDF
  html_render.py          landing and full-text pages
  jats.py, crossref.py    XML exporters; schemas/ holds the XSDs
  evidence.py, criteria/  indexing evidence
  profiles/, csl/, fonts/ journal configuration
  fixtures/               synthetic sample manuscript and metadata
studio_data/              your work (gitignored): database, imports, revisions
```

Set `STUDIO_DATA_DIR` to keep the data somewhere else.

## Known limits

- Equations are converted from Word's equation editor through Pandoc; an
  equation Pandoc cannot convert blocks the proof rather than printing wrongly.
- Reference parsing recognises journal articles in Vancouver form. Anything
  else is kept exactly as typed, printed as typed, and listed for a person to
  check; references with "et al." are never expanded.
- Only the bundled fonts and fonts placed in a profile's `fonts/` folder are
  used; system fonts are ignored on purpose.
