@echo off
REM ============================================================
REM SialviPrint - Offline Ethernet LAN fix (run as Administrator)
REM
REM Why: Windows treats a wired network with no internet as an
REM "Unidentified network" and puts it in the PUBLIC firewall profile.
REM Older SialviPrint installs only opened the firewall for the
REM Private/Domain profiles, so User PCs could not reach the Admin PC
REM over Ethernet-only LAN (it worked on Wi-Fi because Wi-Fi was Private).
REM
REM This script (re)creates the SialviPrint rules for ALL profiles but
REM ONLY for private/local addresses (your LAN) - the internet and
REM unrelated networks stay blocked. It does NOT turn the firewall off.
REM
REM It works on the Admin PC, the User PC, or both: it detects which
REM SialviPrint app is installed in the same folder as this file.
REM ============================================================
setlocal EnableExtensions

net session >nul 2>nul
if errorlevel 1 (
  echo This must be run as Administrator.
  echo Right-click Fix_LAN_Firewall.bat and choose "Run as administrator".
  pause
  exit /b 1
)

set "LAN=LocalSubnet,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,169.254.0.0/16"
set "HERE=%~dp0"
set "DONE=0"

if exist "%HERE%SialviPrintAdmin.exe" (
  echo Configuring Admin PC rules...
  netsh advfirewall firewall delete rule name="SialviPrint Admin" >nul 2>nul
  netsh advfirewall firewall delete rule name="SialviPrint Admin API" >nul 2>nul
  netsh advfirewall firewall delete rule name="SialviPrint Admin Discovery" >nul 2>nul
  netsh advfirewall firewall add rule name="SialviPrint Admin" dir=in action=allow program="%HERE%SialviPrintAdmin.exe" enable=yes profile=any remoteip=%LAN%
  netsh advfirewall firewall add rule name="SialviPrint Admin API" dir=in action=allow protocol=TCP localport=8765 enable=yes profile=any remoteip=%LAN%
  netsh advfirewall firewall add rule name="SialviPrint Admin Discovery" dir=in action=allow protocol=UDP localport=8766 enable=yes profile=any remoteip=%LAN%
  set "DONE=1"
)

if exist "%HERE%SialviPrintUser.exe" (
  echo Configuring User PC rules...
  netsh advfirewall firewall delete rule name="SialviPrint User Print Agent" >nul 2>nul
  netsh advfirewall firewall add rule name="SialviPrint User Print Agent" dir=in action=allow protocol=TCP localport=8767 enable=yes profile=any remoteip=%LAN%
  set "DONE=1"
)

if "%DONE%"=="0" (
  echo Could not find SialviPrintAdmin.exe or SialviPrintUser.exe next to this file.
  echo Run this from the SialviPrint install folder.
  pause
  exit /b 1
)

echo.
echo Current network profile of each connection (informational):
powershell -NoProfile -Command "Get-NetConnectionProfile | Format-Table Name,InterfaceAlias,NetworkCategory -AutoSize"
echo.
echo Done. The rules above allow SialviPrint only from your LAN addresses.
echo Optional: to make the Ethernet connection "Private" instead, run in PowerShell (Admin):
echo    Set-NetConnectionProfile -InterfaceAlias "Ethernet" -NetworkCategory Private
pause
exit /b 0
