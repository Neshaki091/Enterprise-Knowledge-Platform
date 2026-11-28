@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title Load / Soak / Stress Tests - EKP

set "PY=%~dp0.venv\Scripts\python.exe"
if exist "%PY%" goto :py_ready
where python >nul 2>nul
if errorlevel 1 goto :no_python
set "PY=python"
:py_ready
set "PYTHONIOENCODING=utf-8"

set "HAS_K6=0"
where k6 >nul 2>nul
if not errorlevel 1 set "HAS_K6=1"

set "BASE=http://localhost:8000"
if not exist "load_tests\.runtime\config.json" goto :base_ready
"%PY%" -c "import json;print(json.load(open('load_tests/.runtime/config.json',encoding='utf-8')).get('base_url',''))" > "%TEMP%\ekp_load_base.txt" 2>nul
if exist "%TEMP%\ekp_load_base.txt" set /p BASE=<"%TEMP%\ekp_load_base.txt"
del "%TEMP%\ekp_load_base.txt" >nul 2>nul
:base_ready
if "%BASE%"=="" set "BASE=http://localhost:8000"
set "REMOTE_CONFIRMED=0"
call :classify_base

:menu
cls
echo ================================================================
echo   LOAD / SOAK / STRESS TESTS (Sprint 9)
echo ================================================================
echo   Target  : "%BASE%"
if "%IS_LOCAL%"=="1" (echo   Loai    : local) else (echo   Loai    : REMOTE - chi dung staging ban so huu)
if "%HAS_K6%"=="1" (echo   k6      : da cai) else (echo   k6      : CHUA CAI - cac muc 6-8 se khong chay)
echo   Config  : load_tests\.runtime\config.json (seed se tao neu thieu)
echo ----------------------------------------------------------------
echo   1. Smoke day du: seed + IDOR + SSE + ingest duplicate (~3-6 phut)
echo   2. Seed / refresh du lieu test (tenant, user, tai lieu, fixtures)
echo   3. Probe IDOR/BOLA
echo   4. Probe SSE dashboard + chat mock
echo   5. Ingest stress day du: concurrency 1,2,4,8 + size + duplicate
echo   6. k6 Soak Journey 1 (nhap VU, thoi gian, chat mode)
echo   7. k6 Saturation + Spike
echo   8. k6 Upload Journey 2
echo   T. Doi target URL
echo   O. Mo thu muc reports
echo   0. Thoat
echo ----------------------------------------------------------------
echo   Chi chay tren staging/local. Khong chay stress vao production
echo   trong gio lam viec. Khong chay load generator tren VPS backend.
echo ================================================================
set "CH="
set /p "CH=Chon: "
if "%CH%"=="" goto :end
if "%CH%"=="1" goto :smoke
if "%CH%"=="2" goto :seed
if "%CH%"=="3" goto :idor
if "%CH%"=="4" goto :sse
if "%CH%"=="5" goto :ingest
if "%CH%"=="6" goto :soak
if "%CH%"=="7" goto :k6sat
if "%CH%"=="8" goto :k6upload
if /i "%CH%"=="T" goto :setbase
if /i "%CH%"=="O" goto :open_reports
if "%CH%"=="0" goto :end
goto :menu

:smoke
call :need_target
if errorlevel 1 goto :menu
call :need_seed
if errorlevel 1 goto :action_fail
echo.
echo [1/4] IDOR/BOLA probe...
"%PY%" load_tests\idor_probe.py --base-url "%BASE%"
if errorlevel 1 echo [CANH BAO] IDOR probe bao vi pham hoac loi - xem log.
echo.
echo [2/4] SSE dashboard 15s...
"%PY%" load_tests\sse_probe.py --base-url "%BASE%" --mode dashboard --duration 15
echo.
echo [3/4] SSE chat mock 15s...
"%PY%" load_tests\sse_probe.py --base-url "%BASE%" --mode chat --duration 15 --chat-mode mock
echo.
echo [4/4] Ingest duplicate race (10 upload cung file)...
"%PY%" load_tests\ingest_stress.py --base-url "%BASE%" --mode duplicate --duplicate-concurrency 10 --container ""
goto :action_done

:seed
call :need_target
if errorlevel 1 goto :menu
echo.
set "REFRESH="
set /p "REFRESH=Tao lai toan bo fixtures? [y/N]: "
set "EXTRA="
if /i "%REFRESH%"=="y" set "EXTRA=--refresh-fixtures"
set "LARGE="
set /p "LARGE=Sinh them fixture 24MB (cho size test)? [y/N]: "
if /i "%LARGE%"=="y" set "EXTRA=%EXTRA% --large"
if not exist "load_tests\fixtures\generated\ingest_001.txt" set "EXTRA=%EXTRA% --refresh-fixtures"
echo.
echo [seed] Tao fixtures + 2 tenant + 11 user + 3 tai lieu synthetic tren "%BASE%"...
"%PY%" load_tests\seed_test_data.py --base-url "%BASE%" %EXTRA%
if errorlevel 1 goto :action_fail
goto :action_done

