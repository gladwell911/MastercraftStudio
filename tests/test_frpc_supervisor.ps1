# Isolated Windows PowerShell 5.1 integration tests. No daily tasks/processes are fixtures.
$ErrorActionPreference='Stop'
$helper=Join-Path (Split-Path $PSScriptRoot -Parent) 'deploy\mc-relay\frpc-supervisor.ps1'
. $helper -Mode Library
$root=Join-Path $env:TEMP ('relay supervisor tests '+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $root | Out-Null
$fake=Join-Path $root 'fake child.exe'
Add-Type -OutputAssembly $fake -OutputType ConsoleApplication -TypeDefinition @"
using System;
using System.IO;
using System.Threading;
public static class FakeRelay {
 public static void Main(string[] args) {
   string c=args[0].StartsWith("-c=")?args[0].Substring(3):args[1]; string mode=File.ReadAllText(c).Trim();
   File.AppendAllText(c+".starts", "start\n");
   File.WriteAllText(c+".proxy", Environment.GetEnvironmentVariable("HTTP_PROXY")??"absent");
   if(mode=="exit0") return;
   if(mode=="exit7") Environment.Exit(7);
   while(true) Thread.Sleep(100);
 }
}
"@
$cfgA=Join-Path $root 'Alpha config.toml'; $cfgB=Join-Path $root 'Beta config.toml'
'loop' | Set-Content $cfgA; 'loop' | Set-Content $cfgB
$wrappers=New-Object Collections.Generic.List[object]
function Assert($condition,$message) { if(-not $condition){throw ('FAILED: '+$message)}; Write-Output ('PASS '+$message) }
function Children($cfg) { ,@(Get-RelayChildren $fake $cfg) }
function Wait-For($predicate,$message) {
 $until=(Get-Date).AddSeconds(20)
 do { if(& $predicate){return}; Start-Sleep -Milliseconds 200 } while((Get-Date)-lt $until)
 throw ('Timed out: '+$message)
}
function Run-Wrapper($cfg) {
 $args='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$helper+'" -Mode Run -Executable "'+$fake+'" -Config "'+$cfg+'" -RuntimeDirectory "'+$root+'" -RestartSeconds 1'
 $p=Start-Process "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $args -WindowStyle Hidden -PassThru
 $wrappers.Add($p); return $p
}
function Stop-Profile($cfg) { & $helper -Mode Stop -Executable $fake -Config $cfg -RuntimeDirectory $root }
function Resume-Profile($cfg) { & $helper -Mode Resume -Executable $fake -Config $cfg -RuntimeDirectory $root }
try {
 $originalProxy=$env:HTTP_PROXY; $env:HTTP_PROXY='test-proxy'
 $a=Run-Wrapper ($cfgA.ToUpperInvariant().Replace('\','/'))
 $b=Run-Wrapper $cfgB
 Wait-For { (Children $cfgA).Count -eq 1 -and (Children $cfgB).Count -eq 1 } 'distinct configurations'
 Assert ((Children $cfgA)[0].Pid -ne (Children $cfgB)[0].Pid) 'same executable distinct configs including spaces/case/slashes'
 Assert ((Get-Content ($cfgA+'.proxy')) -eq 'absent' -and $env:HTTP_PROXY -eq 'test-proxy') 'proxy removed only in child environment'
 $duplicate=Run-Wrapper $cfgA
 Assert ($duplicate.WaitForExit(10000) -and $duplicate.ExitCode -eq 0 -and (Children $cfgA).Count -eq 1) 'concurrent same-profile wrapper exits harmlessly'
 $beforeB=(Children $cfgB)[0].Pid
 $old=(Children $cfgA)[0].Pid
 Stop-Process -Id $old
 Wait-For { (Children $cfgA).Count -eq 1 -and (Children $cfgA)[0].Pid -ne $old } 'child recovery'
 Assert ((Children $cfgB)[0].Pid -eq $beforeB) 'recovery leaves other configuration untouched'
 $adoptPid=(Children $cfgA)[0].Pid
 Stop-Process -Id $a.Id; $a.WaitForExit()
 $a=Run-Wrapper $cfgA
 Start-Sleep -Seconds 2
 Assert ((Children $cfgA).Count -eq 1 -and (Children $cfgA)[0].Pid -eq $adoptPid -and -not $a.HasExited) 'supplemental wrapper adopts orphan under abandoned lock'
 Stop-Profile $cfgA
 Assert ((Children $cfgA).Count -eq 0 -and (Children $cfgB)[0].Pid -eq $beforeB) 'clean stop is configuration-specific'
 $a=Run-Wrapper $cfgA
 Assert ($a.WaitForExit(10000) -and (Children $cfgA).Count -eq 0) 'maintenance interval blocks relaunch'
 Resume-Profile $cfgA
 # Operation lock blocks a concurrent Resume throughout maintenance.
 $operation=Enter-RelayOperation $cfgA
 try {
   [IO.File]::WriteAllText((Join-Path $root ('maintenance-'+(Get-RelayIdentity $cfgA))), 'held')
   $resumeArgs='-NoProfile -ExecutionPolicy Bypass -File "'+$helper+'" -Mode Resume -Executable "'+$fake+'" -Config "'+$cfgA+'" -RuntimeDirectory "'+$root+'"'
   $resumer=Start-Process "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $resumeArgs -WindowStyle Hidden -PassThru
   $wrappers.Add($resumer)
   Start-Sleep -Seconds 1
   Assert (-not $resumer.HasExited -and (Test-Path (Join-Path $root ('maintenance-'+(Get-RelayIdentity $cfgA))))) 'operation lock serializes concurrent maintenance'
 } finally { $operation.ReleaseMutex();$operation.Dispose() }
 Assert ($resumer.WaitForExit(10000) -and $resumer.ExitCode -eq 0) 'nested and waiting Resume completes without deadlock'
 $equals=Start-Process $fake -ArgumentList ('-c="'+$cfgA+'"') -WindowStyle Hidden -PassThru
 $a=Run-Wrapper $cfgA
 Start-Sleep -Seconds 2
 Assert ((Children $cfgA).Count -eq 1 -and (Children $cfgA)[0].Pid -eq $equals.Id) '-c= quoted config child adopted without duplicate'
 Stop-Profile $cfgA
 Assert ((Children $cfgA).Count -eq 0) '-c= child precisely stopped'
 Resume-Profile $cfgA
 # Exact quoted -c parsing and refusal to choose among duplicates.
 $one=Start-Process $fake -ArgumentList ('-c "'+$cfgA+'"') -WindowStyle Hidden -PassThru
 $two=Start-Process $fake -ArgumentList ('-c "'+$cfgA+'"') -WindowStyle Hidden -PassThru
 $a=Run-Wrapper $cfgA
 Start-Sleep -Seconds 2
 Assert ((Children $cfgA).Count -eq 2 -and -not $one.HasExited -and -not $two.HasExited) 'multiple exact quoted -c children fail closed without killing'
 Stop-Profile $cfgA
 Assert ((Children $cfgA).Count -eq 0) 'explicit stop revalidates and removes matching duplicates'
 foreach($exitMode in @('exit0','exit7')) {
   $exitMode | Set-Content $cfgA
   Remove-Item ($cfgA+'.starts') -ErrorAction SilentlyContinue
   Resume-Profile $cfgA
   $a=Run-Wrapper $cfgA
   Wait-For { (Test-Path ($cfgA+'.starts')) -and @(Get-Content ($cfgA+'.starts')).Count -ge 2 } $exitMode
   Assert (-not $a.HasExited) ($exitMode+' child exit is recovered')
   Stop-Profile $cfgA
 }
 # Missing configuration and denied process-query injection must fail without guessed ownership.
 $missing=Join-Path $root 'missing.toml'
 $a=Run-Wrapper $missing
 Assert ($a.WaitForExit(10000) -and $a.ExitCode -ne 0 -and (Children $cfgB)[0].Pid -eq $beforeB) 'missing config reports failure without touching other child'
 function Get-CimInstance { throw [UnauthorizedAccessException]::new('test denied') }
 $denied=$false
 try { $null=Get-RelayChildren $fake $cfgB } catch { $denied=$true }
 Remove-Item Function:\Get-CimInstance
 Assert $denied 'access denied fails closed'
 $state=& $helper -Mode Status -Executable $fake -Config $cfgB -RuntimeDirectory $root
 Assert ($state.ChildPids.Count -eq 1 -and $state.SupervisorActive -and $state.Deployment -eq 'unmanaged') 'safe status exposes owned state'
 Stop-Profile $cfgB
 Assert ((Children $cfgB).Count -eq 0) 'final clean stop'
 Write-Output 'ALL MATRIX TESTS PASSED'
} finally {
 $env:HTTP_PROXY=$originalProxy
 foreach($cfg in @($cfgA,$cfgB)) { try { Stop-Profile $cfg } catch { } }
 foreach($wrapper in $wrappers) { if(-not $wrapper.HasExited){$wrapper.Kill();$wrapper.WaitForExit()};$wrapper.Dispose() }
 # Remove only this test's resolved temporary directory.
 if ([IO.Path]::GetFullPath($root).StartsWith([IO.Path]::GetFullPath($env:TEMP)+'\',[StringComparison]::OrdinalIgnoreCase)) { Remove-Item -LiteralPath $root -Recurse -Force }
}
