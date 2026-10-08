[CmdletBinding()]
param([string]$HarnessRoot='E:\Deepseek Harness',[Alias('Profile')][string]$TargetProfile='desktop',[string]$DshHome='',[Parameter(Mandatory=$true)][string]$PythonExecutable,[ValidateSet('Existing','Isolated')][string]$PythonMode='Existing',[switch]$DryRun)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Qpm-Common.ps1')
Invoke-QpmOperation 'install' $HarnessRoot $TargetProfile $DshHome $PythonExecutable $PythonMode $DryRun.IsPresent
