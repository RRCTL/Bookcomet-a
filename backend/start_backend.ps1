# Backend Startup Script with Project-Local Poppler
# This script ensures Poppler is available from the project folder

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  AI Accounting Backend Startup" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# Get project root (parent of backend folder)
$projectRoot = Split-Path -Parent $PSScriptRoot
Write-Host "[1/5] Project root: $projectRoot" -ForegroundColor Green

# Add Poppler to PATH for this session
$popplerPath = Join-Path $projectRoot "bin\poppler\poppler-25.12.0\Library\bin"
if (Test-Path $popplerPath) {
    $env:Path = "$popplerPath;$env:Path"
    Write-Host "[2/5] Poppler added to PATH" -ForegroundColor Green
    
    # Verify Poppler
    try {
        $version = & pdfinfo -v 2>&1 | Select-Object -First 1
        Write-Host "      $version" -ForegroundColor Gray
    } catch {
        Write-Host "      Warning: Could not verify pdfinfo" -ForegroundColor Yellow
    }
} else {
    Write-Host "[2/5] Warning: Poppler not found at $popplerPath" -ForegroundColor Yellow
    Write-Host "      PDF processing may not work!" -ForegroundColor Yellow
}

# Activate virtual environment
$venvPath = Join-Path $PSScriptRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $venvPath) {
    Write-Host "[3/5] Activating virtual environment..." -ForegroundColor Green
    & $venvPath
} else {
    Write-Host "[3/5] Error: Virtual environment not found!" -ForegroundColor Red
    Write-Host "      Expected: $venvPath" -ForegroundColor Red
    exit 1
}

# Check .env file
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    Write-Host "[4/5] Configuration file found (.env)" -ForegroundColor Green
} else {
    Write-Host "[4/5] Warning: .env file not found!" -ForegroundColor Yellow
    Write-Host "      Copy config.env.example to .env and configure it" -ForegroundColor Yellow
}

# Start the server
Write-Host "[5/5] Starting backend server..." -ForegroundColor Green
Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  Backend URL: http://localhost:8000" -ForegroundColor White
Write-Host "  API Docs: http://localhost:8000/docs" -ForegroundColor White
Write-Host "  Press Ctrl+C to stop" -ForegroundColor White
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# Warn if port 8000 is already in use. Do not kill processes automatically;
# developers may be running another project or a debugger on the same port.
$portConn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($portConn) {
    Write-Host "  Port 8000 is already in use (PID: $($portConn.OwningProcess))." -ForegroundColor Yellow
    Write-Host "  Stop that process manually or set PORT in backend/.env before starting." -ForegroundColor Yellow
    exit 1
}

# Start via run.py which sets WindowsSelectorEventLoopPolicy BEFORE uvicorn
# creates its event loop.  This prevents the WinError 10054 / ProactorEventLoop
# hang on Ctrl+C that makes the server stick at "Shutting down" indefinitely.
.\.venv\Scripts\python.exe run.py
