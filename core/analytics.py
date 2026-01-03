import time, os, gc, psutil, subprocess, numpy as np, pandas as pd
import geopandas as gpd
import rasterio
from osgeo import gdal
from sqlalchemy import create_engine


class GISBenchmarkEngine:
    def __init__(self, db_conn=None):
        self.db_conn = db_conn
        self.process = psutil.Process(os.getpid())

    def _profile_task(self, label, func, *args, **kwargs):
        """Mierzy czas i RAM jednocześnie w jednym rygorze."""
        gc.collect() # Reset pamięci przed pomiarem
        mem_start = self.process.memory_info().rss / (1024 * 1024)
        t_start = time.perf_counter()
        
        func(*args, **kwargs)
        
        t_end = time.perf_counter()
        mem_end = self.process.memory_info().rss / (1024 * 1024)
        
        return {
            "Metoda": label,
            "Czas [s]": round(t_end - t_start, 4),
            "RAM [MB]": round(max(0.1, mem_end - mem_start), 2)
        }

    # --- 1. WEKTOR: ---
    def run_vector_repro(self, path):
        target = "EPSG:3857"
        results = []
        # GPD/PyProj
        results.append(self._profile_task("GeoPandas/PyProj", lambda: gpd.read_file(path).to_crs(target)))
        # OGR
        out = path.replace(".", "_re.")
        cmd = ["ogr2ogr", "-t_srs", target, out, path]
        results.append(self._profile_task("OGR/ogr2ogr", lambda: subprocess.run(cmd, capture_output=True, shell=True)))
        return pd.DataFrame(results)

    # --- 2. RASTER:  ---
    def run_raster_slope(self, path):
        results = []
        # GDAL (C++)
        out = path.replace(".", "_sl.")
        results.append(self._profile_task("GDAL (Native)", lambda: gdal.DEMProcessing(out, path, "slope")))
        # Rasterio/NumPy
        def rio_np():
            with rasterio.open(path) as src:
                arr = src.read(1)
                dx, dy = np.gradient(arr)
                _ = np.sqrt(dx**2 + dy**2)
        results.append(self._profile_task("Rasterio/NumPy", rio_np))
        return pd.DataFrame(results)

    # --- 3. LiDAR:  ---
    def run_lidar_filter(self, path):
        import laspy
        results = []
        # Laspy (Python/NumPy)
        def las_filter():
            with laspy.open(path) as f:
                las = f.read()
                _ = las.points[las.z > 100]
        results.append(self._profile_task("Laspy (NumPy)", las_filter))
        # PDAL (C++)
        out = path.replace(".", "_f.")
        cmd = ["pdal", "translate", path, out, "range", "--filters.range.limits=Z(100:)"]
        results.append(self._profile_task("PDAL (C++)", lambda: subprocess.run(cmd, capture_output=True, shell=True)))
        return pd.DataFrame(results)

    # --- 4. PostGIS:  ---
    def run_db_deployment(self, path):
        if not self.db_conn: return pd.DataFrame()
        results = []
        engine = create_engine(self.db_conn)
        # SQLAlchemy/GeoPandas
        def gpd_sql():
            gdf = gpd.read_file(path)
            gdf.to_postgis("bench_gpd", engine, if_exists='replace')
        results.append(self._profile_task("SQLAlchemy/GPD", gpd_sql))
        # OGR Deployment
        uri = self.db_conn.replace("postgresql://", "PG:").replace("@", " ").replace("/", " dbname=")
        cmd = ["ogr2ogr", "-f", "PostgreSQL", uri, path, "-nln", "bench_ogr", "-overwrite"]
        results.append(self._profile_task("OGR (ogr2ogr)", lambda: subprocess.run(cmd, capture_output=True, shell=True)))
        return pd.DataFrame(results)