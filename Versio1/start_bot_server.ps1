# Start the Logistics Bot API Server
# Run this file with: .\start_bot_server.ps1

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "    Damm Logistics Bot - API Server" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

& C:\Users\Alexgay\AppData\Local\Programs\Python\Python312\python.exe api_server.py

Write-Host ""
Read-Host "Press Enter to close"
