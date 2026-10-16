# Builds dist\cura-ai-assistant-<version>.zip: the plugin, the service source, the installer and the
# docs. No virtualenv, no tests, no tooling files. The version is the one in plugin.json.
$repo = Split-Path -Parent $PSScriptRoot
$version = (Get-Content (Join-Path $repo "cura_extension\CuraAiAssistant\plugin.json") -Raw | ConvertFrom-Json).version
$name = "cura-ai-assistant-$version"
$stage = Join-Path $env:TEMP $name
$dist = Join-Path $repo "dist"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force $stage, $dist | Out-Null

foreach ($path in "cura_extension", "service\cura_ai", "service\pyproject.toml", "scripts\install_plugin.ps1", "docs") {
    $target = Join-Path $stage $path
    New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
    Copy-Item (Join-Path $repo $path) $target -Recurse -Force
}
foreach ($file in "README.md", "CHANGELOG.md", "LICENSE") {
    if (Test-Path (Join-Path $repo $file)) { Copy-Item (Join-Path $repo $file) $stage }
}
Get-ChildItem $stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

$zip = Join-Path $dist "$name.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip
Remove-Item $stage -Recurse -Force
Write-Host "Built $zip"
