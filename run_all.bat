@echo off
title Iniciar North Comercial
echo ===================================================
echo   INICIANDO ENTORNO DE DESARROLLO - NORTH COMERCIAL
echo ===================================================
echo.

:: 1. Iniciar el servidor de desarrollo Django en el puerto 8001
echo [+] Iniciando Servidor Web Django en puerto 8001...
start "Servidor Web Django" cmd /k ".\.venv\Scripts\activate && python manage.py runserver 8001"

:: 2. Iniciar el cluster de tareas de fondo Django-Q
echo [+] Iniciando Procesador de Tareas (Django-Q)...
start "Worker Django-Q" cmd /k ".\.venv\Scripts\activate && python manage.py qcluster"

echo.
echo ===================================================
echo [OK] Procesos iniciados en ventanas independientes.
echo      Puedes cerrar esta ventana.
echo ===================================================
pause
