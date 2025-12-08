import os
import folium
import geopandas as gpd
import pandas as pd
from folium.plugins import MarkerCluster, MousePosition, Fullscreen
from osgeo import gdal

class WebMapGenerator:
    def __init__(self, data_dir):
        """
        data_dir: Ścieżka do folderu 'dane'.
        """
        # Konwertujemy na ścieżkę absolutną (C:\Users\...) żeby nie było błędów
        self.data_dir = os.path.abspath(data_dir)
        self.cache_dir = os.path.join(self.data_dir, "web_cache")
        
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)

        # Startujemy z mapą
        self.m = folium.Map(location=[51.75, 18.09], zoom_start=12, control_scale = True, tiles="OpenStreetMap")
        # 2. Dodajemy inne podkłady (Basemaps)
        
        # A. Google Satellite Hybrid (Satelita + Drogi)
        folium.TileLayer(
            tiles='https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}',
            attr='Google',
            name='Google Hybrid',
            overlay=False, # To jest warstwa bazowa, nie nakładka
            control=True
        ).add_to(self.m)

        # B. Esri Satellite (Czysty satelita, bardzo dokładny)
        folium.TileLayer(
            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            attr='Esri',
            name='Esri Satellite',
            overlay=False,
            control=True
        ).add_to(self.m)

        # C. CartoDB Dark Matter (Idealne tło pod kolorowe analizy/rastry)
        folium.TileLayer(
            tiles='cartodbdark_matter', # Wbudowany skrót Folium
            attr='CartoDB',
            name='Ciemna Mapa (Dark)',
            overlay=False,
            control=True
        ).add_to(self.m)

        # D. Geoportal (Ortofotomapa Polski - WMS)
        # Uwaga: WMS w Leaflet działa trochę wolniej niż kafelki XYZ
        folium.WmsTileLayer(
            url='https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/StandardResolution',
            layers='Raster',
            name='Geoportal Ortofotomapa',
            fmt='image/jpeg',
            attr='GUGiK',
            overlay=False,
            control=True
        ).add_to(self.m)
        # Dodatki
        Fullscreen().add_to(self.m)
        MousePosition().add_to(self.m)
        #folium.control.Scale(position='bottomleft').add_to(self.m)

    def _is_cached_valid(self, src_path, cache_path):
        if not os.path.exists(cache_path): return False
        if os.path.getmtime(src_path) > os.path.getmtime(cache_path): return False
        return True

    def add_vector_layer(self, vector_path, layer_name, color="blue"):
        if not os.path.exists(vector_path): return False
        
        safe_name = "".join([c for c in layer_name if c.isalnum() or c in ('_','-')]).strip()
        filename = f"{safe_name}.geojson"
        
        # UŻYWAMY TYLKO ŚCIEŻKI ABSOLUTNEJ
        cache_path_abs = os.path.join(self.cache_dir, filename)

        cols = []
        d = ['LOKALNYID', 'BDOT500', 'KODKARTO10K', 'TERYT', 'OZNACZENIEZMIANY', 'PRZESTRZENNAZW', 'SKROTKARTOGRAFICZNY']
        # --- LOGIKA CACHE ---
        if self._is_cached_valid(vector_path, cache_path_abs):
            print(f"⚡ [CACHE] Używam: {layer_name}")
            try:
                # Czytamy nagłówek żeby znać pola do popupu
                # Używamy ścieżki absolutnej!
                gdf_head = gpd.read_file(cache_path_abs, rows=1)
                cols = [c for c in gdf_head.columns if c != 'geometry']
            except: 
                cols = []
        else:
            print(f"⚙️ [PROCESS] Generowanie: {layer_name}...")
            try:
                gdf = gpd.read_file(vector_path)
                if gdf.crs != "EPSG:4326": gdf = gdf.to_crs("EPSG:4326")
                
                # Optymalizacja geometrii
                #gdf['geometry'] = gdf.geometry.simplify(0.0001, preserve_topology=True)
                
                # Naprawa dat
                for col in gdf.columns:
                    if pd.api.types.is_datetime64_any_dtype(gdf[col]) or gdf[col].dtype == 'object':
                        try: gdf[col] = gdf[col].astype(str)
                        except: pass

                # Filtrowanie atrybutów
                all_cols = [c for c in gdf.columns if c != 'geometry']
                if len(gdf) > 1:
                    for col in all_cols:
                        if gdf[col].nunique() > 1: cols.append(col)
                    if not cols and all_cols: cols = all_cols[:1]
                else:
                    cols = all_cols

                # Zapis do cache (ścieżka absolutna)
                if os.path.exists(cache_path_abs): os.remove(cache_path_abs)
                gdf.to_file(cache_path_abs, driver="GeoJSON")
                
            except Exception as e:
                print(f"Błąd wektora {layer_name}: {e}")
                return False

        # --- DODANIE DO MAPY ---
        # Folium potrzebuje ścieżki absolutnej (wtedy sam wczyta plik i wpisze dane do HTML)
        # LUB stringa "file://...", ale podanie ścieżki C:/... działa najlepiej w trybie embed.
        
        # Ponieważ używamy folium.GeoJson z parametrem będącym ścieżką, 
        # Folium otworzy ten plik. Musi mieć pełną ścieżkę, żeby go znaleźć.
        
        path_for_folium = cache_path_abs.replace("\\", "/") # Fix dla Windowsa

        try:
            # 1. Hitbox
            folium.GeoJson(
                path_for_folium,
                name=f"{layer_name}_hitbox",
                style_function=lambda x: {'weight': 15, 'opacity': 0, 'fillOpacity': 0},
                popup=folium.GeoJsonPopup(fields=cols) if cols else None,
                control=False 
            ).add_to(self.m)

            # 2. Wizualizacja
            folium.GeoJson(
                path_for_folium,
                name=layer_name,
                style_function=lambda x: {'color': color, 'weight': 2, 'fillOpacity': 0.4},
                interactive=False
            ).add_to(self.m)
            
            return True
        except Exception as e:
            print(f"Błąd dodawania do Folium: {e}")
            return False

    def add_raster_layer(self, raster_path, layer_name):
        if not os.path.exists(raster_path): return False

        safe_name = "".join([c for c in layer_name if c.isalnum() or c in ('_','-')]).strip()
        filename = f"{safe_name}.png"
        
        # Ścieżki
        cache_path_abs = os.path.join(self.cache_dir, filename)
        
        # Dla rastrów (ImageOverlay) potrzebujemy URL relatywnego względem pliku HTML,
        # ponieważ to PRZEGLĄDARKA ładuje obrazek, a nie Python.
        # HTML będzie w folderze 'dane', a obrazek w 'dane/web_cache'.
        web_url_rel = f"web_cache/{filename}"

        # 1. Cache
        if not self._is_cached_valid(raster_path, cache_path_abs):
            print(f"⚙️ Konwersja rastra: {layer_name}")
            try:
                src_ds = gdal.Open(raster_path)
                dtype = src_ds.GetRasterBand(1).DataType
                opts = ["-of", "PNG"]
                if dtype != gdal.GDT_Byte: opts.extend(["-scale", "-ot", "Byte"])
                
                count = src_ds.RasterCount
                if count >= 3: opts.extend(["-b", "1", "-b", "2", "-b", "3"])
                elif count == 2: opts.extend(["-b", "1", "-b", "2"])
                else: opts.extend(["-b", "1"])

                warp_opts = gdal.WarpOptions(dstSRS="EPSG:4326", format="VRT")
                vrt_ds = gdal.Warp("", src_ds, options=warp_opts)
                gdal.Translate(cache_path_abs, vrt_ds, options=opts)
                vrt_ds = None; src_ds = None
            except Exception as e: 
                print(f"Błąd GDAL: {e}")
                return False
        else:
            print(f"⚡ Cache rastra: {layer_name}")

        # 2. Dodanie (Bounds)
        try:
            tmp = gdal.Warp("", raster_path, options=gdal.WarpOptions(dstSRS="EPSG:4326", format="VRT"))
            gt = tmp.GetGeoTransform()
            w, h = tmp.RasterXSize, tmp.RasterYSize
            # [[min_lat, min_lon], [max_lat, max_lon]]
            bounds = [[gt[3] + h*gt[5], gt[0]], [gt[3], gt[0] + w*gt[1]]]
            
            folium.raster_layers.ImageOverlay(
                name=layer_name,
                image=web_url_rel, # Tutaj musi być względna dla przeglądarki!
                bounds=bounds,
                opacity=0.7,
                interactive=True,
                zindex=1
            ).add_to(self.m)
            
            self.m.fit_bounds(bounds)
            return True
        except Exception as e: 
            print(f"Błąd ImageOverlay: {e}")
            return False
    def add_wms_layer(self, url, layers, name, format="image/png"):
        """
        Dodaje warstwę WMS bezpośrednio do mapy Leaflet.
        """
        try:
            print(f"🌍 Dodawanie WMS do WebMap: {name}")
            folium.raster_layers.WmsTileLayer(
                url=url,
                layers=layers,
                name=name,
                fmt=format,
                transparent=True,
                control=True,
                overlay=True,
                attr="WMS: " + name
            ).add_to(self.m)
            return True
        except Exception as e:
            print(f"Błąd WMS Web: {e}")
            return False
    def save_map(self, output_path):
        folium.LayerControl(collapsed=False).add_to(self.m)
        self.m.save(output_path)
        return output_path