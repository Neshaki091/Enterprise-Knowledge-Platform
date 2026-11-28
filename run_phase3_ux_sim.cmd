@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title Phase 3 UX Simulation - EKP

set "PY=%~dp0.venv\Scripts\python.exe"
if exist "%PY%" goto :py_ready
where python >nul 2>nul
if errorlevel 1 goto :no_python
set "PY=python"
:py_ready
set "PYTHONIOENCODING=utf-8"

set "CFG_TARGET=asgi://local"
set "HAS_LLM_KEY=0"
if not exist "phase3_ux_sim\.env_vu" goto :cfg_done
for /f "usebackq tokens=1,* delims==" %%A in ("phase3_ux_sim\.env_vu") do (
    if /i "%%A"=="SIM_TARGET_API_URL" if not "%%B"=="" set "CFG_TARGET=%%~B"
    if /i "%%A"=="GOOGLE_API_KEY" if not "%%B"=="" set "HAS_LLM_KEY=1"
)
:cfg_done

set "IS_LOCAL=0"
if /i "%CFG_TARGET%"=="asgi://local" set "IS_LOCAL=1"
if /i not "%CFG_TARGET:localhost=%"=="%CFG_TARGET%" set "IS_LOCAL=1"
if /i not "%CFG_TARGET:127.0.0.1=%"=="%CFG_TARGET%" set "IS_LOCAL=1"
if "%IS_LOCAL%"=="1" (set "TARGET_NOTE=local / ASGI") else (set "TARGET_NOTE=REMOTE - chi dung staging ban so huu")
if "%HAS_LLM_KEY%"=="1" (set "LLM_NOTE=co GOOGLE_API_KEY") else (set "LLM_NOTE=KHONG thay GOOGLE_API_KEY")

:menu
cls
echo ================================================================
echo   PHASE 3 - LLM UX SIMULATION ^& SUPABASE MCP (Sprint 9)
echo ================================================================
echo   Target  : "%CFG_TARGET%"
echo   Loai    : %TARGET_NOTE%
echo   LLM key : %LLM_NOTE%
echo ----------------------------------------------------------------
echo   1. Local ASGI smoke - 20 user, 5 active, offline LLM, skip MCP
echo   2. Seed-only 100 user tren target (khong chat, co MCP check)
echo   3. Chay nhanh tren target - seed 20 user, 5 active, 3 steps, 5 LLM query
echo   4. Chay tieu chuan - 20 active, 4 steps, toi da 20 LLM query
echo   5. Chay FULL nghiem thu - 100 active, 4 steps, toi da 20 LLM query
echo   6. Chay voi URL tuy chinh
echo   0. Thoat
echo ----------------------------------------------------------------
echo   Muc 2-6 tao tenant/user/tai lieu THAT va goi LLM that.
echo   Khong dong cua so nay khi dang chay.
echo ================================================================
set "CH="
set /p "CH=Chon [0-6]: "
if "%CH%"=="" goto :end
if "%CH%"=="0" goto :end
if "%CH%"=="1" goto :local
if "%CH%"=="2" goto :seed
if "%CH%"=="3" goto :quick
if "%CH%"=="4" goto :standard
if "%CH%"=="5" goto :full
if "%CH%"=="6" goto :custom
goto :menu

:local
echo.
echo [Phase3] Local ASGI + offline LLM. Khong goi Google API.
echo [Phase3] Telemetry SQLite rieng phase3_ux_sim\sim_local_smoke.db (neu khong co SUPABASE_DB_URL).
set "SIM_LOCAL_FALLBACK_DB=sqlite+aiosqlite:///phase3_ux_sim/sim_local_smoke.db"
"%PY%" -m phase3_ux_sim.runner --target-url asgi://local --target-users 20 --active-users 5 --steps 4 --offline-llm --skip-mcp --source local
set "RC=%ERRORLEVEL%"
set "SIM_LOCAL_FALLBACK_DB="
goto :done

:seed
call :confirm_remote
if errorlevel 1 goto :menu
echo.
echo [Phase3] Seed-only 100 user tren target. Khong chat, van chay MCP check.
"%PY%" -m phase3_ux_sim.runner --seed-only --target-users 100 --source local
set "RC=%ERRORLEVEL%"
goto :done

:quick
call :confirm_remote
if errorlevel 1 goto :menu
echo.
echo [Phase3] Chay nhanh: seed 20 user, 5 active user, 3 steps, toi da 5 LLM query.
"%PY%" -m phase3_ux_sim.runner --target-users 20 --active-users 5 --steps 3 --max-llm-queries 5 --source local
set "RC=%ERRORLEVEL%"
goto :done

