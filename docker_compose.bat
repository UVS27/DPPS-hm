@echo off
chcp 65001 >nul

set "PYTHON_EXE=python"

set "SCRIPT=docker\cntr_compose.py"

if not exist "%SCRIPT%" (
    echo Скрипт %SCRIPT% не найден!
    pause
    exit /b
)

:MENU
echo ================================
echo Управление Docker Compose
echo ================================
echo B - Cборка образов Built
echo U - Поднять Compose
echo D - Опустить Compose
echo Q - Выход
set /p choice=Выберите действие:

set choice=%choice:~0,1%
set choice=%choice:~0,1%
set choice=%choice:~0,1%
set choice=%choice:~0,1%

if /I "%choice%"=="U" (
    %PYTHON_EXE% "%SCRIPT%" U
    goto MENU
) else if /I "%choice%"=="D" (
    %PYTHON_EXE% "%SCRIPT%" D
    goto MENU
) else if /I "%choice%"=="B" (
    %PYTHON_EXE% "%SCRIPT%" B
    goto MENU
) else if /I "%choice%"=="Q" (
    exit /b
) else (
    echo Неверный ввод, попробуйте снова.
    goto MENU
)