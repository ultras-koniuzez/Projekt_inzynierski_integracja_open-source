@echo off

SET OSGEO4W_ROOT=C:\Program Files\QGIS 3.40.13

CALL "%OSGEO4W_ROOT%\bin\o4w_env.bat"


echo.
echo Instalacja wymaganych bibliotek Python dla GISMOOTH...
echo.
python -m pip install psycopg2-binary sqlalchemy pandas matplotlib seaborn openpyxl fiona geopandas rasterio pyproj shapely open3d laspy numpy

echo.
echo Instalacja zakonczona.
echo.