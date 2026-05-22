@echo off
uv tool run --from notebooklm-mcp-cli nlm %*
exit /b %ERRORLEVEL%
