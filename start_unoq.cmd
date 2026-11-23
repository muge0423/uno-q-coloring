@echo off
setlocal EnableExtensions EnableDelayedExpansion
pushd "%~dp0"
set "ADB="
set "SERIAL="
set "MULTIPLE="
set "BOARDDIR="

for /f "delims=" %%A in ('where adb 2^>nul') do if not defined ADB set "ADB=%%A"
if not defined ADB set "ADB=%LOCALAPPDATA%\Arduino15\packages\arduino\tools\adb\32.0.0\adb.exe"
if not exist "!ADB!" goto no_adb

echo Using ADB: !ADB!
"!ADB!" start-server
if errorlevel 1 goto adb_error
echo.
echo Connected devices:
"!ADB!" devices -l
for /f "tokens=1,2" %%A in ('"!ADB!" devices 2^>nul') do (
  if "%%B"=="device" (
    if defined SERIAL set "MULTIPLE=1"
    set "SERIAL=%%A"
  )
)
if not defined SERIAL goto no_device
if defined MULTIPLE goto multiple_devices

echo.
echo UNO Q connected: !SERIAL!
set "BOARDDIR=/tmp/uno-q-color"
"!ADB!" -s "!SERIAL!" shell mkdir -p "!BOARDDIR!" >nul
if errorlevel 1 goto board_shell_error

echo Uploading the board renderer to !BOARDDIR!...
"!ADB!" -s "!SERIAL!" push "%~dp0adreno.py" "!BOARDDIR!/adreno.py"
if errorlevel 1 goto transfer_error
"!ADB!" -s "!SERIAL!" push "%~dp0unoq_renderer.py" "!BOARDDIR!/unoq_renderer.py"
if errorlevel 1 goto transfer_error

"!ADB!" -s "!SERIAL!" forward tcp:18765 tcp:18765
if errorlevel 1 goto forward_error

echo.
echo Starting UNO Q Adreno renderer on port 18765.
echo Keep this window open. A successful start prints the GPU renderer and listening address.
echo Press Ctrl+C to stop the board renderer.
echo.
"!ADB!" -s "!SERIAL!" shell -t python3 "!BOARDDIR!/unoq_renderer.py" --port 18765
echo.
echo The UNO Q renderer has stopped.
goto finish

:no_adb
echo ADB was not found in PATH or the standard Arduino installation folder.
goto failure

:adb_error
echo ADB could not start. Check whether another ADB installation is running or blocked.
goto failure

:no_device
echo No authorized device was found. Connect the UNO Q by USB and approve its debugging prompt.
goto failure

:multiple_devices
echo More than one device is connected. Disconnect other devices and run this command again.
goto failure

:board_shell_error
echo Could not open a shell on the connected device.
goto failure

:transfer_error
echo Could not copy the renderer files to the UNO Q.
goto failure

:forward_error
echo Could not forward local port 18765 to the UNO Q. Check whether the port is already in use.
goto failure

:failure
echo.
pause

:finish
popd
endlocal
