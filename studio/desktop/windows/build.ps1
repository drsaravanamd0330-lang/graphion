<#
.SYNOPSIS
  Build the Windows program and installer for Journal Typesetting Studio.

.DESCRIPTION
  Run from any folder on a Windows machine with Python 3.11+:

      powershell -ExecutionPolicy Bypass -File studio\desktop\windows\build.ps1

  1. Installs the Studio, pywebview and PyInstaller into a build environment.
  2. Builds dist\Journal Typesetting Studio\ with PyInstaller.
  3. Downloads Pandoc (Windows zip, from the official GitHub releases) and
     puts pandoc.exe and its licence inside the program folder, so an editor
     does not have to install Pandoc separately.
  4. If Inno Setup 6 is installed (iscc.exe), compiles
     dist\JournalTypesettingStudio-Setup-<version>.exe.

  -PandocVersion pins a Pandoc release (for example 3.6.4); the default is the
  latest release. -NoPandoc builds without it (the app then asks for it).
#>
param(
    [string]$PandocVersion = "latest",
    [switch]$NoPandoc,
    [switch]$NoInstaller
)

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
Set-Location $Repo
$Dist = Join-Path $Repo "dist"
$AppDir = Join-Path $Dist "Journal Typesetting Studio"
$BuildVenv = Join-Path $Repo ".venv-build"

# ------------------------------------------------------------------ build env
if (-not (Test-Path "$BuildVenv\Scripts\python.exe")) { python -m venv $BuildVenv }
$Py = "$BuildVenv\Scripts\python.exe"
& $Py -m pip install --upgrade pip --quiet --disable-pip-version-check
& $Py -m pip install --quiet --disable-pip-version-check -r studio\requirements.txt -r studio\desktop\requirements.txt "pyinstaller>=6.6"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
$Version = (& $Py -c "from studio.config import STUDIO_VERSION; print(STUDIO_VERSION)").Trim()
Write-Host "Building Journal Typesetting Studio $Version" -ForegroundColor Cyan

# ------------------------------------------------------------------ program
& $Py -m PyInstaller --noconfirm --clean --distpath $Dist --workpath (Join-Path $Repo "build\pyinstaller") studio\desktop\windows\studio-desktop.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# ------------------------------------------------------------------ Pandoc
if (-not $NoPandoc) {
    $api = "https://api.github.com/repos/jgm/pandoc/releases/tags/$PandocVersion"
    if ($PandocVersion -eq "latest") { $api = "https://api.github.com/repos/jgm/pandoc/releases/latest" }
    $headers = @{ "User-Agent" = "studio-build" }
    if ($env:GITHUB_TOKEN) { $headers["Authorization"] = "Bearer $env:GITHUB_TOKEN" }
    $rel = Invoke-RestMethod -Uri $api -Headers $headers
    $asset = $rel.assets | Where-Object { $_.name -match "windows-x86_64\.zip$" } | Select-Object -First 1
    if (-not $asset) { throw "no Windows zip in Pandoc release $($rel.tag_name)" }
    $zip = Join-Path $env:TEMP $asset.name
    Write-Host "Downloading Pandoc $($rel.tag_name)"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -Headers $headers
    $tmp = Join-Path $env:TEMP "pandoc-unzip"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive $zip -DestinationPath $tmp
    $exe = Get-ChildItem $tmp -Recurse -Filter pandoc.exe | Select-Object -First 1
    $dest = Join-Path $AppDir "pandoc"
    New-Item -ItemType Directory -Force $dest | Out-Null
    Copy-Item $exe.FullName $dest
    Get-ChildItem $exe.DirectoryName -File | Where-Object { $_.Name -match "COPYING|COPYRIGHT|LICENSE" } | Copy-Item -Destination $dest
    Set-Content -Path (Join-Path $dest "SOURCE.txt") -Value "Pandoc $($rel.tag_name) is free software under the GNU GPL (see COPYING). Source: https://github.com/jgm/pandoc/releases/tag/$($rel.tag_name)"
}

# ------------------------------------------------------------------ smoke test
# The program has no console, so its check is run through the build env's
# Python against the same code; the frozen program is checked by starting it.
$env:STUDIO_HOME = Join-Path $Repo "build\smoke-home"
$proc = Start-Process -FilePath (Join-Path $AppDir "Journal Typesetting Studio.exe") -ArgumentList "--browser", "--port", "5079" -PassThru
$ok = $false
for ($i = 0; $i -lt 60 -and -not $ok; $i++) {
    Start-Sleep -Milliseconds 500
    try { $ok = ((Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:5079/desktop/ping").Content.Trim() -eq "JournalTypesettingStudio.Desktop") } catch { }
}
$page = if ($ok) { (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:5079/desktop/setup").Content } else { "" }
Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
Remove-Item Env:STUDIO_HOME
if (-not $ok) { throw "the packaged program did not start; see build\smoke-home\data\desktop.log" }
if (-not $NoPandoc -and $page -match "Almost ready") { throw "the packaged program could not find its bundled Pandoc" }
Write-Host "Packaged program starts and answers." -ForegroundColor Green

# ------------------------------------------------------------------ installer
if (-not $NoInstaller) {
    $iscc = Get-Command iscc -ErrorAction SilentlyContinue
    if (-not $iscc) {
        foreach ($p in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
            if (Test-Path $p) { $iscc = $p; break }
        }
    }
    if ($iscc) {
        & $iscc "/DAppVersion=$Version" "/DSourceDir=$AppDir" "/DOutputDir=$Dist" studio\desktop\windows\installer.iss
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
        Write-Host "Installer: $Dist\JournalTypesettingStudio-Setup-$Version.exe" -ForegroundColor Green
    } else {
        Write-Host "Inno Setup 6 not found; skipped the installer. The program folder is $AppDir" -ForegroundColor Yellow
    }
}
