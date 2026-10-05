@echo off
call "%~dp0start.cmd" -Task dev %*
exit /b %ERRORLEVEL%