:idor
call :need_target
if errorlevel 1 goto :menu
call :need_seed
if errorlevel 1 goto :action_fail
echo.
echo [probe] Tenant A khong duoc doc/search tai lieu Tenant B...
"%PY%" load_tests\idor_probe.py --base-url "%BASE%"
if errorlevel 1 goto :action_fail
goto :action_done

:sse
call :need_target
if errorlevel 1 goto :menu
call :need_seed
if errorlevel 1 goto :action_fail
echo.
echo [probe] SSE dashboard 20s...
"%PY%" load_tests\sse_probe.py --base-url "%BASE%" --mode dashboard --duration 20
echo.
echo [probe] SSE chat mock 20s...
"%PY%" load_tests\sse_probe.py --base-url "%BASE%" --mode chat --duration 20 --chat-mode mock
goto :action_done

:ingest
call :need_target
if errorlevel 1 goto :menu
call :need_seed
if errorlevel 1 goto :action_fail
echo.
echo [CANH BAO] Bai nay tao nhieu tai lieu synthetic va goi embedding API cua backend.
echo Khong chay vao production dang phuc vu khach hang.
set "OK="
set /p "OK=Nhap Y de tiep tuc: "
if /i not "%OK%"=="Y" goto :menu
echo.
echo [1/3] Concurrency ramp 1,2,4,8 x 30s...
"%PY%" load_tests\ingest_stress.py --base-url "%BASE%" --mode concurrency --levels 1,2,4,8 --duration 30
echo.
echo [2/3] Size ramp...
"%PY%" load_tests\ingest_stress.py --base-url "%BASE%" --mode size
echo.
echo [3/3] Duplicate race...
"%PY%" load_tests\ingest_stress.py --base-url "%BASE%" --mode duplicate --duplicate-concurrency 10
goto :action_done

:soak
call :need_k6
if errorlevel 1 goto :menu
call :need_target
if errorlevel 1 goto :menu
call :need_seed
if errorlevel 1 goto :action_fail
echo.
set "VUS=3"
set /p "VUS=So VU dong thoi [3]: "
if "%VUS%"=="" set "VUS=3"
set "DUR=30m"
set /p "DUR=Thoi gian (30m, 2h, 4h) [30m]: "
if "%DUR%"=="" set "DUR=30m"
set "CHATMODE=mock"
set /p "CHATMODE=Chat mode mock/retrieval_only/hybrid/real [mock]: "
if "%CHATMODE%"=="" set "CHATMODE=mock"
if /i "%CHATMODE%"=="real" goto :soak_confirm
if /i "%CHATMODE%"=="hybrid" goto :soak_confirm
goto :soak_run

:soak_confirm
echo [CANH BAO] Chat mode "%CHATMODE%" goi LLM that, ton quota va egress.
set "OK="
set /p "OK=Nhap Y de tiep tuc: "
if /i not "%OK%"=="Y" goto :menu

:soak_run
echo.
echo [k6] Journey 1: %VUS% VU, %DUR%, chat=%CHATMODE%
k6 run -e "BASE_URL=%BASE%" -e "VUS=%VUS%" -e "DURATION=%DUR%" -e "CHAT_MODE=%CHATMODE%" --summary-export "load_tests\reports\journey1_summary.json" "load_tests\k6\journey1_employee.js"
if errorlevel 1 goto :action_fail
goto :action_done

:k6sat
call :need_k6
if errorlevel 1 goto :menu
call :need_target
if errorlevel 1 goto :menu
call :need_seed
if errorlevel 1 goto :action_fail
echo.
echo [k6] Saturation ramp 10-60 VU...
k6 run -e "BASE_URL=%BASE%" --summary-export "load_tests\reports\saturation_summary.json" "load_tests\k6\saturation.js"
echo.
echo [k6] Spike 0-20 VU...
k6 run -e "BASE_URL=%BASE%" --summary-export "load_tests\reports\spike_summary.json" "load_tests\k6\spike.js"
goto :action_done

:k6upload
call :need_k6
if errorlevel 1 goto :menu
call :need_target
if errorlevel 1 goto :menu
call :need_seed
if errorlevel 1 goto :action_fail
echo.
set "VUS=1"
set /p "VUS=So VU upload [1]: "
if "%VUS%"=="" set "VUS=1"
set "DUR=10m"
set /p "DUR=Thoi gian (10m, 30m) [10m]: "
if "%DUR%"=="" set "DUR=10m"
echo.
echo [k6] Journey 2 upload: %VUS% VU, %DUR%
k6 run -e "BASE_URL=%BASE%" -e "VUS=%VUS%" -e "DURATION=%DUR%" --summary-export "load_tests\reports\journey2_summary.json" "load_tests\k6\journey2_ingest.js"
if errorlevel 1 goto :action_fail
goto :action_done

