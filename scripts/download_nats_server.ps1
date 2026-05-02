$ErrorActionPreference = "Stop"

$Version = if ($env:NATS_SERVER_VERSION) { $env:NATS_SERVER_VERSION } else { "v2.12.8" }
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$TargetDir = Join-Path $Root "tools/nats-server"
$ZipPath = Join-Path $TargetDir "nats-server.zip"
$Url = "https://github.com/nats-io/nats-server/releases/download/$Version/nats-server-$Version-windows-amd64.zip"
$InstalledPath = Join-Path $TargetDir "nats-server.exe"
$ExtractDir = Join-Path $TargetDir "extract"

New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
Invoke-WebRequest -Uri $Url -OutFile $ZipPath
if (Test-Path $ExtractDir) {
    Remove-Item -Path $ExtractDir -Recurse -Force
}
New-Item -ItemType Directory -Path $ExtractDir -Force | Out-Null
Expand-Archive -Path $ZipPath -DestinationPath $ExtractDir -Force

$NatsServer = Get-ChildItem -Path $ExtractDir -Filter "nats-server.exe" -Recurse -File |
    Select-Object -First 1

if (-not $NatsServer) {
    throw "nats-server.exe not found in archive"
}

Copy-Item -Path $NatsServer.FullName -Destination $InstalledPath -Force
Remove-Item -Path $ZipPath -Force
Remove-Item -Path $ExtractDir -Recurse -Force
Write-Host "Installed nats-server to $InstalledPath"
