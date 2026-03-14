@echo off
setlocal

set APP_EXE_NAME=HAMLab
set APP_VERSION=v2_3

echo [1/4] Creating virtual environment if needed...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
)

call .venv\Scripts\activate.bat

echo [2/4] Installing dependencies...
python -m pip install --upgrade pip
pip install -r requirements.txt

echo [3/4] Building desktop executable with PyInstaller...
pyinstaller --noconfirm --clean --windowed --name %APP_EXE_NAME% --add-data "docs\HAMLAB_Documentation.pdf;docs" hamlab.py

if not exist "dist\%APP_EXE_NAME%\docs" (
    mkdir "dist\%APP_EXE_NAME%\docs"
)
copy /Y "docs\HAMLAB_Documentation.pdf" "dist\%APP_EXE_NAME%\docs\HAMLAB_Documentation.pdf" >nul

echo [4/4] Creating installer.exe with Inno Setup (if available)...
set ISCC_CMD=ISCC
where %ISCC_CMD% >nul 2>nul
if %ERRORLEVEL% neq 0 (
    if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC_CMD="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
)
if %ERRORLEVEL% neq 0 (
    if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC_CMD="%ProgramFiles%\Inno Setup 6\ISCC.exe"
)
if %ERRORLEVEL% neq 0 (
    if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set ISCC_CMD="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
)

%ISCC_CMD% installer\HAMLAB_Setup.iss >nul 2>nul
if %ERRORLEVEL%==0 (
    echo Installer created in dist\installer\
) else (
    echo Inno Setup compiler not found.
    echo Install Inno Setup from: https://jrsoftware.org/isinfo.php
    echo Then run this file again to generate installer.exe.
)

echo Build complete.
endlocal
