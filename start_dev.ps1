# DatApp 前后端一键静默启动脚本 (PowerShell)
$ProjectRoot = $PSScriptRoot

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "正在启动 DatApp 前后端全栈开发服务..." -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 启动后端 FastAPI 服务
Start-Process powershell.exe -WindowStyle Hidden -ArgumentList "-Command", "Set-Location '$ProjectRoot\server'; uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"

# 启动前端 Vite 看板服务
Start-Process powershell.exe -WindowStyle Hidden -ArgumentList "-Command", "Set-Location '$ProjectRoot\client'; npm run dev"

Start-Sleep -Seconds 2

Write-Host "`n[√] 前后端服务已在后台静默启动成功！" -ForegroundColor Green
Write-Host "[•] 后端 API 服务: http://127.0.0.1:8000" -ForegroundColor Yellow
Write-Host "[•] 前端 Web 看板: http://localhost:5173" -ForegroundColor Yellow
Write-Host "[•] Android 模拟器: http://10.0.2.2:8000" -ForegroundColor Yellow
