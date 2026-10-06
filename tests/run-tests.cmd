@echo off
REM 跑全部单元测试（标准库 unittest，零第三方依赖）
cd /d "%~dp0.."
python -m unittest discover -s tests -v