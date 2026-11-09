@echo off
setlocal
cd /d "%~dp0"
if not defined BLENDER_EXE if exist "%~dp0blender\blender.exe" set "BLENDER_EXE=%~dp0blender\blender.exe"
if not defined BLENDER_EXE if exist "%~dp0..\HelmetFit-Windows-Portable-v1.0\blender\blender.exe" set "BLENDER_EXE=%~dp0..\HelmetFit-Windows-Portable-v1.0\blender\blender.exe"
if not defined BLENDER_EXE set "BLENDER_EXE=%ProgramFiles%\Blender Foundation\Blender 4.5\blender.exe"
for %%B in ("%BLENDER_EXE%") do set "STUDIO_PY=%%~dpB4.5\python\bin\python.exe"
if not exist "%STUDIO_PY%" (
  echo Install Blender 4.5 or set BLENDER_EXE to your Blender 4.5 executable.
  pause
  exit /b 1
)
"%STUDIO_PY%" -m studio.app %*
if errorlevel 1 pause
