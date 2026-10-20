param(
    [Parameter(Mandatory=$true)][string]$InstallDirectory,
    [Parameter(Mandatory=$true)][string]$RelayDirectory
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$env:REMOTE_CONTROL_PAIR_ID = 'laptop'
$env:REMOTE_CONTROL_TUNNEL_MODE = 'external'
# Credentials come only from history/remote_machine.json, never inherited desktop overrides.
foreach ($name in @('REMOTE_CONTROL_DOMAIN','REMOTE_CONTROL_TOKEN','CLAUDECODE_REMOTE_CONTROL_DOMAIN','CLAUDECODE_REMOTE_CONTROL_TOKEN','CLAUDECODE_REMOTE_CONTROL_PAIR_ID','CLAUDECODE_REMOTE_CONTROL_TUNNEL_MODE')) {
    [Environment]::SetEnvironmentVariable($name, $null, 'Process')
}
$mutex = New-Object Threading.Mutex($false, 'Local\MC-Laptop-FRP-Startup')
if (-not $mutex.WaitOne(0)) { $mutex.Dispose(); exit 0 }
try {
    $frpc = Join-Path $RelayDirectory 'frpc.exe'
    $runningFrp = @(Get-CimInstance Win32_Process -Filter "Name='frpc.exe'" | Where-Object { $_.ExecutablePath -eq $frpc })
    if ($runningFrp.Count -eq 0) {
        # Clear proxy variables only in this launcher and its FRP child, then restore.
        $saved = @{}
        try {
            foreach ($name in @('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY')) {
                $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
                [Environment]::SetEnvironmentVariable($name, $null, 'Process')
            }
            Start-Process -FilePath $frpc -ArgumentList @('-c', ('"' + (Join-Path $RelayDirectory 'frpc.toml') + '"')) -WorkingDirectory $RelayDirectory -WindowStyle Hidden | Out-Null
        } finally {
            foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
        }
    }
    # A second installation must never claim the laptop identity.
    if (@(Get-Process -Name mc -ErrorAction SilentlyContinue).Count -eq 0) {
        Start-Process -FilePath (Join-Path $InstallDirectory 'mc.exe') -WorkingDirectory $InstallDirectory -WindowStyle Hidden | Out-Null
    }
} finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
