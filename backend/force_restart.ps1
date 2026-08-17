# Force Restart Backend Server
# This script stops all Python processes and clears all caches before restarting

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  Force Restart Backend Server" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Stop all Python processes
Write-Host "[1/5] Stopping all Python processes..." -ForegroundColor Yellow
$pythonProcesses = Get-Process | Where-Object {$_.ProcessName -like "*python*"}
if ($pythonProcesses) {
    $pythonProcesses | Stop-Process -Force
    Write-Host "  Stopped $($pythonProcesses.Count) Python process(es)" -ForegroundColor Green
} else {
    Write-Host "  No Python processes found" -ForegroundColor Gray
}

# Step 2: Clear all Python cache
Write-Host "[2/5] Clearing Python cache..." -ForegroundColor Yellow
$cacheDirs = Get-ChildItem -Path "app" -Recurse -Filter "__pycache__" -Directory -ErrorAction SilentlyContinue
$pycFiles = Get-ChildItem -Path "app" -Recurse -Filter "*.pyc" -ErrorAction SilentlyContinue

if ($cacheDirs) {
    $cacheDirs | Remove-Item -Recurse -Force
    Write-Host "  Removed $($cacheDirs.Count) __pycache__ directory(ies)" -ForegroundColor Green
}
if ($pycFiles) {
    $pycFiles | Remove-Item -Force
    Write-Host "  Removed $($pycFiles.Count) .pyc file(s)" -ForegroundColor Green
}
if (-not $cacheDirs -and -not $pycFiles) {
    Write-Host "  No cache found" -ForegroundColor Gray
}

# Step 3: Wait a moment
Write-Host "[3/5] Waiting for processes to fully stop..." -ForegroundColor Yellow
Start-Sleep -Seconds 2

# Step 4: Verify source code
Write-Host "[4/5] Verifying source code..." -ForegroundColor Yellow
$source = Get-Content "app/services/ai_post_processor.py" -Raw
if ($source -match "def _get_service" -and -not ($source -match "def _get_client")) {
    Write-Host "  [OK] Source code is correct (using _get_service)" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] Source code may have issues!" -ForegroundColor Red
    Write-Host "  Please check app/services/ai_post_processor.py" -ForegroundColor Red
    exit 1
}

# Step 5: Start server
Write-Host "[5/5] Starting backend server..." -ForegroundColor Green
Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  Backend URL: http://localhost:8000" -ForegroundColor White
Write-Host "  API Docs: http://localhost:8000/docs" -ForegroundColor White
Write-Host "  Press Ctrl+C to stop" -ForegroundColor White
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

# Start uvicorn with reload only for app directory
python -m uvicorn app.main:app --reload --reload-dir app --host 0.0.0.0 --port 8000
