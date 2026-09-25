# One-time setup of Control on this PC. Safe to re-run.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) { & (uv python find 3.11) -m venv .venv }
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\python -m pip install -e ..\win-agent-desktop -e "..\Office-Automation[all]" -e ".[dev]"
New-Item -ItemType Directory -Force bin | Out-Null
Push-Location ..\Performance
go build -o ..\hub\bin\winsight.exe .\cmd\winsight
Pop-Location
Write-Host "Control is ready: .venv\Scripts\control doctor"
