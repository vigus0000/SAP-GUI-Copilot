@echo off
setlocal
cd /d "%~dp0"
python setup_mcp.py %*
endlocal
