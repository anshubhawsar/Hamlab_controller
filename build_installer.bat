@echo off
setlocal

set APP_EXE_NAME=HAMLab
set APP_VERSION=v2_3

echo [1/5] Creating virtual environment if needed...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
)

call .venv\Scripts\activate.bat

echo [2/5] Installing dependencies...
python -m pip install --upgrade pip
pip install -r requirements.txt

echo [3/5] Generating app icon from image.png...
python -c "from PIL import Image; img=Image.open('image.png').convert('RGBA'); img.save('hamlab_icon.ico', format='ICO', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"
if %ERRORLEVEL% neq 0 (
    echo Failed to generate hamlab_icon.ico from image.png
    exit /b 1
)

echo [4/5] Building desktop executable with PyInstaller...
pyinstaller --noconfirm --clean --windowed --name %APP_EXE_NAME% --icon "hamlab_icon.ico" --add-data "docs\HAMLAB_Documentation.pdf;docs" --add-data "image.png;." hamlab.py

if not exist "dist\%APP_EXE_NAME%\docs" (
    mkdir "dist\%APP_EXE_NAME%\docs"
)
copy /Y "docs\HAMLAB_Documentation.pdf" "dist\%APP_EXE_NAME%\docs\HAMLAB_Documentation.pdf" >nul
copy /Y "image.png" "dist\%APP_EXE_NAME%\image.png" >nul

echo [5/5] Creating installer.exe with Inno Setup (if available)...
set "ISCC_CMD="
where ISCC >nul 2>nul
if %ERRORLEVEL%==0 set "ISCC_CMD=ISCC"
if "%ISCC_CMD%"=="" if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_CMD=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if "%ISCC_CMD%"=="" if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_CMD=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if "%ISCC_CMD%"=="" if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC_CMD=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

if "%ISCC_CMD%"=="" (
    echo Inno Setup compiler not found.
    echo Install Inno Setup from: https://jrsoftware.org/isinfo.php
    echo Then run this file again to generate installer.exe.
) else (
    if /I "%ISCC_CMD%"=="ISCC" (
        ISCC installer\HAMLAB_Setup.iss >nul 2>nul
    ) else (
        "%ISCC_CMD%" installer\HAMLAB_Setup.iss >nul 2>nul
    )
)

if %ERRORLEVEL%==0 (
    if exist "dist\installer\OneClick_Installer.exe" (
        copy /Y "dist\installer\OneClick_Installer.exe" "OneClick_Installer.exe" >nul
    )
    echo Installer created: dist\installer\OneClick_Installer.exe
    echo Top-level copy: OneClick_Installer.exe
) else (
    if not "%ISCC_CMD%"=="" (
        echo Inno Setup compile failed. Check installer\HAMLAB_Setup.iss
    )
)

echo Build complete.
endlocal
