@echo off
setlocal
set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=ui"
set "PORT=5173"
if /I "%TARGET%"=="api" set "PORT=8000"
if /I not "%TARGET%"=="api" if /I not "%TARGET%"=="ui" (
  echo Usage: %~nx0 [api ^| ui]
  echo Default: ui ^(port 5173^). api uses port 8000.
  exit /b 1
)

echo Cloudflare quick tunnel -^> http://127.0.0.1:%PORT% (%TARGET%)
echo Copy the https://....trycloudflare.com URL: frontend/.env VITE_API_URL; backend/.env CORS_ORIGINS (quick tunnels change each run).
echo.

cloudflared tunnel --url http://127.0.0.1:%PORT%
exit /b %ERRORLEVEL%