:setbase
echo.
echo Nhap URL API backend (co /api/v1/health), khong phai frontend Vercel.
echo Vi du: http://localhost:8000 hoac https://staging.example.com
set "NEW="
set /p "NEW=Base URL [%BASE%]: "
if not "%NEW%"=="" set "BASE=%NEW%"
set "REMOTE_CONFIRMED=0"
if "%BASE:~-1%"=="/" set "BASE=%BASE:~0,-1%"
if /i not "%BASE:~0,4%"=="http" goto :setbase_bad
call :classify_base
goto :menu

:setbase_bad
echo [LOI] URL phai bat dau bang http:// hoac https://
set "BASE=http://localhost:8000"
call :classify_base
goto :menu

:open_reports
if not exist "load_tests\reports" mkdir "load_tests\reports"
start "" "%~dp0load_tests\reports"
goto :menu

:action_done
echo.
echo [OK] Xong. Report trong load_tests\reports (neu bai co xuat file).
echo.
pause
goto :menu

:action_fail
echo.
echo [FAIL] Buoc vua roi tra ve loi. Xem log phia tren.
echo.
pause
goto :menu

:end
endlocal
exit /b 0

:need_target
echo.
echo [check] %BASE%/api/v1/health
"%PY%" -c "import httpx,sys;u=sys.argv[1].rstrip('/');r=httpx.get(u+'/api/v1/health',timeout=8,trust_env=False);j=r.json();print('  ->',r.status_code,j.get('status'),'version',j.get('version','?'));sys.exit(0 if r.status_code==200 else 1)" "%BASE%" 2>nul
if not errorlevel 1 goto :need_target_ok
echo [CANH BAO] Khong ket noi duoc health endpoint.
echo - Kiem tra backend da chay va URL la API backend, khong phai frontend.
echo - Neu backend Docker vua start, doi 5-10 giay roi thu lai.
set "CONT="
set /p "CONT=Nhap Y de van tiep tuc: "
if /i not "%CONT%"=="Y" exit /b 1

:need_target_ok
if "%IS_LOCAL%"=="1" exit /b 0
if "%REMOTE_CONFIRMED%"=="1" exit /b 0
echo.
echo [CANH BAO] Target "%BASE%" khong phai local.
echo Chi tiep tuc neu day la staging ban so huu va dang ngoai gio phuc vu.
set "OK="
set /p "OK=Nhap Y de tiep tuc: "
if /i not "%OK%"=="Y" exit /b 1
set "REMOTE_CONFIRMED=1"
exit /b 0

:need_seed
if not exist "load_tests\.runtime\config.json" goto :need_seed_create
"%PY%" -c "import json,sys,pathlib; p=pathlib.Path('load_tests/.runtime/config.json'); d=json.loads(p.read_text('utf-8')); sys.exit(0 if d.get('base_url','').rstrip('/')==sys.argv[1].rstrip('/') else 1)" "%BASE%"
if errorlevel 1 goto :need_seed_reseed
exit /b 0

:need_seed_create
echo [seed] Chua co config - tao fixtures va seed tenant/user/tai lieu...
call :do_seed
exit /b %ERRORLEVEL%

:need_seed_reseed
echo [seed] Config thuoc target khac - seed lai cho "%BASE%"...
call :do_seed
exit /b %ERRORLEVEL%

:do_seed
set "SEEDEXTRA="
if not exist "load_tests\fixtures\generated\ingest_001.txt" set "SEEDEXTRA=--refresh-fixtures"
"%PY%" load_tests\seed_test_data.py --base-url "%BASE%" %SEEDEXTRA%
exit /b %ERRORLEVEL%

:need_k6
if "%HAS_K6%"=="1" exit /b 0
echo.
echo [LOI] Chua cai k6. Tai MSI:
echo   https://dl.k6.io/msi/k6-latest-amd64.msi
echo Hoac: winget install k6.k6
echo.
pause
exit /b 1

:no_python
echo [LOI] Khong tim thay Python. Cai Python 3.11+ hoac tao .venv:
echo   python -m venv .venv
echo   .venv\Scripts\pip install -r requirements.txt
pause
exit /b 1

:classify_base
set "IS_LOCAL=0"
if /i not "%BASE:localhost=%"=="%BASE%" set "IS_LOCAL=1"
if /i not "%BASE:127.0.0.1=%"=="%BASE%" set "IS_LOCAL=1"
exit /b 0