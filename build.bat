@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0" || exit /b 1
py -3.11 -c "import struct; assert struct.calcsize('P') == 8" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Install 64-bit Python 3.11 on this BUILD computer first.
    goto :failed
)
if not exist ".venv-build\Scripts\python.exe" (
    py -3.11 -m venv .venv-build
    if errorlevel 1 goto :failed
)
".venv-build\Scripts\python.exe" -m pip install -r requirements-windows.lock
if errorlevel 1 goto :failed
".venv-build\Scripts\python.exe" tools\build_release.py
if errorlevel 1 goto :failed
popd
echo.
if /I not "%~1"=="--no-pause" pause
exit /b 0
:failed
popd
echo.
echo Build failed. See the error above.
if /I not "%~1"=="--no-pause" pause
exit /b 1
