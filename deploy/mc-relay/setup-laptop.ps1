param(
    [Parameter(Mandatory=$true)][string]$BundleDirectory,
    [string]$DistPath = 'D:\code\cx',
    [string]$RelayDirectory = (Join-Path $env:LOCALAPPDATA 'MC-Laptop-FRP'),
    [switch]$ValidateOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$bundle = (Resolve-Path -LiteralPath $BundleDirectory).Path
if ($bundle.StartsWith($repo + '\', [StringComparison]::OrdinalIgnoreCase) -or $bundle -eq $repo) { throw 'Keep the private bundle outside the repository.' }
$dist = [IO.Path]::GetFullPath($DistPath)
$install = Join-Path $dist 'mc'
$machinePath = Join-Path $dist 'history\remote_machine.json'
foreach ($file in @('machine.json','frpc.toml','relay-ca.pem','frpc.exe','frpc.sha256')) {
    $item = Get-Item -LiteralPath (Join-Path $bundle $file)
    if ($item.PSIsContainer -or $item.Length -eq 0) { throw "Invalid bundle file: $file" }
}
$machine = Get-Content -Raw -Encoding UTF8 (Join-Path $bundle 'machine.json') | ConvertFrom-Json
if ($machine.pair_id -ne 'laptop' -or $machine.domain -ne 'wss://mc-laptop-test.tingyou.cc:8443/nats' -or [string]::IsNullOrWhiteSpace($machine.token)) { throw 'Bundle must contain the dedicated laptop identity, endpoint and token.' }
if (Test-Path -LiteralPath $machinePath) {
    $old = Get-Content -Raw -Encoding UTF8 $machinePath | ConvertFrom-Json
    if ($old.pair_id -ne 'laptop' -or [string]::IsNullOrWhiteSpace($old.token)) { throw 'Existing history belongs to another identity or has no token; select the original laptop installation.' }
    if ($old.token -ne $machine.token) { throw 'Bundle token differs from existing laptop token; refusing credential reset.' }
}
$expectedHash = (Get-Content -Raw (Join-Path $bundle 'frpc.sha256')).Trim()
if ($expectedHash -notmatch '^[a-fA-F0-9]{64}$' -or (Get-FileHash (Join-Path $bundle 'frpc.exe') -Algorithm SHA256).Hash -ne $expectedHash) { throw 'FRPC checksum mismatch.' }
$config = Get-Content -Raw -Encoding UTF8 (Join-Path $bundle 'frpc.toml')
if ($config -notmatch '(?m)^localPort\s*=\s*18080\s*$' -or $config -notmatch '(?m)^remotePort\s*=\s*19081\s*$' -or ([regex]::Matches($config,'(?m)^\[\[proxies\]\]').Count -ne 1)) { throw 'FRPC bundle must contain only the laptop 18080 -> 19081 proxy.' }
if (@(Get-Process -Name mc,mc_worker -ErrorAction SilentlyContinue).Count -gt 0) { throw 'Exit the existing MC yourself before laptop deployment. No process was stopped.' }
if (@(Get-CimInstance Win32_Process -Filter "Name='frpc.exe'" | Where-Object { $_.ExecutablePath -eq (Join-Path $RelayDirectory 'frpc.exe') }).Count -gt 0) { throw 'Laptop FRPC is running; stop its laptop-only task/process before updating its binary.' }
if ($ValidateOnly) { Write-Output 'Laptop bundle and deployment preflight passed; no changes made.'; exit 0 }
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run deployment from a non-admin PowerShell.' }
# Existing packager retains history. BuildOnly prevents a start before laptop identity is installed.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo 'package_mc.ps1') -DistPath $dist -BuildOnly
if ($LASTEXITCODE -ne 0) { throw 'MC packaging failed.' }
New-Item -ItemType Directory -Path (Split-Path $machinePath),$RelayDirectory -Force | Out-Null
$machineJson = $machine | Select-Object pair_id,domain,token | ConvertTo-Json
[IO.File]::WriteAllText($machinePath, $machineJson, (New-Object Text.UTF8Encoding($false)))
foreach ($file in @('frpc.exe','relay-ca.pem')) { Copy-Item -LiteralPath (Join-Path $bundle $file) -Destination (Join-Path $RelayDirectory $file) -Force }
$ca = (Join-Path $RelayDirectory 'relay-ca.pem').Replace('\','/')
$log = (Join-Path $RelayDirectory 'frpc.log').Replace('\','/')
$config = [regex]::Replace($config,'(?m)^transport\.tls\.trustedCaFile\s*=.*$', ('transport.tls.trustedCaFile = "' + $ca + '"'))
$config = [regex]::Replace($config,'(?m)^log\.to\s*=.*$', ('log.to = "' + $log + '"'))
[IO.File]::WriteAllText((Join-Path $RelayDirectory 'frpc.toml'), $config, (New-Object Text.UTF8Encoding($false)))
& (Join-Path $RelayDirectory 'frpc.exe') verify -c (Join-Path $RelayDirectory 'frpc.toml') | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'FRPC config validation failed. Check Defender protection history if the executable was blocked; no exclusion was changed.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'start-laptop.ps1') -Destination $RelayDirectory -Force
$launcher = Join-Path $RelayDirectory 'start-laptop.ps1'
$argsText = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $launcher + '" -InstallDirectory "' + $install + '" -RelayDirectory "' + $RelayDirectory + '"'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argsText
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
$user = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'MC-Laptop-FRP' -Action $action -Trigger $trigger -Principal $user -Settings $settings -Force | Out-Null
Start-Process -FilePath 'powershell.exe' -ArgumentList $argsText -WindowStyle Hidden | Out-Null
Write-Output 'Laptop MC packaged and startup configured. Verify local/public authenticated connections before changing phone laptop settings.'
