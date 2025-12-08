@echo off
SET OSGEO4W_ROOT=C:\Program Files\QGIS 3.40.3

REM 1. Uruchomienie środowiska OSGeo4W (ustawia GDAL, PROJ, QT)
call "%OSGEO4W_ROOT%\bin\o4w_env.bat"

REM 2. Ustawienie ścieżek dla QGIS
SET PATH=%OSGEO4W_ROOT%\apps\qgis\bin;%PATH%
SET PYTHONPATH=%OSGEO4W_ROOT%\apps\qgis\python;%PYTHONPATH%
SET GRASS_DIR=%OSGEO4W_ROOT%\apps\grass\grass84

REM 3. Ustawienie ścieżek dla GRASS (opcjonalnie)
SET GISBASE=%OSGEO4W_ROOT%\apps\grass\grass84
SET PATH=%GISBASE%\bin;%GISBASE%\lib;%PATH%
SET PYTHONPATH=%GRASS_DIR%\etc\python;%PYTHONPATH%


REM 4. Uruchomienie projektu przez python-qgis.bat 
echo Uruchamianie aplikacji na silniku QGIS...
REM 5. W tym przypadku, w zależności od wersji może być plik python-qgis.bat lub python-qgis-ltr.bat
call "%OSGEO4W_ROOT%\bin\python-qgis.bat" app.py 

pause
