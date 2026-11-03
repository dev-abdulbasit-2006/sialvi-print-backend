@echo off
REM Builds ONLY the Owner app + OwnerSetup.exe. Run from anywhere: installer\build_owner_only.bat
REM Needs Python 3.11/3.12 (64-bit) and Inno Setup 6. Stops at the first failure.
setlocal EnableExtensions
cd /d "%~dp0.."
python -c "import sys; sys.exit(0 if (3,11)<=sys.version_info[:2]<=(3,12) else 1)" || (echo ERROR: need Python 3.11 or 3.12 & goto :fail)
python -m pip install -r licensing\requirements.txt PySide6 pyinstaller || goto :fail
set "QT_QPA_PLATFORM=offscreen"
python -m unittest discover -s tests || goto :fail
python licensing\embed_config.py --check --online || goto :fail
python -m PyInstaller owner\build_owner.spec --clean --noconfirm || goto :fail
if not exist dist\SialviOwnerLicenseManager\_internal\python312.dll if not exist dist\SialviOwnerLicenseManager\_internal\python311.dll (echo ERROR: python DLL missing in _internal & goto :fail)
where iscc >nul 2>nul || set "PATH=%%PATH%%;%%ProgramFiles(x86)%%\Inno Setup 6;%%ProgramFiles%%\Inno Setup 6;%%LocalAppData%%\Programs\Inno Setup 6"
iscc installer\OwnerSetup.iss || goto :fail
echo.
echo DONE: installer\Output\OwnerSetup.exe  (owner only - do NOT give to customers)
pause
exit /b 0
:fail
echo BUILD FAILED - read the error above.
pause
exit /b 1
