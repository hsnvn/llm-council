@echo off
REM council.bat - llm_council shortcut. Example:
REM   council -p "question" -d C:\path\to\repo
python "%~dp0llm_council.py" %*
