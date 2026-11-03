# Windows SAPI → path_b/sample.wav (CPU). Prefer project Gemini/Piper TTS in production.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$line = Get-Content (Join-Path $here "sample_line.txt") -Raw
$wav = Join-Path $here "sample.wav"
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $voices = $synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo }
  $hi = $voices | Where-Object { $_.Culture.Name -like "hi*" } | Select-Object -First 1
  if ($hi) { $synth.SelectVoice($hi.Name) }
  $synth.SetOutputToWaveFile($wav)
  $synth.Speak($line.Trim())
} finally {
  $synth.Dispose()
}
Write-Host "wrote $wav"