:standard
call :confirm_remote
if errorlevel 1 goto :menu
echo.
echo [Phase3] Chay tieu chuan: 20 active user, 4 steps, toi da 20 LLM query.
"%PY%" -m phase3_ux_sim.runner --active-users 20 --steps 4 --max-llm-queries 20 --source local
set "RC=%ERRORLEVEL%"
goto :done

:full
call :confirm_remote
if errorlevel 1 goto :menu
echo.
echo [Phase3] Chay FULL: 100 active user, 4 steps, toi da 20 LLM query.
echo [Phase3] Co the mat 10-20 phut tuy quota va toc do backend.
"%PY%" -m phase3_ux_sim.runner --active-users 100 --steps 4 --max-llm-queries 20 --source local
set "RC=%ERRORLEVEL%"
goto :done

:custom
echo.
echo Luu y: telemetry DB giu danh sach user theo lan seed. Khi doi target,
echo co the can DB telemetry rieng hoac xoa sim_local.db (chi khi local).
echo.
set "CURL=%CFG_TARGET%"
set /p "CURL=URL backend (http/https hoac asgi://local) [%CFG_TARGET%]: "
if "%CURL%"=="" set "CURL=%CFG_TARGET%"
set "CTOTAL=100"
set /p "CTOTAL=Tong user seed [100]: "
if "%CTOTAL%"=="" set "CTOTAL=100"
set "CACT=5"
set /p "CACT=So active user [5]: "
if "%CACT%"=="" set "CACT=5"
set "CQ=5"
set /p "CQ=Toi da LLM query [5]: "
if "%CQ%"=="" set "CQ=5"
call :confirm_url "%CURL%"
if errorlevel 1 goto :menu
echo.
echo [Phase3] Chay tren "%CURL%" - seed %CTOTAL% user, %CACT% active, toi da %CQ% query.
"%PY%" -m phase3_ux_sim.runner --target-url "%CURL%" --target-users %CTOTAL% --active-users %CACT% --steps 4 --max-llm-queries %CQ% --source local
set "RC=%ERRORLEVEL%"
goto :done

:done
echo.
if not "%RC%"=="0" goto :done_fail
echo [OK] Phase 3 hoan tat (exit code 0).
call :snapshot_report
if exist "phase3_ux_sim\reports\latest_ux_report.md" echo Report: %~dp0phase3_ux_sim\reports\latest_ux_report.md
if exist "phase3_ux_sim\reports\latest_ux_report.json" echo JSON  : %~dp0phase3_ux_sim\reports\latest_ux_report.json
goto :done_open

:done_fail
echo [FAIL] Phase 3 exit code %RC%. Xem log phia tren va phase3_ux_sim\reports.

:done_open
echo.
set "OPEN="
set /p "OPEN=Mo thu muc report? [y/N]: "
if /i "%OPEN%"=="y" start "" "%~dp0phase3_ux_sim\reports"
echo.
pause
goto :menu

:end
endlocal
exit /b 0

:confirm_remote
call :confirm_url "%CFG_TARGET%"
exit /b %ERRORLEVEL%

:confirm_url
set "URL=%~1"
if /i "%URL%"=="asgi://local" exit /b 0
if /i not "%URL:localhost=%"=="%URL%" exit /b 0
if /i not "%URL:127.0.0.1=%"=="%URL%" exit /b 0
echo.
echo [CANH BAO] URL "%URL%" khong phai local.
echo Lenh se tao tenant/user/tai lieu THAT va goi LLM that tren backend do.
set "OK="
set /p "OK=Nhap Y de tiep tuc: "
if /i "%OK%"=="Y" exit /b 0
exit /b 1

:snapshot_report
"%PY%" -c "import datetime,pathlib,shutil; r=pathlib.Path('phase3_ux_sim/reports'); st=datetime.datetime.now().strftime('%%Y%%m%%d_%%H%%M%%S'); [shutil.copy2(r/('latest_ux_report.'+e), r/('ux_report_'+st+'.'+e)) for e in ('md','json') if (r/('latest_ux_report.'+e)).exists()]"
exit /b 0

:no_python
echo [LOI] Khong tim thay Python. Cai Python 3.11+ hoac tao .venv:
echo   python -m venv .venv
echo   .venv\Scripts\pip install -r requirements.txt
pause
exit /b 1