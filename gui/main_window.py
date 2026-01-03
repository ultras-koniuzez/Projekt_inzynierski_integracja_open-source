import os
import sys
import subprocess
import geopandas as gpd
import shutil
import http.server
import socketserver
import threading
from qgis.gui import QgsProjectionSelectionDialog, QgsScaleWidget
from qgis.PyQt import QtWidgets, QtGui, QtCore
from qgis.PyQt.QtCore import QVariant
from qgis.core import QgsApplication
try:
    import matplotlib
    import pandas as pd
    
    # Automatyczne wykrywanie backendu (Qt6 dla nowego QGIS, Qt5 dla starego)
    try:
        # Próbujemy załadować backend Qt6 (qtagg)
        import PyQt6
        matplotlib.use('qtagg') 
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    except ImportError:
        # Jeśli nie ma Qt6, próbujemy Qt5
        matplotlib.use('Qt5Agg') 
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

    from matplotlib.figure import Figure
    import matplotlib.pyplot as plt
    
    HAS_PLOTS = True
except Exception as e:
    print(f"Błąd ładowania wykresów: {e}")
    HAS_PLOTS = False
    FigureCanvas = None
plugin_path = os.path.join(QgsApplication.prefixPath(), "python", "plugins")
if plugin_path not in sys.path:
    sys.path.append(plugin_path)
# --- IMPORTY QGIS ---
from qgis.core import (
    QgsApplication,
    QgsProject, 
    QgsVectorLayer, 
    QgsRasterLayer, 
    QgsWkbTypes,
    QgsStyle,
    QgsSingleSymbolRenderer,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsSvgMarkerSymbolLayer,
    QgsFillSymbol,
    QgsVectorLayerSimpleLabeling,
    QgsTextFormat,
    QgsSymbol,
    QgsUnitTypes,
    QgsLayerTreeModel, 
    #QVariant,
    QgsVectorFileWriter,
    QgsVectorLayerCache,
    QgsPointCloudLayer,
    QgsCoordinateReferenceSystem, 
    QgsCoordinateTransform,
    QgsDataSourceUri,
    QgsRectangle,
    QgsLineSymbolLayer,
    QgsLineSymbol,
    QgsApplication
)
from qgis.gui import QgsMapCanvas, QgsLayerTreeMapCanvasBridge, QgsLayerTreeView, QgsAttributeTableView, QgsAttributeTableModel, QgsAttributeTableFilterModel, QgsMapToolIdentify, QgsSymbolSelectorDialog 
HAS_3D = False
try:
    from qgis.gui import Qgs3DMapCanvas
    from qgis._3d import (
        Qgs3DMapCanvas,
        Qgs3DMapSettings,
        QgsCameraPose,
        QgsVector3D,
        QgsDirectionalLightSettings
    )
    HAS_3D = True
except ImportError:
    HAS_3D = False
    print("Błąd: Moduł qgis._3d niedostępny. Upewnij się, że instalacja QGIS zawiera obsługę 3D.")
    
# --- KONFIGURACJA ENV ---
try:
    from dotenv import load_dotenv
    load_dotenv()
    if os.getenv("PG_USER"): PG_USER = os.getenv("PG_USER")
    if os.getenv("PG_PASS"): PG_PASS = os.getenv("PG_PASS")
    if os.getenv("PG_DB"): PG_DB = os.getenv("PG_DB")
except ImportError:
    pass

# --- IMPORTY CORE ---
try:
    from core.db_iface import PostGISConnector
except ImportError:
    PostGISConnector = None

try:
    from core.processing import (
        compute_slope_raster, vector_buffer, generate_contours,
        compute_aspect_raster, compute_hillshade_raster, 
        clip_vector_geopandas, centroids_geopandas,
        pdal_generate_dsm, pdal_generate_dtm, pdal_info,
        extract_by_attribute, clip_raster_gdal, convert_raster_to_jpg, polygon_to_line
    )
except ImportError:
    compute_slope_raster = vector_buffer = generate_contours = None
    compute_aspect_raster = compute_hillshade_raster = None
    clip_vector_geopandas = centroids_geopandas = None
    pdal_generate_dsm = pdal_generate_dtm = pdal_info = None
    extract_by_attribute = validate_geometry = clip_raster_gdal = None

'''try:
    from core.data_io import load_vector, load_raster
except ImportError:
    load_vector = load_raster = None'''

'''try:
    from core.benchmark import Benchmarker
except ImportError:
    Benchmarker = None'''

try:
    from core.map_tools import export_map_to_pdf, apply_basic_style, apply_raster_colormap, set_transparent_fill
except ImportError:
    export_map_to_pdf = apply_basic_style = apply_raster_colormap = set_transparent_fill = None

try:
    from core.workers import Worker
except ImportError:
    Worker = None
try:
    from core.analytics import GISBenchmarkEngine
except ImportError:
    GISBenchmarkEngine = None
try:
    import open3d as o3d
    import laspy
    import numpy as np
    HAS_OPEN3D = True
except ImportError:
    HAS_OPEN3D = False
    print("Brak biblioteki Open3D lub laspy. Zainstaluj: pip install open3d laspy")
try:
    from core.ows_client import OWSClient
except ImportError:
    OWSClient = None
try: 
    from core.web_map import WebMapGenerator
    HAS_FOLIUM = True
except ImportError:
    HAS_FOLIUM = False
    WebMapGenerator = None
try:
    from core.web_map_3d import WebMap3DGenerator
    HAS_PYDECK = True
except ImportError:
    HAS_PYDECK = False
    WebMap3DGenerator = None

