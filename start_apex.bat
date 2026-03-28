@echo off
echo ============================================
echo   APEX — Iniciando Backend + Frontend
echo ============================================
echo.

cd /d d:\MACOV\APEX

echo [1/2] Iniciando Backend (puerto 8008)...
set PYTHONIOENCODING=utf-8
start "APEX-Backend" cmd /k "cd /d d:\MACOV\APEX && d:\MACOV\APEX\venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8008 --reload"

timeout /t 3 /nobreak >nul

echo [2/2] Iniciando Frontend (puerto 5173)...
start "APEX-Frontend" cmd /k "cd /d d:\MACOV\APEX\frontend && npm run dev"

timeout /t 5 /nobreak >nul

echo.
echo ============================================
echo   Servidores iniciados:
echo     Backend:  http://localhost:8008
 echo     Frontend: http://localhost:5173
 echo     Swagger:  http://localhost:8008/docs
echo ============================================
echo.
echo Abre http://localhost:5173 en tu navegador.
echo Cierra las ventanas de cmd para detener.
pause
