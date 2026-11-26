<#
.SYNOPSIS
  Set up Journal Typesetting Studio as a desktop app on Windows, from this
  copy of the repository.

.DESCRIPTION
  1. Finds Python 3.11 or newer.
  2. Creates a private Python environment (.venv) in the repository folder,
     if there is not one already, and installs the Studio's packages.
  3. Checks for Pandoc and, if it is missing, offers to install it with winget.
  4. Puts a "Journal Typesetting Studio" icon on the Desktop and in the Start
     menu. Double-clicking it opens the Studio in its own window.

  Safe to run again: it reuses the environment and refreshes the shortcuts.
  Run with -Uninstall to remove the shortcuts (your articles stay where they are).
#>
param(
    [switch]$Uninstall,
    [switch]$NoDesktopShortcut,
    [switch]$SkipPandoc
)

$ErrorActionPreference = "Stop"
$AppName = "Journal Typesetting Studio"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$Icon = Join-Path $Repo "studio\desktop\branding\icon.ico"
$Venv = Join-Path $Repo ".venv"
$DesktopLink = Join-Path ([Environment]::GetFolderPath("Desktop")) "$AppName.lnk"
$StartMenuLink = Join-Path ([Environment]::GetFolderPath("Programs")) "$AppName.lnk"

function Say($text) { Write-Host "  $text" }
function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }

if ($Uninstall) {
    foreach ($l in @($DesktopLink, $StartMenuLink)) {
        if (Test-Path $l) { Remove-Item $l; Say "removed $l" }
    }
    Say "Shortcuts removed. Articles in $Repo\studio_data were not touched."
    exit 0
}

Write-Host "${AppName}: desktop setup" -ForegroundColor White
Say "repository: $Repo"

# ------------------------------------------------------------------ Python
Step "Python"
$Python = $null
foreach ($cand in @(@("py", "-3"), @("python"), @("python3"))) {
    try {
        $exe = $cand[0]; $args0 = @($cand | Select-Object -Skip 1)
        $v = & $exe @args0 -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) {
            $parts = $v.Trim().Split(".")
            if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11) { $Python = $cand; Say "found Python $v ($($cand -join ' '))"; break }
            Say "skipping Python $v (3.11 or newer is needed)"
        }
    } catch { }
}
if (-not $Python) {
    Write-Host ""
    Write-Host "Python 3.11 or newer was not found." -ForegroundColor Yellow
    Say "Install it from https://www.python.org/downloads/windows/ and tick"
    Say "'Add python.exe to PATH' on the first screen, then run this again."
    exit 1
}

# ------------------------------------------------------------------ environment
Step "Python packages"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$VenvPythonW = Join-Path $Venv "Scripts\pythonw.exe"
if (-not (Test-Path $VenvPython)) {
    Say "creating $Venv"
    $exe = $Python[0]; $args0 = @($Python | Select-Object -Skip 1)
    & $exe @args0 -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "could not create the Python environment" }
}
& $VenvPython -m pip install --upgrade pip --quiet --disable-pip-version-check
& $VenvPython -m pip install --quiet --disable-pip-version-check `
    -r (Join-Path $Repo "studio\requirements.txt") `
    -r (Join-Path $Repo "studio\desktop\requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "package installation failed; see the messages above" }
Say "packages installed"

# ------------------------------------------------------------------ Pandoc
Step "Pandoc"
$pandoc = Get-Command pandoc -ErrorAction SilentlyContinue
if (-not $pandoc) {
    foreach ($p in @("$env:LOCALAPPDATA\Pandoc\pandoc.exe", "$env:ProgramFiles\Pandoc\pandoc.exe")) {
        if (Test-Path $p) { $pandoc = $p; break }
    }
}
if ($pandoc) {
    Say "found Pandoc"
} elseif ($SkipPandoc) {
    Say "Pandoc not found (skipped). The Studio will show how to install it."
} else {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        $answer = Read-Host "  Pandoc is needed to read Word files and is not installed. Install it now with winget? (Y/n)"
        if ($answer -eq "" -or $answer -match "^[Yy]") {
            winget install --id JohnMacFarlane.Pandoc --exact --accept-package-agreements --accept-source-agreements
        }
    } else {
        Say "Pandoc is not installed. Download the Windows .msi from"
        Say "https://github.com/jgm/pandoc/releases/latest and run it."
        Say "The Studio will also show these steps when it opens."
    }
}

# ------------------------------------------------------------------ shortcuts
Step "Shortcuts"
$shell = New-Object -ComObject WScript.Shell
$targets = @($StartMenuLink)
if (-not $NoDesktopShortcut) { $targets += $DesktopLink }
foreach ($path in $targets) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $VenvPythonW
    $lnk.Arguments = "-m studio.desktop"
    $lnk.WorkingDirectory = $Repo
    $lnk.IconLocation = "$Icon,0"
    $lnk.Description = "Typeset journal articles: Word in, PDF, HTML and XML out"
    $lnk.Save()
    Say "created $path"
}

# ------------------------------------------------------------------ check
Step "Check"
& $VenvPython -m studio.desktop --check
Write-Host ""
Write-Host "Done. Double-click '$AppName' on your Desktop to open the Studio." -ForegroundColor Green
