# Start the local voice conversion service (OpenVoice V2) on http://127.0.0.1:8020.
# One-time setup: see voice_clone/README.md ("Short sample: OpenVoice").
Set-Location -Path (Split-Path $PSScriptRoot -Parent)
$py = "tools\openvoice-venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "OpenVoice environment missing - see voice_clone/README.md" }
& $py -W ignore voice_clone\openvoice_service.py --port 8020
