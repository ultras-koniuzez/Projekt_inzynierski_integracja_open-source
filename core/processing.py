import os
import sys
import subprocess
from osgeo import gdal, ogr, osr
import json

# Biblioteki Pythonowe (opcjonalne)
try:
    import rasterio
    import geopandas as gpd
except ImportError:
    rasterio = None
    gpd = None

gdal.UseExceptions()

# =============================================================================
# SEKCJA 1: ANALIZY RASTROWE (GDAL)
# =============================================================================

def compute_slope_raster(src_path, out_path, z_factor=1.0):
    """
    Oblicza nachylenie.
    z_factor: Mnożnik wysokości.
              Dla układów metrycznych (PUWG) = 1.0
              Dla WGS84 (stopnie) ~= 111120.0
    """
    print(f"[GDAL] Slope (Z-Factor={z_factor})...")
    try:
        options = gdal.DEMProcessingOptions(
            format="GTiff", 
            computeEdges=True,
            slopeFormat="degree",
            scale=z_factor 
        )
        gdal.DEMProcessing(out_path, src_path, "slope", options=options)
        print(f" Wynik zapisano: {out_path}")
    except RuntimeError as e:
        print(f" Błąd GDAL Slope: {e}")
        raise e

def compute_aspect_raster(src_path, out_path, z_factor=1.0):
    """Oblicza ekspozycję."""
    print(f"[GDAL] Aspect (Z-Factor={z_factor})...")
    try:
        options = gdal.DEMProcessingOptions(
            format="GTiff", 
            computeEdges=True,
            scale=z_factor
        )
        gdal.DEMProcessing(out_path, src_path, "aspect", options=options)
        print(f" Wynik zapisano: {out_path}")
    except RuntimeError as e:
        print(f" Błąd GDAL Aspect: {e}")
        raise e

def compute_hillshade_raster(src_path, out_path, z_factor=1.0, az=315.0, alt=45.0):
    """
    Cieniowanie z parametrami słońca.
    az: Azymut słońca (domyślnie 315 - NW)
    alt: Wysokość słońca (domyślnie 45 stopni)
    """
    print(f"[GDAL] Hillshade (Z={z_factor}, Az={az}, Alt={alt})...")
    try:
        options = gdal.DEMProcessingOptions(
            format="GTiff", 
            computeEdges=True,
            azimuth=az,
            altitude=alt,
            scale=z_factor
        )
        gdal.DEMProcessing(out_path, src_path, "hillshade", options=options)
        print(f"✅ Wynik zapisano: {out_path}")
    except RuntimeError as e:
        print(f"❌ Błąd GDAL Hillshade: {e}")
        raise e

# =============================================================================
# SEKCJA 2: ANALIZY WEKTOROWE
# =============================================================================

def generate_contours(src_path, out_path, interval=10.0, attr_name="ELEV"):
    print(f"[GDAL] Warstwice co {interval}m...")
    ds = None
    out_ds = None
    try:
        ds = gdal.Open(src_path)
        band = ds.GetRasterBand(1)
        
        # NoData logic
        no_data_val = band.GetNoDataValue()
        has_no_data = 1 if no_data_val is not None else 0
        if not has_no_data: no_data_val = 0.0

        srs = osr.SpatialReference()
        proj = ds.GetProjection()
        if proj: srs.ImportFromWkt(proj)

        # Driver selection
        driver_name = "GPKG" if out_path.lower().endswith(".gpkg") else "ESRI Shapefile"
        drv = ogr.GetDriverByName(driver_name)
        
        if os.path.exists(out_path):
            drv.DeleteDataSource(out_path)
            
        out_ds = drv.CreateDataSource(out_path)
        layer_name = os.path.splitext(os.path.basename(out_path))[0]
        out_layer = out_ds.CreateLayer(layer_name, srs)
        
        field_defn = ogr.FieldDefn(attr_name, ogr.OFTReal)
        out_layer.CreateField(field_defn)
        
        gdal.ContourGenerate(band, interval, 0.0, [], has_no_data, no_data_val, out_layer, -1, 0)
        print(f"✅ Warstwice gotowe.")
    except Exception as e:
        print(f"❌ Błąd Contour: {e}")
        raise e
    finally:
        out_ds = None
        ds = None

def vector_buffer(src_path, out_path, distance):
    print(f"[OGR] Bufor {distance}m...")
    # ... (Kod bufora bez zmian - jest poprawny) ...
    # Skopiuj implementację z poprzedniego pliku lub zostaw, jeśli masz kopię
    # Wklejam skróconą wersję dla pewności:
    src_ds = ogr.Open(src_path)
    layer = src_ds.GetLayer()
    driver = ogr.GetDriverByName("ESRI Shapefile")
    if os.path.exists(out_path): driver.DeleteDataSource(out_path)
    out_ds = driver.CreateDataSource(out_path)
    out_layer = out_ds.CreateLayer("buffer", layer.GetSpatialRef(), ogr.wkbPolygon)
    
    feature_defn = out_layer.GetLayerDefn()
    for feat in layer:
        geom = feat.GetGeometryRef()
        if geom:
            buf = geom.Buffer(distance)
            out_feat = ogr.Feature(feature_defn)
            out_feat.SetGeometry(buf)
            out_layer.CreateFeature(out_feat)
            out_feat = None
    out_ds = None

def clip_vector_geopandas(src_path, mask_path, out_path):
    if not gpd: raise ImportError("Brak GeoPandas")
    print("[GeoPandas] Clip...")
    gdf = gpd.read_file(src_path)
    mask = gpd.read_file(mask_path)
    if gdf.crs != mask.crs: mask = mask.to_crs(gdf.crs)
    clipped = gpd.clip(gdf, mask)
    clipped.to_file(out_path)

