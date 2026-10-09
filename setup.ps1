# Trillion setup. Run by double-clicking "1 - Setup.bat". Safe to run again at any time.
param([switch]$SkipModels)
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

function Step($t) { Write-Host ""; Write-Host "== $t ==" -ForegroundColor Yellow }
function Works($cmd, $arg) { try { & $cmd $arg *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false } }
function RefreshPath { $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") }
function Get-App($id, $name) {
  Write-Host "Installing $name ..."
  winget install -e --id $id --accept-source-agreements --accept-package-agreements --silent
  RefreshPath
}

Step "Checking Windows tools"
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
  Write-Host "Windows is missing 'winget'. Open the Microsoft Store, install 'App Installer', then run Setup again." -ForegroundColor Red
  Read-Host "Press Enter to close"; exit 1
}
if (-not (Works "python" "--version")) { Get-App "Python.Python.3.12" "Python" }
if (-not (Works "ollama" "--version")) { Get-App "Ollama.Ollama" "Ollama (the local brain)" }
if (-not (Works "ffmpeg" "-version")) { Get-App "Gyan.FFmpeg" "FFmpeg" }
if (-not (Works "git" "--version")) { Get-App "Git.Git" "Git" }

if (-not (Works "python" "--version")) {
  Write-Host "Python did not install correctly. Close this window, restart the PC and run Setup again." -ForegroundColor Red
  Read-Host "Press Enter to close"; exit 1
}

Step "Installing Trillion's own parts"
if (-not (Test-Path ".venv\Scripts\python.exe")) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "Some parts failed to install. Check your internet and run Setup again." -ForegroundColor Red }

if (-not $SkipModels) {
  Step "Downloading the local models (about 15 GB the first time)"
  if (-not (Works "ollama" "list")) { Start-Process "ollama" "serve" -WindowStyle Hidden; Start-Sleep 5 }
  foreach ($m in @("qwen2.5:3b", "nomic-embed-text", "qwen2.5:14b", "gemma3:4b")) {
    Write-Host "Model: $m"
    ollama pull $m
  }
}

Step "Creating the desktop shortcut"
try {
  $ws = New-Object -ComObject WScript.Shell
  $lnk = $ws.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath("Desktop"), "Trillion.lnk"))
  $lnk.TargetPath = Join-Path $PSScriptRoot "2 - Start Trillion.bat"
  $lnk.WorkingDirectory = $PSScriptRoot
  $lnk.Save()
} catch { Write-Host "Could not create the shortcut; use '2 - Start Trillion.bat' instead." }

Write-Host ""
Write-Host "Setup finished. Double-click 'Trillion' on the desktop to start." -ForegroundColor Green
Read-Host "Press Enter to close"
