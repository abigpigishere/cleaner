@echo off
setlocal
where cl.exe >nul 2>nul
if not errorlevel 1 goto build
set "CLEANER_VCVARS="
if exist "D:\Visual Studio\VC\Auxiliary\Build\vcvars64.bat" set "CLEANER_VCVARS=D:\Visual Studio\VC\Auxiliary\Build\vcvars64.bat"
if not defined CLEANER_VCVARS if exist "%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" for /f "usebackq delims=" %%I in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "CLEANER_VCVARS=%%I\VC\Auxiliary\Build\vcvars64.bat"
if not defined CLEANER_VCVARS (
  echo MSVC C++ tools were not found. Install Visual Studio C++ Build Tools.
  exit /b 1
)
call "%CLEANER_VCVARS%" >nul
if errorlevel 1 exit /b 1
:build
cd /d "%~dp0"
if not exist build mkdir build
cl /nologo /std:c++20 /O2 /EHsc /W4 /utf-8 /DUNICODE /D_UNICODE scanner.cpp /Fo:build\scanner.obj /Fe:build\cleaner-scan.exe
exit /b %errorlevel%
