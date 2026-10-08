"""Stable Windows entrypoints: ASCII/CRLF, process-only execution policy."""
import pathlib

root=pathlib.Path(__file__).resolve().parents[1]
for operation in ('Install','Check','Uninstall','Repair'):
    lines=[
      '@echo off',
      'setlocal DisableDelayedExpansion',
      'rem Rebuild the native module path; do not inherit PowerShell 7 modules.',
      'set "PSModulePath="',
      r'set "qpmPowerShell=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"',
      r'if exist "%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe" set "qpmPowerShell=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"',
      'if not exist "%qpmPowerShell%" (',
      '  echo QPM: Windows PowerShell 5.1 was not found. 1>&2',
      '  exit /b 2',
      ')',
      rf'"%qpmPowerShell%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\{operation}-Qpm.ps1" %*',
      'set "qpmExit=%errorlevel%"',
      'if not "%qpmExit%"=="0" (',
      '  echo QPM operation failed. Review the error above. 1>&2',
      '  echo No persistent execution policy was changed. Group Policy still takes precedence. 1>&2',
      ')',
      'if "%~1"=="" pause',
      'exit /b %qpmExit%',
    ]
    (root/f'{operation}-Qpm.cmd').write_bytes(('\r\n'.join(lines)+'\r\n').encode('ascii'))
print('Generated four ASCII/CRLF Windows launchers; no persistent policy changes.')
