@echo off
:: ============================================================
:: build_windows.bat
:: Compila dispenser_test_gui.py en un unico .exe para Windows
:: Requiere: Python 3.9+ instalado y en el PATH
:: ============================================================

echo [*] Verificando PyInstaller...
pip show pyinstaller >nul 2>&1
IF ERRORLEVEL 1 (
    echo [*] Instalando PyInstaller...
    pip install pyinstaller
    IF ERRORLEVEL 1 (
        echo [ERROR] No se pudo instalar PyInstaller. Abortando.
        pause
        exit /b 1
    )
)

echo [*] Verificando pyserial...
pip show pyserial >nul 2>&1
IF ERRORLEVEL 1 (
    echo [*] Instalando pyserial...
    pip install pyserial
)

echo [*] Compilando ejecutable...
cd /d "%~dp0"
pyinstaller dispenser_gui.spec --clean --noconfirm

IF ERRORLEVEL 1 (
    echo [ERROR] Fallo la compilacion. Revisa los mensajes anteriores.
    pause
    exit /b 1
)

echo.
echo [OK] Listo! El ejecutable esta en:
echo      %~dp0dist\dispenser_tester.exe
echo.
pause
