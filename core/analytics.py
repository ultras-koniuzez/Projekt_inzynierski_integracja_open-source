import time
import os
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from sqlalchemy import text
import subprocess
import shutil
import psutil # <--- NOWOŚĆ

# Silniki GIS
from osgeo import gdal
from qgis.core import QgsVectorLayer, QgsVectorFileWriter
from core.db_iface import PostGISConnector

try: import rasterio
except: rasterio = None
try: import laspy
except: laspy = None

gdal.UseExceptions()

class PerformanceTester:
    def __init__(self, db_conn_string=None):
        self.db = PostGISConnector(db_conn_string) if db_conn_string else None
        if self.db:
            try: self.db.connect()
            except: self.db = None

    # --- METODA POMIAROWA (NOWOŚĆ) ---
    def _measure(self, func, *args, **kwargs):
        """
        Mierzy czas [s] i przyrost pamięci RAM [MB].
        """
        process = psutil.Process(os.getpid())
        
        # Wymuszamy Garbage Collection przed testem dla czystości
        import gc
        gc.collect()
        
        mem_before = process.memory_info().rss / (1024 * 1024) # MB
        t0 = time.perf_counter()
        
        try:
            func(*args, **kwargs)
        except Exception as e:
            print(f"Błąd w teście: {e}")
            return None, None # Błąd
            
        t1 = time.perf_counter()
        mem_after = process.memory_info().rss / (1024 * 1024) # MB
        
        time_diff = t1 - t0
        # RAM może spaść (jeśli GC zadziała), więc bierzemy max(0, diff)
        mem_diff = max(0.0, mem_after - mem_before)
        
        return time_diff, mem_diff

    # --- POMOCNICZE ---
    def _get_safe_temp_path(self, path, suffix):
        base, ext = os.path.splitext(path)
        return f"{base}_{suffix}{ext}"

    def _cleanup(self, path):
        if not path or not os.path.exists(path): return
        try:
            if os.path.isdir(path): shutil.rmtree(path)
            else:
                if path.lower().endswith(".shp"):
                    base = os.path.splitext(path)[0]
                    for ext in [".shx", ".dbf", ".prj", ".cpg"]:
                        try: os.remove(f"{base}{ext}")
                        except: pass
                os.remove(path)
        except: pass

    def _generate_random_points(self, extent, count=30000):
        minx, miny, maxx, maxy = extent
        x = np.random.uniform(minx, maxx, count)
        y = np.random.uniform(miny, maxy, count)
        return gpd.GeoDataFrame({'geometry': gpd.points_from_xy(x, y)})

    # =================================================================
    # 1. GRUPA WEKTOROWA
    # =================================================================

    def bench_vector_io_read(self, vector_path):
        results = []
        base = os.path.splitext(vector_path)[0]
        formats = {
            "GeoPackage": (base + "_t.gpkg", "GPKG"),
            "Shapefile": (base + "_t.shp", "ESRI Shapefile"),
            "GeoJSON": (base + "_t.geojson", "GeoJSON")
        }
        src = QgsVectorLayer(vector_path, "src", "ogr")
        
        valid_files = []
        for name, (path, driver) in formats.items():
            self._cleanup(path)
            QgsVectorFileWriter.writeAsVectorFormat(src, path, "UTF-8", src.crs(), driver)
            if os.path.exists(path): valid_files.append((name, path))

        print("--- TEST 1: I/O READ ---")
        for name, path in valid_files:
            # Lambda pozwala przekazać funkcję z argumentami do _measure
            dt, mem = self._measure(lambda: gpd.read_file(path))
            if dt is not None:
                results.append({"Nazwa": name, "Czas [s]": dt, "RAM [MB]": mem})
            self._cleanup(path)
        return pd.DataFrame(results)

    def bench_vector_buffer(self, vector_path):
        results = []
        print("--- TEST 2: BUFFER ---")
        out_ogr = self._get_safe_temp_path(vector_path, "buf_ogr.shp")

        # A. GeoPandas
        def run_gpd():
            gdf = gpd.read_file(vector_path)
            _ = gdf.buffer(100)
            
        dt, mem = self._measure(run_gpd)
        if dt: results.append({"Nazwa": "GeoPandas (RAM)", "Czas [s]": dt, "RAM [MB]": mem})

        # B. OGR
        from core.processing import vector_buffer
        self._cleanup(out_ogr)
        dt, mem = self._measure(vector_buffer, vector_path, out_ogr, 100)
        if dt: results.append({"Nazwa": "OGR (Disk)", "Czas [s]": dt, "RAM [MB]": mem}) # OGR zużywa mało RAMu w Pythonie!
        
        self._cleanup(out_ogr)
        return pd.DataFrame(results)

    def bench_vector_spatial_join(self, vector_path, num_points=30000):
        results = []
        print(f"--- TEST 3: SPATIAL JOIN ---")
        try:
            poly = gpd.read_file(vector_path)
            pts = self._generate_random_points(poly.total_bounds, num_points)
            pts.set_crs(poly.crs, inplace=True)
            temp_pts = self._get_safe_temp_path(vector_path, "pts.gpkg")
            pts.to_file(temp_pts)
        except: return pd.DataFrame()

        # A. GeoPandas
        dt, mem = self._measure(lambda: gpd.sjoin(pts, poly, predicate='intersects'))
        if dt: results.append({"Nazwa": "GeoPandas (R-Tree)", "Czas [s]": dt, "RAM [MB]": mem})

        # B. PostGIS
        if self.db:
            try:
                self.db.import_with_ogr2ogr(vector_path, table_name="b_poly", overwrite=True)
                self.db.import_with_ogr2ogr(temp_pts, table_name="b_pts", overwrite=True)
                
                def run_sql():
                    with self.db.engine.connect() as conn:
                        conn.execute(text("SELECT count(*) FROM b_pts a JOIN b_poly b ON ST_Intersects(a.geom, b.geom)"))
                
                dt, mem = self._measure(run_sql)
                if dt: results.append({"Nazwa": "PostGIS (SQL)", "Czas [s]": dt, "RAM [MB]": mem})
            except: pass
        
        self._cleanup(temp_pts)
        return pd.DataFrame(results)

    def bench_vector_reprojection(self, vector_path):
        results = []
        print("--- TEST 4: REPROJEKCJA ---")
        out_ogr = self._get_safe_temp_path(vector_path, "reproj.gpkg")
        target = "EPSG:3857"

        # A. OGR
        cmd = ["ogr2ogr", "-f", "GPKG", "-t_srs", target, out_ogr, vector_path]
        si = subprocess.STARTUPINFO(); si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        dt, mem = self._measure(subprocess.run, cmd, startupinfo=si)
        if dt: results.append({"Nazwa": "ogr2ogr (System)", "Czas [s]": dt, "RAM [MB]": mem})

        # B. GeoPandas
        def run_gpd():
            g = gpd.read_file(vector_path)
            _ = g.to_crs(target)
            
        dt, mem = self._measure(run_gpd)
        if dt: results.append({"Nazwa": "GeoPandas (RAM)", "Czas [s]": dt, "RAM [MB]": mem})
        
        self._cleanup(out_ogr)
        return pd.DataFrame(results)

    def bench_vector_attribute_filter(self, vector_path):
        results = []
        print("--- TEST 5: FILTROWANIE ---")
        out_ogr = self._get_safe_temp_path(vector_path, "filter.shp")
        
        # A. Pandas
        def run_pd():
            g = gpd.read_file(vector_path)
            _ = g.iloc[:int(len(g)/2)]
        dt, mem = self._measure(run_pd)
        if dt: results.append({"Nazwa": "Pandas (RAM)", "Czas [s]": dt, "RAM [MB]": mem})

        # B. OGR
        cmd = ["ogr2ogr", "-f", "ESRI Shapefile", "-where", "FID < 1000", out_ogr, vector_path]
        si = subprocess.STARTUPINFO(); si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        dt, mem = self._measure(subprocess.run, cmd, startupinfo=si)
        if dt: results.append({"Nazwa": "OGR (Disk)", "Czas [s]": dt, "RAM [MB]": mem})
        
        self._cleanup(out_ogr)
        return pd.DataFrame(results)

    def bench_vector_iteration(self, vector_path):
        results = []
        print("--- TEST 6: ITERACJA ---")
        try:
            gdf = gpd.read_file(vector_path)
            
            # A. Wektoryzacja
            dt, mem = self._measure(lambda: gdf.geometry.centroid)
            if dt: results.append({"Nazwa": "Vectorized (C)", "Czas [s]": dt, "RAM [MB]": mem})
            
            # B. Pętla
            dt, mem = self._measure(lambda: [g.centroid for g in gdf.geometry])
            if dt: results.append({"Nazwa": "Loop (Python)", "Czas [s]": dt, "RAM [MB]": mem})
        except: pass
        return pd.DataFrame(results)

    # =================================================================
    # 2. GRUPA RASTROWA
    # =================================================================

    def bench_raster_resample(self, raster_path):
        results = []
        print("--- TEST 7: RASTER WARP ---")
        out_gdal = self._get_safe_temp_path(raster_path, "warp.tif")
        
        # A. GDAL
        dt, mem = self._measure(gdal.Warp, out_gdal, raster_path, xRes=50, yRes=50)
        if dt: results.append({"Nazwa": "GDAL (C++)", "Czas [s]": dt, "RAM [MB]": mem})

        # B. Rasterio
        if rasterio:
            def run_rio():
                with rasterio.open(raster_path) as src:
                    _ = src.read(
                        out_shape=(src.count, int(src.height*0.5), int(src.width*0.5)),
                        resampling=rasterio.enums.Resampling.bilinear
                    )
            dt, mem = self._measure(run_rio)
            if dt: results.append({"Nazwa": "Rasterio (RAM)", "Czas [s]": dt, "RAM [MB]": mem})
            
        self._cleanup(out_gdal)
        return pd.DataFrame(results)

    def bench_raster_slope(self, raster_path):
        results = []
        print("--- TEST 8: SLOPE ---")
        out_gdal = self._get_safe_temp_path(raster_path, "slope.tif")

        # GDAL
        dt, mem = self._measure(gdal.DEMProcessing, out_gdal, raster_path, "slope")
        if dt: results.append({"Nazwa": "GDAL (C++)", "Czas [s]": dt, "RAM [MB]": mem})
        
        # NumPy
        if rasterio:
            def run_np():
                with rasterio.open(raster_path) as src:
                    arr = src.read(1)
                    px, py = np.gradient(arr)
                    _ = np.sqrt(px**2 + py**2)
            dt, mem = self._measure(run_np)
            if dt: results.append({"Nazwa": "NumPy (RAM)", "Czas [s]": dt, "RAM [MB]": mem})
        
        self._cleanup(out_gdal)
        return pd.DataFrame(results)

    def bench_raster_stats(self, raster_path):
        results = []
        print("--- TEST 9: STATYSTYKI ---")
        
        def run_gdal():
            ds = gdal.Open(raster_path)
            ds.GetRasterBand(1).ComputeStatistics(False)
        dt, mem = self._measure(run_gdal)
        if dt: results.append({"Nazwa": "GDAL Stats", "Czas [s]": dt, "RAM [MB]": mem})
        
        if rasterio:
            def run_np():
                with rasterio.open(raster_path) as src:
                    arr = src.read(1)
                    _ = (np.min(arr), np.max(arr))
            dt, mem = self._measure(run_np)
            if dt: results.append({"Nazwa": "NumPy Stats", "Czas [s]": dt, "RAM [MB]": mem})
            
        return pd.DataFrame(results)

    # =================================================================
    # 3. GRUPA DB
    # =================================================================

    def bench_db_import(self, vector_path):
        if not self.db: return pd.DataFrame()
        results = []
        
        # OGR
        dt, mem = self._measure(self.db.import_with_ogr2ogr, vector_path, table_name="bench_ogr", overwrite=True)
        if dt: results.append({"Nazwa": "ogr2ogr (Bin)", "Czas [s]": dt, "RAM [MB]": mem})

        # ORM
        def run_orm():
            gdf = gpd.read_file(vector_path)
            gdf.to_postgis("bench_gpd", self.db.engine, if_exists='replace')
        dt, mem = self._measure(run_orm)
        if dt: results.append({"Nazwa": "SQLAlchemy", "Czas [s]": dt, "RAM [MB]": mem})
            
        return pd.DataFrame(results)

    def bench_db_export(self, vector_path):
        if not self.db: return pd.DataFrame()
        results = []
        self.db.import_with_ogr2ogr(vector_path, table_name="bench_src", overwrite=True)
        out_shp = self._get_safe_temp_path(vector_path, "export.shp")
        
        # OGR
        self._cleanup(out_shp)
        uri = self.db.conn_string.replace("postgresql://", "")
        up, hd = uri.split("@"); u, p = up.split(":"); h, db = hd.rsplit("/", 1); hp = h.split(":") if ":" in h else (h, "5432")
        pg = f"PG:host={hp[0]} port={hp[1]} user={u} password={p} dbname={db}"
        cmd = ["ogr2ogr", "-f", "ESRI Shapefile", out_shp, pg, "bench_src"]
        si = subprocess.STARTUPINFO(); si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        dt, mem = self._measure(subprocess.run, cmd, startupinfo=si)
        if dt: results.append({"Nazwa": "ogr2ogr (Bin)", "Czas [s]": dt, "RAM [MB]": mem})
        
        # Pandas
        dt, mem = self._measure(gpd.read_postgis, "SELECT * FROM bench_src", self.db.engine, geom_col='geom')
        if dt: results.append({"Nazwa": "read_postgis", "Czas [s]": dt, "RAM [MB]": mem})

        self._cleanup(out_shp)
        return pd.DataFrame(results)

    # =================================================================
    # 4. GRUPA LIDAR
    # =================================================================

    def bench_lidar_info(self, las_path):
        results = []
        
        # PDAL
        cmd = ["pdal", "info", las_path, "--summary"]
        si = subprocess.STARTUPINFO(); si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        dt, mem = self._measure(subprocess.run, cmd, capture_output=True, startupinfo=si)
        if dt: results.append({"Nazwa": "PDAL (CLI)", "Czas [s]": dt, "RAM [MB]": mem})

        # Laspy
        if laspy:
            def run_las():
                with laspy.open(las_path) as f: _ = f.header.point_count
            dt, mem = self._measure(run_las)
            if dt: results.append({"Nazwa": "Laspy (RAM)", "Czas [s]": dt, "RAM [MB]": mem})
        
        return pd.DataFrame(results)

    def bench_lidar_filter(self, las_path):
        results = []
        out_las = self._get_safe_temp_path(las_path, "filter.las")

        # PDAL
        cmd = ["pdal", "translate", las_path, out_las, "-f", "range", "--filters.range.limits=Z[100:10000]"]
        si = subprocess.STARTUPINFO(); si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        dt, mem = self._measure(subprocess.run, cmd, startupinfo=si)
        if dt: results.append({"Nazwa": "PDAL (Pipe)", "Czas [s]": dt, "RAM [MB]": mem})

        # Laspy
        if laspy:
            def run_las():
                with laspy.open(las_path) as f:
                    las = f.read()
                    mask = las.z > 100
                    _ = las.points[mask]
            dt, mem = self._measure(run_las)
            if dt: results.append({"Nazwa": "Laspy (NumPy)", "Czas [s]": dt, "RAM [MB]": mem})
            
        self._cleanup(out_las)
        return pd.DataFrame(results)