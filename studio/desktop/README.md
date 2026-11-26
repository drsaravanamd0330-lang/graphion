# Desktop app

`studio/desktop/` turns the Studio's local web interface into a desktop
program. It adds no production logic: the window shows the same Flask app
the `serve` command runs, on `127.0.0.1`.

| File | What it is |
| --- | --- |
| `launcher.py` | Starts the server in a background thread, runs the start-up checks, opens the window (pywebview) or falls back to the browser. `python -m studio.desktop` |
| `branding/logo.svg` | The Studio logo (option A, "Masthead page"). Shown in the web header and as the favicon. |
| `branding/icon.ico`, `icon-256.png`, `icon-512.png` | App icon rendered from `logo.svg` at 16 to 512 px. |
| `branding/options/` | The three logo options that were offered, kept so the choice can be revisited. |
| `requirements.txt` | `pywebview`, the only extra package. Optional: without it the browser is used. |
| `windows/Install Desktop App.bat` / `install.ps1` | Run-from-source setup: venv, packages, Pandoc check, Desktop and Start menu shortcuts. |
| `windows/build.ps1`, `studio-desktop.spec`, `installer.iss` | Packaged build: PyInstaller program folder with Pandoc bundled, then an Inno Setup installer. |

Command line options: `--browser` (no window), `--port N`, `--check` (print
what the start-up checks found and exit; 0 when ready).

## Behaviour worth knowing

- **Port.** 5070 if free, otherwise any free port. A second click on the icon
  while the Studio is open opens the running one rather than a second server.
- **Pandoc** is looked for in `PANDOC_PATH`, a copy beside the program
  (`pandoc\pandoc.exe`, which the installer ships), `PATH`, and the standard
  install folders, so a Pandoc installed while the Studio is open is found by
  "Check again" without a restart.
- **Data.** Run from source, data stays in `studio_data\` as with the CLI.
  Packaged, the program folder is read-only, so data, profiles and criteria
  live in `Documents\Journal Typesetting Studio\` (override with `STUDIO_HOME`).
  Bundled profiles are copied there on first run and never overwrite a file
  the editor has changed. Uninstalling leaves that folder in place.
- **Log.** `desktop.log` in the data folder, since the packaged program has no
  console.
- **Pandoc licence.** Pandoc is GPL. The installer ships its `COPYING` file and
  a `SOURCE.txt` naming the exact release and where its source is.

## Changing the logo

Replace `branding/logo.svg`, then regenerate the PNG and ICO files from it at
16, 24, 32, 48, 64, 128 and 256 px (any SVG renderer plus Pillow's ICO writer)
and rebuild the installer.
