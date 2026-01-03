       
import os
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, LineString, Polygon, MultiPoint, MultiLineString, MultiPolygon

try:
    import pydeck as pdk
    HAS_PYDECK = True
except ImportError:
    HAS_PYDECK = False

try:
    import pydeck as pdk
    HAS_PYDECK = True
except ImportError:
    HAS_PYDECK = False

try:
    import rasterio
    from rasterio.warp import calculate_default_transform, reproject, Resampling
except ImportError:
    rasterio = None

try:
    import laspy
except ImportError:
    laspy = None

try:
    from pyproj import Transformer
except ImportError:
    Transformer = None

class WebMap3DGenerator:
    def __init__(self):
        self.layers = []
        self.osm_layer = pdk.Layer(
            "TileLayer",
            data="https://c.tile.openstreetmap.org/{z}/{x}/{y}.png",
            min_zoom=0,
            max_zoom=19,
            tileSize=256,
            opacity=1.0
        )
        self.layers.append(self.osm_layer)
        self.view_state = pdk.ViewState(latitude=51.75, longitude=18.09, zoom=12, pitch=45, bearing=0)

    def _update_view(self, lat, lon):
        self.view_state = pdk.ViewState(latitude=float(lat), longitude=float(lon), zoom=13, pitch=50, bearing=20)


    def add_vector_layer_3d(self, vector_path, layer_name, height_col=None, color=[255, 140, 0], base_elevation=0):
        if not HAS_PYDECK or not os.path.exists(vector_path): return False
        try:
            gdf = gpd.read_file(vector_path)
            if gdf.empty: return False
            if gdf.crs != "EPSG:4326": gdf = gdf.to_crs("EPSG:4326")


            final_h = 'elevation_value'
            

            if isinstance(height_col, (int, float)):

                gdf[final_h] = float(height_col)
            elif isinstance(height_col, str) and height_col in gdf.columns:

                gdf[final_h] = pd.to_numeric(gdf[height_col], errors='coerce').fillna(5).astype(float)
            else:

                gdf[final_h] = 10.0


            gdf[final_h] = gdf[final_h] - float(base_elevation)
            

            gdf[final_h] = gdf[final_h].apply(lambda x: max(0.1, x))

            geom_type = gdf.geometry.iloc[0].type

            if "Polygon" in geom_type:
                def get_coords(geom):
                    if geom.geom_type == 'Polygon':
                        return [list(geom.exterior.coords)]
                    elif geom.geom_type == 'MultiPolygon':
                        return [list(p.exterior.coords) for p in geom.geoms]
                
                gdf['coordinates'] = gdf.geometry.apply(get_coords)
                layer = pdk.Layer(
                    "PolygonLayer",
                    gdf,
                    get_polygon="coordinates",
                    get_fill_color=color + [180],
                    get_line_color=[0, 0, 0],
                    get_elevation=final_h, # <--- KLUCZ: przekazujemy nazwę 'elevation_value'
                    extruded=True,
                    pickable=True,
                )

            # --- B. PUNKTY ---
            elif "Point" in geom_type:
                gdf["lng"] = gdf.geometry.x
                gdf["lat"] = gdf.geometry.y
                layer = pdk.Layer(
                    "ColumnLayer",
                    gdf,
                    get_position=["lng", "lat"],
                    get_elevation=final_h, # <--- KLUCZ
                    radius=20,
                    get_fill_color=color + [255],
                    extruded=True,
                )

            # --- C. LINIE ---
            else:
                def get_path_coords(geom):
                    if geom.geom_type == 'LineString': return list(geom.coords)
                    elif geom.geom_type == 'MultiLineString': return [list(ls.coords) for ls in geom.geoms][0]
                
                gdf['path_coords'] = gdf.geometry.apply(get_path_coords)
                layer = pdk.Layer(
                    "PathLayer",
                    gdf,
                    get_path="path_coords",
                    get_color=color,
                    width_scale=5,
                    width_min_pixels=2,
                )

            self.layers.append(layer)
            self._update_view(gdf.geometry.centroid.y.mean(), gdf.geometry.centroid.x.mean())
            return True
        except Exception as e:
            print(f"❌ Błąd wektora 3D ({layer_name}): {e}")
            return False
    def _clean_df(self, df):
        """Konwertuje typy numpy na standardowe typy Python dla PyDeck"""
        for col in df.columns:
            if np.issubdtype(df[col].dtype, np.floating):
                df[col] = df[col].astype(float)
            elif np.issubdtype(df[col].dtype, np.integer):
                df[col] = df[col].astype(int)
        return df
    def add_raster_layer_3d(self, raster_path, layer_name, base_elevation=0, z_exaggeration=5):
        if not HAS_PYDECK or not rasterio or not os.path.exists(raster_path): return False
        try:
            with rasterio.open(raster_path) as src:

                dst_crs = 'EPSG:4326'
                transform, width, height = calculate_default_transform(
                    src.crs, dst_crs, src.width, src.height, *src.bounds)
                
                max_dim = 800 
                scale = min(max_dim/width, max_dim/height)
                out_w, out_h = max(1, int(width * scale)), max(1, int(height * scale))
                new_transform = transform * transform.scale((width / out_w), (height / out_h))
                
                destination = np.zeros((out_h, out_w), dtype=np.float32)
                reproject(
                    source=rasterio.band(src, 1),
                    destination=destination,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=new_transform,
                    dst_crs=dst_crs,
                    resampling=Resampling.bilinear
                )

                cols, rows = np.meshgrid(np.arange(out_w), np.arange(out_h))
                xs, ys = rasterio.transform.xy(new_transform, rows, cols)
                lon = np.array(xs).flatten()
                lat = np.array(ys).flatten()
                elev = destination.flatten()
                
                nodata = src.nodata if src.nodata is not None else -9999
                mask = (np.abs(elev) > 0.001) & (elev != nodata) & (np.isfinite(elev))
                
                if not np.any(mask): return False

                df = pd.DataFrame({
                    'lon': lon[mask], 
                    'lat': lat[mask], 
                    'z_raw': elev[mask] 
                })
                
                df['z_final'] = (df['z_raw'] - base_elevation) * z_exaggeration
                
                df = self._clean_df(df)

                z_min, z_max = df['z_raw'].min(), df['z_raw'].max()
                z_range = max(0.1, z_max - z_min)
                
                color_stops = [
                    (0.00, [38, 115, 0]),    
                    (0.25, [139, 209, 0]),   
                    (0.50, [255, 255, 190]), 
                    (0.75, [200, 130, 0]),   
                    (1.00, [100, 40, 0])     
                ]
                
                def get_interpolated_rgb(val):
                    n = (val - z_min) / z_range
                    n = max(0, min(1, n))
                    for i in range(len(color_stops) - 1):
                        low_n, low_rgb = color_stops[i]
                        high_n, high_rgb = color_stops[i+1]
                        if low_n <= n <= high_n:
                            t = (n - low_n) / (high_n - low_n)
                            return [
                                int(low_rgb[0] + (high_rgb[0]-low_rgb[0])*t),
                                int(low_rgb[1] + (high_rgb[1]-low_rgb[1])*t),
                                int(low_rgb[2] + (high_rgb[2]-low_rgb[2])*t)
                            ]
                    return color_stops[-1][1]

                colors = df['z_raw'].apply(get_interpolated_rgb).tolist()
                df[['r', 'g', 'b']] = pd.DataFrame(colors, index=df.index)

                layer = pdk.Layer(
                    "PointCloudLayer",
                    df,
                    get_position=["lon", "lat", "z_final"], 
                    get_color=["r", "g", "b"],
                    point_size=3.0, 
                    pickable=True,
                    opacity=1.0
                )
                self.layers.append(layer)
                self._update_view(df['lat'].mean(), df['lon'].mean())
                return True
        except Exception as e:
            print(f"Błąd ładowania rastra 3D: {e}")
            return False

    def add_lidar_layer_3d(self, las_path, layer_name, max_points=1000000, base_elevation=0, z_exaggeration=5):
        if not HAS_PYDECK or not laspy or not os.path.exists(las_path): return False
        try:

            las = laspy.read(las_path)
            total = len(las.points)
            
 
            if total > max_points:
                indices = np.random.choice(total, max_points, replace=False)
                x = np.array(las.x[indices], dtype='float64')
                y = np.array(las.y[indices], dtype='float64')
                z = np.array(las.z[indices], dtype='float64')
            else:
                x = np.array(las.x, dtype='float64')
                y = np.array(las.y, dtype='float64')
                z = np.array(las.z, dtype='float64')


            lon, lat = x, y
            if np.mean(x) > 180: 
                try:
                    from pyproj import Transformer

                    transformer = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True)
                    lon, lat = transformer.transform(x, y)
                except Exception as te:
                    print(f"Błąd transformacji LiDAR: {te}")


            z_min, z_max = z.min(), z.max()
            z_range = max(0.1, z_max - z_min)
            z_norm = (z - z_min) / z_range
            z_final = (z - base_elevation) * z_exaggeration

            r = (z_norm * 255).astype(int)
            b = ((1 - z_norm) * 255).astype(int)
            g = (z_norm * 40).astype(int) 


            df = pd.DataFrame({
                'lon': lon, 
                'lat': lat, 
                'z_val': z_final, 
                'r': r, 'g': g, 'b': b
            })
            df = self._clean_df(df) 

 
            layer = pdk.Layer(
                "PointCloudLayer",
                df,
                get_position=["lon", "lat", "z_val"],
                get_color=["r", "g", "b"],
                point_size=2,
                pickable=True,
                opacity=1.0
            )
            
            self.layers.append(layer)
            self._update_view(lat.mean(), lon.mean())
            print(f"[✓] LiDAR dodany: {layer_name} ({len(df)} punktów)")
            return True
        except Exception as e:
            print(f"Błąd ładowania LiDAR: {e}")
            import traceback
            traceback.print_exc()
            return False

    def save_map(self, output_path, map_style="osm"):
 
        if not HAS_PYDECK: return False
        try:

            style_map = {
                #"osm": None,  # PyDeck domyślnie używa OSM
                #"dark": "mapbox://styles/mapbox/dark-v10",
                "light": "mapbox://styles/mapbox/light-v10",
                "outdoors": "mapbox://styles/mapbox/outdoors-v12"
            }
            
            selected_style = style_map.get(map_style, None)
            
            if map_style == "osm" or selected_style is None:
                from pydeck.types import String
                
                osm_tile = pdk.Layer(
                    "TileLayer",
                    data="https://c.tile.openstreetmap.org/{z}/{x}/{y}.png",
                    min_zoom=0,
                    max_zoom=19,
                    tileSize=256,
                    opacity=1.0
                )
                
                all_layers = [osm_tile] + self.layers
                
                deck = pdk.Deck(
                    layers=all_layers,
                    initial_view_state=self.view_state,
                    tooltip={"text": "Z: {z} m"}
                )
            else:

                deck = pdk.Deck(
                    layers=self.layers,
                    initial_view_state=self.view_state,
                    map_style=selected_style,
                    tooltip={"text": "Z: {z} m"}
                )
            
            deck.to_html(output_path)
            print(f"[✓] Mapa zapisana: {output_path}")
            print(f"    Styl: {map_style} (OSM - bezpłatny)")
            return True
        except Exception as e:
            print(f"[!] Błąd zapisywania mapy: {e}")
            import traceback
            traceback.print_exc()
            return False