@echo off
rem 启动监督者看门狗（无窗口常驻）。开机自启请指向本文件，而不是直接指向 supervisor_ui.py。
set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not exist "%PYW%" for /f "delims=" %%i in ('where pythonw 2^>nul') do set "PYW=%%i"
if not exist "%PYW%" (
  echo [watchdog] 找不到 pythonw.exe，请改本文件里的 PYW 路径 1>&2
  exit /b 1
)
start "" "%PYW%" "%~dp0watchdog.py"
