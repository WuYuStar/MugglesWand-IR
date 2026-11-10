@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto :fallback
for %%V in (3.12 3.11 3.13 3.10) do (
    py -%%V -c "import sys" >nul 2>nul
    if not errorlevel 1 (
        py -%%V launch.py %*
        goto :done
    )
)
:fallback
python launch.py %*
:done
if errorlevel 1 (
    echo.
    echo 启动未完成，请查看上方的具体提示。
    pause
)
