# One-command install on Windows with Docker Desktop (PowerShell 5.1 or 7):
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Listen 0.0.0.0 -Port 4990
#
# Writes .env on the first run (API key and admin password), builds and starts the
# proxy and the sign-in browser in Docker Desktop mode (docker-compose.desktop.yml),
# waits for /health and prints where to go. Running it again keeps .env and the data.
param(
    [string]$Listen = "",
    [int]$Port = 0
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Docker Desktop is required: https://docs.docker.com/desktop/setup/install/windows-install/"
}
docker compose version *> $null
if ($LASTEXITCODE -ne 0) { Write-Error "Docker Compose v2 is required (start Docker Desktop first)" }

# No host networking on Docker Desktop: see docker-compose.desktop.yml
$env:COMPOSE_FILE = "docker-compose.yml;docker-compose.desktop.yml"
$env:COMPOSE_PATH_SEPARATOR = ";"

function New-Secret {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ([Convert]::ToBase64String($bytes) -replace '[+/=]', '')
}

function Write-EnvFile([string[]]$Lines) {
    # UTF-8 without BOM and LF line ends: docker compose and the proxy read it on Linux.
    $text = ($Lines -join "`n") + "`n"
    [System.IO.File]::WriteAllText((Join-Path (Get-Location) ".env"), $text, (New-Object System.Text.UTF8Encoding $false))
}

$newPassword = ""
if (-not (Test-Path .env)) {
    $newPassword = New-Secret
    $hostValue = if ($Listen) { $Listen } else { "127.0.0.1" }
    $portValue = if ($Port) { $Port } else { 4982 }
    Write-EnvFile @(
        "# Written by scripts/install.ps1. All settings: docs/configuration.md",
        "GOP_HOST=$hostValue",
        "GOP_PORT=$portValue",
        "# Bearer key for OpenAI clients (master key; named client keys: admin UI -> Keys)",
        "GOP_API_KEY=$(New-Secret)",
        "# Password of the admin UI at /admin/ui",
        "GOP_ADMIN_PASSWORD=$newPassword"
    )
    # Only the current user may read it: it holds the API key and the admin password.
    icacls .env /inheritance:r /grant:r "$($env:USERNAME):(R,W)" *> $null
    Write-Host "== Created .env (keep it private: it holds the API key and admin password)"
} else {
    $lines = Get-Content .env
    if ($Listen) { $lines = $lines -replace '^GOP_HOST=.*', "GOP_HOST=$Listen" }
    if ($Port) { $lines = $lines -replace '^GOP_PORT=.*', "GOP_PORT=$Port" }
    if ($Listen -or $Port) { Write-EnvFile $lines }
    Write-Host "== Keeping the existing .env"
}

$settings = @{}
foreach ($line in Get-Content .env) {
    if ($line -match '^\s*([A-Z_]+)=(.*)$') { $settings[$Matches[1]] = $Matches[2] }
}
$hostSetting = if ($settings["GOP_HOST"]) { $settings["GOP_HOST"] } else { "127.0.0.1" }
$portSetting = if ($settings["GOP_PORT"]) { $settings["GOP_PORT"] } else { "4982" }

New-Item -ItemType Directory -Force -Path data | Out-Null

# The admin UI shows the build and what is newer on GitHub (System tab, Updates).
if (Get-Command git -ErrorAction SilentlyContinue) {
    $commit = git rev-parse HEAD 2>$null
    if ($LASTEXITCODE -eq 0) {
        $env:GOP_BUILD_COMMIT = $commit
        $env:GOP_BUILD_BRANCH = git rev-parse --abbrev-ref HEAD
        $env:GOP_BUILD_DATE = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
}

Write-Host "== Building images (first run takes a few minutes)"
docker compose build
if ($LASTEXITCODE -ne 0) { Write-Error "docker compose build failed" }

Write-Host "== Starting"
docker compose up -d
if ($LASTEXITCODE -ne 0) { Write-Error "docker compose up failed" }

Write-Host "== Waiting for /health"
for ($i = 0; $i -lt 60; $i++) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:$portSetting/health"
        if ($r.Content -match '"ok"') {
            $shown = if ($hostSetting -eq "0.0.0.0") { $env:COMPUTERNAME } else { $hostSetting }
            Write-Host ""
            Write-Host "Ready."
            Write-Host "  Admin UI:   http://${shown}:$portSetting/admin/ui"
            if ($newPassword) { Write-Host "  Password:   $newPassword" }
            else { Write-Host "  Password:   GOP_ADMIN_PASSWORD in .env" }
            Write-Host "  OpenAI API: http://${shown}:$portSetting/openai/v1   (Bearer: GOP_API_KEY from .env)"
            Write-Host ""
            Write-Host "Next: open the admin UI, press the sign-in button on the default account and sign"
            Write-Host "in to Google in the browser shown on the page. Add more accounts with '+'."
            exit 0
        }
    } catch { }
    Start-Sleep -Seconds 3
}

Write-Host "Health check timed out - recent logs:"
docker compose logs --tail=80
exit 1
