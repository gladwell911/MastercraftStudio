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

function Clear-PackageOutput {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DistPath,
        [Parameter(Mandatory = $true)]
        [string]$PackageName
    )

    $resolvedDistPath = Resolve-Path -LiteralPath $DistPath -ErrorAction SilentlyContinue
    if ($null -eq $resolvedDistPath) {
        return
    }

    $targetPath = Join-Path $resolvedDistPath.Path $PackageName
    if (-not (Test-Path -LiteralPath $targetPath)) {
        return
    }

    $resolvedTargetPath = Resolve-Path -LiteralPath $targetPath
    $distRoot = $resolvedDistPath.Path.TrimEnd('\')
    $targetRoot = $resolvedTargetPath.Path.TrimEnd('\')
    if ($targetRoot -eq $distRoot -or -not $targetRoot.StartsWith($distRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        Write-Error "Refusing to clean package output outside dist path: $targetRoot"
        exit 1
    }

    $running = Get-Process -Name $PackageName -ErrorAction SilentlyContinue
    if ($running) {
        Write-Error "mc.exe is still running. Close it before packaging."
        exit 1
    }

    @(
        Get-Item -LiteralPath $targetPath -Force
        Get-ChildItem -LiteralPath $targetPath -Recurse -Force
    ) | ForEach-Object {
        try {
            $_.Attributes = [System.IO.FileAttributes]::Normal
        } catch {
            Write-Error "Failed to reset attributes for $($_.FullName): $_"
            exit 1
        }
    }

    try {
        Remove-Item -LiteralPath $targetPath -Recurse -Force
    } catch {
        Write-Error "Failed to clean package output '$targetPath'. Close any program using files under that directory and retry. $_"
        exit 1
    }
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
    Clear-PackageOutput -DistPath $DistPath -PackageName "mc"
    & $resolvedPythonExe -m PyInstaller -y --clean --distpath $DistPath --workpath $resolvedWorkPath $resolvedSpecPath
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
