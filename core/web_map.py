import os
import folium
import geopandas as gpd
import pandas as pd
from folium.plugins import MarkerCluster, MousePosition, Fullscreen
from osgeo import gdal
import os
import json
import folium
import numpy as np
import geopandas as gpd
import pandas as pd
from folium.plugins import MarkerCluster
from osgeo import gdal

class WebMapGenerator:
    def __init__(self, data_dir):
        self.data_dir = os.path.abspath(data_dir)
        self.cache_dir = os.path.join(self.data_dir, "web_cache")
        if not os.path.exists(self.cache_dir): os.makedirs(self.cache_dir)


        self.m = folium.Map(location=[51.75, 18.09], zoom_start=12, tiles="OpenStreetMap")
        
        # DODATEK CSS: Stałe etykiety bez ramek
        halo_css = """
        <style>
        .leaflet-tooltip-pane .halo-label {
            background-color: transparent !important;
            border: none !important;
            box-shadow: none !important;
            font-weight: bold;
            color: black;
            font-size: 11px;
            text-shadow: -1.5px -1.5px 0 #fff, 1.5px -1.5px 0 #fff, -1.5px 1.5px 0 #fff, 1.5px 1.5px 0 #fff, 0px 1.5px 0 #fff, 0px -1.5px 0 #fff, 1.5px 0px 0 #fff, -1.5px 0px 0 #fff;
        }
        </style>
        """
        self.m.get_root().header.add_child(folium.Element(halo_css))

    def _is_cached_valid(self, src_path, cache_path):
        if not os.path.exists(cache_path): return False
        return os.path.getmtime(src_path) <= os.path.getmtime(cache_path)

    def add_vector_layer(self, vector_path, layer_name, style_params=None):
        import os, json
        if not os.path.exists(vector_path): return False
        if not style_params: style_params = {}

        s_color = style_params.get('color', '#3388ff')
        s_weight = style_params.get('weight', 2)
        s_svg_url = style_params.get('svgUrl')
        s_size = style_params.get('weight', 30)
        label_field = style_params.get('labelField')

        try:
            gdf = gpd.read_file(vector_path)
            if gdf.empty: return False
            if 'geom' in gdf.columns: gdf.set_geometry('geom', inplace=True)
            gdf = gdf[gdf.geometry.notnull()].explode(index_parts=False)
            if gdf.crs != "EPSG:4326": gdf = gdf.to_crs("EPSG:4326")

            for col in gdf.columns:
                if pd.api.types.is_datetime64_any_dtype(gdf[col]) or gdf[col].dtype == 'object':
                    gdf[col] = gdf[col].astype(str).replace('None', '')

            dynamic_cols = [c for c in gdf.columns if c != 'geometry' and gdf[c].nunique() > 1]
            geojson_data = json.loads(gdf.to_json())
            actual_geom = geojson_data["features"][0]["geometry"]["type"]

            if "Point" in actual_geom:
                cluster = MarkerCluster(name=layer_name).add_to(self.m)
                for feat in geojson_data["features"]:
                    coords = feat["geometry"]["coordinates"]
                    props = feat["properties"]

                    rows = "".join([f"<tr><th>{c}</th><td>{props.get(c,'')}</td></tr>" for c in dynamic_cols])
                    popup = folium.Popup(f"<table style='font-size:11px;'>{rows}</table>", max_width=300)

                    l_text = str(props.get(label_field, "")) if label_field else ""

                    if s_svg_url:
  
                        abs_p = os.path.join(self.cache_dir, os.path.basename(s_svg_url))
                        icon = folium.CustomIcon(abs_p, icon_size=(s_size, s_size))
                        icon.options['iconUrl'] = s_svg_url
                        

                        tooltip = folium.Tooltip(l_text, permanent=True, direction='top', offset=[0,-10], class_name="halo-label") if l_text and l_text.lower() != 'none' else None
                        folium.Marker(location=[coords[1], coords[0]], icon=icon, popup=popup, tooltip=tooltip).add_to(cluster)
                    
                    elif l_text and l_text.lower() != 'none':

                        folium.Marker(
                            location=[coords[1], coords[0]],
                            icon=folium.DivIcon(
                                icon_size=(150,30),
                                icon_anchor=(75,15), 
                                html=f'<div class="halo-label" style="text-align:center;">{l_text}</div>'
                            ),
                            popup=popup
                        ).add_to(cluster)
                    else:
 
                        folium.CircleMarker(location=[coords[1], coords[0]], radius=2, color=s_color, fill=True).add_to(cluster)

            elif style_params.get('doubleLine'):
                folium.GeoJson(geojson_data, style_function=lambda x: {'color': style_params['color'], 'weight': style_params['weight'], 'opacity': 1.0}, control=False).add_to(self.m)
                folium.GeoJson(geojson_data, name=layer_name, style_function=lambda x: {'color': style_params['inner_color'], 'weight': style_params['inner_weight'], 'opacity': 1.0},
                    popup=folium.GeoJsonPopup(fields=dynamic_cols)).add_to(self.m)

            else:
                folium.GeoJson(geojson_data, name=layer_name, style_function=lambda x: {'color': s_color, 'fillColor': s_color, 'weight': s_weight, 'fillOpacity': 0.4},
                    popup=folium.GeoJsonPopup(fields=dynamic_cols)).add_to(self.m)

            return True
        except: return False

    def add_raster_layer(self, raster_path, layer_name):
        import numpy as np
        from PIL import Image
        import matplotlib.pyplot as plt

        base_name = os.path.splitext(os.path.basename(raster_path))[0]
        filename = f"{base_name}_colored.png"
        cache_path = os.path.join(self.cache_dir, filename)

        if not self._is_cached_valid(raster_path, cache_path):
            ds = gdal.Open(raster_path)

            w, h = ds.RasterXSize, ds.RasterYSize
            scale = min(2000/w, 2000/h) if max(w,h) > 2000 else 1.0
            data = ds.GetRasterBand(1).ReadAsArray(buf_xsize=int(w*scale), buf_ysize=int(h*scale)).astype(float)
            
            mask = (data != ds.GetRasterBand(1).GetNoDataValue()) & (data != 0) & (np.isfinite(data))
            if not np.any(mask): return False

            d_min, d_max = data[mask].min(), data[mask].max()
            norm = np.clip((data - d_min) / (d_max - d_min), 0, 1)
            
            cmap = plt.cm.colors.LinearSegmentedColormap.from_list("h", ["#267300", "#8BD100", "#FFFFBE", "#C88200", "#642800"])
            rgba = (cmap(norm) * 255).astype(np.uint8)
            rgba[:, :, 3] = (mask * 255).astype(np.uint8) 
            
            Image.fromarray(rgba, 'RGBA').save(cache_path)

        ds_info = gdal.Open(raster_path)
        warp = gdal.Warp("", ds_info, options=gdal.WarpOptions(dstSRS="EPSG:4326", format="VRT"))
        gt = warp.GetGeoTransform()
        wi, hi = warp.RasterXSize, warp.RasterYSize
        bounds = [[gt[3] + hi*gt[5], gt[0]], [gt[3], gt[0] + wi*gt[1]]]
        
        folium.raster_layers.ImageOverlay(name=layer_name, image=cache_path, bounds=bounds, opacity=0.8, zindex=1).add_to(self.m)
        return True

    
    def add_wms_layer(self, url, layers, name, format="image/png"):
        """
        Dodaje warstwę WMS bezpośrednio do mapy Leaflet.
        """
        try:
            folium.raster_layers.WmsTileLayer(
                url=url,
                layers=layers,
                name=name,
                fmt=format,
                transparent=True,       
                control=True,
                overlay=True,           
                attr=f"WMS: {name}"     
            ).add_to(self.m)
            return True
        except Exception as e:
            print(f"Błąd Folium WMS ({name}): {e}")
            return False
    def save_map(self, output_path):
        folium.LayerControl(collapsed=False).add_to(self.m)
        self.m.save(output_path)