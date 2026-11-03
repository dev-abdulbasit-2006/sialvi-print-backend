@echo off
REM Builds the Owner License Manager (Windows, from the project root). Python 3.12 venv recommended.
cd /d "%~dp0\.."
python -m pip install -r licensing\requirements.txt || exit /b 1
python -m unittest discover -s tests || exit /b 1
pyinstaller owner\build_owner.spec --clean --noconfirm || exit /b 1
echo Built: dist\SialviOwnerLicenseManager\SialviOwnerLicenseManager.exe  (onedir - ship the WHOLE folder)
