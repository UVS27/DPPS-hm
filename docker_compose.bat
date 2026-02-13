@echo off
chcp 65001 >nul

set "PYTHON_EXE=python"
set "SCRIPT=docker\cntr_compose.py"

if not exist "%SCRIPT%" (
    echo Скрипт %SCRIPT% не найден!
    pause
    exit /b 1
)

:MENU
echo ================================
echo Управление Docker Compose
echo ================================
echo U - Поднять Compose
echo S - Остановить (stop)
echo R - Запустить остановленные (start)
echo D - Удалить Compose (down)
echo B - Собрать образы
echo Q - Выход
set /p choice=Выберите действие:

set "choice=%choice:~0,1%"

if /I "%choice%"=="U" (
    %PYTHON_EXE% "%SCRIPT%" U
    goto MENU
) else if /I "%choice%"=="S" (
    %PYTHON_EXE% "%SCRIPT%" S
    goto MENU
) else if /I "%choice%"=="R" (
    %PYTHON_EXE% "%SCRIPT%" R
    goto MENU
) else if /I "%choice%"=="D" (
    %PYTHON_EXE% "%SCRIPT%" D
    goto MENU
) else if /I "%choice%"=="B" (
    %PYTHON_EXE% "%SCRIPT%" B
    goto MENU
) else if /I "%choice%"=="Q" (
    exit /b 0
) else (
    echo Неверный ввод, попробуйте снова.
    goto MENU
)
