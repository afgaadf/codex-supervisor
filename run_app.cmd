@echo off
rem 打开监督者界面（PySide6 版）。旧 Tk 版已退休：backup\supervisor_app.py.retired-20261006
setlocal
cd /d "%~dp0"
set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not exist "%PYW%" for /f "delims=" %%i in ('where pythonw 2^>nul') do set "PYW=%%i"
if not exist "%PYW%" (
  echo [监督者] 找不到 pythonw.exe
  exit /b 1
)
start "" "%PYW%" "%~dp0supervisor_gui.py"