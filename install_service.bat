@echo off
REM ============================================================
REM  APEX Backend — Instalador de Servicio Windows (NSSM)
REM  Ejecutar como Administrador
REM ============================================================

setlocal enabledelayedexpansion

REM ── Configuracion ──────────────────────────────────────────
set SERVICE_NAME=APEX_Backend
set SERVICE_DISPLAY=APEX Backend API
set SERVICE_DESC=Servicio API backend para APEX - Analisis Predictivo de Ecosistemas con IA (PROFEPA)
set BACKEND_PORT=8003

REM Ruta del directorio actual (donde esta este .bat)
set APEX_DIR=%~dp0
set BACKEND_DIR=%APEX_DIR%backend

REM Buscar Python en PATH o en venv
if exist "%APEX_DIR%.venv\Scripts\python.exe" (
    set PYTHON_EXE=%APEX_DIR%.venv\Scripts\python.exe
) else if exist "%APEX_DIR%venv\Scripts\python.exe" (
    set PYTHON_EXE=%APEX_DIR%venv\Scripts\python.exe
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] No se encontro Python. Instala Python 3.10+ o crea un virtualenv.
        pause
        exit /b 1
    )
    set PYTHON_EXE=python
)

REM Buscar NSSM
where nssm >nul 2>&1
if errorlevel 1 (
    if exist "%APEX_DIR%nssm.exe" (
        set NSSM=%APEX_DIR%nssm.exe
    ) else (
        echo [ERROR] NSSM no encontrado. Descargalo de https://nssm.cc/download
        echo         y coloca nssm.exe en %APEX_DIR% o en el PATH.
        pause
        exit /b 1
    )
) else (
    set NSSM=nssm
)

echo ============================================================
echo   APEX Backend — Instalador de Servicio Windows
echo ============================================================
echo.
echo   Servicio:   %SERVICE_NAME%
echo   Python:     %PYTHON_EXE%
echo   Directorio: %BACKEND_DIR%
echo   Puerto:     %BACKEND_PORT%
echo.

REM ── Verificar permisos de administrador ────────────────────
net session >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Este script requiere permisos de Administrador.
    echo         Haz clic derecho y selecciona "Ejecutar como administrador".
    pause
    exit /b 1
)

REM ── Menu ───────────────────────────────────────────────────
echo   Opciones:
echo     1. Instalar servicio
echo     2. Desinstalar servicio
echo     3. Iniciar servicio
echo     4. Detener servicio
echo     5. Estado del servicio
echo     6. Salir
echo.
set /p CHOICE="  Selecciona una opcion (1-6): "

if "%CHOICE%"=="1" goto :install
if "%CHOICE%"=="2" goto :uninstall
if "%CHOICE%"=="3" goto :start
if "%CHOICE%"=="4" goto :stop
if "%CHOICE%"=="5" goto :status
if "%CHOICE%"=="6" goto :end

echo [ERROR] Opcion invalida.
pause
exit /b 1

REM ── Instalar ───────────────────────────────────────────────
:install
echo.
echo [INFO] Instalando servicio %SERVICE_NAME%...

REM Detener si ya existe
%NSSM% stop %SERVICE_NAME% >nul 2>&1
%NSSM% remove %SERVICE_NAME% confirm >nul 2>&1

REM Instalar
%NSSM% install %SERVICE_NAME% "%PYTHON_EXE%" "-m" "uvicorn" "backend.main:app" "--host" "0.0.0.0" "--port" "%BACKEND_PORT%"
if errorlevel 1 (
    echo [ERROR] Fallo al instalar el servicio.
    pause
    exit /b 1
)

REM Configurar
%NSSM% set %SERVICE_NAME% DisplayName "%SERVICE_DISPLAY%"
%NSSM% set %SERVICE_NAME% Description "%SERVICE_DESC%"
%NSSM% set %SERVICE_NAME% AppDirectory "%APEX_DIR%"
%NSSM% set %SERVICE_NAME% AppStdout "%APEX_DIR%logs\apex_stdout.log"
%NSSM% set %SERVICE_NAME% AppStderr "%APEX_DIR%logs\apex_stderr.log"
%NSSM% set %SERVICE_NAME% AppStdoutCreationDisposition 4
%NSSM% set %SERVICE_NAME% AppStderrCreationDisposition 4
%NSSM% set %SERVICE_NAME% AppRotateFiles 1
%NSSM% set %SERVICE_NAME% AppRotateBytes 10485760
%NSSM% set %SERVICE_NAME% AppRotateOnline 1
%NSSM% set %SERVICE_NAME% Start SERVICE_AUTO_START
%NSSM% set %SERVICE_NAME% AppExit Default Restart
%NSSM% set %SERVICE_NAME% AppRestartDelay 5000

REM Crear directorio de logs si no existe
if not exist "%APEX_DIR%logs" mkdir "%APEX_DIR%logs"

echo.
echo [OK] Servicio %SERVICE_NAME% instalado correctamente.
echo      - Se inicia automaticamente con Windows
echo      - Se reinicia automaticamente si falla (delay: 5s)
echo      - Logs en: %APEX_DIR%logs\
echo.
echo      Para iniciar ahora: nssm start %SERVICE_NAME%
pause
goto :end

REM ── Desinstalar ────────────────────────────────────────────
:uninstall
echo.
echo [INFO] Desinstalando servicio %SERVICE_NAME%...
%NSSM% stop %SERVICE_NAME% >nul 2>&1
%NSSM% remove %SERVICE_NAME% confirm
echo [OK] Servicio desinstalado.
pause
goto :end

REM ── Iniciar ────────────────────────────────────────────────
:start
echo.
echo [INFO] Iniciando servicio %SERVICE_NAME%...
%NSSM% start %SERVICE_NAME%
pause
goto :end

REM ── Detener ────────────────────────────────────────────────
:stop
echo.
echo [INFO] Deteniendo servicio %SERVICE_NAME%...
%NSSM% stop %SERVICE_NAME%
pause
goto :end

REM ── Estado ─────────────────────────────────────────────────
:status
echo.
%NSSM% status %SERVICE_NAME%
pause
goto :end

:end
endlocal