APP_TITLE = "Projekt inżynierski na potrzeby pracy dyplomowej"

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1400, 850)
        
        self.db = None
        self.workers = []

        # === USTAWIANIE FOLDERU ROBOCZEGO (DANE) ===
        base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        self.data_dir = os.path.join(base_dir, "dane")
        if not os.path.exists(self.data_dir):
            try:
                os.makedirs(self.data_dir)
            except:
                self.data_dir = base_dir 
        
        self.terminal_cwd = self.data_dir

        # === 1. MAP CANVAS ===
        self.canvas = QgsMapCanvas()
        self.canvas.setCanvasColor(QtGui.QColor("white"))
        self.canvas.enableAntiAliasing(True)
        # Domyślnie Web Mercator dla podkładów
        target_crs = QgsCoordinateReferenceSystem("EPSG:3857")
        self.canvas.setDestinationCrs(target_crs)
        self.setCentralWidget(self.canvas)

        self.project = QgsProject.instance()
        self.root = self.project.layerTreeRoot()
        self.bridge = QgsLayerTreeMapCanvasBridge(self.root, self.canvas)

        # === 2. DOCK WARSTW ===
        self.layers_dock = QtWidgets.QDockWidget("Warstwy", self)
        self.layers_dock.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
        self.layer_tree_view = QgsLayerTreeView()
        self.layer_tree_model = QgsLayerTreeModel(self.root)
        self.layer_tree_model.setFlag(QgsLayerTreeModel.AllowNodeChangeVisibility, True)
        self.layer_tree_model.setFlag(QgsLayerTreeModel.AllowNodeReorder, True)
        self.layer_tree_model.setFlag(QgsLayerTreeModel.ShowLegend, True)
        self.layer_tree_model.setFlag(QgsLayerTreeModel.AllowNodeRename, True)
        self.layer_tree_view.setModel(self.layer_tree_model)
        self.layers_dock.setWidget(self.layer_tree_view)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.layers_dock)

        # === 3. DOCK STEROWANIA ===
        self.tools_dock = QtWidgets.QDockWidget("Panel Sterowania", self)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.tools_dock)
        self.tabs = QtWidgets.QTabWidget()
        self.tools_dock.setWidget(self.tabs)

        self.tab_data = QtWidgets.QWidget()
        self.tabs.addTab(self.tab_data, "📁 Dane")
        self._build_tab_data()

        self.tab_analysis = QtWidgets.QWidget()
        self.tabs.addTab(self.tab_analysis, "⚙ Analizy")
        self._build_tab_analysis()

        self.tab_db = QtWidgets.QWidget()
        self.tabs.addTab(self.tab_db, "🛢️ PostGIS")
        self._build_tab_db()

        self.tab_terminal = QtWidgets.QWidget()
        self.tabs.addTab(self.tab_terminal, ">_ Terminal GDAL")
        self._build_tab_terminal()
        
        self.tab_benchmark = QtWidgets.QWidget()
        self.tabs.addTab(self.tab_benchmark, "📊 Benchmark")
        self._build_tab_benchmark()

        self.tab_publish = QtWidgets.QWidget()
        self.tabs.addTab(self.tab_publish, "🌍 Mapy")
        self._build_tab_publish()

        self.status = self.statusBar()
        self.last_raster_layer = None
        self.last_vector_layer = None
        self.last_point_cloud_layer = None
        self.status = self.statusBar()
        self.server_thread = None # <--- NOWOŚĆ: Uchwyt do wątku serwera
        self.httpd = None         # <--- NOWOŚĆ: Uchwyt do serwera
        if self.db:
            self.db.connect()
        self.scale_widget = QgsScaleWidget(self)
        self.scale_widget.setMapCanvas(self.canvas)
        self.scale_widget.setShowCurrentScaleButton(True)
        self.scale_widget.scaleChanged.connect(self.canvas.zoomScale)
        self.status.addPermanentWidget(self.scale_widget)
        self.start_local_web_server()
        # === 4. PANEL TABELI ATRYBUTÓW (DÓŁ) ===
        self.table_dock = QtWidgets.QDockWidget("Tabela Atrybutów", self)
        self.table_dock.setAllowedAreas(QtCore.Qt.BottomDockWidgetArea | QtCore.Qt.TopDockWidgetArea)
        
        # Tworzymy widok tabeli QGIS
        self.attribute_view = QgsAttributeTableView()
        self.table_dock.setWidget(self.attribute_view)
        
        # Dodajemy panel na dół (domyślnie ukryty lub widoczny - jak wolisz)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.table_dock)
        
        # Zmienne do przechowywania modeli (żeby Python ich nie usunął z pamięci)
        self.layer_cache = None
        self.table_model = None
        self.filter_model = None
        
        self.identify_tool = ClickIdentifyTool(self.canvas, self)
        self.load_default_basemap()
        
    # --- BUILDERS ---
    # --- NOWA METODA POMOCNICZA ---
    def get_target_layer(self, layer_type):
        """
        Zwraca warstwę do analizy.
        Priorytet 1: Warstwa zaznaczona myszką w drzewku.
        Priorytet 2: Ostatnio wczytana warstwa (fallback).
        """
        idxs = self.layer_tree_view.selectionModel().selectedRows()
        if idxs:
            node = self.layer_tree_view.index2node(idxs[0])
            if node and node.layer():
                layer = node.layer()
                if isinstance(layer, layer_type):
                    return layer
        
        if layer_type == QgsRasterLayer: return self.last_raster_layer
        if layer_type == QgsVectorLayer: return self.last_vector_layer
        if layer_type == QgsPointCloudLayer: return self.last_point_cloud_layer
        
        return None
        
    def _build_tab_data(self):
        layout = QtWidgets.QVBoxLayout(self.tab_data)
        layout.setAlignment(QtCore.Qt.AlignTop)
        btn_base = QtWidgets.QPushButton("🗺️ Zmień Podkład Mapowy")
        btn_base.clicked.connect(self.change_basemap_action)
        btn_base.setStyleSheet("background-color: #e1e1e1; font-weight: bold;")
        layout.addWidget(btn_base)
        layout.addSpacing(10)
        layout.addWidget(QtWidgets.QLabel("<b>Wczytaj dane:</b>"))
        btn_vec = QtWidgets.QPushButton("📥 Wczytaj wektor")
        btn_vec.clicked.connect(self.load_vector_action)
        btn_rast = QtWidgets.QPushButton("🖼 Wczytaj raster")
        btn_rast.clicked.connect(self.load_raster_action)
        btn_info = QtWidgets.QPushButton("ℹ️ Identyfikacja (Kliknij na mapie)")
        btn_info.setCheckable(True) # Przycisk włącz/wyłącz
        btn_info.clicked.connect(self.activate_identify_tool)
        
        btn_style = QtWidgets.QPushButton("🎨 Auto Styl")
        btn_style.clicked.connect(self.auto_style_action)
        btn_hollow = QtWidgets.QPushButton("⬜ Tylko Obrys (Przezroczyste)")
        btn_hollow.clicked.connect(self.set_outline_style_action)
        
        btn_rename = QtWidgets.QPushButton("✏️ Zmień nazwę")
        btn_rename.clicked.connect(self.rename_layer_action)
        btn_rem = QtWidgets.QPushButton("❌ Usuń")
        btn_rem.clicked.connect(self.remove_layer_action)
        btn_save = QtWidgets.QPushButton("💾 Zapisz warstwę na dysku")
        btn_save.clicked.connect(self.save_selected_layer_action)
        btn_save.setStyleSheet("background-color: #ddffdd;")
        btn_lidar = QtWidgets.QPushButton("☁️ Wczytaj LiDAR (LAS)")
        btn_lidar.clicked.connect(self.load_point_cloud_action)
        btn_3d = QtWidgets.QPushButton("🧊 Podgląd 3D (Nowe Okno)")
        btn_3d.clicked.connect(self.open_3d_viewer_action)
        
        layout.addWidget(btn_vec)
        layout.addWidget(btn_rast)
        layout.addWidget(btn_lidar)
        layout.addWidget(btn_info)
        layout.addSpacing(10)
        
        layout.addWidget(QtWidgets.QLabel("<b>Usługi sieciowe:</b>"))
        
        btn_wms = QtWidgets.QPushButton("🌐 Wczytaj z WMS")
        btn_wms.clicked.connect(self.load_wms_action)
        
        btn_wfs = QtWidgets.QPushButton("🌐 Wczytaj z WFS")
        btn_wfs.clicked.connect(self.load_wfs_action)
        
        btn_wcs = QtWidgets.QPushButton("GRID Dodaj WCS (Dane Rastrowe)")
        btn_wcs.clicked.connect(self.load_wcs_action)
        
        layout.addWidget(btn_wms)
        layout.addWidget(btn_wfs)
        layout.addWidget(btn_wcs)
        
        layout.addWidget(QtWidgets.QLabel("<b>Operacje na warstwach:</b>"))
        layout.addWidget(btn_rename)
        layout.addWidget(btn_style)
        layout.addWidget(btn_rem)
        layout.addWidget(btn_hollow)
        layout.addWidget(btn_save)
        
        layout.addSpacing(10)
        layout.addWidget(QtWidgets.QLabel("<b>Widok 3D:</b>"))
        layout.addWidget(btn_3d)

    def _build_tab_analysis(self):
        l = QtWidgets.QVBoxLayout(self.tab_analysis); l.setAlignment(QtCore.Qt.AlignTop)
        
        l.addWidget(QtWidgets.QLabel("<b>Raster (GDAL):</b>"))
        for t, f in [("⛰ Slope", self.compute_slope_action), ("🧭 Aspect", self.compute_aspect_action),
                     ("🌑 Hillshade", self.compute_hillshade_action),("n-DSM", self.analyze_ndsm_action), 
                     ("〰 Warstwice", self.generate_contours_action), 
                     ("Konwertuj raster na jpg", self.convert_to_jpg_action), 
                     ("Konwertuj poligon na linię", self.polygon_to_line_action),
                     ("✂️ Obrys rastra", self.generate_boundary_from_raster_action)]:
            b = QtWidgets.QPushButton(t); b.clicked.connect(f); l.addWidget(b)
            
        l.addSpacing(10); l.addWidget(QtWidgets.QLabel("<b>Wektor (OGR/Pandas):</b>"))
        for t, f in [("⭕ Bufor", self.compute_buffer_action), ("✂️ Przytnij", self.clip_vector_action),
                     ("📍 Centroidy", self.compute_centroids_action), ("🔍 Wyodrębnij obiekt (Filtr)", self.extract_feature_action)]:
            b = QtWidgets.QPushButton(t); b.clicked.connect(f); l.addWidget(b)

        l.addSpacing(10); l.addWidget(QtWidgets.QLabel("<b>LiDAR (PDAL):</b>"))
        b_dsm = QtWidgets.QPushButton("🏠 Generuj DSM (Max Z)"); b_dsm.clicked.connect(self.compute_dsm_action)
        b_dtm = QtWidgets.QPushButton("🚜 Generuj DTM (Grunt)"); b_dtm.clicked.connect(self.compute_dtm_action)
        b_inf = QtWidgets.QPushButton("ℹ️ Info LAS"); b_inf.clicked.connect(self.pdal_info_action)
        l.addWidget(b_dsm); l.addWidget(b_dtm); l.addWidget(b_inf)

    def _build_tab_db(self):
        layout = QtWidgets.QVBoxLayout(self.tab_db)
        layout.setAlignment(QtCore.Qt.AlignTop)
        self.lbl_db_status = QtWidgets.QLabel("Status: Rozłączony")
        layout.addWidget(self.lbl_db_status)
        
        btn_conn = QtWidgets.QPushButton("Połącz z DB")
        btn_conn.clicked.connect(self.connect_db_action)
        layout.addWidget(btn_conn)
        
        btn_upload = QtWidgets.QPushButton("Wyślij do bazy danych")
        btn_upload.clicked.connect(self.upload_layer_to_postgis_action)
        layout.addWidget(btn_upload)

        btn_load_db = QtWidgets.QPushButton("⬇Pobierz warstwę z bazy danych")
        btn_load_db.clicked.connect(self.load_layer_from_postgis_action)
        layout.addWidget(btn_load_db)

    def _build_tab_publish(self):
        layout = QtWidgets.QVBoxLayout(self.tab_publish)
        layout.setAlignment(QtCore.Qt.AlignTop)

        layout.addWidget(QtWidgets.QLabel("<b>Eksport Statyczny:</b>"))
        btn_pdf = QtWidgets.QPushButton("📄 Eksport PDF")
        btn_pdf.clicked.connect(self.export_pdf_action)
        layout.addWidget(btn_pdf)
        
        layout.addSpacing(15)
        
        layout.addWidget(QtWidgets.QLabel("<b>Eksport Interaktywny (Web):</b>"))
        
        btn_update_web = QtWidgets.QPushButton("🔄 Aktualizuj treść mapy (HTML)")
        btn_update_web.clicked.connect(self.update_web_map_content_action)
        btn_update_web.setStyleSheet("background-color: #ffaa00; font-weight: bold;") # Wyróżniamy go
        layout.addWidget(btn_update_web)
        btn_web3d = QtWidgets.QPushButton("🏢 Generuj Mapę 3D (PyDeck)")
        btn_web3d.clicked.connect(self.generate_3d_web_action)
        btn_web3d.setStyleSheet("background-color: #88ccff; font-weight: bold;")
        layout.addWidget(btn_web3d)
        btn_open_web = QtWidgets.QPushButton("🌍 Otwórz w przeglądarce")
        btn_open_web.clicked.connect(self.open_web_map_url_action)
        layout.addWidget(btn_open_web)
        
        layout.addSpacing(15)
        
        layout.addWidget(QtWidgets.QLabel("<b>Serwer OGC:</b>"))
        btn_gs = QtWidgets.QPushButton("🌐 Publikuj GeoServer")
        btn_gs.clicked.connect(self.publish_current_postgis_layer_action)
        layout.addWidget(btn_gs)
    def _build_tab_benchmark(self):
        l = QtWidgets.QVBoxLayout(self.tab_benchmark)
        
        ctrl = QtWidgets.QHBoxLayout()
        self.combo_bench = QtWidgets.QComboBox()
        self.combo_bench.addItems(["Wektor: Reprojekcja", "Raster: Analiza Slope", "LiDAR: Filtracja Z", "Baza: Deployment ETL"])
        
        btn = QtWidgets.QPushButton("🚀 Uruchom Zestaw Porównawczy")
        btn.clicked.connect(self.run_benchmark_action)
        btn.setStyleSheet("background-color: #2c3e50; color: white; font-weight: bold; padding: 8px;")
        
        ctrl.addWidget(QtWidgets.QLabel("Zestaw testowy:"))
        ctrl.addWidget(self.combo_bench)
        ctrl.addStretch()
        ctrl.addWidget(btn)
        l.addLayout(ctrl)
        
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
        from matplotlib.figure import Figure
        self.bench_fig = Figure(figsize=(9, 5), dpi=100)
        self.bench_canvas = FigureCanvas(self.bench_fig)
        l.addWidget(self.bench_canvas)
        self.txt_bench_results = QtWidgets.QTextEdit()
        self.bench_table = QtWidgets.QTableWidget()
        l.addWidget(self.bench_table)
    
    def _build_tab_terminal(self):
        layout = QtWidgets.QVBoxLayout(self.tab_terminal)
        self.term_output = QtWidgets.QTextEdit()
        self.term_output.setReadOnly(True)
        self.term_output.setStyleSheet("background-color: #1e1e1e; color: #00ff00; font-family: Consolas; font-size: 10pt;")
        
        self.term_input = QtWidgets.QLineEdit()
        self.term_input.setStyleSheet("background-color: #333; color: white; font-family: Consolas;")
        self.term_input.setPlaceholderText("Wpisz komendę (np. gdalinfo, dir, cd ..)")
        self.term_input.returnPressed.connect(self.run_terminal_command)
        
        btn_run = QtWidgets.QPushButton("Uruchom")
        btn_run.clicked.connect(self.run_terminal_command)
        
        layout.addWidget(self.term_output)
        layout.addWidget(self.term_input)
        layout.addWidget(btn_run)
        
        self.term_output.append(f"GDAL/OGR Terminal\nKatalog roboczy: {self.terminal_cwd}\n")

    def run_terminal_command(self):
        cmd = self.term_input.text().strip()
        if not cmd: return
        
        self.term_input.clear()
        self.term_output.append(f"\n{self.terminal_cwd}> {cmd}")
        
        if cmd.lower().startswith("cd "):
            new_path = cmd[3:].strip()
            if new_path == "..":
                target_dir = os.path.dirname(self.terminal_cwd)
            else:
                if os.path.isabs(new_path):
                    target_dir = new_path
                else:
                    target_dir = os.path.join(self.terminal_cwd, new_path)
            
            if os.path.isdir(target_dir):
                self.terminal_cwd = os.path.normpath(target_dir)
                self.term_output.append(f"Zmieniono katalog na: {self.terminal_cwd}")
            else:
                self.term_output.append(f"Błąd: Nie znaleziono ścieżki '{target_dir}'")
            return

        try:
            process = subprocess.Popen(
                cmd, 
                cwd=self.terminal_cwd, 
                shell=True, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                text=True,
                encoding='cp852'
            )
            out, err = process.communicate()
            if out: self.term_output.append(out)
            if err: self.term_output.append(f"STDERR: {err}")
        except Exception as e:
            self.term_output.append(f"CRITICAL ERROR: {str(e)}")

    # --- LOGIKA WARSTW ---
    def start_local_web_server(self):
        """Uruchamia prosty serwer HTTP w tle dla folderu z danymi."""
        if self.server_thread: return # Już działa

        PORT = 8000
        DIRECTORY = self.data_dir
        
        def run_server():
            class Handler(http.server.SimpleHTTPRequestHandler):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, directory=DIRECTORY, **kwargs)
                def log_message(self, format, *args): pass

            try:
                # Allow_reuse_address pozwala na szybki restart portu
                socketserver.TCPServer.allow_reuse_address = True
                with socketserver.TCPServer(("", PORT), Handler) as httpd:
                    self.httpd = httpd
                    print(f"WEB SERVER: Działa na http://localhost:{PORT}")
                    print(f"WEB ROOT: {DIRECTORY}")
                    httpd.serve_forever()
            except OSError as e:
                print(f"WEB SERVER ERROR: Port {PORT} zajęty? {e}")
                
        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
    def run_benchmark_action(self):
        idx = self.combo_bench.currentIndex()
        layer = self.get_currently_selected_layer()
        if not layer: return

        src = layer.source().split("|")[0]
        conn = self.db.conn_string if self.db else None
        engine = GISBenchmarkEngine(conn)

        self.status.showMessage(f"Benchmark w toku: {self.combo_bench.currentText()}...", 0)
        
        def run():
            if idx == 0: return engine.run_vector_repro(src)
            if idx == 1: return engine.run_raster_slope(src)
            if idx == 2: return engine.run_lidar_filter(src)
            if idx == 3: return engine.run_db_deployment(src)

        self.start_worker(run, result_callback=self.display_bench_results)

    def display_bench_results(self, df):
        if df is None or df.empty: return
        self.bench_table.setRowCount(len(df)); self.bench_table.setColumnCount(len(df.columns))
        self.bench_table.setHorizontalHeaderLabels(df.columns)
        for i, row in df.iterrows():
            for j, val in enumerate(row):
                self.bench_table.setItem(i, j, QtWidgets.QTableWidgetItem(str(val)))

        self.bench_fig.clear()
        ax1 = self.bench_fig.add_subplot(121) 
        ax2 = self.bench_fig.add_subplot(122) 

        labels = df["Metoda"]
        
        ax1.bar(labels, df["Czas [s]"], color='#3498db', alpha=0.8)
        ax1.set_title("Wydajność: Czas [s]")
        ax1.set_ylabel("Sekundy")
        ax1.grid(axis='y', linestyle='--', alpha=0.6)

        ax2.bar(labels, df["RAM [MB]"], color='#e74c3c', alpha=0.8)
        ax2.set_title("Zasoby: Przyrost RAM [MB]")
        ax2.set_ylabel("MB")
        ax2.grid(axis='y', linestyle='--', alpha=0.6)

        self.bench_fig.tight_layout()
        self.bench_canvas.draw()
        self.status.showMessage("Benchmark zakończony sukcesem.", 5000)
        
        self.txt_bench_results.append("✅ Wykres zaktualizowany.")
        
    def add_layer_smart(self, layer):

        if not layer.isValid():
            print("Błąd: Warstwa niepoprawna (isValid=False)")
            return False
        
        if isinstance(layer, QgsPointCloudLayer) and not layer.crs().isValid():
            layer.setCrs(QgsCoordinateReferenceSystem("EPSG:2180"))

        self.project.addMapLayer(layer)
        
        if isinstance(layer, QgsRasterLayer): self.last_raster_layer = layer
        elif isinstance(layer, QgsVectorLayer): self.last_vector_layer = layer
        elif isinstance(layer, QgsPointCloudLayer): self.last_point_cloud_layer = layer

        try:
            extent = layer.extent()
            
            if extent.isEmpty() or not extent.isFinite():
                print("Info: Warstwa ma pusty zasięg (WFS?), pomijam auto-zoom.")
            else:
                tc = self.canvas.mapSettings().destinationCrs()
                if layer.crs() != tc:
                    tr = QgsCoordinateTransform(layer.crs(), tc, self.project)
                    ext = tr.transformBoundingBox(extent)
                    if ext.isFinite(): self.canvas.setExtent(ext)
                else:
                    self.canvas.setExtent(extent)
        except Exception as e:
            print(f"Błąd zoomu: {e}")

        self.canvas.refresh()
        return True
    def _is_layer_alive(self, layer):
        try:
            if layer is None: return False
            return layer.isValid()
        except RuntimeError:
            return False
    def load_default_basemap(self):

        uri = "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&zmax=19&zmin=0"
        osm = QgsRasterLayer(uri, "OpenStreetMap", "wms")
        
        if osm.isValid():
            self.project.addMapLayer(osm)
            

            poland_extent = QgsRectangle(1500000, 6250000, 2700000, 7450000)
            self.canvas.setExtent(poland_extent)
            
            self.canvas.refresh()
        else:
            print("Błąd: Nie udało się pobrać podkładu mapowego")
    def change_basemap_action(self):

        maps = {
            "OpenStreetMap (Standard)": "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&zmax=19&zmin=0",
            
            "Google Hybrid (Satelita + Drogi)": "type=xyz&url=https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
            
            "Google Satellite (Czysty)": "type=xyz&url=https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
            
            "Esri Satellite (ArcGIS)": "type=xyz&url=https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            
            "Esri Topo (Topograficzna)": "type=xyz&url=https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
            
            "CartoDB Dark (Do analiz)": "type=xyz&url=https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
            
            "Geoportal Ortofotomapa (PL)": "contextualWMSLegend=0&crs=EPSG:2180&dpiMode=7&featureCount=10&format=image/jpeg&layers=Raster&styles=&url=https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/StandardResolution"
        }

        item, ok = QtWidgets.QInputDialog.getItem(self, "Podkład", "Wybierz mapę bazową:", list(maps.keys()), 0, False)
        
        if ok and item:
            uri = maps[item]
            name = item.split(" (")[0] # Skracamy nazwę do legendy
            
            layer = QgsRasterLayer(uri, name, "wms")
            
            if layer.isValid():
                self.project.addMapLayer(layer)
                
                root = self.project.layerTreeRoot()
                node = root.findLayer(layer.id())
                clone = node.clone()
                root.addChildNode(clone)
                root.removeChildNode(node)
                
                self.status.showMessage(f"Wczytano podkład: {name}", 3000)
            else:
                QtWidgets.QMessageBox.warning(self, "Błąd", "Nie udało się wczytać podkładu (sprawdź internet).")

    def load_vector_action(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Otwórz", self.data_dir, "Wektor (*.shp *.gpkg *.geojson *.gml)")
        if path:
            l = QgsVectorLayer(path, os.path.basename(path), "ogr")
            self.add_layer_smart(l)

    def load_raster_action(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Otwórz", self.data_dir, "Raster (*.tif *.tiff *.asc)")
        if path:
            l = QgsRasterLayer(path, os.path.basename(path))
            self.add_layer_smart(l)
    def load_point_cloud_action(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "LiDAR", self.data_dir, "LAS (*.las *.laz)")
        if not path: return

        folder = os.path.dirname(path)
        filename = os.path.basename(path)
        base_name = os.path.splitext(filename)[0]
        
        junk_paths = [
            os.path.join(folder, base_name + "_copc"),    
            os.path.join(folder, base_name + "_ept"),      
            os.path.join(folder, filename + ".inf")      
        ]
        
        for junk in junk_paths:
            if os.path.exists(junk):
                try:
                    if os.path.isdir(junk):
                        shutil.rmtree(junk) 
                    else:
                        os.remove(junk)     
                    print(f"Usunięto stary indeks: {junk}")
                except Exception as e:
                    print(f"Nie udało się usunąć indeksu {junk}: {e}")

        self.add_layer_smart(QgsPointCloudLayer(path, filename, "pdal"))

    def load_wms_action(self):
        
        if not OWSClient:
            QtWidgets.QMessageBox.critical(self, "Błąd", "Brak modułu ows_client (lub biblioteki OWSLib).")
            return

        default_url = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/StandardResolution"
        url, ok = QtWidgets.QInputDialog.getText(self, "WMS", "Podaj adres URL usługi WMS:", text=default_url)
        
        if not ok or not url: return

        def fetch_layers():
            return OWSClient.get_wms_layers(url)

        def on_layers_fetched(layers):
            if not layers:
                QtWidgets.QMessageBox.warning(self, "Info", "Usługa nie zwróciła żadnych warstw.")
                return
            
            display_list = [f"{title} ({name})" for name, title in layers]
            item, ok = QtWidgets.QInputDialog.getItem(self, "Wybierz Warstwę", "Dostępne warstwy WMS:", display_list, 0, False)
            
            if ok and item:
                idx = display_list.index(item)
                layer_name = layers[idx][0] 
                layer_title = layers[idx][1]
                
                crs = "EPSG:2180"

                uri = (
                    f"url={url}"
                    f"&layers={layer_name}"
                    f"&format=image/png"
                    f"&crs={crs}"
                    f"&styles="
                )
                
                rlayer = QgsRasterLayer(uri, layer_title, "wms")
                self.add_layer_smart(rlayer)
                self.status.showMessage(f"Dodano WMS: {layer_title}", 5000)

        self.status.showMessage("Pobieranie Capabilities serwera WMS...")
        self.start_worker(fetch_layers, result_callback=on_layers_fetched)
        
    def activate_identify_tool(self, checked):
        if checked:
            self.canvas.setMapTool(self.identify_tool)
            self.status.showMessage("Tryb identyfikacji: Kliknij w obiekt na mapie.", 0)
        else:
            self.canvas.unsetMapTool(self.identify_tool)
            self.status.clearMessage()

    def show_feature_popup(self, feature, layer):
        
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"Obiekt z warstwy: {layer.name()}")
        dlg.resize(400, 500)
        
        layout = QtWidgets.QVBoxLayout(dlg)
        
        table = QtWidgets.QTableWidget()
        fields = layer.fields()
        table.setRowCount(len(fields))
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Atrybut", "Wartość"])
        table.verticalHeader().setVisible(False)
        
        for i, field in enumerate(fields):
            name = field.name()
            val = feature.attribute(name)
            
            table.setItem(i, 0, QtWidgets.QTableWidgetItem(str(name)))
            table.setItem(i, 1, QtWidgets.QTableWidgetItem(str(val)))
        
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        
        layout.addWidget(table)
        
        btn_close = QtWidgets.QPushButton("Zamknij")
        btn_close.clicked.connect(dlg.accept)
        layout.addWidget(btn_close)
        
        dlg.exec()
    
    def load_wfs_action(self):
        if not OWSClient: 
            QtWidgets.QMessageBox.critical(self, "Błąd", "Brak modułu OWSClient.")
            return

        default_url = "https://ikerg.um.kalisz.pl/kalisz-egib"
        url_input, ok = QtWidgets.QInputDialog.getText(self, "WFS", "Adres usługi WFS:", text=default_url)
        
        if not ok or not url_input: return

        base_url = url_input.split("?")[0]

        def fetch(): 
            return OWSClient.get_wfs_layers(base_url)
        
        def done(layers):
            if not layers:
                QtWidgets.QMessageBox.warning(self, "Info", "Nie znaleziono warstw (lub błąd sieci).")
                return
            
            display_list = [f"{t} ({n})" for n, t in layers]
            item, ok = QtWidgets.QInputDialog.getItem(self, "Wybierz Warstwę", "Dostępne warstwy:", display_list, 0, False)
            
            if ok and item:
                idx = display_list.index(item)
                layer_name = layers[idx][0]
                layer_title = layers[idx][1]
                
                uri = QgsDataSourceUri()
                uri.setParam("url", base_url)
                uri.setParam("typename", layer_name)
                
                print(f"Ładowanie WFS (Auto): {uri.uri()}")
                
                vlayer = QgsVectorLayer(uri.uri(), layer_title, "WFS")
                
                if self.add_layer_smart(vlayer):
                    self.status.showMessage(f"Dodano: {layer_title}", 5000)
                    QtWidgets.QMessageBox.information(self, "Sukces", 
                        "Warstwa dodana.\nPamiętaj o przybliżeniu mapy!")
                else:
      
                    uri.setParam("version", "2.0.0")
                    vlayer2 = QgsVectorLayer(uri.uri(), layer_title, "WFS")
                    if self.add_layer_smart(vlayer2):
                        self.status.showMessage(f"Dodano (v2.0): {layer_title}", 5000)
                    else:
                        QtWidgets.QMessageBox.warning(self, "Błąd", "Nie udało się połączyć z warstwą.")

        self.status.showMessage("Pobieranie metadanych WFS...")
        self.start_worker(fetch, result_callback=done)
        
    def load_wcs_action(self):
        if not OWSClient: return

        default_url = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID/WCS/Hypsometry"
        url, ok = QtWidgets.QInputDialog.getText(self, "WCS", "Adres usługi WCS:", text=default_url)
        
        if not ok or not url: return
        
        base_url = url.split("?")[0]

        def fetch(): return OWSClient.get_wcs_layers(base_url)
        
        def done(layers):
            if not layers:
                QtWidgets.QMessageBox.warning(self, "Info", "Brak warstw WCS.")
                return
            
            display_list = [f"{t} ({n})" for n, t in layers]
            item, ok = QtWidgets.QInputDialog.getItem(self, "Wybierz", "Dostępne pokrycia:", display_list, 0, False)
            
            if ok:
                idx = display_list.index(item)
                identifier = layers[idx][0]
                title = layers[idx][1]
                
                
                uri = f"url={base_url}&identifier={identifier}&version=1.0.0"
                
                print(f"Ładowanie WCS: {uri}")
                
                layer = QgsRasterLayer(uri, title, "wcs")
                
                if self.add_layer_smart(layer):
                    self.status.showMessage(f"Dodano WCS: {title}", 5000)
                    QtWidgets.QMessageBox.information(self, "Sukces", 
                        "Dodano warstwę WCS (Dane wysokościowe).\n"
                        "Teraz możesz wykonać na niej analizy (Slope, Aspect)\n"
                        "lub zapisać na dysku jako GeoTIFF.")
                else:
                    QtWidgets.QMessageBox.warning(self, "Błąd", "Nie udało się wczytać warstwy WCS.")

        self.status.showMessage("Pobieranie listy WCS...")
        self.start_worker(fetch, result_callback=done)
        
    def rename_layer_action(self):
        idx = self.layer_tree_view.selectionModel().selectedRows()
        if idx:
            node = self.layer_tree_view.index2node(idx[0])
            l = node.layer()
            n, ok = QtWidgets.QInputDialog.getText(self, "Nazwa", "Nowa nazwa:", text=l.name())
            if ok: l.setName(n); node.setName(n)

    def remove_layer_action(self):
        idx = self.layer_tree_view.selectionModel().selectedRows()
        for i in idx:
            node = self.layer_tree_view.index2node(i)
            self.project.removeMapLayer(node.layerId())

    def make_double_line_symbol(self, outer_color, inner_color,
                            width_main, width_outer, unit):
        from qgis.core import QgsLineSymbol, QgsSimpleLineSymbolLayer

        sym = QgsLineSymbol()
        sym.setOutputUnit(unit)

        while sym.symbolLayerCount() > 0:
            sym.deleteSymbolLayer(0)

        outer_layer = QgsSimpleLineSymbolLayer()
        outer_layer.setWidth(width_outer)
        outer_layer.setColor(outer_color)
        outer_layer.setOutputUnit(unit)

        inner_layer = QgsSimpleLineSymbolLayer()
        inner_layer.setWidth(width_main)
        inner_layer.setColor(inner_color)
        inner_layer.setOutputUnit(unit)

        sym.appendSymbolLayer(outer_layer)
        sym.appendSymbolLayer(inner_layer)

        return sym

    def auto_style_action(self):
 
        import os
        from qgis.core import (
            QgsVectorLayer, QgsRasterLayer, QgsSymbol, QgsSingleSymbolRenderer, 
            QgsApplication, QgsSvgMarkerSymbolLayer, QgsSimpleLineSymbolLayer,
            QgsPalLayerSettings, QgsVectorLayerSimpleLabeling, QgsTextFormat, 
            QgsUnitTypes, QgsTextBufferSettings, QgsTextBackgroundSettings
        )
        from qgis.PyQt import QtGui, QtCore, QtWidgets
        from qgis.PyQt.QtCore import Qt

        # 1. Pobierz warstwę
        idx = self.layer_tree_view.selectionModel().selectedRows()
        layer = None
        node = None
        if idx:
            node = self.layer_tree_view.index2node(idx[0])
            if node: layer = node.layer()

        if not layer:
            if hasattr(self, 'last_vector_layer') and self.last_vector_layer: layer = self.last_vector_layer
            elif hasattr(self, 'last_raster_layer') and self.last_raster_layer: layer = self.last_raster_layer

        if not layer:
            QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę w legendzie.")
            return

        #  WEKTOR
        if isinstance(layer, QgsVectorLayer):
            renderer = layer.renderer()
            if not renderer: return
            
            if renderer.type() != 'singleSymbol':
                symbol = QgsSymbol.defaultSymbol(layer.geometryType())
            else:
                symbol = renderer.symbol().clone()

            geom_type = layer.geometryType()
            is_point = (geom_type == 0)
            is_line = (geom_type == 1)
            is_polygon = (geom_type == 2)

            dlg = QtWidgets.QDialog(self)
            dlg.setWindowTitle(f"Styl i Etykiety: {layer.name()}")
            dlg.setMinimumWidth(400)
            layout = QtWidgets.QVBoxLayout(dlg)

            tabs = QtWidgets.QTabWidget()
            tab_style = QtWidgets.QWidget()
            tab_labels = QtWidgets.QWidget()
            tabs.addTab(tab_style, "Symbolika")
            tabs.addTab(tab_labels, "Etykiety")
            layout.addWidget(tabs)

            layout_style = QtWidgets.QVBoxLayout(tab_style)
            layout_labels = QtWidgets.QVBoxLayout(tab_labels)
            
            curr_fill_color = symbol.color()
            curr_stroke_color = QtGui.QColor("black")
            if is_line and symbol.symbolLayerCount() > 1:
                curr_fill_color = symbol.symbolLayer(symbol.symbolLayerCount()-1).color()
                curr_stroke_color = symbol.symbolLayer(0).color()

     
            layout_style.addWidget(QtWidgets.QLabel("<b>Kolor Główny (Wypełnienie/Środek):</b>"))
            btn_fill_color = QtWidgets.QPushButton("🎨 Wybierz Kolor")
            btn_fill_color.setStyleSheet(f"background-color: {curr_fill_color.name()}; height: 30px;")
            fill_color_container = [curr_fill_color]
            def pick_fill_color():
                c = QtWidgets.QColorDialog.getColor(fill_color_container[0], dlg)
                if c.isValid():
                    fill_color_container[0] = c
                    btn_fill_color.setStyleSheet(f"background-color: {c.name()};")
            btn_fill_color.clicked.connect(pick_fill_color)
            layout_style.addWidget(btn_fill_color)

            layout_style.addWidget(QtWidgets.QLabel("<b>Kolor Obrysu (Tło/Ramka):</b>"))
            btn_stroke_color = QtWidgets.QPushButton("🎨 Wybierz Kolor")
            btn_stroke_color.setStyleSheet(f"background-color: {curr_stroke_color.name()}; height: 30px;")
            stroke_color_container = [curr_stroke_color]
            def pick_stroke_color():
                c = QtWidgets.QColorDialog.getColor(stroke_color_container[0], dlg)
                if c.isValid():
                    stroke_color_container[0] = c
                    btn_stroke_color.setStyleSheet(f"background-color: {c.name()};")
            btn_stroke_color.clicked.connect(pick_stroke_color)
            layout_style.addWidget(btn_stroke_color)

            grid = QtWidgets.QGridLayout()
            layout_style.addLayout(grid)

            grid.addWidget(QtWidgets.QLabel("Grubość środka:"), 0, 0)
            spin_inner_width = QtWidgets.QDoubleSpinBox()
            spin_inner_width.setRange(0.01, 100.0); spin_inner_width.setValue(0.4)
            grid.addWidget(spin_inner_width, 0, 1)

            grid.addWidget(QtWidgets.QLabel("Grubość obrysu:"), 3, 0) 
            spin_outline_width = QtWidgets.QDoubleSpinBox()
            spin_outline_width.setRange(0.0, 100.0); spin_outline_width.setValue(0.2)
            grid.addWidget(spin_outline_width, 3, 1)

            grid.addWidget(QtWidgets.QLabel("Jednostka:"), 1, 0)
            combo_units = QtWidgets.QComboBox()
            combo_units.addItems(["Milimetry (mm)", "Jednostki Mapy (m)", "Piksele (px)"])
            grid.addWidget(combo_units, 1, 1)

            grid.addWidget(QtWidgets.QLabel("Przezroczystość wewnątrz(%):"), 2, 0)
            slider_op_in = QtWidgets.QSlider(Qt.Horizontal); slider_op_in.setRange(0, 100); slider_op_in.setValue(0)
            grid.addWidget(slider_op_in, 2, 1)
            grid.addWidget(QtWidgets.QLabel("Przezroczystość obrysu(%):"), 3, 2)
            slider_op_out = QtWidgets.QSlider(Qt.Horizontal); slider_op_out.setRange(0, 100); slider_op_out.setValue(0)
            grid.addWidget(slider_op_out, 2, 2)

            group_line = QtWidgets.QGroupBox("Ustawienia linii (tylko dla linii)")
            line_form = QtWidgets.QFormLayout(group_line)
            combo_pen_style = QtWidgets.QComboBox(); combo_pen_style.addItems(["Ciągła (-)", "Kreskowa (--)", "Kropkowa (..)", "Brak"])
            combo_cap = QtWidgets.QComboBox(); combo_cap.addItems(["Prosty (|)", "Zaokrąglony (u)", "Kwadratowy ([])"])
            combo_join = QtWidgets.QComboBox(); combo_join.addItems(["Ostre (^)", "Zaokrąglone (n)", "Skośne (/)"])
            chk_double_line = QtWidgets.QCheckBox("Użyj podwójnej linii")
            if is_line:
                if symbol.symbolLayerCount() > 1: chk_double_line.setChecked(True)
                line_form.addRow("Typ:", combo_pen_style); line_form.addRow("Koniec:", combo_cap)
                line_form.addRow("Połączenie:", combo_join); line_form.addRow(chk_double_line)
                layout_style.addWidget(group_line)

            combo_icon = None
            svg_map = {}
            if is_point or is_line:
                layout_style.addWidget(QtWidgets.QLabel("<b>Ikona SVG (opcjonalnie):</b>"))
                combo_icon = QtWidgets.QComboBox(); combo_icon.addItem("-- Domyślny Punkt --")
                base_path = QgsApplication.prefixPath()
                svg_dirs = [os.path.join(base_path, "svg"), os.path.abspath(os.path.join(base_path, "..", "..", "apps", "qgis", "svg"))]
                real_svg = next((d for d in svg_dirs if os.path.exists(d)), None)
                if real_svg:
                    icons = []
                    for r, _, f in os.walk(real_svg):
                        for file in f:
                            if file.lower().endswith(".svg"):
                                full = os.path.join(r, file); rel = os.path.relpath(full, real_svg).replace("\\", "/")
                                icons.append(rel); svg_map[rel] = full
                    icons.sort(); combo_icon.addItems(icons)
                layout_style.addWidget(combo_icon)

            #  ETYKIETY
            
            chk_labels = QtWidgets.QCheckBox("Włącz etykiety"); chk_labels.setChecked(layer.labelsEnabled())
            layout_labels.addWidget(chk_labels)
            combo_fields = QtWidgets.QComboBox(); combo_fields.addItems([f.name() for f in layer.fields()])
            layout_labels.addWidget(combo_fields)
            combo_label_style = QtWidgets.QComboBox(); combo_label_style.addItems(["Standard", "Miejscowość (Halo/Bufor)", "Numer Drogi (Ramka)"])
            layout_labels.addWidget(QtWidgets.QLabel("Preset:")); layout_labels.addWidget(combo_label_style)
            spin_font = QtWidgets.QDoubleSpinBox(); spin_font.setValue(10.0)
            layout_labels.addWidget(QtWidgets.QLabel("Czcionka:")); layout_labels.addWidget(spin_font)
            layout_labels.addStretch()

            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            buttons.accepted.connect(dlg.accept); buttons.rejected.connect(dlg.reject)
            layout.addWidget(buttons)

            if dlg.exec_():

                alpha_inner = int(255 * (1.0 - slider_op_in.value() / 100.0))
                alpha_outline = int(255 * (1.0 - slider_op_out.value() / 100.0))

                final_fill = QtGui.QColor(fill_color_container[0])
                final_fill.setAlpha(alpha_inner)
                
                final_stroke = QtGui.QColor(stroke_color_container[0])
                final_stroke.setAlpha(alpha_outline)
                qgs_unit = [QgsUnitTypes.RenderMillimeters, QgsUnitTypes.RenderMapUnits, QgsUnitTypes.RenderPixels][combo_units.currentIndex()]

                if is_line:
                    pen_styles = [Qt.SolidLine, Qt.DashLine, Qt.DotLine, Qt.NoPen]
                    cap_styles = [Qt.FlatCap, Qt.RoundCap, Qt.SquareCap]
                    join_styles = [Qt.MiterJoin, Qt.RoundJoin, Qt.BevelJoin]
                    
                    while symbol.symbolLayerCount() > 0: symbol.deleteSymbolLayer(0)
                    
                    total_width = spin_inner_width.value() + spin_outline_width.value()
                    
                    l1 = QgsSimpleLineSymbolLayer()
                    l1.setColor(final_stroke) 
                    l1.setWidth(total_width)
                    l1.setPenStyle(pen_styles[combo_pen_style.currentIndex()])
                    l1.setPenCapStyle(cap_styles[combo_cap.currentIndex()])
                    l1.setPenJoinStyle(join_styles[combo_join.currentIndex()])
                    l1.setOutputUnit(qgs_unit)
                    symbol.appendSymbolLayer(l1)
                    
                    if chk_double_line.isChecked():

                        l2 = QgsSimpleLineSymbolLayer()
                        l2.setColor(final_fill)
                        l2.setWidth(spin_inner_width.value())
                        l2.setPenCapStyle(cap_styles[combo_cap.currentIndex()])
                        l2.setPenJoinStyle(join_styles[combo_join.currentIndex()])
                        l2.setOutputUnit(qgs_unit)
                        symbol.appendSymbolLayer(l2)
                    if combo_icon and combo_icon.currentIndex() > 0:
                        path = svg_map.get(combo_icon.currentText())
                        if path:
                            from qgis.core import QgsMarkerLineSymbolLayer, QgsMarkerSymbol, QgsWkbTypes
                            
 
                            svg_marker_layer = QgsSvgMarkerSymbolLayer(path)
                            svg_marker_layer.setSize(spin_inner_width.value() * 3) 
                            svg_marker_layer.setFillColor(final_fill)
                            svg_marker_layer.setStrokeColor(final_stroke)
                            
                            marker_sym = QgsMarkerSymbol()
                            marker_sym.changeSymbolLayer(0, svg_marker_layer)
                            

                            marker_line_layer = QgsMarkerLineSymbolLayer()
                            marker_line_layer.setSubSymbol(marker_sym)
                            marker_line_layer.setInterval(20) 
                            marker_line_layer.setPlacement(QgsMarkerLineSymbolLayer.Interval)
                            marker_line_layer.setOutputUnit(qgs_unit)
                            
                            symbol.appendSymbolLayer(marker_line_layer)
                elif is_point:
                    if combo_icon and combo_icon.currentIndex() > 0:
                        path = svg_map.get(combo_icon.currentText())
                        if path:
                            new_l = QgsSvgMarkerSymbolLayer(path)
                            while symbol.symbolLayerCount()>0: symbol.deleteSymbolLayer(0)
                            symbol.appendSymbolLayer(new_l)
                    for i in range(symbol.symbolLayerCount()):
                        sl = symbol.symbolLayer(i)
                        sl.setSize(spin_inner_width.value())
                        if hasattr(sl, 'setFillColor'): sl.setFillColor(final_fill)
                        elif hasattr(sl, 'setColor'): sl.setColor(final_fill)
                        if hasattr(sl, 'setStrokeColor'): sl.setStrokeColor(final_stroke)
                        if hasattr(sl, 'setStrokeWidth'): sl.setStrokeWidth(0.2) 
                        sl.setOutputUnit(qgs_unit)

                layer.setRenderer(QgsSingleSymbolRenderer(symbol))

                if chk_labels.isChecked():
                    settings = QgsPalLayerSettings(); settings.fieldName = combo_fields.currentText(); settings.enabled = True
                    txt_format = QgsTextFormat(); txt_format.setSize(spin_font.value()); txt_format.setColor(Qt.black)
                    style_choice = combo_label_style.currentText()
                    if "Miejscowość" in style_choice:
                        buf = QgsTextBufferSettings(); buf.setEnabled(True); buf.setSize(0.5); buf.setColor(Qt.white)
                        txt_format.setBuffer(buf); font = txt_format.font(); font.setBold(False); txt_format.setFont(font)
                        if is_point: settings.placement = QgsPalLayerSettings.AroundPoint; settings.dist = 1.5
                    elif "Numer Drogi" in style_choice:
                        bg = QgsTextBackgroundSettings(); bg.setEnabled(True); bg.setType(QgsTextBackgroundSettings.ShapeRectangle)
                        bg.setFillColor(Qt.white); bg.setStrokeColor(Qt.black); bg.setStrokeWidth(0.3); txt_format.setBackground(bg)
                        if is_line: settings.placement = QgsPalLayerSettings.Line
                    settings.setFormat(txt_format)
                    if is_line:
                        settings.mergeLines = True; settings.labelPerPart = False; settings.repeatDistance = 100
                        settings.repeatDistanceUnit = QgsUnitTypes.RenderMillimeters; settings.placement = QgsPalLayerSettings.Line; settings.placementFlags = QgsPalLayerSettings.OnLine
                    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings)); layer.setLabelsEnabled(True)
                else: layer.setLabelsEnabled(False)
                
                layer.triggerRepaint(); self.canvas.refresh()
        elif isinstance(layer, QgsRasterLayer):
            from qgis.core import (
                QgsSingleBandPseudoColorRenderer, QgsRasterShader, QgsColorRampShader,
                QgsRasterBandStats, QgsRasterTransparency, QgsHillshadeRenderer
            )
            from qgis.PyQt.QtGui import QColor

            # 1. ROZSZERZONY WYBÓR ANALIZY
            modes = [
                "Hipsometria (Wysokość)", 
                "Cieniowanie (Hillshade)", 
                "Ekspozycja (Aspect)",
                "Nachylenie (Slope)"
            ]
            mode, ok = QtWidgets.QInputDialog.getItem(self, "Styl Rastra", "Wybierz typ analizy:", modes, 0, False)
            if not ok: return

            try:
                provider = layer.dataProvider()
                stats = provider.bandStatistics(1, QgsRasterBandStats.All, layer.extent(), 0)
                min_v, max_v = stats.minimumValue, stats.maximumValue
                rng = max_v - min_v

                if "Hipsometria" in mode:
                    stops = [
                        (0.00, QColor(38, 115, 0)), (0.25, QColor(139, 209, 0)),
                        (0.50, QColor(255, 255, 190)), (0.75, QColor(200, 130, 0)), (1.00, QColor(100, 40, 0))
                    ]
                    color_shader = QgsColorRampShader(min_v, max_v)
                    color_shader.setColorRampType(QgsColorRampShader.Interpolated)
                    items = [QgsColorRampShader.ColorRampItem(min_v + (p*rng), c, f"{min_v+(p*rng):.1f}m") for p, c in stops]
                    color_shader.setColorRampItemList(items)
                    raster_shader = QgsRasterShader()
                    raster_shader.setRasterShaderFunction(color_shader)
                    renderer = QgsSingleBandPseudoColorRenderer(provider, 1, raster_shader)

                elif "Cieniowanie" in mode:
                    renderer = QgsHillshadeRenderer(provider, 1, 315.0, 45.0)
                    renderer.setMultiDirectional(True)
                    renderer.setZFactor(1.5)
                elif "Ekspozycja" in mode:

                    color_shader = QgsColorRampShader(0, 360)
                    color_shader.setColorRampType(QgsColorRampShader.Discrete) 

                    aspect_stops = [
                        (-0.1, QColor(166, 166, 166), "Flat (-1)"),       
                        (22.5, QColor(255, 0, 0), "North (0 - 22,5)"),    
                        (67.5, QColor(255, 170, 0), "North-East (22,5 - 67,5)"),
                        (112.5, QColor(255, 255, 0), "East (67,5 - 112,5)"),        
                        (157.5, QColor(0, 255, 0), "South-East (112,5 - 157,5)"), 
                        (202.5, QColor(0, 255, 255), "South (157,5 - 202,5)"),     
                        (247.5, QColor(0, 112, 255), "South-West (202,5 - 247,5)"), 
                        (292.5, QColor(76, 0, 255), "West (247,5 - 292,5)"),        
                        (337.5, QColor(255, 0, 255), "North-West (292,5 - 337,5)"), 
                        (360.0, QColor(255, 0, 0), "North (337,5 - 360)")            
                    ]
                    
                    items = [QgsColorRampShader.ColorRampItem(v, c, n) for v, c, n in aspect_stops]
                    color_shader.setColorRampItemList(items)
                    
                    raster_shader = QgsRasterShader()
                    raster_shader.setRasterShaderFunction(color_shader)
                    renderer = QgsSingleBandPseudoColorRenderer(provider, 1, raster_shader)

                elif "Nachylenie" in mode:
 
                    color_shader = QgsColorRampShader(0, 20)
                    color_shader.setColorRampType(QgsColorRampShader.Interpolated)
                    slope_stops = [
                        (0, QColor(50, 150, 50), "Płasko (0°)"),
                        (3, QColor(255, 255, 0), "Łagodne (3°)"),
                        (8, QColor(255, 127, 0), "Umiarkowane (8°)"),
                        (15, QColor(255, 0, 0), "Strome (15°)"),
                        (20, QColor(150, 0, 0), "Bardzo strome (>20°)")
                    ]
                    items = [QgsColorRampShader.ColorRampItem(v, c, n) for v, c, n in slope_stops]
                    color_shader.setColorRampItemList(items)
                    raster_shader = QgsRasterShader()
                    raster_shader.setRasterShaderFunction(color_shader)
                    renderer = QgsSingleBandPseudoColorRenderer(provider, 1, raster_shader)

                transparency = QgsRasterTransparency()
                pixel = QgsRasterTransparency.TransparentSingleValuePixel()
                pixel.pixelValue = 0.0
                pixel.percentTransparent = 100.0
                transparency.setTransparentSingleValuePixelList([pixel])
                renderer.setRasterTransparency(transparency)

                layer.setRenderer(renderer)
                layer.triggerRepaint()
                if node: self.layer_tree_model.refreshLayerLegend(node)
                self.canvas.refresh()
                self.status.showMessage(f"Zastosowano: {mode}", 4000)

            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Błąd", f"Wystąpił błąd: {str(e)}")
                
    def set_outline_style_action(self):

        layer = self.get_target_layer(QgsVectorLayer)
        
        if not layer:
            QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę poligonową (np. Granice).")
            return
            
        colors = ["red", "black", "blue", "green", "magenta", "yellow"]
        color, ok = QtWidgets.QInputDialog.getItem(self, "Styl Obrysu", "Wybierz kolor ramki:", colors, 0, False)
        
        if ok:

            if set_transparent_fill:
                set_transparent_fill(layer, color, width=0.6)
                self.status.showMessage(f"Zmieniono styl warstwy: {layer.name()}", 3000)
            else:
                QtWidgets.QMessageBox.critical(self, "Błąd", "Funkcja stylizacji niedostępna (błąd importu).")
                
    def save_selected_layer_action(self):

        layer = self.get_target_layer(QgsRasterLayer) or self.get_target_layer(QgsVectorLayer)

        if not layer or not layer.isValid():
            
            QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę, którą chcesz zapisać.")
            return

        layer_name = layer.name()
        safe_name = "".join([c for c in layer_name if c.isalnum() or c in ('_', '-')])

        if isinstance(layer, QgsVectorLayer):
            out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "Zapisz wektor",
                os.path.join(self.data_dir, safe_name),
                "GeoPackage (*.gpkg);;ESRI Shapefile (*.shp);;GeoJSON (*.geojson)"
            )
            if not out_path:
                return

            self.status.showMessage(f"Zapisywanie: {out_path}...", 0)
            QtWidgets.QApplication.processEvents()

            try:
                from qgis.core import QgsVectorFileWriter
                options = QgsVectorFileWriter.SaveVectorOptions()
                options.driverName = "GPKG"
                if out_path.lower().endswith(".shp"):
                    options.driverName = "ESRI Shapefile"
                elif out_path.lower().endswith("json"):
                    options.driverName = "GeoJSON"
                options.fileEncoding = "UTF-8"

                error = QgsVectorFileWriter.writeAsVectorFormatV3(
                    layer, out_path, self.project.transformContext(), options
                )

                if error[0] == QgsVectorFileWriter.NoError:
                    QtWidgets.QMessageBox.information(self, "Sukces", f"Zapisano:\n{out_path}")
                else:
                    QtWidgets.QMessageBox.critical(self, "Błąd", f"Błąd zapisu: {error}")

            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Błąd", str(e))
            finally:
                self.status.clearMessage()

        elif isinstance(layer, QgsRasterLayer):

            is_wms = layer.providerType() == "wms"

            filter_str = "GeoTIFF (*.tif);;PNG (*.png);;JPG (*.jpg)" if is_wms else "GeoTIFF (*.tif);;JPG (*.jpg)"
            out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "Zapisz raster/WMS",
                os.path.join(self.data_dir, safe_name),
                filter_str
            )
            if not out_path:
                return

            self.status.showMessage(f"Pobieranie i zapisywanie: {out_path}...", 0)
            QtWidgets.QApplication.processEvents()

            try:
                from qgis.core import QgsRasterFileWriter, QgsRasterPipe, QgsMapSettings, QgsMapRendererSequentialJob
                from qgis.PyQt.QtCore import QSize

                # --- ŚCIEŻKA 1: standardowy raster (nie WMS) ---
                if not is_wms:
                    width = layer.width()
                    height = layer.height()
                    extent = layer.extent()
                    crs = layer.crs()

                    file_writer = QgsRasterFileWriter(out_path)
                    pipe = QgsRasterPipe()

                    if pipe.set(layer.dataProvider().clone()):
                        err = file_writer.writeRaster(
                            pipe,
                            width,
                            height,
                            extent,
                            crs
                        )
                        if err == QgsRasterFileWriter.NoError:
                            QtWidgets.QMessageBox.information(self, "Sukces", f"Zapisano obraz:\n{out_path}")
                        else:
                            QtWidgets.QMessageBox.critical(self, "Błąd", f"Błąd zapisu rastra: {err}")
                    else:
                        QtWidgets.QMessageBox.critical(self, "Błąd", "Nie udało się otworzyć strumienia danych.")

                else:
                    extent = self.canvas.extent()
                    crs = self.canvas.mapSettings().destinationCrs()
                    size = self.canvas.size()
                    width = size.width()
                    height = size.height()


                    settings = QgsMapSettings()
                    settings.setLayers([layer])
                    settings.setDestinationCrs(crs)
                    settings.setExtent(extent)
                    settings.setOutputSize(QSize(width, height))
                    settings.setBackgroundColor(QtGui.QColor("white"))

                    job = QgsMapRendererSequentialJob(settings)
                    job.start()
                    job.waitForFinished()

                    img = job.renderedImage()

                    img.save(out_path)
                    QtWidgets.QMessageBox.information(self, "Sukces", f"Wyrenderowano i zapisano WMS:\n{out_path}")

            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Błąd", str(e))
            finally:
                self.status.clearMessage()


    def compute_slope_action(self):
        l = self.get_target_layer(QgsRasterLayer)
        if not l: 
            QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę rastrową.")
            return
        s = l.source().split("|")[0]
        out, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Zapisz", "", "TIF (*.tif)")
        if out:
            z, ok = QtWidgets.QInputDialog.getDouble(self, "Z-Factor", "1.0 (Metry) / 111120 (Stopnie)", 1.0, 0, 999999, 5)
            if ok: self.start_worker(compute_slope_raster, s, out, z_factor=z, result_path=out)
    def analyze_ndsm_action(self):

        from qgis.analysis import QgsRasterCalculator, QgsRasterCalculatorEntry
        
        layers = self.canvas.layers()
        nmpt_layer = None
        nmt_layer = None
        
        for l in layers:
            if "nmpt" in l.name().lower(): nmpt_layer = l
            if "nmt" in l.name().lower(): nmt_layer = l

        if not nmpt_layer or not nmt_layer:
            QtWidgets.QMessageBox.warning(self, "Błąd", "Wczytaj obie warstwy: NMT oraz NMPT.")
            return

        out, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Zapisz analizę n-DSM", "", "TIF (*.tif)")
        if not out: return

        entries = []
        e1 = QgsRasterCalculatorEntry()
        e1.ref = 'nmpt@1'
        e1.raster = nmpt_layer
        e1.bandNumber = 1
        entries.append(e1)

        e2 = QgsRasterCalculatorEntry()
        e2.ref = 'nmt@1'
        e2.raster = nmt_layer
        e2.bandNumber = 1
        entries.append(e2)


        formula = 'nmpt@1 - nmt@1'
        
        calc = QgsRasterCalculator(
            formula, out, 'GTiff', 
            nmpt_layer.extent(), nmpt_layer.width(), nmpt_layer.height(), 
            entries
        )
        
        self.status.showMessage("Obliczanie wysokości obiektów (n-DSM)...")
        if calc.processCalculation() == 0:

            res_layer = QgsRasterLayer(out, "Wysokosc_Obiektow_nDSM")
            QgsProject.instance().addMapLayer(res_layer)
            self.status.showMessage("Analiza n-DSM zakończona sukcesem.", 5000)
        else:
            QtWidgets.QMessageBox.critical(self, "Błąd", "Błąd kalkulatora rastrowego.")

    def compute_aspect_action(self):
        l = self.get_target_layer(QgsRasterLayer)
        if not l: 
            QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę rastrową.")
            return
        src = l.source().split("|")[0]
        out, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Zapisz", "", "TIF (*.tif)")
        if out:
            z, ok = QtWidgets.QInputDialog.getDouble(self, "Z-Factor", "1.0 (Metry) / 111120 (Stopnie)", 1.0, 0, 999999, 5)
            if ok: self.start_worker(compute_aspect_raster, src, out, z_factor=z, result_path=out)

    def compute_hillshade_action(self):
        l = self.get_target_layer(QgsRasterLayer)
        if not l: 
            QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę rastrową.")
            return
        src = l.source().split("|")[0]
        out, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Zapisz", "", "TIF (*.tif)")
        if out:
            z, ok = QtWidgets.QInputDialog.getDouble(self, "Z-Factor", "1.0 (Metry) / 111120 (Stopnie)", 1.0, 0, 999999, 5)
            if ok: 
                az, _ = QtWidgets.QInputDialog.getDouble(self, "Az", "Azymut:", 315, 0, 360)
                alt, _ = QtWidgets.QInputDialog.getDouble(self, "Alt", "Wysokość:", 45, 0, 90)
                self.start_worker(compute_hillshade_raster, src, out, z_factor=z, az=az, alt=alt, result_path=out)

    def generate_contours_action(self):
        l = self.get_target_layer(QgsRasterLayer)
        if not l: 
            QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę rastrową.")
            return
        src = l.source().split("|")[0]
        out, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Zapisz", "", "GPKG (*.gpkg)")
        if out:
            i, ok = QtWidgets.QInputDialog.getDouble(self, "Interwał", "Metry:", 10, 0.1, 10000, 2)
            if ok: self.start_worker(generate_contours, src, out, interval=i, result_path=out)
    def convert_to_jpg_action(self):
        layer = self.get_target_layer(QgsRasterLayer)
        if not layer:
            QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę rastrową (TIF/ASC).")
            return
            
        src = layer.source().split("|")[0]
        

        base_name = os.path.splitext(os.path.basename(src))[0]
        default_out = os.path.join(self.data_dir, f"{base_name}.jpg")
        
        out, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Zapisz JPG", default_out, "JPEG Image (*.jpg)")
        
        if out:
            self.start_worker(convert_raster_to_jpg, src, out, result_path=out)
    def generate_3d_web_action(self):
        """Metoda wywoływana po kliknięciu przycisku w aplikacji - obsługuje Wektory, Rastery i LiDAR"""
        from qgis.core import QgsVectorLayer, QgsRasterLayer, QgsPointCloudLayer, QgsVectorFileWriter, QgsProject
        import os, http.server, socketserver, threading

        gen = WebMap3DGenerator()
        layers = self.canvas.layers() 
        count = 0

        temp_dir = os.path.join(self.data_dir, "temp_3d")
        if not os.path.exists(temp_dir): os.makedirs(temp_dir)
        base_elevation, ok = QtWidgets.QInputDialog.getDouble(
            self, "Parametry wizualizacji 3D", 
            "Wysokość bazowa do odjęcia (m):\n(Wpisz np. minimalną wysokość terenu, aby posadzić model na mapie)", 
            0.0, -5000, 5000, 1
        )
        if not ok: return
        for layer in layers:
            if not layer.isValid(): continue
            
            src = layer.source().split("|")[0]
            
            if isinstance(layer, QgsVectorLayer):
                temp_file = os.path.join(temp_dir, f"{layer.id()}.gpkg")
                options = QgsVectorFileWriter.SaveVectorOptions()
                options.driverName = "GPKG"
                QgsVectorFileWriter.writeAsVectorFormatV3(
                    layer, temp_file, QgsProject.instance().transformContext(), options
                )
                
                color = [0, 150, 255]
                try:
                    c = layer.renderer().symbol().color()
                    color = [c.red(), c.green(), c.blue()]
                except: pass

                if gen.add_vector_layer_3d(temp_file, layer.name(), height_col=10, color=color, base_elevation=base_elevation):
                    count += 1

            elif isinstance(layer, QgsRasterLayer):
                if os.path.exists(src):
                    if gen.add_raster_layer_3d(src, layer.name(), base_elevation=base_elevation):
                        count += 1

            elif isinstance(layer, QgsPointCloudLayer):
                if os.path.exists(src) and src.lower().endswith(('.las', '.laz')):
                    if gen.add_lidar_layer_3d(src, layer.name(), max_points=150000, base_elevation=base_elevation):
                        count += 1
                        print(f"✅ Przesłano LiDAR do 3D: {layer.name()}")

  
        if count > 0:
            out_html_name = "mapa_3d.html"
            out_path = os.path.join(self.data_dir, out_html_name)
            gen.save_map(out_path)
            

            def start_server(path_dir):
                os.chdir(path_dir)
                handler = http.server.SimpleHTTPRequestHandler
                try:
                    with socketserver.TCPServer(("", 8000), handler) as httpd:
                        print("📡 Serwer HTTP 3D działa na porcie 8000")
                        httpd.serve_forever()
                except Exception as e:
                    print(f"ℹ️ Serwer prawdopodobnie już działa: {e}")

            if not hasattr(self, '_server_started'):
                threading.Thread(target=start_server, args=(self.data_dir,), daemon=True).start()
                self._server_started = True

            import webbrowser
            webbrowser.open("http://localhost:8000/mapa_3d.html")
            self.status.showMessage("Mapa 3D zintegrowana (Wektor+Raster+LiDAR) gotowa.", 5000)
        else:
            self.status.showMessage("Brak warstw do wyrenderowania w 3D.", 5000)
            
    def validate_geometry_action(self):
        l = self.get_target_layer(QgsVectorLayer)
        if not l: 
             QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę wektorową.")
             return
        
        src = l.source().split("|")[0]
        
        self.status.showMessage("Trwa walidacja topologii...", 0)
        QtWidgets.QApplication.processEvents()
        

        from core.processing import validate_geometry 
        report = validate_geometry(src)
        

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Raport Walidacji")
        dlg.resize(400, 300)
        layout = QtWidgets.QVBoxLayout(dlg)
        text_edit = QtWidgets.QTextEdit()
        text_edit.setPlainText(report)
        text_edit.setReadOnly(True)
        layout.addWidget(text_edit)
        btn = QtWidgets.QPushButton("OK")
        btn.clicked.connect(dlg.accept)
        layout.addWidget(btn)
        
        self.status.showMessage("Walidacja zakończona.", 5000)
        dlg.exec()
        
    def compute_buffer_action(self):
        l = self.get_target_layer(QgsVectorLayer)
        if not l: 
            QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę wektorową.")
            return
        d, ok = QtWidgets.QInputDialog.getDouble(self, "Bufor", "Metry:", 100, 0.1, 100000, 2)
        if ok:
            s = l.source().split("|")[0]
            o, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Zapisz", "", "SHP (*.shp)")
            if o: self.start_worker(vector_buffer, s, o, distance=d, result_path=o)

    def clip_vector_action(self):

        layer = None
        idx = self.layer_tree_view.selectionModel().selectedRows()
        if idx:
            node = self.layer_tree_view.index2node(idx[0])
            if node: layer = node.layer()
        
        try:
            if not layer or not layer.isValid(): raise ValueError()
        except:
            QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę do przycięcia.")
            return

        src_path = layer.source().split("|")[0]
        
        mask_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Wybierz Maskę (Granice)", self.data_dir, "Wektor (*.shp *.gpkg *.geojson)"
        )
        if not mask_path: return

        if isinstance(layer, QgsVectorLayer):
            out, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Zapisz", "", "SHP (*.shp)")
            if out:
                self.start_worker(clip_vector_geopandas, src_path, mask_path, out, result_path=out)

        elif isinstance(layer, QgsRasterLayer):
            out, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Zapisz Raster", "", "GeoTIFF (*.tif)")
            if not out: return
            
            if layer.providerType() == "wcs" or "http" in src_path:
                self.status.showMessage("Pobieranie fragmentu WCS (zakres maski)...", 0)
                QtWidgets.QApplication.processEvents()
                
                try:
                    mask_layer = QgsVectorLayer(mask_path, "mask_temp", "ogr")
                    if not mask_layer.isValid(): raise RuntimeError("Błędna maska.")
                    
                    mask_extent = mask_layer.extent()
                    if mask_layer.crs() != layer.crs():
                        tr = QgsCoordinateTransform(mask_layer.crs(), layer.crs(), self.project)
                        mask_extent = tr.transformBoundingBox(mask_extent)
                    
                    pixel_size = 1.0 
                    width = int(mask_extent.width() / pixel_size)
                    height = int(mask_extent.height() / pixel_size)
                    
                    if width > 4000 or height > 4000:
                        scale_factor = max(width, height) / 4000
                        width = int(width / scale_factor)
                        height = int(height / scale_factor)
                    

                    width = max(100, width)
                    height = max(100, height)
                    
                    print(f"Pobieranie WCS: {width}x{height} px...")

                    temp_wcs = os.path.join(self.data_dir, "temp_wcs_download.tif")
                    from qgis.core import QgsRasterFileWriter, QgsRasterPipe
                    
                    pipe = QgsRasterPipe()
                    if not pipe.set(layer.dataProvider().clone()):
                        raise RuntimeError("Błąd providera WCS.")
                        
                    writer = QgsRasterFileWriter(temp_wcs)
                    err = writer.writeRaster(pipe, width, height, mask_extent, layer.crs())
                    
                    if err == QgsRasterFileWriter.NoError:
                        src_path = temp_wcs
                        print(f"Pobrano WCS do: {src_path}")
                    else:
                        raise RuntimeError(f"Błąd pobierania WCS: {err}")

                except Exception as e:
                    QtWidgets.QMessageBox.critical(self, "Błąd WCS", f"Nie udało się pobrać danych:\n{e}")
                    return

            if clip_raster_gdal:
                self.start_worker(clip_raster_gdal, src_path, mask_path, out, result_path=out)
            else:
                QtWidgets.QMessageBox.critical(self, "Błąd", "Funkcja clip_raster_gdal niedostępna.")
        
        else:
            QtWidgets.QMessageBox.warning(self, "Info", "Nieobsługiwany typ warstwy.")

    def compute_centroids_action(self):
        l = self.get_target_layer(QgsVectorLayer)
        if not l: 
            QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę wektorową.")
            return
        s = l.source().split("|")[0]
        o, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Zapisz", "", "SHP (*.shp)")
        if o: self.start_worker(centroids_geopandas, s, o, result_path=o)
        
    def polygon_to_line_action(self):

        layer = self.get_target_layer(QgsVectorLayer)
        if not layer:
            QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę poligonową.")
            return
            
        src = layer.source().split("|")[0]
        
        out, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Zapisz Linie", "", "SHP (*.shp);;GPKG (*.gpkg)")
        
        if out:
            self.start_worker(polygon_to_line, src, out, result_path=out)

    def extract_feature_action(self):
        layer = self.get_target_layer(QgsVectorLayer)
        if not layer: 
            QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę z dzielnicami.")
            return

        fields = layer.fields()
        field_names = [f.name() for f in fields]
        
        if not field_names:
            QtWidgets.QMessageBox.warning(self, "Info", "Ta warstwa nie ma atrybutów.")
            return

        col_name, ok = QtWidgets.QInputDialog.getItem(self, "Krok 1/2", "Wybierz kolumnę (atrybut):", field_names, 0, False)
        if not ok: return
        
        idx = fields.indexFromName(col_name)
        unique_values = layer.uniqueValues(idx)
        values_str = sorted([str(v) for v in unique_values])

        fld = fields[idx]
        is_numeric = fld.type() in (QVariant.Int, QVariant.Double, QVariant.LongLong, QVariant.UInt)

        if is_numeric:
            val_str, ok = QtWidgets.QInputDialog.getText(
                self,
                "Krok 2/2",
                f"Podaj wartość lub prosty warunek dla '{col_name}'\n"
                "(np. 10, >10, <5, >=100):"
            )
            if not ok or not val_str.strip():
                return
        else:

            val_str, ok = QtWidgets.QInputDialog.getItem(
                self,
                "Krok 2/2",
                f"Wybierz wartość z '{col_name}':",
                values_str,
                0,
                False
            )
            if not ok:
                return

        
        src_layer = layer
        out, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Zapisz wynik",
            "",
            "SHP (*.shp);;GPKG (*.gpkg);;GeoJSON (*.geojson)"
        )

        if out:
            src = src_layer.source().split("|")[0]

            if src_layer.providerType() in ("wfs", "postgres", "memory") or not os.path.exists(src):
                tmp_dir = os.path.join(self.data_dir, "tmp_extract")
                if not os.path.exists(tmp_dir):
                    os.makedirs(tmp_dir)

                tmp_path = os.path.join(
                    tmp_dir,
                    f"extract_src_{src_layer.id()}.gpkg"
                )

                options = QgsVectorFileWriter.SaveVectorOptions()
                options.driverName = "GPKG"
                options.fileEncoding = "UTF-8"

                err = QgsVectorFileWriter.writeAsVectorFormatV3(
                    src_layer,
                    tmp_path,
                    self.project.transformContext(),
                    options
                )

                if err[0] != QgsVectorFileWriter.NoError:
                    QtWidgets.QMessageBox.critical(
                        self,
                        "Błąd",
                        f"Nie udało się zrzucić WFS do pliku tymczasowego:\n{err}"
                    )
                    return

                src = tmp_path  

            self.start_worker(
                extract_by_attribute,
                src,
                out,
                column=col_name,
                value=val_str,
                result_path=out
            )
    

    def compute_dsm_action(self):
        l = self.get_target_layer(QgsPointCloudLayer)
        
        if not l:
             QtWidgets.QMessageBox.warning(self, "Info", "Wczytaj najpierw plik LAS/LAZ.")
             return
             
        src = l.source().split("|")[0] # Ścieżka do pliku
        
        out, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Zapisz DSM", "", "TIF (*.tif)")
        if out:
            res, ok = QtWidgets.QInputDialog.getDouble(self, "Rozdzielczość", "Rozmiar piksela (m):", 1.0, 0.1, 100.0, 2)
            if ok:
                self.start_worker(pdal_generate_dsm, src, out, resolution=res, result_path=out)

    def compute_dtm_action(self):
        """Generuje Model Terenu (sam grunt)."""
        l = self.get_target_layer(QgsPointCloudLayer)
        if not l:
             QtWidgets.QMessageBox.warning(self, "Info", "Wczytaj najpierw plik LAS/LAZ.")
             return
             
        src = l.source().split("|")[0]
        
        out, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Zapisz DTM", "", "TIF (*.tif)")
        if out:
            res, ok = QtWidgets.QInputDialog.getDouble(self, "Rozdzielczość", "Rozmiar piksela (m):", 1.0, 0.1, 100.0, 2)
            if ok:
                QtWidgets.QMessageBox.information(self, "Info", "To może chwilę potrwać.\nAlgorytm SMRF klasyfikuje grunt.")
                self.start_worker(pdal_generate_dtm, src, out, resolution=res, result_path=out)

    def pdal_info_action(self):
        l = self.get_target_layer(QgsPointCloudLayer)
        if not l: 
            QtWidgets.QMessageBox.warning(self, "Info", "Wczytaj najpierw chmurę punktów.")
            return
            
        src = l.source().split("|")[0]
        
        def get_info():
            return pdal_info(src)
            
        def show_info(json_str):
            import json
            try:
                data = json.loads(json_str)
                msg = f"<b>Plik:</b> {os.path.basename(src)}<br>"
                
                if 'stats' in data and 'bbox' in data['stats']:
                    bbox = data['stats']['bbox']['native']['bbox']
                    count = data.get('count', 'N/A') 
                    
                    if count == 'N/A':
                         stats_list = data['stats'].get('statistic', [])
                         if stats_list:
                             for stat in stats_list:
                                 if stat.get('name') == 'X':
                                     count = stat.get('count', 'N/A')
                                     break

                    msg += f"<b>Liczba punktów:</b> {count}<br>"
                    msg += "<hr>"
                    msg += f"<b>X:</b> {bbox['minx']:.2f}  ➜  {bbox['maxx']:.2f}<br>"
                    msg += f"<b>Y:</b> {bbox['miny']:.2f}  ➜  {bbox['maxy']:.2f}<br>"
                    msg += f"<b>Z:</b> {bbox['minz']:.2f}  ➜  {bbox['maxz']:.2f}<br>"

                elif 'summary' in data:
                    s = data['summary']
                    b = s.get('bounds', {}).get('min', {})
                    b_max = s.get('bounds', {}).get('max', {})
                    msg += f"<b>Liczba punktów:</b> {s.get('num_points', 0)}<br>"
                    msg += "<hr>"
                    msg += f"<b>X:</b> {b.get('X', '?')} ➜ {b_max.get('X', '?')}<br>"
                    msg += f"<b>Y:</b> {b.get('Y', '?')} ➜ {b_max.get('Y', '?')}<br>"
                    msg += f"<b>Z:</b> {b.get('Z', '?')} ➜ {b_max.get('Z', '?')}<br>"
                
                else:
                    msg += "<br><i>Nie udało się znaleźć struktury 'stats' ani 'summary'.<br>"
                    msg += "Poniżej surowe dane:</i><br>"
                    msg += str(json_str)[:300]

                box = QtWidgets.QMessageBox(self)
                box.setWindowTitle("PDAL Info")
                box.setTextFormat(QtCore.Qt.RichText)
                box.setText(msg)
                box.exec()

            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Błąd parsowania", f"{e}\n\nRaw: {str(json_str)[:200]}")

        self.status.showMessage("Skanowanie pliku LAS... (To może chwilę potrwać)", 0)
        self.start_worker(get_info, result_callback=show_info)


    def connect_db_action(self):
        if not PostGISConnector: return
        
        default_conn = f"postgresql://{PG_USER}:{PG_PASS}@localhost:5432/{PG_DB}"
        conn, ok = QtWidgets.QInputDialog.getText(self, "DB", "Conn String:", text=default_conn)
        
        if ok:
            try:
                try: dbname = conn.rsplit("/", 1)[-1]
                except: dbname = "gismooth"
                
                self.status.showMessage(f"Łączenie z DB: {dbname}...", 0)
                QtWidgets.QApplication.processEvents()
                
                self.db = PostGISConnector(conn)

                self.db.ensure_database(dbname)
                self.db.connect()
                
                caps = self.db.check_advanced_capabilities()
                
                status_msg = "Połączono z Bazą Danych!\n\nStatus modułów:"
                status_msg += f"\n✅ Wektory (PostGIS): {'Dostępne' if caps['postgis'] else 'BŁĄD'}"
                status_msg += f"\n{'✅' if caps['postgis_raster'] else '❌'} Rastry: {'Dostępne' if caps['postgis_raster'] else 'Brak (zainstaluj postgis_raster)'}"
                status_msg += f"\n{'✅' if caps['pointcloud'] else '❌'} LiDAR: {'Dostępne' if caps['pointcloud'] else 'Brak (wymaga pgpointcloud)'}"
                
                self.lbl_db_status.setText("POŁĄCZONO ✅")
                self.lbl_db_status.setStyleSheet("color: green; font-weight: bold;")
                
                QtWidgets.QMessageBox.information(self, "Sukces", status_msg)
                self.status.showMessage(f"Połączono: {dbname}", 5000)
                
            except Exception as e:
                self.lbl_db_status.setText("BŁĄD ❌")
                self.lbl_db_status.setStyleSheet("color: red;")
                QtWidgets.QMessageBox.critical(self, "Błąd", str(e))
                self.status.clearMessage()
    def open_3d_viewer_action(self):

        layers = self.canvas.layers()
        if not layers:
            QtWidgets.QMessageBox.warning(self, "Info", "Brak widocznych warstw.")
            return
        z_factor, ok = QtWidgets.QInputDialog.getDouble(self, "Z-Factor", "Przesunięcie w pionie (Z-Factor):", 1.0, 0.1, 100.0, 2)
        if not ok: return
        import numpy as np
        import math
        import json
        
        temp_dir = os.path.join(self.data_dir, "3d_temp")
        if os.path.exists(temp_dir):
            import shutil
            try: shutil.rmtree(temp_dir)
            except: pass
        os.makedirs(temp_dir)

        self.status.showMessage("Przygotowywanie danych 3D...", 0)
        QtWidgets.QApplication.processEvents()

        scene_data = []
        center_x, center_y = 0, 0
        first_layer = True
        dest_crs = self.project.crs()
        try:
            for layer in layers:
                if not layer.isValid(): 
                    continue
                
                name = layer.name()
                safe_name = "".join([c for c in name if c.isalnum()])
                transform = QgsCoordinateTransform(layer.crs(), dest_crs, self.project)
                if isinstance(layer, QgsPointCloudLayer):
                    try:
                        las_path = layer.source().split("|")[0]
                        if os.path.exists(las_path):
                            if first_layer:
                                ext = layer.extent()
                                center_x = ext.center().x()
                                center_y = ext.center().y()
                                first_layer = False
                            
                            scene_data.append(f"LAS|{las_path}|0")
                            print(f"[+] LAS: {name}")
                    except Exception as e:
                        print(f"[!] Błąd przy ładowaniu LAS {name}: {e}")
                        continue

                elif isinstance(layer, QgsRasterLayer):
                    try:
                        provider = layer.dataProvider()
                        source = layer.source()
                        
                        is_web_layer = (
                            "type=xyz" in source.lower() or 
                            "tile.openstreetmap.org" in source.lower() or
                            "wms" in source.lower() or
                            provider.name() == "wms"
                        )
                        
                        if is_web_layer:
                            print(f"[i] ⚠️  Warstwa sieciowa '{name}' - pomijam renderowanie ręczne")
                            continue
                        
                        print(f"[i] Ładuję raster: {name}")
                        
                        z_val, ok = QtWidgets.QInputDialog.getDouble(self, f"Raster: {name}", 
                            f"Przesunięcie w pionie (Offset Z) dla '{name}':\n(0 = brak przesunięcia)", 
                            0.0, -5000, 5000, 2)
                        if not ok: 
                            continue
                        
                        ext = layer.extent()
                        w = layer.width()
                        h = layer.height()
                        
                        scale = 1
                        if w > 500: 
                            scale = int(w / 500)
                        
                        num_bands = provider.bandCount()
                        print(f"[i] Liczba band w {name}: {num_bands}")
                        
                        blocks = {}
                        for band_num in range(1, min(num_bands + 1, 4)):
                            blocks[band_num] = provider.block(band_num, ext, w//scale, h//scale)
                        
                        print(f"[i] Generuję chmurę punktów 3D...")
                        
                        pts = []
                        colors = []
                        x_min, y_max = ext.xMinimum(), ext.yMaximum()
                        
                        block = blocks[1]
                        w_b = block.width()
                        h_b = block.height()
                        
                        res_x = ext.width() / w_b if w_b > 0 else 1
                        res_y = ext.height() / h_b if h_b > 0 else 1
                        
                        try:
                            no_data = float(provider.sourceNoDataValue(1)) if provider.sourceNoDataValue(1) else None
                        except:
                            no_data = None
                        
                        print(f"[i] Generuję chmurę punktów 3D...")
                    
                        pts = []
                        colors = []
                        x_min, y_max = ext.xMinimum(), ext.yMaximum()
                        
                        block = blocks[1]
                        w_b = block.width()
                        h_b = block.height()
                        
                        res_x = ext.width() / w_b if w_b > 0 else 1
                        res_y = ext.height() / h_b if h_b > 0 else 1
                        
                        try:
                            no_data = float(provider.sourceNoDataValue(1)) if provider.sourceNoDataValue(1) else None
                        except:
                            no_data = None
                        
                        for r in range(h_b):
                            y = y_max - (r * res_y)
                            for c in range(w_b):
                                val = block.value(r, c)
                                is_valid = True
                                if no_data is not None:
                                    is_valid = (val != no_data)
                                
                                if is_valid:
                                    try:
                                        if not math.isnan(float(val)):
                                            x = x_min + (c * res_x)
                                            pts.append([x, y, (float(val)*z_factor) + float(z_val)])
                                            
                                            if 2 in blocks and 3 in blocks:
                                                val_r = blocks[1].value(r, c)
                                                val_g = blocks[2].value(r, c)
                                                val_b = blocks[3].value(r, c)
                                                
                                                r_norm = float(val_r) / 255.0
                                                g_norm = float(val_g) / 255.0
                                                b_norm = float(val_b) / 255.0
                                                
                                                colors.append([
                                                    min(1.0, max(0.0, r_norm)),
                                                    min(1.0, max(0.0, g_norm)),
                                                    min(1.0, max(0.0, b_norm))
                                                ])
                                            else:
                                                val_norm = float(val) / 255.0
                                                colors.append([val_norm, val_norm, val_norm])
                                    except:
                                        colors.append([0.5, 0.5, 0.5])
                        
                        if not pts: 
                            print(f"[!] Brak danych w rasterze {name}")
                            continue

                        if first_layer:
                            center_x = pts[0][0]
                            center_y = pts[0][1]
                            first_layer = False

                        npy_path = os.path.join(temp_dir, f"rast_{safe_name}.npy")
                        np.save(npy_path, np.array(pts, dtype=np.float32))
                        
                        if colors:
                            color_path = os.path.join(temp_dir, f"rast_colors_{safe_name}_col.npy")
                            np.save(color_path, np.array(colors, dtype=np.float32))
                            scene_data.append(f"RAST|{npy_path}|{color_path}")
                        else:
                            scene_data.append(f"RAST|{npy_path}|GRADIENT")
                        
                        print(f"[+] Raster 3D: {name} ({len(pts)} pkt, {num_bands} band)")
                    
                    except Exception as e:
                        print(f"[!] Błąd przy przetwarzaniu rasteru {name}: {e}")
                        import traceback
                        traceback.print_exc()
                        continue

                elif isinstance(layer, QgsVectorLayer):
                    try:
                        geom_type = layer.geometryType()
                        
                        if geom_type in [QgsWkbTypes.LineGeometry, QgsWkbTypes.PolygonGeometry]:
                            z_val, ok = QtWidgets.QInputDialog.getDouble(self, 
                                f"Warstwa wektorowa: {name}", 
                                f"Wysokość (Z) dla geometrii:\n(np. 50 = 50m nad podkładem)", 
                                50.0,
                                -5000, 5000, 2)
                            if not ok: 
                                continue
                        else:
                            z_val = 0.0

                        print(f"[i] Wybieranie koloru dla: {name}")
                        color = QtWidgets.QColorDialog.getColor(
                            QtGui.QColor(255, 255, 255),  
                            self,
                            f"Kolor dla warstwy: {name}"
                        )
                        
                        if color.isValid():

                            color_rgb = np.array([
                                color.red() / 255.0,
                                color.green() / 255.0,
                                color.blue() / 255.0
                            ], dtype=np.float32)
                            color_name = color.name()
                            print(f"[i] Kolor: {color_name}")
                        else:

                            color_rgb = np.array([1.0, 1.0, 1.0], dtype=np.float32)
                            color_name = "WHITE"
                            print(f"[i] Kolor: domyślny (białe)")
                        
                        pts = []
                        feature_count = 0
                        
                        for feature in layer.getFeatures():
                            geom = feature.geometry()
                            if geom.isNull(): 
                                continue
                            
                            feature_count += 1
                            
                            try:
                                if geom_type == QgsWkbTypes.PointGeometry:
                                    if geom.isMultipart():
                                        for point in geom.asMultiPoint():
                                            z_raw = float(point.z()) if point.z() else float(z_val)
                                            pts.append([float(point.x()), float(point.y()), z_raw * z_factor])
                                    else:
                                        point = geom.asPoint()
                                        z_raw = float(point.z()) if point.z() else float(z_val)
                                        pts.append([float(point.x()), float(point.y()), z_raw * z_factor])
                                
                                elif geom_type == QgsWkbTypes.LineGeometry:
                                    if geom.isMultipart():
                                        lines = geom.asMultiPolyline()
                                    else:
                                        lines = [geom.asPolyline()]
                                    
                                    for line in lines:
                                        for i in range(len(line) - 1):
                                            p1, p2 = line[i], line[i+1]
                                            dist = ((float(p2.x())-float(p1.x()))**2 + (float(p2.y())-float(p1.y()))**2)**0.5
                                            steps = max(2, int(dist / 0.5))
                                            
                                            for s in range(steps):
                                                t = s / steps
                                                x = float(p1.x()) + t * (float(p2.x()) - float(p1.x()))
                                                y = float(p1.y()) + t * (float(p2.y()) - float(p1.y()))
                                                pts.append([x, y, float(z_val)])
                                
                                elif geom_type == QgsWkbTypes.PolygonGeometry:
                                    if geom.isMultipart():
                                        polygons = geom.asMultiPolygon()
                                    else:
                                        polygons = [geom.asPolygon()]
                                    
                                    for polygon in polygons:
                                        for ring in polygon:
                                            for i in range(len(ring) - 1):
                                                p1, p2 = ring[i], ring[i+1]
                                                dist = ((float(p2.x())-float(p1.x()))**2 + (float(p2.y())-float(p1.y()))**2)**0.5
                                                steps = max(2, int(dist / 0.75))
                                                
                                                for s in range(steps):
                                                    t = s / steps
                                                    x = float(p1.x()) + t * (float(p2.x()) - float(p1.x()))
                                                    y = float(p1.y()) + t * (float(p2.y()) - float(p1.y()))
                                                    pts.append([x, y, float(z_val)])
                            
                            except Exception as e:
                                print(f"[!] Błąd: {e}")
                                continue
                        
                        if pts:
                            if first_layer:
                                center_x = float(pts[0][0])
                                center_y = float(pts[0][1])
                                first_layer = False
                            
                            npy_path = os.path.join(temp_dir, f"vec_{safe_name}.npy")
                            np.save(npy_path, np.array(pts, dtype=np.float32))
                            
                            
                            color_path = os.path.join(temp_dir, f"vec_color_{safe_name}.npy")
                            np.save(color_path, color_rgb)
                            
                            
                            scene_data.append(f"VEC|{npy_path}|{color_path}")
                            
                            print(f"[+] Wektor: {name} ({len(pts)} pkt z {feature_count} feature'ów, kolor: {color_name})")
                        else:
                            print(f"[!] Brak geometrii w warstwie {name}")
                    
                    except Exception as e:
                        print(f"[!] Błąd: {e}")
                        import traceback
                        traceback.print_exc()
                        continue

            if not scene_data:
                QtWidgets.QMessageBox.warning(self, "Info", "Brak danych do wizualizacji.")
                return

            print(f"\n[✓] Przygotowano {len(scene_data)} warstw do wyświetlenia")

            py_code = f"""
import sys
import os
import json
import numpy as np
import warnings
warnings.filterwarnings('ignore')

try:
    import open3d as o3d
    import laspy
except ImportError as e:
    print(f"BRAK BIBLIOTEK: {{e}}")
    input("Naciśnij ENTER...")
    sys.exit(1)

OFFSET_X = {center_x}
OFFSET_Y = {center_y}
Z_FACTOR = {z_factor}

data_list = [
"""
            for item in scene_data:
                py_code += f"    r'{item}',\n"
            
            py_code += """]

def run():
    print("\\n=== WIZUALIZACJA 3D ===")
    print(f"Liczba warstw: {len(data_list)}")
    geometries = []

    for idx, item in enumerate(data_list):
        parts = item.split('|')
        typ = parts[0]
        path = parts[1]
        style = parts[2]
        
        print(f"\\n[{idx+1}/{len(data_list)}] {typ}: {os.path.basename(path)}")
        pts = None
        colors = None
        
        try:
            if typ == 'LAS':
                las = laspy.read(path)
                x = np.array(las.x, dtype=np.float32) - OFFSET_X
                y = np.array(las.y, dtype=np.float32) - OFFSET_Y
                z = np.array(las.z, dtype=np.float32) * Z_FACTOR
                pts = np.column_stack((x, y, z))
                
                if hasattr(las, 'red') and las.red is not None:
                    r = np.array(las.red, dtype=np.float32) / 65535.0
                    g = np.array(las.green, dtype=np.float32) / 65535.0
                    b = np.array(las.blue, dtype=np.float32) / 65535.0
                    colors = np.column_stack((r, g, b))

            elif typ == 'RAST' or typ == 'VEC':
                raw = np.load(path)
                pts = np.column_stack((raw[:,0] - OFFSET_X, raw[:,1] - OFFSET_Y, raw[:,2]))
                pcd = o3d.geometry.PointCloud()
                pcd.points = o3d.utility.Vector3dVector(pts)
                
                if typ == 'VEC':
                    color_data = np.load(style)
                    pcd.colors = o3d.utility.Vector3dVector(np.tile(color_data, (len(pts), 1)))
                else:
                    # --- ZAAWANSOWANA HIPSOMETRIA DLA RASTRA ---
                    z_vals = pts[:, 2]
                    z_min, z_max = np.min(z_vals), np.max(z_vals)
                    z_range = z_max - z_min + 1e-6
                    z_norm = (z_vals - z_min) / z_range

                    # Definiujemy stopy kolorystyczne (R, G, B w skali 0-1)
                    # Dokładnie takie jak w Twoim auto_style
                    stops = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
                    colors_rgb = np.array([
                        [38/255, 115/255, 0/255],   # Ciemna zieleń
                        [139/255, 209/255, 0/255],  # Jasna zieleń
                        [255/255, 255/255, 190/255],# Żółty/Krem
                        [200/255, 130/255, 0/255],  # Pomarańcz/Brąz
                        [100/255, 40/255, 0/255]    # Ciemny brąz
                    ])

                    # Interpolacja liniowa dla każdego kanału
                    final_colors = np.zeros((len(z_norm), 3))
                    for i in range(3): # Dla R, G, B
                        final_colors[:, i] = np.interp(z_norm, stops, colors_rgb[:, i])
                    
                    pcd.colors = o3d.utility.Vector3dVector(final_colors)
                
                geometries.append(pcd)
            
            # TWORZENIE OBIEKTU OPEN3D
            pcd = o3d.geometry.PointCloud()
            pcd.points = o3d.utility.Vector3dVector(pts.astype(np.float64))
            pcd.colors = o3d.utility.Vector3dVector(np.clip(colors, 0, 1).astype(np.float64))
            
            if len(pcd.points) > 2000000:
                pcd = pcd.uniform_down_sample(5)
                print(f"  [↓] Zdecymowano do {len(pcd.points):,}")
                
            geometries.append(pcd)
            
        except Exception as e:
            print(f"  [✗] Błąd: {e}")
            import traceback
            traceback.print_exc()
            continue

    if not geometries:
        print("\\n[!] Brak geometrii!")
        input("ENTER...")
        return

    print(f"\\n[✓] Załadowano {{len(geometries)}} warstw")
    
    # Osie
    geometries.append(o3d.geometry.TriangleMesh.create_coordinate_frame(size=100))
    
    print("\\n=== STEROWANIE ===")
    print("  🖱️  Lewy przycisk: OBRÓT")
    print("  🖱️  Scroll: ZOOM (bez limitów)")
    print("  🖱️  Prawy przycisk: PRZESUNIĘCIE")
    print("  ⌨️  C: Wyśrodkuj widok")
    print("  ⌨️  Q: Zamknij")
    print("\\n[>] Otwieranie okna 3D...")
    
    vis = o3d.visualization.Visualizer()
    vis.create_window(width=1400, height=900, window_name="QGIS 3D Viewer")
    
    for g in geometries:
        vis.add_geometry(g)
    
    ctr = vis.get_view_control()
    ctr.set_zoom(0.8)
    
    opt = vis.get_render_option()
    opt.background_color = np.asarray([0, 0, 0])
    opt.point_size = 3.0
    
    vis.run()
    vis.destroy_window()
    print("\\n[✓] Zamknięto okno 3D")

if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"\\n[BŁĄD] {e}")
        import traceback
        traceback.print_exc()
        input("ENTER...")
"""
        
            script_path = os.path.join(self.data_dir, "viz_simple.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(py_code)

            current_exe = sys.executable
            qgis_root = os.path.dirname(os.path.dirname(current_exe))
            apps_dir = os.path.join(qgis_root, "apps")
            real_python_home = None
            
            if os.path.exists(apps_dir):
                for d in sorted(os.listdir(apps_dir), reverse=True):
                    if d.lower().startswith("python3") and os.path.isdir(os.path.join(apps_dir, d)):
                        candidate = os.path.join(apps_dir, d)
                        if os.path.exists(os.path.join(candidate, "python.exe")):
                            real_python_home = candidate
                            break
            
            if not real_python_home:
                real_python_home = os.path.dirname(sys.executable)

            python_exe = os.path.join(real_python_home, "python.exe")
            
            if not os.path.exists(python_exe):
                QtWidgets.QMessageBox.critical(self, "Błąd", f"Nie znaleziono: {python_exe}")
                return

            osgeo_bin = os.path.join(qgis_root, "bin")

            bat_path = os.path.join(self.data_dir, "launch_simple.bat")
            bat_content = f"""@echo off
chcp 65001 >nul
set "PYTHONHOME={real_python_home}"
set "PYTHONPATH="
set "PATH={real_python_home};{real_python_home}\\Scripts;{osgeo_bin};%SystemRoot%\\system32;%SystemRoot%"
set "QT_PLUGIN_PATH="
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONIOENCODING=utf-8"

"{python_exe}" "{script_path}"

if errorlevel 1 pause
"""
            with open(bat_path, "w", encoding="utf-8") as f:
                f.write(bat_content)

            os.startfile(bat_path)
            print(f"✓ Uruchomiono wizualizację 3D")

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Błąd", f"{type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()

    def _find_tool(self, tool_name):


        if not tool_name.endswith(".exe"):
            tool_name += ".exe"

        tool_path = shutil.which(tool_name)
        if tool_path: return tool_path

        try:
            from qgis.core import QgsApplication
            qgis_prefix = QgsApplication.prefixPath()
            possible_paths = [
                os.path.join(qgis_prefix, "bin", tool_name),
                os.path.join(qgis_prefix, "..", "bin", tool_name),
                os.path.join(os.path.dirname(qgis_prefix), "bin", tool_name),
            ]
            for path in possible_paths:
                if os.path.exists(path): return path
        except: pass

        for ver in range(16, 9, -1):
            pg_path = f"C:\\Program Files\\PostgreSQL\\{ver}\\bin\\{tool_name}"
            if os.path.exists(pg_path):
                print(f"Znaleziono w PostgreSQL: {pg_path}")
                return pg_path
            
            pg_path_x86 = f"C:\\Program Files (x86)\\PostgreSQL\\{ver}\\bin\\{tool_name}"
            if os.path.exists(pg_path_x86): return pg_path_x86

        reply = QtWidgets.QMessageBox.question(
            self, "Nie znaleziono narzędzia", 
            f"Nie mogę znaleźć '{tool_name}'.\nCzy chcesz wskazać plik .exe ręcznie?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            f, _ = QtWidgets.QFileDialog.getOpenFileName(self, f"Wskaż {tool_name}", "C:\\", "Executable (*.exe)")
            if f: return f

        return None

    def get_currently_selected_layer(self):

        from qgis.core import QgsLayerTreeLayer
        
        try:

            selected_nodes = self.layer_tree_view.selectedNodes()
            
            if selected_nodes:

                for node in selected_nodes:

                    if isinstance(node, QgsLayerTreeLayer):
                        layer = node.layer()
                        if layer:
                            print(f"[✓] Selected from tree view: {layer.name()}")
                            return layer
        except Exception as e:
            print(f"[!] selectedNodes error: {e}")
        
        try:
            layers = list(self.project.mapLayers().values())
            if layers:
                last_layer = layers[-1]
                print(f"[✓] Last added layer (fallback): {last_layer.name()}")
                return last_layer
        except Exception as e:
            print(f"[!] mapLayers error: {e}")
        
        print("[!] No layer available")
        return None

    
    def qgis_layer_to_style_params(qgs_layer: QgsVectorLayer):

        renderer = qgs_layer.renderer()
        if renderer is None or renderer.type() != 'singleSymbol':
            return {'color': 'blue', 'weight': 2, 'fillOpacity': 0.4}

        symbol = renderer.symbol()

        color = '#0000ff'
        weight = 2
        fill_opacity = 0.4

        if isinstance(symbol, QgsMarkerSymbol):
            color = symbol.color().name()
            weight = symbol.size()  
            fill_opacity = 1.0

        elif isinstance(symbol, QgsLineSymbol):
            color = symbol.color().name()
            weight = symbol.width()
            fill_opacity = 0.0

        elif isinstance(symbol, QgsFillSymbol):
            color = symbol.color().name()
            weight = symbol.borderWidth()
            fill_opacity = symbol.opacity()

        return {
            'color': color,
            'weight': float(weight),
            'fillOpacity': float(fill_opacity),
        }

    def upload_layer_to_postgis_action(self):

        from qgis.core import QgsVectorLayer, QgsRasterLayer, QgsPointCloudLayer
        
        layer = self.get_currently_selected_layer()
        
        if not layer:
            QtWidgets.QMessageBox.warning(
                self, 
                "Info", 
                "Zaznacz warstwę w Layers Panel!\n\n"
                "Kliknij na warstwę w panelu po lewej."
            )
            return
        
        if not self.db: 
            QtWidgets.QMessageBox.warning(self, "Info", "Najpierw połącz się z bazą danych.")
            return

        layer_name = layer.name()
        layer_type = type(layer).__name__
        src_path = layer.source().split("|")[0]
        provider_type = layer.providerType()
        print(f"[✓] Wybrana warstwa: {layer_name} ({layer_type})")

        if isinstance(layer, QgsVectorLayer):
            self._upload_vector_to_postgis(layer)

        elif isinstance(layer, QgsRasterLayer):
            src_path = layer.source().split("|")[0]
            print(f"[i] Raster: {src_path}")
            self._upload_raster_to_postgis(layer_name, src_path)

        elif isinstance(layer, QgsPointCloudLayer):
            src_path = layer.source().split("|")[0]
            print(f"[i] LiDAR: {src_path}")
            self._upload_lidar_to_postgis(layer_name, src_path)
        
        else:
            QtWidgets.QMessageBox.warning(
                self, 
                "Info", 
                f"Nieznany typ warstwy: {layer_type}\n\n"
                f"Obsługuję: Vektor, Raster, LiDAR"
            )

    def _upload_vector_to_postgis(self, layer_obj):
        from qgis.core import QgsVectorFileWriter, QgsCoordinateTransform, QgsCoordinateReferenceSystem, QgsProject
        
        layer_name = layer_obj.name()
        src_path = layer_obj.source().split("|")[0]
        provider_type = layer_obj.providerType()

        default_name = layer_name.replace(" ", "_").lower()
        table_name, ok = QtWidgets.QInputDialog.getText(self, "Tabela PostGIS", "Nazwa tabeli:", text=default_name)
        if not ok or not table_name: return

        items = ["EPSG:2180 (PUWG 1992)", "EPSG:4326 (WGS 84)", "Bez zmian"]
        item, ok2 = QtWidgets.QInputDialog.getItem(self, "Układ", "Reprojekcja przed wysyłką:", items, 0, False)
        if not ok2: return

        target_srid = None
        if "2180" in item: target_srid = 2180
        elif "4326" in item: target_srid = 4326

        if provider_type == "wfs" or "http" in src_path:
            self.status.showMessage("Pobieranie danych WFS do pamięci podręcznej...", 0)
            QtWidgets.QApplication.processEvents()

            temp_gpkg = os.path.join(self.data_dir, f"tmp_upload_{layer_obj.id()}.gpkg")

            if os.path.exists(temp_gpkg):
                try: os.remove(temp_gpkg)
                except: pass

            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"

            if target_srid:
                options.ct = QgsCoordinateTransform(layer_obj.crs(), QgsCoordinateReferenceSystem(target_srid), QgsProject.instance())

            error = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer_obj, 
                temp_gpkg, 
                QgsProject.instance().transformContext(), 
                options
            )

            if error[0] == QgsVectorFileWriter.NoError:
                src_path = temp_gpkg
                target_srid = None 
            else:
                QtWidgets.QMessageBox.critical(self, "Błąd WFS", f"Nie udało się pobrać danych WFS (Kod błędu: {error[0]})\n{error[1]}")
                return

        self.status.showMessage(f"Wysyłanie {table_name} do PostGIS...", 0)
        self.start_worker(self.db.import_with_ogr2ogr, src_path, table_name=table_name, target_srid=target_srid)


    def _upload_raster_to_postgis(self, layer_name, src_path):
        from sqlalchemy import text
        import subprocess
        
        if not os.path.exists(src_path):
            QtWidgets.QMessageBox.warning(self, "Info", "Plik rastra musi być lokalny.")
            return

        default_name = layer_name.replace(" ", "_").lower()
        table_name, ok = QtWidgets.QInputDialog.getText(self, "Tabela Raster", "Nazwa tabeli:", text=default_name)
        if not ok: return

        raster2pgsql = self._find_tool("raster2pgsql")
        if not raster2pgsql:
            QtWidgets.QMessageBox.critical(self, "Błąd", "Nie znaleziono narzędzia raster2pgsql.")
            return

        self.status.showMessage("Importowanie rastra...", 0)
        QtWidgets.QApplication.processEvents()

        def run_import():

            srid = "2180"
            cmd = [raster2pgsql, "-I", "-C", "-s", srid, "-F", src_path, table_name] 
            
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
            if proc.returncode != 0: raise RuntimeError(f"raster2pgsql error: {proc.stderr}")

            sql = proc.stdout
            if not sql: raise RuntimeError("Pusty SQL z raster2pgsql")


            with self.db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.execute(text(sql))

                conn.execute(text(f'ANALYZE "{table_name}";'))
            return True

        self.start_worker(run_import)


    def _upload_lidar_to_postgis(self, layer_name, las_path):

        from sqlalchemy import text
        

        default_name = os.path.splitext(os.path.basename(las_path))[0].lower()
        table_name, ok = QtWidgets.QInputDialog.getText(
            self, "Tabela LiDAR", "Nazwa tabeli w PostGIS:", text=default_name
        )
        if not ok or not table_name: return

        # Wybór metody
        method, ok2 = QtWidgets.QInputDialog.getItem(
            self,
            "Metoda",
            "Jak importować LiDAR?",
            ["Punkty (XYZ - szybko)", "Raster DEM (tiff + tiles - wizualizacja)"],
            0,
            False
        )
        if not ok2: return

        self.status.showMessage("Importowanie LiDAR...", 0)
        QtWidgets.QApplication.processEvents()

        if method.startswith("Punkty"):
            try:
                import laspy
                import pandas as pd
                import geopandas as gpd
                import numpy as np
                
                las = laspy.read(las_path)
                
                x = np.asarray(las.x, dtype='float64').flatten()
                y = np.asarray(las.y, dtype='float64').flatten()
                z = np.asarray(las.z, dtype='float64').flatten()
                
                data = {
                    'x': x, 'y': y, 'z': z,
                    'intensity': np.asarray(las.intensity, dtype='float64').flatten() if hasattr(las, 'intensity') else np.zeros(len(x)),
                    'classification': np.asarray(las.classification, dtype='int32').flatten() if hasattr(las, 'classification') else np.zeros(len(x), dtype='int32'),
                }
                
                if hasattr(las, 'return_number'):
                    data['return_num'] = np.asarray(las.return_number, dtype='int32').flatten()
                
                df = pd.DataFrame(data)
                
                # CRS detection
                crs_epsg = "EPSG:2180"
                try:
                    from pyproj import CRS
                    if hasattr(las.header, 'parse_crs'):
                        crs_wkt = las.header.parse_crs()
                        if crs_wkt:
                            crs_obj = CRS.from_wkt(crs_wkt)
                            epsg = crs_obj.to_epsg()
                            if epsg: crs_epsg = f"EPSG:{epsg}"
                except:
                    pass
                

                max_points = 1_000_000
                if len(df) > max_points:
                    reply = QtWidgets.QMessageBox.question(
                        self, "Dużo punktów",
                        f"LiDAR ma {len(df):,} punktów.\n\nZaimportować tylko {max_points:,}?",
                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                        QtWidgets.QMessageBox.Yes
                    )
                    if reply == QtWidgets.QMessageBox.Yes:
                        df = df.sample(n=max_points, random_state=42)
                
                gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df['x'], df['y']), crs=crs_epsg)
                gdf = gdf.drop(columns=['x', 'y'])
                
                gdf.to_postgis(table_name, self.db.engine, if_exists='replace', index=False, chunksize=10000)
                
                try:
                    with self.db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                        conn.execute(text(f"CREATE INDEX idx_{table_name}_geom ON {table_name} USING GIST(geometry)"))
                        conn.execute(text(f"VACUUM ANALYZE {table_name}"))
                except Exception as e:
                    print(f"Ostrzeżenie przy tworzeniu indeksu: {e}")
                
                QtWidgets.QMessageBox.information(self, "Sukces", f"LiDAR XYZ zaimportowany do {table_name}")
                
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Błąd", f"Import LiDAR:\n{e}")
        
        else:
            try:
                import laspy
                import numpy as np
                from scipy.interpolate import griddata
                
                las = laspy.read(las_path)
                x = np.asarray(las.x, dtype='float64').flatten()
                y = np.asarray(las.y, dtype='float64').flatten()
                z = np.asarray(las.z, dtype='float64').flatten()

                resolution = 1.0
                x_min, x_max = x.min(), x.max()
                y_min, y_max = y.min(), y.max()
                x_grid = np.arange(x_min, x_max, resolution)
                y_grid = np.arange(y_min, y_max, resolution)
                xx, yy = np.meshgrid(x_grid, y_grid)
                
                zz = griddata((x, y), z, (xx, yy), method='nearest')
                
                geotiff_path = os.path.join(self.data_dir, f"{table_name}_dem.tif")
                
                try:
                    import rasterio
                    from rasterio.transform import from_bounds
                    crs_epsg = "EPSG:2180"
                    transform = from_bounds(x_min, y_min, x_max, y_max, zz.shape[1], zz.shape[0])
                    
                    with rasterio.open(geotiff_path, 'w', driver='COG', height=zz.shape[0], width=zz.shape[1], 
                                     count=1, dtype=zz.dtype, crs=crs_epsg, transform=transform) as dst:
                        dst.write(zz, 1)
                except Exception as e:
                    print(f"Rasterio error: {e}")

                tiles_dir = os.path.join(self.data_dir, f"{table_name}_tiles")
                os.makedirs(tiles_dir, exist_ok=True)
                
                gdal2tiles = self._find_tool("gdal2tiles.py") or self._find_tool("gdal2tiles")
                if gdal2tiles:
                    cmd = [gdal2tiles, "-z", "0-18", "-w", "all", geotiff_path, tiles_dir]
                    subprocess.run(cmd, capture_output=True, text=True)
                    QtWidgets.QMessageBox.information(self, "Sukces", f"LiDAR DEM stworzony w {tiles_dir}")
                
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Błąd", f"DEM generowanie:\n{e}")
                
    def generate_boundary_from_raster_action(self):

        from osgeo import gdal, ogr, osr
        from qgis.core import QgsRasterLayer, QgsVectorLayer, QgsProject
        import os

        raster_layer = self.get_target_layer(QgsRasterLayer)
        if not raster_layer:
            QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz raster (np. DTM), z którego chcesz pobrać obrys.")
            return

        src_tif = raster_layer.source().split("|")[0]
        out_vec, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Zapisz obrys wektorowy", "", "GPKG (*.gpkg)")
        if not out_vec: return

        self.status.showMessage("Generowanie obrysu wektorowego...")

        def task():

            ds = gdal.Open(src_tif)
            band = ds.GetRasterBand(1)
            mask_band = band.GetMaskBand() 

            driver = ogr.GetDriverByName("GPKG")
            if os.path.exists(out_vec): driver.DeleteDataSource(out_vec)
            
            out_ds = driver.CreateDataSource(out_vec)

            srs = osr.SpatialReference()
            srs.ImportFromWkt(ds.GetProjectionRef())

            out_layer = out_ds.CreateLayer("obrys", srs, ogr.wkbPolygon)

            fd = ogr.FieldDefn("DN", ogr.OFTInteger)
            out_layer.CreateField(fd)

            gdal.Polygonize(band, mask_band, out_layer, 0, [], callback=None)
            
            out_ds = None 
            return out_vec

        def finished(res_path):

            v_layer = QgsVectorLayer(res_path, f"Obrys_{raster_layer.name()}", "ogr")
            QgsProject.instance().addMapLayer(v_layer)
            self.status.showMessage("Obrys wygenerowany pomyślnie.", 5000)

        self.start_worker(task, result_callback=finished)
        
    def load_layer_from_postgis_action(self):
        if not self.db: 
            QtWidgets.QMessageBox.warning(self, "Info", "Połącz się najpierw z bazą.")
            return
            
        try:

            ls = self.db.get_available_layers()
            if not ls: 
                QtWidgets.QMessageBox.information(self, "Info", "Brak warstw w DB.")
                return

            display = []
            for r in ls:
                icon = "🗺️" if r[4] == 'VEK' else "⬛"
                display.append(f"{icon} {r[0]}.{r[1]} ({r[4]})")

            item, ok = QtWidgets.QInputDialog.getItem(self, "Wybierz", "Dostępne warstwy:", display, 0, False)
            
            if ok and item:
                idx = display.index(item)
                schema, table, geom_col, srid, layer_type = ls[idx]

                uri_str = self.db.conn_string.replace("postgresql://", "")

                if "@" in uri_str:
                    userpass, hostdb = uri_str.split("@")
                    if ":" in userpass:
                        u, p = userpass.split(":")
                    else:
                        u = userpass; p = ""
                        
                    if "/" in hostdb:
                        h, db = hostdb.rsplit("/", 1)
                    else:
                        h = hostdb; db = "gismooth"
                        
                    if ":" in h:
                        hp = h.split(":")
                        host_ip = hp[0]
                        port = hp[1]
                    else:
                        host_ip = h
                        port = "5432"
                else:

                    host_ip, port, db, u, p = "localhost", "5432", "gismooth", "postgres", "admin"
                uri = QgsDataSourceUri()
                uri.setConnection(host_ip, port, db, u, p)
                
                layer = None

                if layer_type == 'VEK':
                    uri.setDataSource(schema, table, geom_col)
                    layer = QgsVectorLayer(uri.uri(), table, "postgres")

                elif layer_type == 'RAST':

                    uri.setDataSource(schema, table, geom_col)
                    layer = QgsRasterLayer(uri.uri(), table, "postgresraster")


                if layer and layer.isValid():
                    self.add_layer_smart(layer)
                    self.status.showMessage(f"Pobrano {layer_type}: {table}", 5000)
                else:
                    QtWidgets.QMessageBox.warning(self, "Błąd", f"Nie udało się wczytać warstwy typu {layer_type}.")
                    
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Błąd", str(e))

    def export_pdf_action(self):
        if not export_map_to_pdf: return
        author, ok1 = QtWidgets.QInputDialog.getText(self, "Metryka", "Autor:", text="Igor Koniusz")
        if not ok1: return
        title, ok2 = QtWidgets.QInputDialog.getText(self, "Metryka", "Tytuł:", text="Analiza")
        if not ok2: return
        
        crs_dlg = QgsProjectionSelectionDialog(self)
        crs_dlg.setCrs(self.canvas.mapSettings().destinationCrs())
        if crs_dlg.exec():
            out, _ = QtWidgets.QFileDialog.getSaveFileName(self, "PDF", "", "PDF (*.pdf)")
            if out:
                self.start_worker(export_map_to_pdf, self.project, self.canvas, out, title, author, crs_dlg.crs())


    def update_web_map_content_action(self):
        if not HAS_FOLIUM: return
        
        valid_layers = self.canvas.layers()[::-1]
        if not valid_layers: 
            QtWidgets.QMessageBox.warning(self, "Info", "Brak warstw.")
            return

        out_html = os.path.join(self.data_dir, "index.html")
        cache_dir = os.path.join(self.data_dir, "web_cache")
        if not os.path.exists(cache_dir): os.makedirs(cache_dir)

        self.status.showMessage("Aktualizacja mapy...", 0)
        QtWidgets.QApplication.processEvents()

        try:
            web_gen = WebMapGenerator(self.data_dir)
            
            # Import potrzebny do wykrycia stylu "NoBrush"
            from qgis.PyQt.QtCore import Qt
            from qgis.core import QgsSymbol, QgsWkbTypes, QgsSimpleLineSymbolLayer

            count = 0
            
            for i, layer in enumerate(valid_layers):
                if not layer.isValid(): continue
                name = layer.name()
                provider = layer.providerType()
                source = layer.source()
                src_path = source.split("|")[0]

                if provider == "wms" or isinstance(layer, QgsRasterLayer) and provider == "wms":
                    try:
                        print(f"🔍 Przetwarzanie WMS: {name}")
    
                        uri = QgsDataSourceUri(source)
                        url = uri.param("url")
                        layers_id = uri.param("layers")
                        

                        if not url or not layers_id:
                            params = dict(item.split("=") for item in source.split("&") if "=" in item)
                            url = params.get("url")
                            layers_id = params.get("layers")


                        if url:
                            clean_url = url.split('?')[0]
                            fmt = "image/png" 
                            
                            print(f"🌍 Wysyłanie do Folium -> URL: {clean_url} | Layer: {layers_id}")

                            if web_gen.add_wms_layer(clean_url, layers_id, name, fmt):
                                count += 1
                                print(f"✅ Dodano WMS: {name}")
                            else:
                                print(f"❌ Generator odrzucił WMS: {name}")
                        else:
                            print(f"⚠️ Nie udało się wyodrębnić adresu URL z warstwy {name}")
                            
                    except Exception as e:
                        print(f"❌ Błąd krytyczny WMS {name}: {e}")

                elif isinstance(layer, QgsVectorLayer):
                    src_path = source.split("|")[0]
                    svg_name = None
                    geom_type = QgsWkbTypes.geometryType(layer.wkbType())
                    # Logika Cache (dla WFS/DB)
                    is_remote = (provider in ["postgres", "wfs", "memory"]) or (not os.path.exists(src_path))
                    if is_remote:
                        try:
                            from qgis.core import QgsVectorFileWriter, QgsCoordinateReferenceSystem
                            safe_name = "".join([c for c in name if c.isalnum()])
                            cache_file = os.path.join(cache_dir, f"cache_{safe_name}.geojson")
                            if os.path.exists(cache_file):
                                try: os.remove(cache_file)
                                except: pass
                            err = QgsVectorFileWriter.writeAsVectorFormat(
                                layer, cache_file, "UTF-8",
                                QgsCoordinateReferenceSystem("EPSG:4326"), "GeoJSON"
                            )
                            if err[0] == QgsVectorFileWriter.NoError: src_path = cache_file
                        except: continue
                    label_field = None
                    if layer.labelsEnabled():

                        label_field = layer.labeling().settings().fieldName

                    style_params = {
                        'color': '#3388ff',      
                        'fillColor': '#3388ff',  
                        'weight': 2,
                        'fillOpacity': 0.4,
                        'dashArray': None,       
                        'geomType': int(QgsWkbTypes.geometryType(layer.wkbType())),
                        'svgUrl': None,          
                        'labelField': label_field 
                    }

                    try:
                        renderer = layer.renderer()

                        if renderer:
                            sym = renderer.symbol()
                            if sym:

                                style_params['color'] = sym.color().name()
                                style_params['fillColor'] = sym.color().name()
                                
  
                                if sym.symbolLayerCount() > 1 and geom_type == QgsWkbTypes.LineGeometry:
                                    style_params['doubleLine'] = True
                                    style_params['color'] = sym.symbolLayer(0).color().name() # Obrys
                                    style_params['weight'] = max(4, int(sym.symbolLayer(0).width() * 4))
                                    style_params['inner_color'] = sym.symbolLayer(1).color().name() # Środek
                                    style_params['inner_weight'] = max(1, int(sym.symbolLayer(1).width() * 4))
                                else:
                                    sl0 = sym.symbolLayer(0)
  
                                    if hasattr(sl0, 'penStyle'):
                                        ps = sl0.penStyle()
                                        if ps == Qt.DashLine: style_params['dashArray'] = "10, 10"
                                        elif ps == Qt.DotLine: style_params['dashArray'] = "2, 5"
                                        elif ps == Qt.DashDotLine: style_params['dashArray'] = "10, 5, 2, 5"
                                    
                                    if style_params['geomType'] == QgsWkbTypes.PolygonGeometry:
                                        if hasattr(sl0, 'strokeColor'):
                                            style_params['color'] = sl0.strokeColor().name()
                                        if hasattr(sl0, 'fillColor'):
                                            style_params['fillColor'] = sl0.fillColor().name()
                                        if hasattr(sl0, 'strokeWidth'):
                                            style_params['weight'] = max(1, int(sl0.strokeWidth() * 3.5))

                                        if hasattr(sl0, 'brushStyle') and sl0.brushStyle() == Qt.NoBrush:
                                            style_params['fillOpacity'] = 0.0
                                        else:
                                            style_params['fillOpacity'] = sym.opacity()

                                    elif style_params['geomType'] == QgsWkbTypes.LineGeometry:
                                        style_params['weight'] = max(1, int(sym.width() * 3.5))
                                        style_params['fillOpacity'] = 0.0

                                    else:
                                        size_px = max(20, int(sym.size() * 4))
                                        style_params['weight'] = size_px
                                        
                                        if sym.symbolLayerCount() > 0:
                                            sl0 = sym.symbolLayer(0)
                                            from qgis.core import QgsSvgMarkerSymbolLayer, QgsApplication
                                            if isinstance(sl0, QgsSvgMarkerSymbolLayer):
                                                import shutil
                                                svg_path = sl0.path()
                                                abs_svg_path = None

                                                if os.path.isabs(svg_path) and os.path.exists(svg_path):
                                                    abs_svg_path = svg_path
                                                else:
                                                    for p in QgsApplication.svgPaths():
                                                        test_p = os.path.join(p, svg_path)
                                                        if os.path.exists(test_p):
                                                            abs_svg_path = test_p
                                                            break
                                                
                                                if abs_svg_path:
                                                    clean_name = os.path.basename(abs_svg_path).lower().replace(" ", "_")
                                                    for pl, en in zip("ąćęłńóśźż", "acelnoszz"): clean_name = clean_name.replace(pl, en)
                                                    
                                                    dest = os.path.join(cache_dir, clean_name)
                                                    shutil.copy2(abs_svg_path, dest)
                                                    style_params['svgUrl'] = f"web_cache/{clean_name}"
                                                    print(f"✅ Ikona skopiowana: {clean_name}")
                    except Exception as ex:
                        print(f"Błąd stylu: {ex}")

                    if web_gen.add_vector_layer(src_path, name, style_params=style_params):
                        count += 1

                elif isinstance(layer, QgsRasterLayer) and provider == "gdal":
                    if src_path.lower().endswith(('.tif', '.tiff', '.asc')):
                        if web_gen.add_raster_layer(src_path, name): count += 1

            if count > 0:
                web_gen.save_map(out_html)
                self.status.showMessage("Mapa zaktualizowana.", 5000)
            else:
                QtWidgets.QMessageBox.warning(self, "Pusto", "Brak warstw.")

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Błąd", str(e))

    def open_web_map_url_action(self):

        url = "http://localhost:8000/index.html"
        
        # Sprawdzamy czy plik w ogóle istnieje
        index_path = os.path.join(self.data_dir, "index.html")
        if not os.path.exists(index_path):
            QtWidgets.QMessageBox.warning(self, "Brak mapy", "Najpierw kliknij 'Aktualizuj treść mapy', aby wygenerować plik.")
            return

        import webbrowser
        webbrowser.open(url)
        
    def publish_current_postgis_layer_action(self):

        try:
            from core.geoserver_publish import GeoServerPublisher
            from qgis.core import QgsVectorLayer, QgsProject
        except ImportError:
            return

        if not self.db or not self.db.conn_string:
            QtWidgets.QMessageBox.warning(self, "Błąd", "Najpierw połącz się z bazą danych PostGIS.")
            return

        try:
            # Format: postgresql://user:pass@host:port/dbname
            uri = self.db.conn_string.replace("postgresql://", "")
            user_pass, host_db = uri.split("@")
            db_user, db_pass = user_pass.split(":")
            host_port, db_name = host_db.split("/")
            if ":" in host_port:
                db_host, db_port = host_port.split(":")
            else:
                db_host, db_port = host_port, "5432"
        except:
            QtWidgets.QMessageBox.critical(self, "Błąd", "Nie udało się sparsować parametrów połączenia z bazą.")
            return

        available_layers = self.db.get_available_layers()

        vector_options = [f"{row[1]}" for row in available_layers if row[4] == 'VEK']
        
        if not vector_options:
            QtWidgets.QMessageBox.warning(self, "Błąd", "Brak tabel wektorowych w bazie.")
            return

        table, ok = QtWidgets.QInputDialog.getItem(self, "Publikacja", "Wybierz tabelę wektorową:", vector_options, 0, False)
        if not ok: return

        selected_row = [r for r in available_layers if r[1] == table][0]
        srs_code = f"EPSG:{selected_row[3]}"

        # 3. Dane GeoServera
        gs_url, ok = QtWidgets.QInputDialog.getText(self, "GeoServer", "URL API:", text="http://localhost:8080/geoserver")
        if not ok: return
        gs_user, ok = QtWidgets.QInputDialog.getText(self, "User", "Użytkownik:", text="admin")
        if not ok: return
        gs_pass, ok = QtWidgets.QInputDialog.getText(self, "Pass", "Hasło:", text="geoserver")
        if not ok: return
        workspace, ok = QtWidgets.QInputDialog.getText(self, "Workspace", "Nazwa Workspace:", text="inzynierka")
        if not ok: return

        try:
            self.status.showMessage(f"Publikowanie warstwy: {table}...", 0)
            QtWidgets.QApplication.processEvents()

            gp = GeoServerPublisher(gs_url, gs_user, gs_pass)
            gp.create_workspace(workspace)

            store_name = "postgis_db"
            gp.create_postgis_datastore(workspace, store_name, db_host, db_port, db_name, db_user, db_pass)

            success = gp.publish_table_as_layer(workspace, store_name, table, native_srs=srs_code)

            if success:
                wfs_uri = f"{gs_url}/{workspace}/ows?service=WFS&version=1.0.0&request=GetFeature&typeName={workspace}:{table}"
                new_layer = QgsVectorLayer(wfs_uri, f"[GeoServer WFS] {table}", "WFS")
                
                if new_layer.isValid():
                    QgsProject.instance().addMapLayer(new_layer)
                    QtWidgets.QMessageBox.information(self, "Sukces", f"Warstwa '{table}' została opublikowana!")
                else:
                    QtWidgets.QMessageBox.warning(self, "Uwaga", "Opublikowano, ale wystąpił błąd ładowania podglądu (sprawdź zasięg).")
            else:
                raise RuntimeError("GeoServer nie mógł przeliczyć granic warstwy.")

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Błąd", f"Szczegóły błędu:\n{str(e)}")
        finally:
            self.status.clearMessage()

    
    def start_worker(self, func, *args, **kwargs):

        result_path = kwargs.pop('result_path', None)
        result_callback = kwargs.pop('result_callback', None) 
        
        worker = Worker(func, *args, **kwargs)
        
        def on_success(res):
            self.status.showMessage("Zadanie zakończone.", 5000)

            if result_callback:
                result_callback(res)

            elif result_path and os.path.exists(result_path):
                name = os.path.basename(result_path)
                if result_path.lower().endswith(('.tif', '.asc', '.tiff')):
                    l = QgsRasterLayer(result_path, name)
                else:
                    l = QgsVectorLayer(result_path, name, "ogr")
                self.add_layer_smart(l)

        worker.finished.connect(on_success)
        worker.error.connect(lambda e: QtWidgets.QMessageBox.critical(self, "Błąd", str(e)))
        self.workers.append(worker)
        worker.start()
        
class ClickIdentifyTool(QgsMapToolIdentify):
    def __init__(self, canvas, main_window):
        super().__init__(canvas)
        self.main_window = main_window
    
    def canvasReleaseEvent(self, event):

        results = self.identify(event.x(), event.y(), self.TopDownStopAtFirst, self.VectorLayer)
        
        if results:
            result = results[0] 
            feature = result.mFeature
            layer = result.mLayer

            self.main_window.show_feature_popup(feature, layer)