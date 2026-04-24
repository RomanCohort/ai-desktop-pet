@echo off
chcp 65001 >nul
cd /d %~dp0

set "PYEXE=%~dp0.venv\Scripts\python.exe"
if exist "%PYEXE%" goto run_make

where py >nul 2>nul
if not errorlevel 1 (
	set "PYEXE=py -3"
	goto run_make
)

where python >nul 2>nul
if not errorlevel 1 (
	set "PYEXE=python"
	goto run_make
)

echo 未找到可用的 Python 运行时，请先双击 install.bat。
pause
exit /b 1

:run_make
echo 正在根据源图生成 normal1/normal2/blink1...
%PYEXE% make_assets.py
echo.
echo 完成后请运行 run.bat 查看效果。
pause
