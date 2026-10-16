# Copies the Cura plugin into the user plugin folder and writes its config.json.
# Run after every change under cura_extension\, then restart Cura.
param(
    [string]$CuraVersion = "5.13",
    [switch]$FakeClaude  # canned answers instead of the real Claude CLI, for UI tests only
)

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo "service\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Service virtualenv missing: $python (see README)" }

$target = Join-Path $env:APPDATA "cura\$CuraVersion\plugins\CuraAiAssistant"
if (Test-Path $target) { Remove-Item $target -Recurse -Force }
New-Item -ItemType Directory -Force $target | Out-Null
Copy-Item (Join-Path $repo "cura_extension\CuraAiAssistant\*") $target -Recurse -Force
Get-ChildItem $target -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

$workDir = Join-Path $repo ".work"
New-Item -ItemType Directory -Force $workDir | Out-Null
$config = [ordered]@{
    python      = $python
    service_dir = Join-Path $repo "service"
    work_dir    = $workDir
}
if ($FakeClaude) {
    $config.claude = @($python, (Join-Path $repo "scripts\fake_claude.py"))
    $config.open_trigger = Join-Path $workDir "open_assistant.trigger"  # tests open the dialog by file
    $config.export_dir = Join-Path $workDir "export"  # test G-code stays inside the repository
}
$config = $config | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $target "config.json"), $config, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "Installed to $target"
if (Get-Process -Name "UltiMaker-Cura" -ErrorAction SilentlyContinue) {
    Write-Host "Cura is running - restart it to load the plugin."
}
