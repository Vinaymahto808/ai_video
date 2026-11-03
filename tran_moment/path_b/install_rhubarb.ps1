# CPU binary for Path B visemes. MIT. No GPU, no Python model.
# https://github.com/DanielSWolf/rhubarb-lip-sync/releases
$ErrorActionPreference = "Stop"
$ver = "1.13.0"
$zipName = "Rhubarb-Lip-Sync-$ver-Windows.zip"
$url = "https://github.com/DanielSWolf/rhubarb-lip-sync/releases/download/v$ver/$zipName"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$tools = Join-Path $here "tools"
New-Item -ItemType Directory -Force -Path $tools | Out-Null
$zip = Join-Path $tools $zipName
Write-Host "downloading $url"
Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
Expand-Archive -Path $zip -DestinationPath $tools -Force
$exe = Get-ChildItem -Path $tools -Filter rhubarb.exe -Recurse | Select-Object -First 1
if (-not $exe) { throw "rhubarb.exe not found after extract" }
Copy-Item $exe.FullName (Join-Path $tools "rhubarb.exe") -Force
Write-Host "installed $($exe.FullName) -> tools\rhubarb.exe"
Write-Host "then: uv run python generate_visemes.py --wav sample.wav --text `"Namaste`""
