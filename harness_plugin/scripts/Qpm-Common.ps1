# Shared Windows PowerShell 5.1/7 installation implementation.
function Read-QpmJson([string]$Path) { return ([IO.File]::ReadAllText($Path,[Text.Encoding]::UTF8).TrimStart([char]0xFEFF) | ConvertFrom-Json) }
function Get-QpmHash([string]$Path) { $algorithm=[Security.Cryptography.SHA256]::Create();$stream=[IO.File]::OpenRead($Path);try{return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$stream.Dispose();$algorithm.Dispose()} }
function Write-QpmAtomic([string]$Path,[string]$Text) {
 $tmp=$Path+'.qpm-'+[Guid]::NewGuid().ToString('N')+'.tmp'
 try { [IO.File]::WriteAllText($tmp,$Text,[Text.UTF8Encoding]::new($false));if(Test-Path -LiteralPath $Path){$previous=$tmp+'.previous';[IO.File]::Replace($tmp,$Path,$previous);Remove-Item -LiteralPath $previous}else{[IO.File]::Move($tmp,$Path)} } finally {if(Test-Path -LiteralPath $tmp){Remove-Item -LiteralPath $tmp}}
}
function Invoke-QpmNative([string]$Executable,[string[]]$Arguments) {
 $savedPreference=$ErrorActionPreference;$ErrorActionPreference='Continue'
 try{$lines=@(& $Executable @Arguments 2>&1 | ForEach-Object {$_.ToString()});$code=$LASTEXITCODE;return [PSCustomObject]@{ExitCode=$code;Text=($lines | ForEach-Object {$_.ToString()}) -join "`n"}}finally{$ErrorActionPreference=$savedPreference}
}
function Assert-QpmNative($Result,[string]$Code) {if($Result.ExitCode -ne 0){$category=if($Result.Text -match 'META_FETCH|registry|ENOTFOUND|EACCES'){ 'dependency_fetch_or_access_failed' }elseif($Result.Text -match 'lock|holder|still running'){'profile_busy'}else{'command_failed'};throw "$Code (exit=$($Result.ExitCode); category=$category)"}}
function Get-QpmPaths([string]$HarnessRoot,[string]$TargetProfile,[string]$DshHome,[string]$ReleaseRoot) {
 if($TargetProfile -notmatch '^[A-Za-z0-9_-]+$'){throw 'invalid_profile_name'}
 $base=if($DshHome){[IO.Path]::GetFullPath($DshHome)}elseif($env:DSH_HOME){[IO.Path]::GetFullPath($env:DSH_HOME)}else{Join-Path $env:USERPROFILE '.dsh'}
 $paths=[PSCustomObject]@{HarnessRoot=[IO.Path]::GetFullPath($HarnessRoot);ReleaseRoot=[IO.Path]::GetFullPath($ReleaseRoot);DshHome=$base;Profile=$TargetProfile;ProfileDir=(Join-Path $base "profiles/$TargetProfile");Cli=(Join-Path $HarnessRoot 'resources/runtime/cli/bin/dsh.cmd');Node=(Join-Path $HarnessRoot 'DeepSeek Harness.exe')}
 foreach($p in @($paths.Cli,$paths.Node,(Join-Path $paths.ProfileDir 'package.json'))){if(!(Test-Path -LiteralPath $p)){throw "required_path_missing: $p"}}
 return $paths
}
function Invoke-QpmHelper($Paths,[string]$Action,$Extra=@{}) {
 $request=@{action=$Action;harnessRoot=$Paths.HarnessRoot;releaseRoot=$Paths.ReleaseRoot;profileDir=$Paths.ProfileDir;dshHome=$Paths.DshHome}
 foreach($key in $Extra.Keys){$request[$key]=$Extra[$key]}
 $dir=Join-Path $Paths.ReleaseRoot '.install-work';New-Item -ItemType Directory -Force -Path $dir | Out-Null
 $file=Join-Path $dir ([Guid]::NewGuid().ToString('N')+'.json')
 try{Write-QpmAtomic $file ($request | ConvertTo-Json -Depth 50);$r=Invoke-QpmNative $Paths.Node @('--expose-internals',(Join-Path $PSScriptRoot 'profile-helper.cjs'),$file);if($r.ExitCode -ne 0){throw "helper_$Action (exit=$($r.ExitCode)): $($r.Text)"};return ($r.Text | ConvertFrom-Json).value}finally{if(Test-Path -LiteralPath $file){Remove-Item -LiteralPath $file}}
}
function Test-QpmRelease([string]$Root) {
 $manifest=Read-QpmJson (Join-Path $Root 'dist/SHA256SUMS.json')
 foreach($entry in $manifest.PSObject.Properties){$file=[IO.Path]::GetFullPath((Join-Path $Root $entry.Name));if(!$file.StartsWith([IO.Path]::GetFullPath($Root)+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'invalid_checksum_path'};if(!(Test-Path -LiteralPath $file) -or (Get-QpmHash $file) -ne $entry.Value){throw "release_checksum_mismatch: $($entry.Name)"}}
 foreach($file in Get-ChildItem -LiteralPath (Join-Path $Root 'scripts') -Filter '*.ps1'){$b=[IO.File]::ReadAllBytes($file.FullName);if($b.Length -lt 3 -or $b[0] -ne 239 -or $b[1] -ne 187 -or $b[2] -ne 191){throw "missing_script_BOM: $($file.Name)"}}
}
function Invoke-QpmOperation([string]$Operation,[string]$HarnessRoot,[string]$TargetProfile,[string]$DshHome,[string]$PythonExecutable,[string]$PythonMode,[bool]$DryRun,[string]$PythonModuleRoot='') {
 $names=@('ELECTRON_RUN_AS_NODE','DSH_HOME','PYTHONPATH','PYTHONUTF8','PYTHONDONTWRITEBYTECODE');$saved=@{};foreach($n in $names){$saved[$n]=[Environment]::GetEnvironmentVariable($n,'Process')}
 $encoding=[Console]::OutputEncoding;$oldOutputEncoding=$OutputEncoding
 $backup=$null;$stage='preflight';$changed=$false;$patchApplied=$false;$prepared=$null;$patchHash=$null;$paths=$null;$snap=$null
 try{
  [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);$OutputEncoding=[Text.UTF8Encoding]::new($false)
  $env:PYTHONUTF8='1';$env:PYTHONDONTWRITEBYTECODE='1';$env:ELECTRON_RUN_AS_NODE='1'
  $root=Split-Path $PSScriptRoot -Parent
  if($Operation -eq 'check'){
   if(Test-Path -LiteralPath (Join-Path $root 'installation.json')){$installed=Read-QpmJson (Join-Path $root 'installation.json');if(!$PythonExecutable){$PythonExecutable=$installed.pythonExecutable};if(!$PythonModuleRoot){$PythonModuleRoot=$installed.pythonModuleRoot}}
   if(!$PythonExecutable){throw '请指定-PythonExecutable或先安装'}
   if(!$PythonModuleRoot -and (Test-Path -LiteralPath (Join-Path $root 'runtime/python-core'))){$PythonModuleRoot=Join-Path $root 'runtime/python-core'}
   if($PythonModuleRoot){$env:PYTHONPATH=$PythonModuleRoot}
   $r=Invoke-QpmNative $PythonExecutable @('-c','import json; from qpm_simulation.environment import doctor; print(json.dumps(doctor(), ensure_ascii=True))')
   Assert-QpmNative $r 'python_environment_check_failed';Write-Output $r.Text
   if((Test-Path -LiteralPath (Join-Path $root 'installation.json')) -and (Read-QpmJson (Join-Path $root 'installation.json')).installed -ne $false){$paths=Get-QpmPaths $HarnessRoot $TargetProfile $DshHome $root;$env:DSH_HOME=$paths.DshHome;Invoke-QpmHelper $paths 'validate' | ConvertTo-Json}
   Write-Host '只读检查通过；未启动COMSOL。';return
  }
  Test-QpmRelease $root
  $paths=Get-QpmPaths $HarnessRoot $TargetProfile $DshHome $root;$env:DSH_HOME=$paths.DshHome
  $version=Invoke-QpmNative $paths.Cli @('--version');Assert-QpmNative $version 'harness_version_check_failed';if($version.Text.Trim() -ne '0.2.0-rc.2'){throw "unsupported_harness_version: $($version.Text.Trim())"}
  $snap=Invoke-QpmHelper $paths 'snapshot'
  $release=Read-QpmJson (Join-Path $root 'release.json')
  $wheel=Join-Path $root ("dist/qpm_simulation-"+$release.python_version+"-py3-none-any.whl");$tgz=Join-Path $root ("dist/dsh-qpm-agent-"+$release.plugin_version+".tgz")
  if($Operation -eq 'install'){
   $PythonExecutable=(Get-Command -Name $PythonExecutable -CommandType Application -ErrorAction Stop).Source
   $versionCode='import sys; assert sys.version_info >= (3,12), ''requires Python>=3.12'''
   $r=Invoke-QpmNative $PythonExecutable @('-c',$versionCode);Assert-QpmNative $r 'python_version_check_failed'
   if($DryRun){Write-Host "前置文件、版本和配置语法检查通过；目标=$TargetProfile；尚未安装。";return}
   $stage='python_prepare';$runtimeRoot=Join-Path $root 'runtime';New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
   if($PythonMode -eq 'Isolated'){
    $venv=Join-Path $runtimeRoot ('venv-'+(Get-QpmHash $wheel).Substring(0,12));$r=Invoke-QpmNative $PythonExecutable @('-m','venv',$venv);Assert-QpmNative $r 'venv_creation_failed';$PythonExecutable=Join-Path $venv 'Scripts/python.exe'
    $r=Invoke-QpmNative $PythonExecutable @('-m','pip','install',($wheel+'[comsol]'));Assert-QpmNative $r 'python_dependency_install_failed';$PythonModuleRoot=''
   }else{
    $r=Invoke-QpmNative $PythonExecutable @('-c','import numpy,scipy,pandas,matplotlib');Assert-QpmNative $r 'python_scientific_dependencies_missing'
    $PythonModuleRoot=Join-Path $runtimeRoot ('python-core-'+(Get-QpmHash $wheel).Substring(0,12));New-Item -ItemType Directory -Force -Path $PythonModuleRoot | Out-Null
    $r=Invoke-QpmNative $PythonExecutable @('-c','import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])',$wheel,$PythonModuleRoot);Assert-QpmNative $r 'wheel_extract_failed'
   }
   if($PythonModuleRoot){$env:PYTHONPATH=$PythonModuleRoot}else{Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue}
   $r=Invoke-QpmNative $PythonExecutable @('-c','from qpm_simulation.harness_worker import PROTOCOL; assert PROTOCOL == ''qpm/1''');Assert-QpmNative $r 'python_worker_import_failed'
   $prepared=Invoke-QpmHelper $paths 'prepare' @{pythonExecutable=[IO.Path]::GetFullPath($PythonExecutable);pythonModuleRoot=$PythonModuleRoot}
  }elseif($DryRun){Write-Host "卸载前置检查通过；目标=$TargetProfile；尚未卸载。";return}
  $backup=Join-Path $root ('backups/'+$Operation+'-'+$TargetProfile+'-'+(Get-Date -Format 'yyyyMMdd-HHmmss-fff'));New-Item -ItemType Directory -Force -Path $backup | Out-Null
  foreach($name in @('package.json','cordis.patch.yml','pnpm-lock.yaml','pnpm-workspace.yaml')){$file=Join-Path $paths.ProfileDir $name;if(Test-Path -LiteralPath $file){Copy-Item -LiteralPath $file -Destination (Join-Path $backup $name)}}
  Write-QpmAtomic (Join-Path $backup 'qpm-state.json') ($snap | ConvertTo-Json -Depth 50)
  if($Operation -eq 'install'){
   # Content-addressed archive prevents pnpm reusing an old tarball at the same version/path.
   $stage='plugin_archive';$archiveHash=Get-QpmHash $tgz
   $archiveDir=Join-Path $root '.install-work/packages';New-Item -ItemType Directory -Force -Path $archiveDir | Out-Null
   $immutableArchive=Join-Path $archiveDir ('dsh-qpm-agent-'+$release.plugin_version+'-'+$archiveHash+'.tgz')
   if(!(Test-Path -LiteralPath $immutableArchive)){Copy-Item -LiteralPath $tgz -Destination $immutableArchive}
   if((Get-QpmHash $immutableArchive) -ne $archiveHash){throw 'immutable_plugin_archive_corrupt'}
   $tgz=$immutableArchive
   $stage='plugin_add';$changed=$true;$r=Invoke-QpmNative $paths.Cli @('plugin','--profile',$TargetProfile,'add',$tgz,'--config.auto-install-peers=false');Assert-QpmNative $r 'plugin_add_failed'
   $stage='config_apply';$patchHash=(Invoke-QpmHelper $paths 'apply' @{candidateBase64=$prepared.candidateBase64;expectedHash=$prepared.patchHash}).patchHash;$patchApplied=$true
   $stage='static_validation';$validation=Invoke-QpmHelper $paths 'validate' @{expectedPython=$prepared.config.pythonExecutable;expectedModuleRoot=$prepared.config.pythonModuleRoot}
   $record=@{installed=$true;pythonExecutable=$prepared.config.pythonExecutable;pythonModuleRoot=$prepared.config.pythonModuleRoot;harnessRoot=$paths.HarnessRoot;dshHome=$paths.DshHome;profile=$TargetProfile;validation=$validation}
   $stage='installation_record';Write-QpmAtomic (Join-Path $root 'installation.json') ($record | ConvertTo-Json -Depth 20)
  }else{
   $stage='config_remove';$patchHash=(Invoke-QpmHelper $paths 'apply' @{remove=$true;expectedHash=$snap.patchHash}).patchHash;$patchApplied=$true
   $stage='plugin_remove';$changed=$true
   if($snap.dependency){$r=Invoke-QpmNative $paths.Cli @('plugin','--profile',$TargetProfile,'remove','dsh-qpm-agent');Assert-QpmNative $r 'plugin_remove_failed'}
   Invoke-QpmHelper $paths 'restore-bundle' @{bundleIndex=-1} | Out-Null
   $stage='uninstall_validation';Invoke-QpmHelper $paths 'removed' | Out-Null
   $metadata=Join-Path $root 'installation.json';if(Test-Path -LiteralPath $metadata){$record=Read-QpmJson $metadata;$record | Add-Member -NotePropertyName installed -NotePropertyValue $false -Force;Write-QpmAtomic $metadata ($record | ConvertTo-Json -Depth 20)}
  }
  Write-QpmAtomic (Join-Path $backup 'operation.json') (@{operation=$Operation;stage='completed';success=$true;profile=$TargetProfile;harnessRoot=$paths.HarnessRoot;dshHome=$paths.DshHome;exitCode=0} | ConvertTo-Json)
  Write-Host "$Operation 完成：包登记与静态配置检查通过；桌面实际加载待重启验证。备份=$backup"
 }catch{
  $failure=$_.Exception.Message;$recovery='not_required';$restoreError=$null
  if($changed -or $patchApplied){
   try{
    if($changed){
     if($snap.dependency){$spec=$snap.dependency;if($spec.StartsWith('file:') -and ![IO.Path]::IsPathRooted($spec.Substring(5))){$spec='file:'+([IO.Path]::GetFullPath((Join-Path $paths.ProfileDir $spec.Substring(5))))};$r=Invoke-QpmNative $paths.Cli @('plugin','--profile',$TargetProfile,'add',$spec,'--config.auto-install-peers=false')}else{$now=Read-QpmJson (Join-Path $paths.ProfileDir 'package.json');if($now.dependencies.'dsh-qpm-agent'){$r=Invoke-QpmNative $paths.Cli @('plugin','--profile',$TargetProfile,'remove','dsh-qpm-agent')}else{$r=[PSCustomObject]@{ExitCode=0}}};Assert-QpmNative $r 'registration_restore_failed'
     Invoke-QpmHelper $paths 'restore-bundle' @{bundleIndex=$snap.bundleIndex} | Out-Null
    }
    if($patchApplied){Invoke-QpmHelper $paths 'restore-patch' @{patchBase64=$snap.patchBase64;expectedHash=$patchHash} | Out-Null}
    $recovery='restored'
   }catch{$recovery='manual_recovery_required';$restoreError=$_.Exception.Message}
  }
  if($backup){Write-QpmAtomic (Join-Path $backup 'operation.json') (@{operation=$Operation;stage=$stage;success=$false;error=$failure;recovery=$recovery;restoreError=$restoreError;profile=$TargetProfile;harnessRoot=$paths.HarnessRoot;dshHome=$paths.DshHome} | ConvertTo-Json -Depth 10)}
  $hint=if($recovery -eq 'manual_recovery_required'){$repairEntry=(Join-Path $root 'Repair-Qpm.cmd').Replace("'","''");$repairBackup=$backup.Replace("'","''");"关闭Harness后执行：& '$repairEntry' -BackupDirectory '$repairBackup'。不要整份覆盖模型配置。"}elseif($recovery -eq 'restored'){'本次QPM变更已恢复；处理错误原因后可重试。'}else{'尚未修改profile；处理前置检查错误后可重试。'}
  throw "QPM $Operation 失败；阶段=$stage；原因=$failure；恢复=$recovery；备份=$backup。$hint"
 }finally{foreach($n in $names){[Environment]::SetEnvironmentVariable($n,$saved[$n],'Process')};[Console]::OutputEncoding=$encoding;$OutputEncoding=$oldOutputEncoding}
}
