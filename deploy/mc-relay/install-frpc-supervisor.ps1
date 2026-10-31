[CmdletBinding()]
param(
    [ValidateSet('MCRelay','ControlTower','All')][string]$Profile = 'All',
    [switch]$AdoptExisting
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$helper = Join-Path $PSScriptRoot 'frpc-supervisor.ps1'
. $helper -Mode Library
$exe = Join-Path $env:LOCALAPPDATA 'MCRelay\frpc.exe'
$profiles = @(
    @{Name='MCRelay'; Directory=(Join-Path $env:LOCALAPPDATA 'MCRelay'); Task='MCRelay-frpc'},
    @{Name='ControlTower'; Directory=(Join-Path $env:LOCALAPPDATA 'controlTower\relay'); Task='ControlTower-frpc'}
) | Where-Object { $Profile -eq 'All' -or $_.Name -eq $Profile }
$operations = New-Object Collections.Generic.List[object]
try {
# Hold every selected identity in stable order from preflight through success/rollback.
foreach ($p in @($profiles | Sort-Object Name)) {
    $operations.Add((Enter-RelayOperation (Join-Path $p.Directory 'frpc.toml')))
}
# Preflight every selected profile before any task or runtime mutation. Output may contain secrets.
foreach ($p in $profiles) {
    $config = Join-Path $p.Directory 'frpc.toml'
    if (-not (Test-Path -LiteralPath $exe) -or -not (Test-Path -LiteralPath $config)) { throw 'Missing existing relay files' }
    $verify = & $exe verify -c $config 2>&1
    if ($LASTEXITCODE -ne 0) { throw ('Configuration preflight failed: ' + $p.Name) }
    $manifest = Join-Path $p.Directory 'supervisor-install.json'
    if (Test-Path -LiteralPath $manifest) {
        if ((Get-RelayDrift $p.Directory $exe $config $p.Task) -ne 'clean') { throw ('External script or identity drift; inspect before installation: ' + $p.Name) }
    } elseif (-not $AdoptExisting) { throw 'First installation requires explicit -AdoptExisting after inspecting the existing wrappers' }
    $task = Get-ScheduledTask -TaskName $p.Task -TaskPath '\'
    Initialize-RelayNative
    $savedTaskName=$TaskName; $savedDirectory=$RuntimeDirectory
    $TaskName=$p.Task; $RuntimeDirectory=$p.Directory
    try { Assert-RelayTask } finally { $TaskName=$savedTaskName; $RuntimeDirectory=$savedDirectory }
}
$backupRoot = Join-Path $env:LOCALAPPDATA ('MCRelay-supervisor-backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
foreach ($p in $profiles) {
    $backup = Join-Path $backupRoot $p.Name
    New-Item -ItemType Directory -Path $backup | Out-Null
    $oldTask = Get-ScheduledTask -TaskName $p.Task -TaskPath '\'
    $wasRunning = $oldTask.State -eq 'Running'
    $wasEnabled = [bool]$oldTask.Settings.Enabled
    $marker = Join-Path $p.Directory ('maintenance-' + (Get-RelayIdentity (Join-Path $p.Directory 'frpc.toml')))
    $hadMarker = Test-Path -LiteralPath $marker
    $markerBytes = if ($hadMarker) { [IO.File]::ReadAllBytes($marker) } else { $null }
    $xmlText = Export-ScheduledTask -TaskName $p.Task -TaskPath '\'
    [IO.File]::WriteAllText((Join-Path $backup 'task.xml'), $xmlText)
    $files = @('start-frpc.ps1','frpc-supervisor.ps1','supervisor-install.json')
    foreach ($name in $files) {
        $file = Join-Path $p.Directory $name
        if (Test-Path -LiteralPath $file) { Copy-Item -LiteralPath $file -Destination (Join-Path $backup $name) }
    }
    try {
        & $helper -Mode Stop -Executable $exe -Config (Join-Path $p.Directory 'frpc.toml') -TaskName $p.Task -RuntimeDirectory $p.Directory
        Copy-Item -LiteralPath $helper -Destination (Join-Path $p.Directory 'frpc-supervisor.ps1') -Force
        $wrapper = @"
`$ErrorActionPreference = 'Stop'
& (Join-Path `$PSScriptRoot 'frpc-supervisor.ps1') -Mode Run -Executable '$($exe.Replace("'","''"))' -Config (Join-Path `$PSScriptRoot 'frpc.toml') -TaskName '$($p.Task)' -RuntimeDirectory `$PSScriptRoot
"@
        [IO.File]::WriteAllText((Join-Path $p.Directory 'start-frpc.ps1'), $wrapper, (New-Object Text.UTF8Encoding($true)))
        $hashes = @{}
        foreach ($name in @('start-frpc.ps1','frpc-supervisor.ps1')) { $hashes[$name] = (Get-FileHash -LiteralPath (Join-Path $p.Directory $name)).Hash }
        @{Profile=$p.Name; Config=(Normalize-RelayPath (Join-Path $p.Directory 'frpc.toml')); Executable=(Normalize-RelayPath $exe); Task=$p.Task; InstalledUtc=[DateTime]::UtcNow.ToString('o'); Hashes=$hashes} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $p.Directory 'supervisor-install.json') -Encoding UTF8
        $logon = @($oldTask.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' })
        if ($logon.Count -eq 0) { throw 'Missing existing login trigger' }
        $supplement = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
        $settings = $oldTask.Settings
        $settings.MultipleInstances = 'IgnoreNew'
        $settings.StartWhenAvailable = $true
        $settings.ExecutionTimeLimit = 'PT0S'
        $settings.DisallowStartIfOnBatteries = $false
        $settings.StopIfGoingOnBatteries = $false
        $settings.Enabled = $true
        Register-ScheduledTask -TaskName $p.Task -TaskPath '\' -Action $oldTask.Actions -Trigger @($logon + $supplement) -Settings $settings -Principal $oldTask.Principal -Force -ErrorAction Stop | Out-Null
        & $helper -Mode Resume -Executable $exe -Config (Join-Path $p.Directory 'frpc.toml') -TaskName $p.Task -RuntimeDirectory $p.Directory
        $deadline = (Get-Date).AddSeconds(20)
        do {
            $state = & $helper -Mode Status -Executable $exe -Config (Join-Path $p.Directory 'frpc.toml') -TaskName $p.Task -RuntimeDirectory $p.Directory
            if ($state.SupervisorActive -and $state.ChildPids.Count -eq 1 -and $state.Deployment -eq 'clean') { break }
            Start-Sleep -Milliseconds 250
        } while ((Get-Date) -lt $deadline)
        if (-not $state.SupervisorActive -or $state.ChildPids.Count -ne 1 -or $state.Deployment -ne 'clean') { throw 'Installed supervisor did not become ready' }
        [pscustomobject]@{Profile=$p.Name; Installed=$true; Backup=$backup}
    } catch {
        # Roll back only this profile. Neither configuration nor the shared binary is replaced.
        try {
            & $helper -Mode Stop -Executable $exe -Config (Join-Path $p.Directory 'frpc.toml') -TaskName $p.Task -RuntimeDirectory $p.Directory
            foreach ($name in $files) {
                $old = Join-Path $backup $name
                $target = Join-Path $p.Directory $name
                if (Test-Path -LiteralPath $old) { Copy-Item -LiteralPath $old -Destination $target -Force } elseif (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
            }
            Register-ScheduledTask -TaskName $p.Task -TaskPath '\' -Xml $xmlText -Force -ErrorAction Stop | Out-Null
            if ($hadMarker) { [IO.File]::WriteAllBytes($marker, [byte[]]$markerBytes) }
            elseif (Test-Path -LiteralPath $marker) { Remove-Item -LiteralPath $marker -Force -ErrorAction Stop }
            if ($wasEnabled) { Enable-ScheduledTask -TaskName $p.Task -TaskPath '\' | Out-Null }
            else { Disable-ScheduledTask -TaskName $p.Task -TaskPath '\' | Out-Null }
            if ($wasRunning) { Start-ScheduledTask -TaskName $p.Task -TaskPath '\' }
        } catch { throw ('Installation and rollback failed: ' + $p.Name + '; backup: ' + $backup) }
        throw ('Installation failed and affected profile restored: ' + $p.Name)
    }
}
} finally {
    for ($i=$operations.Count-1; $i -ge 0; $i--) { $operations[$i].ReleaseMutex(); $operations[$i].Dispose() }
}
