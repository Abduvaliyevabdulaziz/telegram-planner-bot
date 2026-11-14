@echo off
cd /d "%~dp0"
echo Starting Telegram Planner Bot...
call .\venv\Scripts\activate.bat
python main.py
pause
