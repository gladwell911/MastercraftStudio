param(
    [string]$DistPath = "D:\code\cx",
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

function Assert-PackageChild {
    param([string]$Root, [string]$Path)
    $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $childPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if (-not $childPath.StartsWith($rootPath + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to change a path outside package output: $childPath"
    }
}

function Remove-PackageNode {
    param([string]$Root, [string]$Path)
    Assert-PackageChild -Root $Root -Path $Path
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) { return }
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        # Directory.Delete removes the junction/symlink node without visiting its target.
        if ($item.PSIsContainer) { [IO.Directory]::Delete($item.FullName) }
        else { [IO.File]::Delete($item.FullName) }
        return
    }
    if ($item.PSIsContainer) {
        foreach ($child in Get-ChildItem -LiteralPath $item.FullName -Force) {
            Remove-PackageNode -Root $Root -Path $child.FullName
        }
        $item.Attributes = [IO.FileAttributes]::Normal
        Remove-Item -LiteralPath $item.FullName -Force
    } else {
        $item.Attributes = [IO.FileAttributes]::Normal
        Remove-Item -LiteralPath $item.FullName -Force
    }
}

function Assert-PackageArtifacts {
    param([string]$PackagePath)
    foreach ($relativePath in @('mc.exe', 'mc_worker.exe', '_internal\assets\chat_title_rules.json')) {
        $artifact = Join-Path $PackagePath $relativePath
        $item = Get-Item -LiteralPath $artifact -Force -ErrorAction SilentlyContinue
        if ($null -eq $item -or $item.PSIsContainer -or $item.Length -eq 0 -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Required package artifact missing or invalid: $artifact"
        }
    }
}

function Copy-PackageTree {
    param([string]$Source, [string]$Target, [string]$PackageRoot)
    foreach ($entry in Get-ChildItem -LiteralPath $Source -Force) {
        if ($entry.Name -in @('.codex-home', 'history')) { continue }
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Unexpected link in built package: $($entry.FullName)"
        }
        $destination = Join-Path $Target $entry.Name
        Assert-PackageChild -Root $PackageRoot -Path $destination
        $existing = Get-Item -LiteralPath $destination -Force -ErrorAction SilentlyContinue
        if ($null -ne $existing -and (($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
                $existing.PSIsContainer -ne $entry.PSIsContainer)) {
            Remove-PackageNode -Root $PackageRoot -Path $destination
        }
        if ($entry.PSIsContainer) {
            New-Item -ItemType Directory -Path $destination -Force | Out-Null
            Copy-PackageTree -Source $entry.FullName -Target $destination -PackageRoot $PackageRoot
        } else {
            Copy-Item -LiteralPath $entry.FullName -Destination $destination -Force
        }
    }
}

function Update-PackageOutput {
    param([string]$BuiltPackage, [string]$Target, [string]$DistRoot)
    Assert-PackageChild -Root $DistRoot -Path $Target
    $existing = Get-Item -LiteralPath $Target -Force -ErrorAction SilentlyContinue
    if ($null -ne $existing -and ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Package output must not be a directory link: $Target"
    }
    New-Item -ItemType Directory -Path $Target -Force | Out-Null
    $internal = Join-Path $Target '_internal'
    $internalItem = Get-Item -LiteralPath $internal -Force -ErrorAction SilentlyContinue
    if ($null -ne $internalItem) {
        if (($internalItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or -not $internalItem.PSIsContainer) {
            Remove-PackageNode -Root $Target -Path $internal
        } else {
            foreach ($entry in Get-ChildItem -LiteralPath $internal -Force) {
                if ($entry.Name -notin @('.codex-home', 'history')) {
                    Remove-PackageNode -Root $Target -Path $entry.FullName
                }
            }
        }
    }
    Copy-PackageTree -Source $BuiltPackage -Target $Target -PackageRoot $Target
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$resolvedPythonExe = if ([IO.Path]::IsPathRooted($PythonExe)) { $PythonExe } else { Join-Path $repoRoot $PythonExe }
$resolvedSpecPath = if ([IO.Path]::IsPathRooted($SpecPath)) { $SpecPath } else { Join-Path $repoRoot $SpecPath }
$resolvedWorkPath = if ([IO.Path]::IsPathRooted($WorkPath)) { $WorkPath } else { Join-Path $repoRoot $WorkPath }
$resolvedDistPath = [IO.Path]::GetFullPath($DistPath)
$stagingPath = Join-Path $resolvedDistPath ('.mc-build-' + [Guid]::NewGuid().ToString('N'))
$finalPackage = Join-Path $resolvedDistPath 'mc'
$exitCode = 1

Push-Location $repoRoot
try {
    if (Test-IsAdministrator) {
        throw 'Run package_mc.ps1 from a non-admin PowerShell session.'
    }
    if (-not (Test-Path -LiteralPath $resolvedPythonExe -PathType Leaf)) {
        throw "Python interpreter not found: $resolvedPythonExe"
    }
    if (-not (Test-Path -LiteralPath $resolvedSpecPath -PathType Leaf)) {
        throw "Spec file not found: $resolvedSpecPath"
    }
    foreach ($process in Get-Process -Name 'mc', 'mc_worker' -ErrorAction SilentlyContinue) {
        if ($process.Path -and ([IO.Path]::GetFullPath($process.Path)).StartsWith($finalPackage + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'mc.exe is still running. Close it before packaging.'
        }
    }
    New-Item -ItemType Directory -Path $stagingPath -Force | Out-Null
    & $resolvedPythonExe -m PyInstaller -y --clean --distpath $stagingPath --workpath $resolvedWorkPath $resolvedSpecPath
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
    $builtPackage = Join-Path $stagingPath 'mc'
    Assert-PackageArtifacts -PackagePath $builtPackage
    Update-PackageOutput -BuiltPackage $builtPackage -Target $finalPackage -DistRoot $resolvedDistPath
    Remove-PackageNode -Root $resolvedDistPath -Path $stagingPath
    Assert-PackageArtifacts -PackagePath $finalPackage
    # Launch once from the final directory, without waiting for the GUI to exit.
    Start-Process -FilePath (Join-Path $finalPackage 'mc.exe') -WorkingDirectory $finalPackage -ErrorAction Stop | Out-Null
    $exitCode = 0
} catch {
    Write-Error -Message $_ -ErrorAction Continue
} finally {
    try { Remove-PackageNode -Root $resolvedDistPath -Path $stagingPath }
    catch { Write-Error -Message $_ -ErrorAction Continue; $exitCode = 1 }
    Pop-Location
}
exit $exitCode
