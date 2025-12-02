import time
import os
from qgis.core import QgsVectorLayer
from sqlalchemy import text
from core.processing import vector_buffer

# Próba importu GeoPandas (może go nie być)
try:
    import geopandas as gpd
    HAS_GPD = True
except ImportError:
    HAS_GPD = False

class Benchmarker:
    def __init__(self, db_connector=None):
        """
        db_connector: Instancja PostGISConnector (połączona)
        """
        self.db = db_connector

    def run_buffer_comparison(self, shapefile_path, distance=100, runs=1):
        """
        Uruchamia test buforowania 3 metodami i zwraca słownik z czasami.
        """
        results = {}
        layer_name = os.path.splitext(os.path.basename(shapefile_path))[0]
        
        # --- TEST 1: QGIS / GDAL (Native C++) ---
        # To jest metoda, którą masz w core/processing.py
        temp_out = f"temp_bench_qgis_{layer_name}.shp"
        try:
            start = time.perf_counter()
            for _ in range(runs):
                vector_buffer(shapefile_path, temp_out, distance)
            end = time.perf_counter()
            results['Native QGIS/OGR (C++)'] = (end - start) / runs
        except Exception as e:
            results['Native QGIS/OGR (C++)'] = f"Błąd: {e}"
        finally:
            if os.path.exists(temp_out):
                # Usuwanie plików tymczasowych (uproszczone)
                try: os.remove(temp_out) 
                except: pass

        # --- TEST 2: GeoPandas (Python Pure) ---
        if HAS_GPD:
            temp_out_gpd = f"temp_bench_gpd_{layer_name}.shp"
            try:
                start = time.perf_counter()
                for _ in range(runs):
                    gdf = gpd.read_file(shapefile_path)
                    # GeoPandas buffer
                    gdf.geometry = gdf.geometry.buffer(distance)
                    gdf.to_file(temp_out_gpd)
                end = time.perf_counter()
                results['GeoPandas (Python)'] = (end - start) / runs
            except Exception as e:
                results['GeoPandas (Python)'] = f"Błąd: {e}"
        else:
            results['GeoPandas (Python)'] = "Brak biblioteki"

        # --- TEST 3: PostGIS (Server-side SQL) ---
        if self.db and self.db.engine:
            try:
                # 1. Import (nie liczymy tego do czasu obliczeń, bo dane mogą już tam być)
                # Używamy OGR2OGR bo jest szybki
                bench_table = f"bench_{layer_name.lower()}"
                self.db.import_with_ogr2ogr(shapefile_path, table_name=bench_table, overwrite=True, target_srid=3857) # Wymuszamy metry
                
                start = time.perf_counter()
                for _ in range(runs):
                    query = text(f"""
                        DROP TABLE IF EXISTS {bench_table}_result;
                        CREATE TABLE {bench_table}_result AS 
                        SELECT ST_Buffer(geom, {distance}) as geom 
                        FROM {bench_table};
                    """)
                    with self.db.engine.connect() as conn:
                        conn.execute(query)
                end = time.perf_counter()
                results['PostGIS (SQL Server)'] = (end - start) / runs
            except Exception as e:
                results['PostGIS (SQL Server)'] = f"Błąd DB: {e}"
        else:
            results['PostGIS (SQL Server)'] = "Brak połączenia z DB"

        return results