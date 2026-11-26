# Schemas used to validate XML exports

Copied, unmodified except where noted, from the Crossref schema repository
(https://gitlab.com/crossref/schema, commit 6e75ce63, retrieved 2026-11-26).

- `crossref5.3.1.xsd` and the files it includes: Crossref deposit schema 5.3.1.
- `JATS-journalpublishing1-3d2-mathml3.xsd` and `-elements.xsd`: NISO JATS
  Journal Publishing tag set 1.3d2 with MathML 3, as Crossref distributes it.
- `standard-modules/`: xlink, xml, ALI and MathML 3 modules both import.

Change: `crossref5.3.1.xsd` and `common5.3.1.xsd` import MathML 3 from
`http://www.w3.org/Math/XMLSchema/...`; that location is rewritten to the local
`standard-modules/mathml3/mathml3.xsd` so validation needs no network.

To validate against a newer schema version, add its files here and change
`SCHEMA_FILE` in `studio/crossref.py` or `studio/jats.py`. The schema used is
recorded in every validation result.
