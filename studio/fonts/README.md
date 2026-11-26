# Fonts

Typst compiles with system fonts switched off, so a proof made on Windows and
one made on Linux use the same fonts. Available fonts are the ones Typst
bundles (Libertinus Serif, New Computer Modern, DejaVu Sans Mono), the files in
this folder, and any `fonts/` folder inside a journal profile.

- `DejaVuSans.ttf`, `DejaVuSans-Bold.ttf`: DejaVu Sans, Bitstream Vera licence
  with DejaVu changes in the public domain (see `LICENSE-DejaVu.txt`).

To use a journal's own typeface, put its licensed font files in
`profiles/<publisher>/<journal>/fonts/` and name the family in the profile.
A family Typst cannot find is reported as a warning in the production report.
