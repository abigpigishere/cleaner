@echo off
setlocal
call "D:\Visual Studio\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
cd /d "%~dp0"
if not exist build mkdir build
cl /nologo /std:c++20 /O2 /EHsc /W4 /utf-8 /DUNICODE /D_UNICODE scanner.cpp /Fo:build\scanner.obj /Fe:build\cleaner-scan.exe
exit /b %errorlevel%
