# Tries install.ps1 in throw-away folders (no change to your Cura plugins), then removes them again.
$repo = Split-Path -Parent $PSScriptRoot
$tmp = Join-Path $env:TEMP "cai-install-test"
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
$plugins = Join-Path $tmp "plugins"
$dir = Join-Path $tmp "install"
New-Item -ItemType Directory -Force $plugins | Out-Null

& (Join-Path $repo "install.ps1") -CuraVersion 5.13 -InstallDir $dir -PluginRoot $plugins
$py = Join-Path $dir "venv\Scripts\python.exe"
Write-Host "--- plugin files:"; Get-ChildItem (Join-Path $plugins "CuraAiAssistant") -Name
Write-Host "--- config.json:"; Get-Content (Join-Path $plugins "CuraAiAssistant\config.json") -Raw
Write-Host "--- the installed service answers a request from another folder:"
Set-Location $env:TEMP
'{"action":"list_models","provider":"claude"}' | & $py -m cura_ai request

& (Join-Path $repo "install.ps1") -CuraVersion 5.13 -InstallDir $dir -PluginRoot $plugins -Uninstall
Write-Host "--- left over after uninstall:"; Get-ChildItem $tmp -Recurse -Name | Select-Object -First 5
Remove-Item $tmp -Recurse -Force
