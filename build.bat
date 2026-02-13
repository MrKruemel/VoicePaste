@echo off
REM ==========================================================================
REM Build script for Voice-to-Summary Paste Tool
REM ==========================================================================
REM
REM Usage:
REM   build.bat              Build release .exe (windowed, UPX)
REM   build.bat release      Build release .exe (same as above)
REM   build.bat debug        Build debug .exe (console, no UPX, verbose)
REM   build.bat clean        Remove build artifacts (build/, dist/, __pycache__)
REM
REM Prerequisites:
REM   - Python 3.11+ on PATH
REM   - PyInstaller installed:  pip install pyinstaller
REM   - pip install -r requirements.txt
REM
REM Output:
REM   dist\VoicePaste.exe         (~100-150 MB)
REM   dist\config.example.toml    (copied alongside the .exe)
REM ==========================================================================

setlocal enabledelayedexpansion

REM -- Determine the script directory (project root) --
set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

REM -- Parse command-line argument --
set "BUILD_MODE=release"
if /i "%~1"=="clean"       goto :clean
if /i "%~1"=="debug"       set "BUILD_MODE=debug"
if /i "%~1"=="release"     set "BUILD_MODE=release"

REM ==========================================================================
REM  BUILD
REM ==========================================================================

echo.
echo ======================================================================
echo  VoicePaste Build (%BUILD_MODE%)
echo ======================================================================
echo.

REM -- Verify Python is available --
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found on PATH. Install Python 3.11+ and try again.
    exit /b 1
)

REM -- Verify PyInstaller is available --
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] PyInstaller not found. Install it with: pip install pyinstaller
    exit /b 1
)

REM -- Use the unified .spec file --
set "SPEC_FILE=%PROJECT_DIR%voice_paste.spec"
set "EXE_NAME=VoicePaste"

REM -- Verify the .spec file exists --
if not exist "!SPEC_FILE!" (
    echo [ERROR] !SPEC_FILE! not found.
    exit /b 1
)

REM -- Generate application icon --
echo [1/5] Generating application icon...
python "%PROJECT_DIR%assets\generate_icons.py"
if errorlevel 1 (
    echo [ERROR] Icon generation failed.
    exit /b 1
)
echo.

REM -- Clean previous build artifacts --
echo [2/5] Cleaning previous build...
if exist "%PROJECT_DIR%build"    rmdir /s /q "%PROJECT_DIR%build"
if exist "%PROJECT_DIR%dist"     rmdir /s /q "%PROJECT_DIR%dist"

REM -- Run PyInstaller --
echo [3/5] Running PyInstaller (%BUILD_MODE% mode)...
echo.

if /i "%BUILD_MODE%"=="debug" (
    python -m PyInstaller "!SPEC_FILE!" -- --debug
) else (
    python -m PyInstaller "!SPEC_FILE!"
)

if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller build failed. Check the output above for details.
    echo         Hint: Check build\VoicePaste\warn-VoicePaste.txt for missing modules.
    exit /b 1
)

REM -- Verify the .exe was created --
if not exist "%PROJECT_DIR%dist\!EXE_NAME!.exe" (
    echo [ERROR] dist\!EXE_NAME!.exe was not created. Build may have failed silently.
    exit /b 1
)

REM -- Copy config.example.toml alongside the .exe --
echo [4/5] Copying config.example.toml to dist\...
if exist "%PROJECT_DIR%config.example.toml" (
    copy /y "%PROJECT_DIR%config.example.toml" "%PROJECT_DIR%dist\config.example.toml" >nul
    echo       dist\config.example.toml copied.
) else (
    echo [WARN] config.example.toml not found in project root. Skipping.
)

REM -- Report the result --
echo [5/5] Build complete.
echo.
echo ======================================================================
echo  Output:  dist\!EXE_NAME!.exe
echo.

REM -- Print file size --
for %%F in ("%PROJECT_DIR%dist\!EXE_NAME!.exe") do (
    set "SIZE_BYTES=%%~zF"
    set /a "SIZE_MB=!SIZE_BYTES! / 1048576"
    echo  Size:    !SIZE_BYTES! bytes (~!SIZE_MB! MB^)
)

echo.
echo  Config:  dist\config.example.toml
echo.
echo  To use:
echo    1. Copy dist\!EXE_NAME!.exe to your desired location.
echo    2. Copy dist\config.example.toml to config.toml next to the .exe.
echo    3. Edit config.toml and add your OpenAI API key.
echo    4. Run !EXE_NAME!.exe.
echo ======================================================================
echo.

exit /b 0

REM ==========================================================================
REM  CLEAN
REM ==========================================================================
:clean
echo.
echo ======================================================================
echo  VoicePaste Clean
echo ======================================================================
echo.

set "CLEANED=0"

if exist "%PROJECT_DIR%build" (
    echo  Removing build\...
    rmdir /s /q "%PROJECT_DIR%build"
    set /a "CLEANED+=1"
)

if exist "%PROJECT_DIR%dist" (
    echo  Removing dist\...
    rmdir /s /q "%PROJECT_DIR%dist"
    set /a "CLEANED+=1"
)

REM -- Clean __pycache__ directories --
for /d /r "%PROJECT_DIR%src" %%d in (__pycache__) do (
    if exist "%%d" (
        echo  Removing %%d...
        rmdir /s /q "%%d"
        set /a "CLEANED+=1"
    )
)

for /d /r "%PROJECT_DIR%tests" %%d in (__pycache__) do (
    if exist "%%d" (
        echo  Removing %%d...
        rmdir /s /q "%%d"
        set /a "CLEANED+=1"
    )
)

if !CLEANED! equ 0 (
    echo  Nothing to clean.
) else (
    echo.
    echo  Cleaned !CLEANED! directories.
)

echo.
echo ======================================================================
echo.

exit /b 0
