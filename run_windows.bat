@echo off
SET OSGEO4W_ROOT=C:\Program Files\QGIS 3.40.13

REM 1. Uruchomienie środowiska OSGeo4W (ustawia GDAL, PROJ, QT)
call "%OSGEO4W_ROOT%\bin\o4w_env.bat"

REM 2. Ustawienie ścieżek dla QGIS
SET PATH=%OSGEO4W_ROOT%\apps\qgis-ltr\bin;%PATH%
SET PYTHONPATH=%OSGEO4W_ROOT%\apps\qgis\python;%PYTHONPATH%

REM 3. Ustawienie ścieżek dla GRASS (opcjonalnie)
SET GISBASE=%OSGEO4W_ROOT%\apps\grass\grass84
SET PATH=%GISBASE%\lib;%PATH%
SET PYTHONPATH=%GISBASE%\etc\python;%PYTHONPATH%

REM 4. Uruchomienie projektu przez python-qgis.bat 
echo Uruchamianie aplikacji na silniku QGIS...
REM 5. W tym przypadku, w zależności od wersji może być plik python-qgis.bat lub python-qgis-ltr.bat
call "%OSGEO4W_ROOT%\bin\python-qgis-ltr.bat" app.py 

pause
