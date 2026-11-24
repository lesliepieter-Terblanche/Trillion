# Trillion setup. Run by double-clicking "1 - Setup.bat". Safe to run again at any time.
param([switch]$SkipModels, [switch]$SkipModelsHeavy)
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
if (-not (Works "adb" "version")) { Get-App "Google.PlatformTools" "Android phone tools (ADB)" }
if (-not (Works "node" "--version")) { Get-App "OpenJS.NodeJS.LTS" "Node.js (needed by connectors)" }

if (-not (Works "python" "--version")) {
  Write-Host "Python did not install correctly. Close this window, restart the PC and run Setup again." -ForegroundColor Red
  Read-Host "Press Enter to close"; exit 1
}

Step "Installing Trillion's own parts"
if (-not (Test-Path ".venv\Scripts\python.exe")) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "Some parts failed to install. Check your internet and run Setup again." -ForegroundColor Red }

# Optional: the smart knowledge store for the second brain. If it will not install, Trillion still works
# (she falls back to her built-in memory search), so a failure here does not stop setup.
# --no-deps: these optional extras pull in packages (kubernetes, gTTS's click pin) that clash with Trillion's
# core and used to downgrade it mid-setup (the long "looking at multiple versions" backtrack + red conflict
# errors). Installing them without their dependency tree keeps core exactly as installed above, so setup is
# fast and clean; the extras use what core already provides and fall back gracefully if something's missing.
Step "Adding the smart knowledge store (optional)"
& .\.venv\Scripts\python.exe -m pip install --quiet --no-deps chromadb
if ($LASTEXITCODE -ne 0) { Write-Host "Skipped the knowledge store; Trillion's built-in memory still works." -ForegroundColor DarkYellow }

# Optional: casting to Google Home / Chromecast (and text-to-speech for announcements).
Step "Adding casting and cameras (optional)"
& .\.venv\Scripts\python.exe -m pip install --quiet --no-deps pychromecast gTTS onvif-zeep
if ($LASTEXITCODE -ne 0) { Write-Host "Skipped casting/camera extras; everything else still works." -ForegroundColor DarkYellow }

# Optional: OpenCV for the local PC webcam. If it will not install, the webcam still works via ffmpeg,
# so a failure here does not stop setup.
& .\.venv\Scripts\python.exe -m pip install --quiet opencv-python
if ($LASTEXITCODE -ne 0) { Write-Host "Skipped OpenCV; the webcam still works through ffmpeg." -ForegroundColor DarkYellow }

if (-not $SkipModels) {
  Step "Downloading the local models (about 15 GB the first time)"
  if (-not (Works "ollama" "list")) { Start-Process "ollama" "serve" -WindowStyle Hidden; Start-Sleep 5 }
  # qwen3:14b is the main brain - much more reliable at calling Trillion's tools than qwen2.5.
  # moondream is a tiny vision model so Trillion can actually "see" the webcam ("what do you see").
  foreach ($m in @("qwen3:4b", "nomic-embed-text", "qwen3:14b", "moondream")) {
    Write-Host "Model: $m"
    ollama pull $m
  }
  # The bigger "smart" brain for coding and hard reasoning (about 14 GB, fits your 16 GB card at Q4).
  # Skipped with -SkipModelsHeavy; you can also get it later by telling Trillion "install the smart model".
  if (-not $SkipModelsHeavy) {
    Write-Host "Model: mistral-small:24b (the smart brain - about 14 GB)"
    ollama pull mistral-small:24b
    # qwen2.5-coder:14b designs bespoke websites from a prompt (free, local, ~9 GB, fits your 16 GB card).
    Write-Host "Model: qwen2.5-coder:14b (the website design brain - about 9 GB)"
    ollama pull qwen2.5-coder:14b
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
