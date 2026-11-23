@echo off
setlocal
pushd "%~dp0" || exit /b 1

where python >nul 2>nul
if errorlevel 1 goto no_python

python -u server.py --host 127.0.0.1 --port 8765 --unoq-render-port 18765 --open-browser
set "SERVER_EXIT=%ERRORLEVEL%"
if not "%SERVER_EXIT%"=="0" pause
popd
exit /b %SERVER_EXIT%

:no_python
echo Python was not found. Install Python 3 and the required OpenCV and NumPy packages.
pause
popd
exit /b 1