def centroids_geopandas(src_path, out_path):
    if not gpd: raise ImportError("Brak GeoPandas")
    print("[GeoPandas] Centroids...")
    gdf = gpd.read_file(src_path)
    gdf['geometry'] = gdf.geometry.centroid
    gdf.to_file(out_path)
    
def pdal_info(las_path):
    """
    Pobiera pełne statystyki pliku LAS (skanuje punkty, żeby mieć pewne min/max).
    """
    print(f"[PDAL] Info (skanowanie punktów): {las_path}")
    # ZMIANA: Używamy --stats zamiast --summary. 
    # To trwa chwilę dłużej, ale gwarantuje poprawne współrzędne nawet przy zepsutym nagłówku.
    cmd = ["pdal", "info", las_path, "--stats"]
    
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    
    # Uruchamiamy proces
    result = subprocess.run(cmd, capture_output=True, text=True, startupinfo=si)
    
    if result.returncode != 0:
        raise RuntimeError(f"PDAL Error: {result.stderr}")
        
    return result.stdout

def _run_pdal_pipeline(pipeline_json):
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp:
        json.dump(pipeline_json, tmp); tmp_path = tmp.name
    try:
        cmd = ["pdal", "pipeline", tmp_path]
        si = subprocess.STARTUPINFO(); si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        res = subprocess.run(cmd, capture_output=True, text=True, startupinfo=si)
        if res.returncode != 0: raise RuntimeError(res.stderr)
    finally:
        if os.path.exists(tmp_path): os.remove(tmp_path)

def pdal_generate_dsm(las_path, out_tif, resolution=1.0):
    """Generuje DSM (Max Z)."""
    print(f"[PDAL] Generowanie DSM...")
    pipeline = [
        {"type": "readers.las", "filename": las_path},
        {
            "type": "writers.gdal",
            "filename": out_tif,
            "resolution": resolution,
            "output_type": "max",
            "data_type": "float32",
            "gdalopts": "COMPRESS=DEFLATE"
        }
    ]
    _run_pdal_pipeline(pipeline)

def pdal_generate_dtm(las_path, out_tif, resolution=1.0):
    """Generuje DTM (Ground Filter)."""
    print(f"[PDAL] Generowanie DTM...")
    pipeline = [
        {"type": "readers.las", "filename": las_path},
        {"type": "filters.smrf", "ignore": "Classification[7:7]"},
        {"type": "filters.range", "limits": "Classification[2:2]"},
        {
            "type": "writers.gdal",
            "filename": out_tif,
            "resolution": resolution,
            "output_type": "mean",
            "data_type": "float32",
            "gdalopts": "COMPRESS=DEFLATE"
        }
    ]
    _run_pdal_pipeline(pipeline)
def extract_by_attribute(src_path, out_path, column, value):
    """
    Filtruje warstwę wektorową (np. wybiera jedną dzielnicę).
    """
    if not gpd: raise ImportError("Brak GeoPandas")
    
    print(f"[GeoPandas] Wyodrębnianie: {column} = {value}...")
    
    try:
        gdf = gpd.read_file(src_path)
        
        # Sprawdzenie typu danych (czy wartość to liczba czy tekst)
        # GeoPandas wczytuje typy, ale musimy upewnić się, że porównujemy jabłka z jabłkami
        if gdf[column].dtype == 'object':
            value = str(value)
        else:
            try:
                value = float(value)
            except: pass # Zostawiamy jak jest
            
        # FILTROWANIE
        filtered_gdf = gdf[gdf[column] == value]
        
        if len(filtered_gdf) == 0:
            raise ValueError(f"Brak obiektów spełniających warunek {column}={value}")
            
        filtered_gdf.to_file(out_path)
        print(f"✅ Zapisano {len(filtered_gdf)} obiektów do: {out_path}")
        
    except Exception as e:
        print(f"❌ Błąd ekstrakcji: {e}")
        raise e
def validate_geometry(src_path):
    """
    Sprawdza poprawność topologiczną warstwy (QA/QC).
    Wymóg dla Mapy Numerycznej: Obiekty muszą być 'Valid'.
    Zwraca raport tekstowy.
    """
    if not gpd: return "Brak biblioteki GeoPandas."
    
    print(f"Walidacja geometrii: {src_path}")
    try:
        gdf = gpd.read_file(src_path)
        total = len(gdf)
        
        # Sprawdzenie poprawności (is_valid)
        # To funkcja silnika GEOS - bardzo szybka i dokładna
        invalid_mask = ~gdf.is_valid
        invalid_rows = gdf[invalid_mask]
        count_invalid = len(invalid_rows)
        
        report = f"--- RAPORT WALIDACJI ---\n"
        report += f"Plik: {os.path.basename(src_path)}\n"
        report += f"Liczba obiektów: {total}\n"
        report += f"Poprawne: {total - count_invalid}\n"
        report += f"Błędne: {count_invalid}\n"
        report += "-" * 30 + "\n"
        
        if count_invalid > 0:
            report += "SZCZEGÓŁY BŁĘDÓW:\n"
            # Wyciągamy powód błędu
            # (Shapely posiada explain_validity, ale w pandas używamy apply)
            from shapely.validation import explain_validity
            
            for idx, row in invalid_rows.iterrows():
                reason = explain_validity(row.geometry)
                report += f"ID {idx}: {reason}\n"
                if idx > 10: # Ograniczamy raport
                    report += "... i więcej ...\n"
                    break
            
            report += "\nZALECENIE: Użyj funkcji 'Napraw Geometrię' (buffer 0) w QGIS."
        else:
            report += "✅ WARSTWA POPRAWNA TOPOLOGICZNIE.\nMożna użyć do Mapy Numerycznej."
            
        return report

    except Exception as e:
        return f"Błąd walidacji: {e}"