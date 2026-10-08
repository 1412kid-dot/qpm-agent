[CmdletBinding()]
param([string]$PythonExecutable='', [string]$PythonModuleRoot='', [string]$HarnessRoot='E:\Deepseek Harness',[Alias('Profile')][string]$TargetProfile='desktop',[string]$DshHome='')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Qpm-Common.ps1')
$installation=Join-Path (Split-Path $PSScriptRoot -Parent) 'installation.json'
if(Test-Path -LiteralPath $installation){$i=Read-QpmJson $installation;if(!$PSBoundParameters.ContainsKey('HarnessRoot')){$HarnessRoot=$i.harnessRoot};if(!$PSBoundParameters.ContainsKey('TargetProfile') -and $i.profile){$TargetProfile=$i.profile};if(!$DshHome){$DshHome=$i.dshHome}}
Invoke-QpmOperation 'check' $HarnessRoot $TargetProfile $DshHome $PythonExecutable 'Existing' $false $PythonModuleRoot
