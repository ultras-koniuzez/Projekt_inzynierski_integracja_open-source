# core/data_io.py
import geopandas as gpd
import rasterio
import pandas as pd
from shapely.geometry import Point
from pathlib import Path

def load_vector(path, sql=None):
    """
    Load vector layers into GeoDataFrame using geopandas.
    Optionally provide SQL for driver supporting it.
    """
    path = str(path)
    return gpd.read_file(path)

def load_raster(path):
    """
    Open raster with rasterio and return dataset object
    """
    src = rasterio.open(path)
    return src

def csv_to_gdf(path, xcol='lon', ycol='lat', crs='EPSG:4326'):
    df = pd.read_csv(path)
    gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df[xcol], df[ycol]), crs=crs)
    return gdf
