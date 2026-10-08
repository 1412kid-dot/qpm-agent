[CmdletBinding()]
param([string]$HarnessRoot='E:\Deepseek Harness',[Alias('Profile')][string]$TargetProfile='desktop',[string]$DshHome='',[switch]$DryRun)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Qpm-Common.ps1')
Invoke-QpmOperation 'uninstall' $HarnessRoot $TargetProfile $DshHome '' 'Existing' $DryRun.IsPresent
