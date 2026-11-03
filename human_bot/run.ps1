param([int]$Port = 8010, [string]$Persona = "")
$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path $PSScriptRoot -Parent)
if ($Persona) {
  Write-Host "human_bot ($Persona) -> http://127.0.0.1:$Port/" -ForegroundColor Cyan
  python human_bot\server\app.py --port $Port --persona $Persona
} else {
  Write-Host "human_bot -> http://127.0.0.1:$Port/" -ForegroundColor Cyan
  python human_bot\server\app.py --port $Port
}
