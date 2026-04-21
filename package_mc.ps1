param(
    [string]$DistPath = "C:\code\cx",
    [string]$WorkPath = "build_pyinstaller",
    [string]$SpecPath = "zgwd.spec",
    [string]$PythonExe = ".venv\Scripts\python.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (Test-IsAdministrator) {
    Write-Error "Run package_mc.ps1 from a non-admin PowerShell session. The current admin shell triggers the PyInstaller deprecation warning."
    exit 1
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$resolvedPythonExe = if ([System.IO.Path]::IsPathRooted($PythonExe)) {
    $PythonExe
} else {
    Join-Path $repoRoot $PythonExe
}
$resolvedSpecPath = if ([System.IO.Path]::IsPathRooted($SpecPath)) {
    $SpecPath
} else {
    Join-Path $repoRoot $SpecPath
}
$resolvedWorkPath = if ([System.IO.Path]::IsPathRooted($WorkPath)) {
    $WorkPath
} else {
    Join-Path $repoRoot $WorkPath
}

if (-not (Test-Path $resolvedPythonExe)) {
    Write-Error "Python interpreter not found: $resolvedPythonExe"
    exit 1
}

if (-not (Test-Path $resolvedSpecPath)) {
    Write-Error "Spec file not found: $resolvedSpecPath"
    exit 1
}

Push-Location $repoRoot
try {
    & $resolvedPythonExe -m PyInstaller -y --clean --distpath $DistPath --workpath $resolvedWorkPath $resolvedSpecPath
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
