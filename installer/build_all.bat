@echo off
REM ============================================================
REM SialviPrint - full build script (v2, crash-proof)
REM Run this from a Windows machine with Python 3.11/3.12, pywin32 and
REM Inno Setup 6 installed (see documentation\BUILD_REQUIREMENTS.md).
REM
REM Usage:  installer\build_all.bat
REM
REM This window now ALWAYS stays open at the end (pause), so you can
REM read any error. It never closes by itself.
REM ============================================================

setlocal EnableExtensions

REM Always run from the project root regardless of where this was launched
cd /d "%~dp0.."
if errorlevel 1 goto :fail_cd

set "PY="
set "BUILD_RC=0"

echo.
echo === Step 1/6: Checking prerequisites ===
echo Project folder: %CD%

REM --- find a usable Python (3.11 or 3.12) ---
python -c "import sys; sys.exit(0 if (3,11)<=sys.version_info[:2]<=(3,12) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto :python_ok

py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=py -3.12"
if defined PY goto :python_ok

py -3.11 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=py -3.11"
if defined PY goto :python_ok

echo ERROR: Python 3.11 or 3.12 was not found.
echo The pinned PySide6 6.7.2, PyMuPDF 1.24.9 and pywin32 306 have no
echo wheels for Python 3.13 or newer.
echo.
echo What "python" currently is on this PC:
python --version
echo.
echo FIX: install Python 3.12 from python.org and tick "Add python.exe to PATH",
echo then run this file again.
set "BUILD_RC=1"
goto :done

:python_ok
echo Using Python: %PY%
%PY% --version

REM --- find Inno Setup's iscc.exe ---
where iscc >nul 2>nul
if not errorlevel 1 goto :iscc_ok
if exist "%ProgramFiles(x86)%\Inno Setup 6\iscc.exe" set "PATH=%PATH%;%ProgramFiles(x86)%\Inno Setup 6"
where iscc >nul 2>nul
if not errorlevel 1 goto :iscc_ok
if exist "%ProgramFiles%\Inno Setup 6\iscc.exe" set "PATH=%PATH%;%ProgramFiles%\Inno Setup 6"
where iscc >nul 2>nul
if not errorlevel 1 goto :iscc_ok
if exist "%LocalAppData%\Programs\Inno Setup 6\iscc.exe" set "PATH=%PATH%;%LocalAppData%\Programs\Inno Setup 6"
where iscc >nul 2>nul
if not errorlevel 1 goto :iscc_ok

echo ERROR: Inno Setup's iscc.exe was not found.
echo Install Inno Setup 6 from https://jrsoftware.org/isinfo.php
echo then run this file again.
set "BUILD_RC=1"
goto :done

:iscc_ok
echo Inno Setup found.

echo.
echo === Step 2/6: Installing Admin dependencies ===
%PY% -m pip install -r admin\requirements.txt
if errorlevel 1 goto :fail_admin_deps

echo.
echo === Step 3/6: Installing User dependencies ===
%PY% -m pip install -r user\requirements.txt
if errorlevel 1 goto :fail_user_deps

echo.
echo === Step 4/6: Verifying pywin32 is actually usable ===
%PY% -c "import win32print, win32ui, win32gui; from PIL import ImageWin; print('pywin32 + Pillow import OK')"
if errorlevel 1 goto :fail_pywin32

echo.
echo === Checking cloud licensing: embedded key must match the live Render server ===
%PY% licensing\embed_config.py --check --online
if errorlevel 1 (
  set "BUILD_RC=1"
  goto :done
)

echo === Step 5/6: Building executables with PyInstaller ===
%PY% -m PyInstaller admin\build_admin.spec --clean --noconfirm
if errorlevel 1 goto :fail_admin_build

%PY% -m PyInstaller user\build_user.spec --clean --noconfirm
if errorlevel 1 goto :fail_user_build

%PY% -m PyInstaller owner\build_owner.spec --clean --noconfirm
if errorlevel 1 goto :fail_owner_build

if not exist dist\SialviPrintAdmin\SialviPrintAdmin.exe goto :fail_admin_exe
if not exist dist\SialviPrintUser\SialviPrintUser.exe goto :fail_user_exe
if not exist dist\SialviOwnerLicenseManager\_internal\python312.dll if not exist dist\SialviOwnerLicenseManager\_internal\python311.dll goto :fail_owner_exe

echo.
echo === Step 6/6: Building installers with Inno Setup ===
iscc installer\AdminSetup.iss
if errorlevel 1 goto :fail_admin_iss

iscc installer\UserSetup.iss
if errorlevel 1 goto :fail_user_iss

iscc installer\OwnerSetup.iss
if errorlevel 1 goto :fail_owner_iss

echo.
echo ============================================================
echo BUILD COMPLETE
echo   installer\Output\AdminSetup.exe
echo   installer\Output\UserSetup.exe
echo   installer\Output\OwnerSetup.exe
echo.
echo NEXT: run these on a clean Windows VM with no dev tools
echo installed, per documentation\CLEAN_PC_TEST_CHECKLIST.md,
echo before considering this build final.
echo ============================================================
goto :done

:fail_cd
echo ERROR: Could not change to the project root folder.
set "BUILD_RC=1"
goto :done

:fail_admin_deps
echo ERROR: Failed to install Admin dependencies. Read the red text above.
set "BUILD_RC=1"
goto :done

:fail_user_deps
echo ERROR: Failed to install User dependencies. Read the red text above.
set "BUILD_RC=1"
goto :done

:fail_pywin32
echo ERROR: pywin32 did not import correctly. Try this, then run again:
echo     %PY% -m pip install --force-reinstall pywin32==306
set "BUILD_RC=1"
goto :done

:fail_admin_build
echo ERROR: Admin build failed. See the traceback above.
set "BUILD_RC=1"
goto :done

:fail_user_build
echo ERROR: User build failed. See the traceback above.
set "BUILD_RC=1"
goto :done

:fail_admin_exe
echo ERROR: Admin build did not produce an exe. Aborting.
set "BUILD_RC=1"
goto :done

:fail_user_exe
echo ERROR: User build did not produce an exe. Aborting.
set "BUILD_RC=1"
goto :done

:fail_owner_build
echo ERROR: Owner build failed. See the traceback above.
set "BUILD_RC=1"
goto :done

:fail_owner_exe
echo ERROR: Owner build is missing _internal\python3xx.dll. Aborting.
set "BUILD_RC=1"
goto :done

:fail_owner_iss
echo ERROR: OwnerSetup.iss compilation failed.
set "BUILD_RC=1"
goto :done

:fail_admin_iss
echo ERROR: AdminSetup.iss compilation failed.
set "BUILD_RC=1"
goto :done

:fail_user_iss
echo ERROR: UserSetup.iss compilation failed.
set "BUILD_RC=1"
goto :done

:done
echo.
if "%BUILD_RC%"=="0" (echo Finished OK.) else (echo Build STOPPED because of the error above. Send me the last 30 lines.)
echo.
pause
endlocal & exit /b %BUILD_RC%
