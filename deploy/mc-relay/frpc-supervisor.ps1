[CmdletBinding()]
param(
    [ValidateSet('Run','Stop','Status','Resume','Library')][string]$Mode = 'Status',
    [string]$Executable, [string]$Config, [string]$TaskName,
    [string]$RuntimeDirectory = $PSScriptRoot,
    [ValidateRange(1,60)][int]$RestartSeconds = 2
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Normalize-RelayPath([string]$Path) {
    if (-not [IO.Path]::IsPathRooted($Path)) { throw 'Absolute path required' }
    [IO.Path]::GetFullPath($Path.Replace('/', '\')).TrimEnd('\').ToLowerInvariant()
}
function Get-RelayIdentity([string]$ConfigPath) {
    $bytes = [Text.Encoding]::UTF8.GetBytes(([Security.Principal.WindowsIdentity]::GetCurrent().User.Value + '|' + (Normalize-RelayPath $ConfigPath)))
    $sha = [Security.Cryptography.SHA256]::Create()
    try { ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
}
function Initialize-RelayNative {
    if ('RelayNative' -as [type]) { return }
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class RelayNative {
 [DllImport("shell32.dll", SetLastError=true)] static extern IntPtr CommandLineToArgvW([MarshalAs(UnmanagedType.LPWStr)] string s, out int n);
 [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr p);
 public static string[] Args(string s) {
   int n; IntPtr p=CommandLineToArgvW(s,out n); if(p==IntPtr.Zero) throw new InvalidOperationException();
   try { var a=new string[n]; for(int i=0;i<n;i++) a[i]=Marshal.PtrToStringUni(Marshal.ReadIntPtr(p,i*IntPtr.Size)); return a; }
   finally { LocalFree(p); }
 }
}
"@
}
function Get-RelayChildren([string]$ExePath, [string]$ConfigPath) {
    Initialize-RelayNative
    $exeKey = Normalize-RelayPath $ExePath
    $configKey = Normalize-RelayPath $ConfigPath
    $name = [IO.Path]::GetFileName($ExePath).Replace("'", "''")
    foreach ($item in @(Get-CimInstance Win32_Process -Filter "Name='$name'")) {
        if (-not $item.ExecutablePath -or -not $item.CommandLine) { throw 'Process identity unavailable' }
        if ((Normalize-RelayPath $item.ExecutablePath) -ne $exeKey) { continue }
        $argv = [RelayNative]::Args($item.CommandLine)
        $configs = @()
        for ($i=1; $i -lt $argv.Length; $i++) {
            if ($argv[$i] -in @('-c','--config')) {
                if (++$i -ge $argv.Length) { throw 'Ambiguous config argument' }
                $configs += $argv[$i]
            } elseif ($argv[$i].StartsWith('-c=')) { $configs += $argv[$i].Substring(3) } elseif ($argv[$i].StartsWith('--config=')) { $configs += $argv[$i].Substring(9) }
        }
        if ($configs.Count -ne 1) { continue }
        if (-not [IO.Path]::IsPathRooted($configs[0])) { throw 'Relative child config identity unavailable' }
        if ((Normalize-RelayPath $configs[0]) -ne $configKey) { continue }
        [pscustomobject]@{ Pid=[int]$item.ProcessId; Created=$item.CreationDate.ToUniversalTime().Ticks }
    }
}
function Get-RelayProcess($Identity, [string]$ExePath, [string]$ConfigPath) {
    $current = @(Get-RelayChildren $ExePath $ConfigPath | Where-Object { $_.Pid -eq $Identity.Pid -and $_.Created -eq $Identity.Created })
    if ($current.Count -ne 1) { return $null }
    try {
        $p = [Diagnostics.Process]::GetProcessById($Identity.Pid)
        # Hold an OS handle before the final identity check, preventing PID reuse on Kill.
        $null = $p.Handle
        if (($p.StartTime.ToUniversalTime().Ticks - ($p.StartTime.ToUniversalTime().Ticks % 10)) -ne $Identity.Created) { $p.Dispose(); return $null }
        return $p
    } catch [ArgumentException] { return $null }
}
function Write-RelayEvent([string]$State) {
    # State is always a literal supplied by this script; exceptions and command lines are never logged.
    try {
        $file = Join-Path $RuntimeDirectory 'supervisor.log'
        if ((Test-Path $file) -and (Get-Item $file).Length -gt 1048576) { Move-Item $file ($file + '.previous') -Force }
        Add-Content -LiteralPath $file -Value ((Get-Date).ToUniversalTime().ToString('o') + ' ' + $State) -Encoding UTF8
    } catch { }
}
function Enter-RelayOperation([string]$ConfigPath) {
    # Named mutexes are recursive on the calling thread: installer -> Stop/Resume is safe.
    $operation = New-Object Threading.Mutex($false, ('Global\MCRelay-operation-' + (Get-RelayIdentity $ConfigPath)))
    try {
        try { $acquired = $operation.WaitOne(30000) } catch [Threading.AbandonedMutexException] { $acquired=$true }
        if (-not $acquired) { throw 'Relay operation already in progress' }
        return $operation
    } catch { $operation.Dispose(); throw }
}
function Get-RelayDrift([string]$Directory=$RuntimeDirectory, [string]$ExePath=$Executable, [string]$ConfigPath=$Config, [string]$Task=$TaskName) {
    $manifestPath = Join-Path $Directory 'supervisor-install.json'
    if (-not (Test-Path -LiteralPath $manifestPath)) { return 'unmanaged' }
    try {
        $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
        $expectedProfile = switch ($Task) { 'MCRelay-frpc' { 'MCRelay' }; 'ControlTower-frpc' { 'ControlTower' }; default { throw 'Unknown managed profile' } }
        if ($manifest.Profile -cne $expectedProfile -or $manifest.Task -cne $Task -or
            (Normalize-RelayPath $manifest.Config) -ne (Normalize-RelayPath $ConfigPath) -or
            (Normalize-RelayPath $manifest.Executable) -ne (Normalize-RelayPath $ExePath)) { return 'drift' }
        $required = @('start-frpc.ps1','frpc-supervisor.ps1')
        $entries = @($manifest.Hashes.PSObject.Properties)
        if ($entries.Count -ne $required.Count) { return 'drift' }
        foreach ($name in $required) {
            $entry = @($entries | Where-Object { $_.Name -ceq $name })
            if ($entry.Count -ne 1 -or $entry[0].Value -notmatch '^[A-Fa-f0-9]{64}$') { return 'drift' }
            $path = Join-Path $Directory $name
            if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry[0].Value) { return 'drift' }
        }
        return 'clean'
    } catch { return 'drift' }
}
function Assert-RelayTask {
    if (-not $TaskName) { return }
    $task = Get-ScheduledTask -TaskName $TaskName -TaskPath '\'
    if ($task.Actions.Count -ne 1) { throw 'Unexpected task action' }
    $args = [RelayNative]::Args(('powershell.exe ' + $task.Actions[0].Arguments))
    $index = [Array]::IndexOf($args, '-File')
    if ($index -lt 0 -or $index+1 -ge $args.Length -or (Normalize-RelayPath $args[$index+1]) -ne (Normalize-RelayPath (Join-Path $RuntimeDirectory 'start-frpc.ps1'))) { throw 'Unexpected task wrapper' }
    if ((Normalize-RelayPath $task.Actions[0].Execute) -ne (Normalize-RelayPath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe")) { throw 'Unexpected task executable' }
}
if ($Mode -eq 'Library') { return }
$Executable = Normalize-RelayPath $Executable
$Config = Normalize-RelayPath $Config
$RuntimeDirectory = Normalize-RelayPath $RuntimeDirectory
$identity = Get-RelayIdentity $Config
$maintenance = Join-Path $RuntimeDirectory ('maintenance-' + $identity)
$mutex = New-Object Threading.Mutex($false, ('Global\MCRelay-' + $identity))
$locked = $false
$operation = $null
Initialize-RelayNative
try {
    if ($Mode -eq 'Status') {
        $children = @(Get-RelayChildren $Executable $Config)
        try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked=$true }
        [pscustomobject]@{Identity=$identity; Config=$Config; ChildPids=@($children | ForEach-Object Pid); SupervisorActive=(-not $locked); Maintenance=(Test-Path -LiteralPath $maintenance); Deployment=(Get-RelayDrift)}
        return
    }
    if ($Mode -in @('Stop','Resume')) { $operation = Enter-RelayOperation $Config }
    if ($Mode -eq 'Resume') {
        Assert-RelayTask
        if (Test-Path -LiteralPath $maintenance) { Remove-Item -LiteralPath $maintenance -Force -ErrorAction Stop }
        if ($TaskName) { Enable-ScheduledTask -TaskName $TaskName -TaskPath '\' | Out-Null; Start-ScheduledTask -TaskName $TaskName -TaskPath '\' }
        return
    }
    if ($Mode -eq 'Stop') {
        Assert-RelayTask
        # Persist before disabling/stopping so a concurrent supplemental launch also exits.
        [IO.File]::WriteAllText($maintenance, 'maintenance')
        if ($TaskName) {
            Disable-ScheduledTask -TaskName $TaskName -TaskPath '\' | Out-Null
            Stop-ScheduledTask -TaskName $TaskName -TaskPath '\'
            $deadline = (Get-Date).AddSeconds(30)
            while ((Get-ScheduledTask -TaskName $TaskName -TaskPath '\').State -eq 'Running') {
                if ((Get-Date) -gt $deadline) { throw 'Task did not stop' }
                Start-Sleep -Milliseconds 200
            }
        }
        try { $locked = $mutex.WaitOne(30000) } catch [Threading.AbandonedMutexException] { $locked=$true }
        if (-not $locked) { throw 'Supervisor did not release identity lock' }
        foreach ($child in @(Get-RelayChildren $Executable $Config)) {
            $process = Get-RelayProcess $child $Executable $Config
            if ($process) { try { $process.Kill(); if (-not $process.WaitForExit(10000)) { throw 'Child did not stop' } } finally { $process.Dispose() } }
        }
        if (@(Get-RelayChildren $Executable $Config).Count -ne 0) { throw 'Owned children remain' }
        Write-RelayEvent 'stopped'
        return
    }
    try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked=$true }
    if (-not $locked) { return }
    if (Test-Path -LiteralPath $maintenance) { return }
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf) -or -not (Test-Path -LiteralPath $Config -PathType Leaf)) { throw 'Missing executable or config' }
    if ((Get-RelayDrift) -eq 'drift') { throw 'Deployment drift' }
    $delay = $RestartSeconds
    while (-not (Test-Path -LiteralPath $maintenance)) {
        $process = $null
        try {
            $children = @(Get-RelayChildren $Executable $Config)
            if ($children.Count -gt 1) { Write-RelayEvent 'multiple-children-refused'; throw 'Multiple children' }
            if ($children.Count -eq 1) {
                $process = Get-RelayProcess $children[0] $Executable $Config
                if (-not $process) { throw 'Child identity changed' }
                Write-RelayEvent 'adopted'
            } else {
                $info = New-Object Diagnostics.ProcessStartInfo
                $info.FileName = $Executable
                $info.Arguments = '-c "' + $Config + '"'
                $info.WorkingDirectory = [IO.Path]::GetDirectoryName($Config)
                $info.UseShellExecute = $false
                $info.CreateNoWindow = $true
                foreach ($key in @('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')) { $info.EnvironmentVariables.Remove($key) }
                $process = [Diagnostics.Process]::Start($info)
                $null = $process.Handle
                Write-RelayEvent 'started'
            }
            $started = Get-Date
            while (-not $process.WaitForExit(250)) {
                if (Test-Path -LiteralPath $maintenance) { break }
            }
            if (Test-Path -LiteralPath $maintenance) { break }
            Write-RelayEvent 'child-exited'
            if (((Get-Date)-$started).TotalSeconds -ge 30) { $delay=$RestartSeconds }
        } catch { Write-RelayEvent 'supervision-error' }
        finally { if ($process) { $process.Dispose() } }
        # All exits, including successful exits and startup/identity failures, back off.
        for ($wait=0; $wait -lt ($delay*4); $wait++) {
            if (Test-Path -LiteralPath $maintenance) { break }
            Start-Sleep -Milliseconds 250
        }
        $delay = [Math]::Min(30, $delay*2)
    }
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
    if ($operation) { $operation.ReleaseMutex(); $operation.Dispose() }
}
