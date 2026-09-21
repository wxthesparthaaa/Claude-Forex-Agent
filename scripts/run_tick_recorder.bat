@echo off
rem Starts the tick recorder and restarts it if it ever crashes. Leave this window open; Ctrl+C to stop.
cd /d "%~dp0.."
:loop
echo [%date% %time%] starting tick recorder
venv\Scripts\python.exe scripts\tick_recorder.py
echo recorder exited -- restarting in 10 seconds (close this window to stop)
timeout /t 10 /nobreak >nul
goto loop
