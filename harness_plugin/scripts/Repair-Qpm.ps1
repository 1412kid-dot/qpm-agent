[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$BackupDirectory)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Qpm-Common.ps1')
$operation=Read-QpmJson (Join-Path $BackupDirectory 'operation.json');$snapshot=Read-QpmJson (Join-Path $BackupDirectory 'qpm-state.json')
$paths=Get-QpmPaths $operation.harnessRoot $operation.profile $operation.dshHome (Split-Path $PSScriptRoot -Parent)
$names=@('DSH_HOME','ELECTRON_RUN_AS_NODE');$saved=@{};foreach($n in $names){$saved[$n]=[Environment]::GetEnvironmentVariable($n,'Process')}
try{
 $env:DSH_HOME=$paths.DshHome;$env:ELECTRON_RUN_AS_NODE='1'
 if($snapshot.dependency){$spec=$snapshot.dependency;if($spec.StartsWith('file:') -and ![IO.Path]::IsPathRooted($spec.Substring(5))){$spec='file:'+([IO.Path]::GetFullPath((Join-Path $paths.ProfileDir $spec.Substring(5))))};$r=Invoke-QpmNative $paths.Cli @('plugin','--profile',$paths.Profile,'add',$spec,'--config.auto-install-peers=false');Assert-QpmNative $r 'repair_plugin_registration_failed'}else{$now=Read-QpmJson (Join-Path $paths.ProfileDir 'package.json');if($now.dependencies.'dsh-qpm-agent'){$r=Invoke-QpmNative $paths.Cli @('plugin','--profile',$paths.Profile,'remove','dsh-qpm-agent');Assert-QpmNative $r 'repair_plugin_registration_failed'}}
 Invoke-QpmHelper $paths 'restore-bundle' @{bundleIndex=$snapshot.bundleIndex} | Out-Null
 Invoke-QpmHelper $paths 'restore-owned' @{qpmRows=@($snapshot.qpmRows)} | Out-Null
 if($snapshot.dependency -and $snapshot.bundleIndex -ge 0){Invoke-QpmHelper $paths 'validate' | ConvertTo-Json}
 Write-Host 'QPM登记及用户覆盖已恢复；其他插件/模型配置保留。请重启Harness验证。'
}finally{foreach($n in $names){[Environment]::SetEnvironmentVariable($n,$saved[$n],'Process')}}
