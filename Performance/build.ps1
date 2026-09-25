# Build WinSight on Windows:  powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force dist | Out-Null
$env:CGO_ENABLED = '0'
foreach ($arch in 'amd64', 'arm64') {
  $env:GOOS = 'windows'; $env:GOARCH = $arch
  go build -trimpath -ldflags "-s -w" -o "dist\winsight-$arch.exe" .\cmd\winsight
}
Copy-Item dist\winsight-amd64.exe dist\winsight.exe -Force
Write-Host "Built dist\winsight.exe"
