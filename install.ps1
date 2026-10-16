# Installs the Cura AI Assistant for the current user: a private Python environment with the
# service (outside the folder you unzipped), then the plugin into Cura's plugin folder.
# Run it through install.cmd (double-click) or: powershell -ExecutionPolicy Bypass -File install.ps1
param(
    [string]$CuraVersion,   # e.g. 5.13; default: the newest Cura found under %APPDATA%\cura
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "CuraAiAssistant"),
    [string]$PluginRoot,    # override of the Cura plugin folder (tests)
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot

function Find-CuraVersion {
    $root = Join-Path $env:APPDATA "cura"
    $found = Get-ChildItem $root -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^\d+\.\d+$' } |
        Sort-Object { [version]$_.Name } -Descending
    if (-not $found) { throw "No Cura user folder found under $root. Start Cura once, then run this again." }
    return $found[0].Name
}

function Find-Python {
    # The first Python 3.11+ found: its executable and the arguments that select the version.
    # The Microsoft Store build is only used when there is nothing else: it cannot see %APPDATA%.
    $candidates = @(
        @{ Exe = "py"; Args = @("-3.13") }, @{ Exe = "py"; Args = @("-3.12") },
        @{ Exe = "py"; Args = @("-3.11") }, @{ Exe = "python"; Args = @() }, @{ Exe = "python3"; Args = @() }
    )
    $store = $null
    foreach ($c in $candidates) {
        $cmd = Get-Command $c.Exe -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try { $v = & $cmd.Source @($c.Args) -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null } catch { continue }
        if (-not ($v -and [version]$v -ge [version]"3.11")) { continue }
        $found = @{ Exe = $cmd.Source; Args = $c.Args }
        if ($cmd.Source -like "*\WindowsApps\*") { if (-not $store) { $store = $found }; continue }
        return $found
    }
    return $store
}

function Find-Claude {
    # Claude Code as PowerShell sees it (a Store Python cannot see %APPDATA%).
    $cmd = Get-Command claude -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $root = Join-Path $env:APPDATA "Claude\claude-code"
    $exes = Get-ChildItem $root -Recurse -Filter claude.exe -ErrorAction SilentlyContinue |
        Sort-Object { try { [version]$_.Directory.Parent.Name } catch { [version]"0.0" } }
    if ($exes) { return @($exes)[-1].FullName }
    return $null
}

if (-not $CuraVersion) { $CuraVersion = Find-CuraVersion }
$pluginsDir = if ($PluginRoot) { $PluginRoot } else { Join-Path $env:APPDATA "cura\$CuraVersion\plugins" }
$pluginTarget = Join-Path $pluginsDir "CuraAiAssistant"

if ($Uninstall) {
    if (Test-Path $pluginTarget) { Remove-Item $pluginTarget -Recurse -Force; Write-Host "Removed $pluginTarget" }
    if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force; Write-Host "Removed $InstallDir" }
    Write-Host "Done. Restart Cura."
    return
}

$pluginSource = Join-Path $here "cura_extension\CuraAiAssistant"
$serviceSource = Join-Path $here "service"
if (-not (Test-Path $pluginSource) -or -not (Test-Path (Join-Path $serviceSource "pyproject.toml"))) {
    throw "Run this from the unzipped release (or the repository): cura_extension\ and service\ are missing next to install.ps1."
}

Write-Host "Cura $CuraVersion, installing into $InstallDir"
$python = Find-Python
if (-not $python) {
    throw "Python 3.11 or newer was not found. Install it (for example: winget install Python.Python.3.12) and run this again."
}
Write-Host ("Using " + $python.Exe + " " + ($python.Args -join " "))

New-Item -ItemType Directory -Force $InstallDir | Out-Null
$venv = Join-Path $InstallDir "venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    & $python.Exe @($python.Args) -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Python environment in $venv" }
}
$venvPython = Join-Path $venv "Scripts\python.exe"

Write-Host "Installing the service (first time this downloads numpy, scipy and pydantic) ..."
& $venvPython -m pip install --quiet --disable-pip-version-check --upgrade $serviceSource
if ($LASTEXITCODE -ne 0) { throw "pip could not install the service" }

if (Test-Path $pluginTarget) { Remove-Item $pluginTarget -Recurse -Force }
New-Item -ItemType Directory -Force $pluginTarget | Out-Null
Copy-Item (Join-Path $pluginSource "*") $pluginTarget -Recurse -Force
Get-ChildItem $pluginTarget -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

$work = Join-Path $InstallDir "work"
New-Item -ItemType Directory -Force $work | Out-Null
$config = [ordered]@{ python = $venvPython; service_dir = $InstallDir; work_dir = $work }
$claude = Find-Claude
$storePython = $python.Exe -like "*\WindowsApps\*"
if ($claude -and $storePython) {
    # This Python cannot look into %APPDATA% itself, so it is told where Claude Code is now.
    $config.claude = @($claude)
}
[System.IO.File]::WriteAllText((Join-Path $pluginTarget "config.json"), ($config | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))

Write-Host ""
Write-Host "Plugin installed: $pluginTarget"
if ($claude) {
    Write-Host "Claude Code found: $claude"
    if ($storePython) {
        Write-Host "Note: your Python is the Microsoft Store build, so the Claude Code location was stored."
        Write-Host "      After a Claude Code update run install.cmd again (or install Python from python.org)."
    }
} else {
    Write-Host "Claude Code was not found. Install it and sign in, or choose another provider on the"
    Write-Host "assistant's Options page (OpenAI, Gemini, Ollama ...). See the README."
}
Write-Host ""
Write-Host "Next: restart Cura, then Extensions > AI Assistant > Open assistant..."
