@echo off
setlocal DisableDelayedExpansion
rem Rebuild the native module path; do not inherit PowerShell 7 modules.
set "PSModulePath="
set "qpmPowerShell=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if exist "%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe" set "qpmPowerShell=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%qpmPowerShell%" (
  echo QPM: Windows PowerShell 5.1 was not found. 1>&2
  exit /b 2
)
"%qpmPowerShell%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Install-Qpm.ps1" %*
set "qpmExit=%errorlevel%"
if not "%qpmExit%"=="0" (
  echo QPM operation failed. Review the error above. 1>&2
  echo No persistent execution policy was changed. Group Policy still takes precedence. 1>&2
)
if "%~1"=="" pause
exit /b %qpmExit%
