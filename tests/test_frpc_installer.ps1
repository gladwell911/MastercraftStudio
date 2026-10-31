# Real temporary runtime files/children; Task Scheduler calls are isolated mocks.
$ErrorActionPreference='Stop'
$sourceRoot=Split-Path $PSScriptRoot -Parent
$installer=Join-Path $sourceRoot 'deploy\mc-relay\install-frpc-supervisor.ps1'
$helper=Join-Path $sourceRoot 'deploy\mc-relay\frpc-supervisor.ps1'
. $helper -Mode Library
$testRoot=Join-Path $env:TEMP ('relay installer '+[guid]::NewGuid().ToString('N'))
$originalLocal=$env:LOCALAPPDATA
$env:LOCALAPPDATA=$testRoot
$runtime=Join-Path $testRoot 'MCRelay'
$other=Join-Path $testRoot 'controlTower\relay'
New-Item -ItemType Directory -Path $runtime,$other -Force | Out-Null
$fake=Join-Path $runtime 'frpc.exe'
Add-Type -OutputAssembly $fake -OutputType ConsoleApplication -TypeDefinition @"
using System; using System.IO; using System.Threading;
public static class InstallFake {
 public static void Main(string[] args) {
  if(args[0]=="verify") { File.AppendAllText(args[2]+".verify","v"); return; }
  while(true) Thread.Sleep(100);
 }
}
"@
$cfg=Join-Path $runtime 'frpc.toml';$otherCfg=Join-Path $other 'frpc.toml'
'unchanged MC' | Set-Content $cfg
'unchanged other' | Set-Content $otherCfg
$global:FakeTasks=@{}
$global:TaskChildren=@{}
$global:RegistrationFailure=$false
$global:SuppressStartup=$false
$global:RejectMarkerRemoval=$false
$global:StartCalls=0
$global:NewRegistration=$false
$global:LaunchConcurrent=$false
$global:ConcurrentBlocked=$false
$global:ConcurrentWorker=$null
function global:Get-ScheduledTask {
 param($TaskName,$TaskPath)
 if($global:LaunchConcurrent) {
  $global:LaunchConcurrent=$false
  $count=(Get-Item ($global:ConcurrencyConfig+'.verify')).Length
  $global:ConcurrentWorker=Start-Process "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "'+$global:ConcurrencyScript+'"') -WindowStyle Hidden -PassThru
  Start-Sleep -Seconds 1
  $global:ConcurrentBlocked=(-not $global:ConcurrentWorker.HasExited -and (Get-Item ($global:ConcurrencyConfig+'.verify')).Length-eq $count)
 }
 $global:FakeTasks[$TaskName]
}
function global:Export-ScheduledTask { param($TaskName,$TaskPath) $global:FakeTasks[$TaskName] | ConvertTo-Json -Depth 9 }
function global:Disable-ScheduledTask { param($TaskName,$TaskPath) $global:FakeTasks[$TaskName].Settings.Enabled=$false; $global:FakeTasks[$TaskName] }
function global:Enable-ScheduledTask { param($TaskName,$TaskPath) $global:FakeTasks[$TaskName].Settings.Enabled=$true; $global:FakeTasks[$TaskName] }
function global:Stop-ScheduledTask {
 param($TaskName,$TaskPath)
 if($global:TaskChildren.ContainsKey($TaskName)) {
  $p=$global:TaskChildren[$TaskName]
  if(-not $p.HasExited){$p.Kill();$p.WaitForExit()};$p.Dispose();$global:TaskChildren.Remove($TaskName)
 }
 $global:FakeTasks[$TaskName].State='Ready'
}
function global:Start-ScheduledTask {
 param($TaskName,$TaskPath)
 $global:StartCalls++
 $global:FakeTasks[$TaskName].State='Running'
 if($global:NewRegistration -and -not $global:SuppressStartup) {
  $action=$global:FakeTasks[$TaskName].Actions[0]
  $global:TaskChildren[$TaskName]=Start-Process -FilePath $action.Execute -ArgumentList $action.Arguments -WindowStyle Hidden -PassThru
 }
}
function global:New-ScheduledTaskTrigger { param([switch]$Once,$At,$RepetitionInterval) [pscustomobject]@{CimClass=[pscustomobject]@{CimClassName='MSFT_TaskTimeTrigger'}} }
function global:Register-ScheduledTask {
 [CmdletBinding()]param($TaskName,$TaskPath,$Action,$Trigger,$Settings,$Principal,$Xml,[switch]$Force)
 if($Xml) { $global:FakeTasks[$TaskName]=$Xml|ConvertFrom-Json; $global:NewRegistration=$false;return }
 if($global:RegistrationFailure){$global:RegistrationFailure=$false;throw 'isolated registration failure'}
 $global:FakeTasks[$TaskName]=[pscustomobject]@{Actions=$Action;Triggers=$Trigger;Settings=$Settings;Principal=$Principal;State='Ready'}
 $global:NewRegistration=$true
}
function global:Remove-Item {
 [CmdletBinding()]param($LiteralPath,$Path,[switch]$Force,[switch]$Recurse)
 if($global:RejectMarkerRemoval -and $LiteralPath -match 'maintenance-'){throw 'isolated deletion failure'}
 if($LiteralPath){Microsoft.PowerShell.Management\Remove-Item -LiteralPath $LiteralPath -Force:$Force -Recurse:$Recurse -ErrorAction Stop}
 else{Microsoft.PowerShell.Management\Remove-Item -Path $Path -Force:$Force -Recurse:$Recurse -ErrorAction Stop}
}
function Assert($condition,$message){if(-not $condition){throw ('FAILED '+$message)};'PASS '+$message}
function Same-Bytes($path,$bytes){(Test-Path -LiteralPath $path) -and [Convert]::ToBase64String([IO.File]::ReadAllBytes($path)) -eq [Convert]::ToBase64String([byte[]]$bytes)}
function Reset-Fixture([bool]$maintenance=$false) {
 $global:NewRegistration=$false;$global:SuppressStartup=$false;$global:RegistrationFailure=$false
 foreach($n in @('start-frpc.ps1','frpc-supervisor.ps1','supervisor-install.json')){
  $path=Join-Path $runtime $n
  if(Test-Path $path){Microsoft.PowerShell.Management\Remove-Item -LiteralPath $path -Force}
 }
 [IO.File]::WriteAllBytes((Join-Path $runtime 'start-frpc.ps1'),[byte[]](35,32,111,108,100,13,10))
 [IO.File]::WriteAllBytes((Join-Path $runtime 'frpc-supervisor.ps1'),[byte[]](35,32,111,108,100,32,104,101,108,112,101,114))
 $global:FakeTasks['MCRelay-frpc']=[pscustomobject]@{
  Actions=@([pscustomobject]@{Execute="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe";Arguments=('-NoProfile -ExecutionPolicy Bypass -File "'+(Join-Path $runtime 'start-frpc.ps1')+'"')})
  Triggers=@([pscustomobject]@{CimClass=[pscustomobject]@{CimClassName='MSFT_TaskLogonTrigger'}})
  Settings=[pscustomobject]@{Enabled=(-not $maintenance);MultipleInstances='IgnoreNew';StartWhenAvailable=$false;ExecutionTimeLimit='PT0S';DisallowStartIfOnBatteries=$false;StopIfGoingOnBatteries=$false}
  Principal=[pscustomobject]@{UserId='isolated'};State=$(if($maintenance){'Disabled'}else{'Running'})
 }
 $marker=Join-Path $runtime ('maintenance-'+(Get-RelayIdentity $cfg))
 if(Test-Path $marker){Microsoft.PowerShell.Management\Remove-Item -LiteralPath $marker -Force}
 if($maintenance){[IO.File]::WriteAllBytes($marker,[byte[]](0,255,42,13,10))}
}
$marker=Join-Path $runtime ('maintenance-'+(Get-RelayIdentity $cfg))
$originalConfig=[IO.File]::ReadAllBytes($cfg);$otherBytes=[IO.File]::ReadAllBytes($otherCfg)
try {
 foreach($maintenanceState in @($false,$true)) {
  Reset-Fixture $maintenanceState
  $wrapperBefore=[IO.File]::ReadAllBytes((Join-Path $runtime 'start-frpc.ps1'))
  $helperBefore=[IO.File]::ReadAllBytes((Join-Path $runtime 'frpc-supervisor.ps1'))
  $global:RegistrationFailure=$true
  $failed=$false;try{& $installer -Profile MCRelay -AdoptExisting | Out-Null}catch{$failed=$true}
  Assert ($failed -and (Same-Bytes (Join-Path $runtime 'start-frpc.ps1') $wrapperBefore) -and (Same-Bytes (Join-Path $runtime 'frpc-supervisor.ps1') $helperBefore) -and -not (Test-Path (Join-Path $runtime 'supervisor-install.json'))) ('registration failure restores original script bytes, maintenance='+$maintenanceState)
  Assert (($global:FakeTasks['MCRelay-frpc'].Settings.Enabled -eq (-not $maintenanceState)) -and ($global:FakeTasks['MCRelay-frpc'].State -eq $(if($maintenanceState){'Disabled'}else{'Running'}))) ('rollback restores enabled/running task state, maintenance='+$maintenanceState)
  Assert ((Test-Path $marker) -eq $maintenanceState) 'rollback restores marker existence'
  if($maintenanceState){Assert (Same-Bytes $marker ([byte[]](0,255,42,13,10))) 'rollback restores original marker bytes'}
  Assert ((Same-Bytes $cfg $originalConfig) -and (Same-Bytes $otherCfg $otherBytes)) 'failed install preserves both configuration bytes'
 }
 Reset-Fixture
 $before=[IO.File]::ReadAllBytes((Join-Path $runtime 'start-frpc.ps1'))
 $global:SuppressStartup=$true
 $failed=$false;try{& $installer -Profile MCRelay -AdoptExisting | Out-Null}catch{$failed=$true}
 Assert ($failed -and (Same-Bytes (Join-Path $runtime 'start-frpc.ps1') $before) -and $global:FakeTasks['MCRelay-frpc'].State -eq 'Running' -and -not (Test-Path $marker)) 'queued startup without supervisor/child fails boundedly and rolls back'
 Reset-Fixture $true
 $global:RejectMarkerRemoval=$true;$starts=$global:StartCalls
 $failed=$false;try{& $helper -Mode Resume -Executable $fake -Config $cfg -TaskName 'MCRelay-frpc' -RuntimeDirectory $runtime}catch{$failed=$true}
 $global:RejectMarkerRemoval=$false
 Assert ($failed -and $global:StartCalls -eq $starts -and -not $global:FakeTasks['MCRelay-frpc'].Settings.Enabled -and (Test-Path $marker)) 'Resume marker-removal failure never enables or starts task'
 Reset-Fixture
 # A real second installer must not even verify files while the first owns preflight/rollback.
 $global:ConcurrencyConfig=$cfg
 $global:ConcurrencyScript=Join-Path $testRoot 'concurrent-installer.ps1'
 $workerText="function Get-ScheduledTask { throw 'isolated boundary after preflight' }`r`ntry { & '"+$installer.Replace("'","''")+"' -Profile MCRelay -AdoptExisting | Out-Null } catch { exit 7 }"
 [IO.File]::WriteAllText($global:ConcurrencyScript,$workerText)
 $global:LaunchConcurrent=$true;$global:RegistrationFailure=$true
 try{& $installer -Profile MCRelay -AdoptExisting | Out-Null}catch{}
 Assert $global:ConcurrentBlocked 'concurrent installer blocks before preflight during first installer transition'
 Assert ($global:ConcurrentWorker.WaitForExit(10000) -and $global:ConcurrentWorker.ExitCode-eq 7) 'waiting installer resumes after rollback releases operation lock'
 $global:ConcurrentWorker.Dispose();$global:ConcurrentWorker=$null
 Reset-Fixture
 & $installer -Profile MCRelay -AdoptExisting | Out-Null
 Assert ((Get-RelayDrift $runtime $fake $cfg 'MCRelay-frpc') -eq 'clean') 'successful install waits for child and writes valid clean manifest'
 & $helper -Mode Stop -Executable $fake -Config $cfg -TaskName 'MCRelay-frpc' -RuntimeDirectory $runtime
 $manifestFile=Join-Path $runtime 'supervisor-install.json'
 $good=[IO.File]::ReadAllBytes($manifestFile)
 $goodWrapper=[IO.File]::ReadAllBytes((Join-Path $runtime 'start-frpc.ps1'))
 $goodHelper=[IO.File]::ReadAllBytes((Join-Path $runtime 'frpc-supervisor.ps1'))
 $global:RegistrationFailure=$true
 $failed=$false;try{& $installer -Profile MCRelay | Out-Null}catch{$failed=$true}
 Assert ($failed -and (Same-Bytes $manifestFile $good) -and (Same-Bytes (Join-Path $runtime 'start-frpc.ps1') $goodWrapper) -and (Same-Bytes (Join-Path $runtime 'frpc-supervisor.ps1') $goodHelper)) 'managed installation rollback restores existing manifest and script bytes'
 foreach($variant in @('empty','malformed','profile','config','exe','task','modified','deleted','deleted-manifest')) {
  [IO.File]::WriteAllBytes($manifestFile,$good)
  [IO.File]::WriteAllBytes((Join-Path $runtime 'start-frpc.ps1'),$goodWrapper)
  $manifest=Get-Content -Raw $manifestFile|ConvertFrom-Json
  switch($variant){
   'empty' {$manifest.Hashes=[pscustomobject]@{}}
   'profile' {$manifest.Profile='ControlTower'}
   'config' {$manifest.Config=$otherCfg}
   'exe' {$manifest.Executable=(Join-Path $other 'frpc.exe')}
   'task' {$manifest.Task='ControlTower-frpc'}
   'modified' {Add-Content (Join-Path $runtime 'start-frpc.ps1') '# external'}
   'deleted' {Microsoft.PowerShell.Management\Remove-Item -LiteralPath (Join-Path $runtime 'start-frpc.ps1') -Force}
  }
  if($variant-eq 'malformed'){[IO.File]::WriteAllText($manifestFile,'{bad')}else{$manifest|ConvertTo-Json -Depth 5|Set-Content $manifestFile -Encoding UTF8}
  if($variant-eq 'deleted-manifest'){Microsoft.PowerShell.Management\Remove-Item -LiteralPath $manifestFile -Force}
  $wrapperExisted=Test-Path (Join-Path $runtime 'start-frpc.ps1')
  $wrapperSnapshot=if($wrapperExisted){[IO.File]::ReadAllBytes((Join-Path $runtime 'start-frpc.ps1'))}else{$null}
  $manifestExisted=Test-Path $manifestFile
  $manifestBefore=if($manifestExisted){[IO.File]::ReadAllBytes($manifestFile)}else{$null};$starts=$global:StartCalls
  $failed=$false;try{& $installer -Profile MCRelay | Out-Null}catch{$failed=$true}
  Assert ($failed -and ((-not $manifestExisted -and -not (Test-Path $manifestFile)) -or ($manifestExisted -and (Same-Bytes $manifestFile $manifestBefore))) -and (Same-Bytes (Join-Path $runtime 'frpc-supervisor.ps1') $goodHelper) -and ((-not $wrapperExisted -and -not (Test-Path (Join-Path $runtime 'start-frpc.ps1'))) -or ($wrapperExisted -and (Same-Bytes (Join-Path $runtime 'start-frpc.ps1') $wrapperSnapshot))) -and $global:StartCalls-eq $starts -and @(Get-RelayChildren $fake $cfg).Count-eq 0) ('drift '+$variant+' refused without overwrite or child startup')
 }
 'ALL INSTALLER TESTS PASSED'
} finally {
 $global:RejectMarkerRemoval=$false
 if($global:ConcurrentWorker){if(-not $global:ConcurrentWorker.HasExited){$global:ConcurrentWorker.Kill();$global:ConcurrentWorker.WaitForExit()};$global:ConcurrentWorker.Dispose()}
 foreach($task in @($global:TaskChildren.Keys)){try{Stop-ScheduledTask -TaskName $task}catch{}}
 try{& $helper -Mode Stop -Executable $fake -Config $cfg -RuntimeDirectory $runtime}catch{}
 $env:LOCALAPPDATA=$originalLocal
 foreach($name in @('Get-ScheduledTask','Export-ScheduledTask','Disable-ScheduledTask','Enable-ScheduledTask','Stop-ScheduledTask','Start-ScheduledTask','New-ScheduledTaskTrigger','Register-ScheduledTask','Remove-Item')){Microsoft.PowerShell.Management\Remove-Item ('Function:\'+$name) -ErrorAction SilentlyContinue}
 if([IO.Path]::GetFullPath($testRoot).StartsWith([IO.Path]::GetFullPath($env:TEMP)+'\',[StringComparison]::OrdinalIgnoreCase)){Microsoft.PowerShell.Management\Remove-Item -LiteralPath $testRoot -Recurse -Force}
}
