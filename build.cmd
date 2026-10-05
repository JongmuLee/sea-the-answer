@echo off
call "%~dp0start.cmd" -Task build %*
exit /b %ERRORLEVEL%
