import time
import os
import pandas as pd
import geopandas as gpd
from qgis.core import QgsVectorLayer, QgsVectorFileWriter, QgsProject
from sqlalchemy import text
from core.processing import vector_buffer
from core.db_iface import PostGISConnector

class PerformanceTester:
    def __init__(self, db_conn_string=None):
        self.db = PostGISConnector(db_conn_string) if db_conn_string else None

    # --- TEST 1: PORÓWNANIE SILNIKÓW ---
    def run_engine_benchmark(self, vector_path, iterations=3):
        results = []
        filename = os.path.basename(vector_path)
        temp_dir = os.path.dirname(vector_path)
        temp_shp = os.path.join(temp_dir, "bench_temp.shp")
        
        print(f"--- TEST SILNIKÓW: {filename} ---")

        # 1. OGR
        try:
            times = []
            for _ in range(iterations):
                t0 = time.perf_counter()
                vector_buffer(vector_path, temp_shp, 100)
                times.append(time.perf_counter() - t0)
            results.append({"Kategoria": "Silnik", "Nazwa": "QGIS/OGR (C++)", "Czas [s]": sum(times)/len(times)})
        except Exception as e:
            print(f"Błąd OGR: {e}")

        # 2. GeoPandas
        try:
            times = []
            for _ in range(iterations):
                t0 = time.perf_counter()
                gdf = gpd.read_file(vector_path)
                res = gdf.buffer(100)
                res.to_file(temp_shp)
                times.append(time.perf_counter() - t0)
            results.append({"Kategoria": "Silnik", "Nazwa": "GeoPandas (Python)", "Czas [s]": sum(times)/len(times)})
        except Exception as e:
            print(f"Błąd GeoPandas: {e}")

        # 3. PostGIS
        if self.db:
            try:
                tbl = "bench_test"
                self.db.import_with_ogr2ogr(vector_path, table_name=tbl, overwrite=True)
                times = []
                for _ in range(iterations):
                    t0 = time.perf_counter()
                    with self.db.engine.connect() as conn:
                        conn.execute(text(f"DROP TABLE IF EXISTS {tbl}_res; CREATE TABLE {tbl}_res AS SELECT ST_Buffer(geom, 100) FROM {tbl};"))
                    times.append(time.perf_counter() - t0)
                results.append({"Kategoria": "Silnik", "Nazwa": "PostGIS (SQL)", "Czas [s]": sum(times)/len(times)})
            except Exception as e:
                print(f"Błąd PostGIS: {e}")

        return pd.DataFrame(results)

    # --- TEST 2: FORMATY PLIKÓW (NAPRAWIONY) ---
    def run_format_benchmark(self, vector_path):
        """Sprawdza czas odczytu dla SHP, GPKG, GeoJSON."""
        results = []
        
        base = os.path.splitext(vector_path)[0]
        formats = {
            "ESRI Shapefile": (base + "_test.shp", "ESRI Shapefile"),
            "GeoPackage": (base + "_test.gpkg", "GPKG"),
            "GeoJSON": (base + "_test.geojson", "GeoJSON")
        }
        
        src_layer = QgsVectorLayer(vector_path, "src", "ogr")
        if not src_layer.isValid():
            raise RuntimeError("Błąd wczytania warstwy źródłowej do testu.")

        print("--- PRZYGOTOWANIE PLIKÓW ---")
        created_files = []
        
        for name, (path, driver) in formats.items():
            # Usuń jeśli istnieje
            if os.path.exists(path):
                try: os.remove(path)
                except: pass
            
            # Zapisz
            err = QgsVectorFileWriter.writeAsVectorFormat(src_layer, path, "UTF-8", src_layer.crs(), driver)
            
            if err[0] == QgsVectorFileWriter.NoError:
                created_files.append((name, path))
            else:
                print(f"Błąd tworzenia pliku {name}: Kod {err[0]}")

        print("--- TEST ODCZYTU ---")
        for name, path in created_files:
            if not os.path.exists(path):
                print(f"Pominięto {name} - plik nie istnieje.")
                continue

            try:
                start = time.perf_counter()
                _ = gpd.read_file(path)
                duration = time.perf_counter() - start
                results.append({"Kategoria": "Format", "Nazwa": name, "Czas [s]": duration})
            except Exception as e:
                print(f"Błąd odczytu {name}: {e}")
            
            # Sprzątanie
            try: 
                # Dla SHP usuwamy też pliki pomocnicze
                if path.endswith(".shp"):
                    for ext in [".shx", ".dbf", ".prj"]:
                        aux = path.replace(".shp", ext)
                        if os.path.exists(aux): os.remove(aux)
                os.remove(path)
            except: pass

        return pd.DataFrame(results)

    # --- TEST 3: SKALOWALNOŚĆ ---
    def run_scalability_test(self, vector_path):
        results = []
        try:
            gdf = gpd.read_file(vector_path)
            total = len(gdf)
            percentages = [10, 25, 50, 75, 100]
            
            for p in percentages:
                sample_size = int(total * (p/100))
                if sample_size == 0: continue
                
                sample = gdf.iloc[:sample_size]
                start = time.perf_counter()
                _ = sample.buffer(100)
                duration = time.perf_counter() - start
                results.append({"Procent": p, "Liczba Obiektów": sample_size, "Czas [s]": duration})
        except Exception as e:
            print(f"Błąd skalowalności: {e}")
            
        return pd.DataFrame(results)