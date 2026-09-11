@echo off
chcp 65001 >nul
echo ==========================================
echo 正在启动 DatApp 前后端全栈开发服务...
echo ==========================================

REM 静默后台启动后端 FastAPI 服务 (Port 8000)
powershell -Command "Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-Command', 'Set-Location ''%~dp0server''; uv run uvicorn app.main:app --host 0.0.0.0 --port 8000'"

REM 静默后台启动前端 Vite 开发看板 (Port 5173)
powershell -Command "Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-Command', 'Set-Location ''%~dp0client''; npm run dev'"

echo.
echo [√] 前后端服务已在系统后台静默启动！
echo [•] 后端 API 服务: http://127.0.0.1:8000
echo [•] 前端 Web 看板: http://localhost:5173
echo [•] Android 模拟器: http://10.0.2.2:8000
echo.
pause
