import os
import sys
import subprocess
import shutil
import http.server
import socketserver
import threading
from qgis.gui import QgsProjectionSelectionDialog, QgsScaleWidget
from qgis.PyQt import QtWidgets, QtGui, QtCore

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
    
# --- IMPORTY QGIS ---
from qgis.core import (
    QgsProject, 
    QgsVectorLayer, 
    QgsRasterLayer, 
    QgsLayerTreeModel, 
    QgsVectorFileWriter,
    QgsPointCloudLayer,
    QgsCoordinateReferenceSystem, 
    QgsCoordinateTransform,
    QgsDataSourceUri,
    QgsRectangle
)
from qgis.gui import QgsMapCanvas, QgsLayerTreeMapCanvasBridge, QgsLayerTreeView
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
        extract_by_attribute, validate_geometry
    )
except ImportError:
    compute_slope_raster = vector_buffer = generate_contours = None
    compute_aspect_raster = compute_hillshade_raster = None
    clip_vector_geopandas = centroids_geopandas = None

try:
    from core.data_io import load_vector, load_raster
except ImportError:
    load_vector = load_raster = None

try:
    from core.benchmark import Benchmarker
except ImportError:
    Benchmarker = None

try:
    from core.map_tools import export_map_to_pdf, apply_basic_style, apply_raster_colormap
except ImportError:
    export_map_to_pdf = apply_basic_style = apply_raster_colormap = None

try:
    from core.workers import Worker
except ImportError:
    Worker = None
try:
    from core.analytics import PerformanceTester
except ImportError:
    PerformanceTester = None
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
        self.tabs.addTab(self.tab_db, "PostGIS")
        self._build_tab_db()

        self.tab_terminal = QtWidgets.QWidget()
        self.tabs.addTab(self.tab_terminal, "Terminal GDAL")
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
            
        self.load_default_basemap()

    # --- BUILDERS ---
    # --- NOWA METODA POMOCNICZA ---
    def get_target_layer(self, layer_type):
        """
        Zwraca warstwę do analizy.
        Priorytet 1: Warstwa zaznaczona myszką w drzewku.
        Priorytet 2: Ostatnio wczytana warstwa (fallback).
        """
        # 1. Sprawdź co jest zaznaczone w legendzie
        idxs = self.layer_tree_view.selectionModel().selectedRows()
        if idxs:
            node = self.layer_tree_view.index2node(idxs[0])
            if node and node.layer():
                layer = node.layer()
                # Sprawdź czy typ się zgadza (np. czy to Raster)
                if isinstance(layer, layer_type):
                    return layer
        
        # 2. Jeśli nic nie zaznaczono, weź ostatnią dodaną (stara logika)
        if layer_type == QgsRasterLayer: return self.last_raster_layer
        if layer_type == QgsVectorLayer: return self.last_vector_layer
        if layer_type == QgsPointCloudLayer: return self.last_point_cloud_layer
        
        return None
        
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
        
        btn_style = QtWidgets.QPushButton("🎨 Auto Styl")
        btn_style.clicked.connect(self.auto_style_action)
        
        btn_rename = QtWidgets.QPushButton("✏️ Zmień nazwę")
        btn_rename.clicked.connect(self.rename_layer_action)
        btn_rem = QtWidgets.QPushButton("❌ Usuń")
        btn_rem.clicked.connect(self.remove_layer_action)
        
        btn_lidar = QtWidgets.QPushButton("☁️ Wczytaj LiDAR (LAS)")
        btn_lidar.clicked.connect(self.load_point_cloud_action)
        btn_3d = QtWidgets.QPushButton("🧊 Podgląd 3D (Nowe Okno)")
        btn_3d.clicked.connect(self.open_3d_viewer_action)
        
        layout.addWidget(btn_vec)
        layout.addWidget(btn_rast)
        layout.addWidget(btn_lidar)
        layout.addSpacing(10)
        
        layout.addWidget(QtWidgets.QLabel("<b>Usługi sieciowe:</b>"))
        
        btn_wms = QtWidgets.QPushButton("🌐 Wczytaj z WMS")
        btn_wms.clicked.connect(self.load_wms_action)
        
        btn_wfs = QtWidgets.QPushButton("🌐 Wczytaj z WFS")
        btn_wfs.clicked.connect(self.load_wfs_action)
        
        layout.addWidget(btn_wms)
        layout.addWidget(btn_wfs)
        
        layout.addWidget(QtWidgets.QLabel("<b>Operacje na warstwach:</b>"))
        layout.addWidget(btn_rename)
        layout.addWidget(btn_style)
        layout.addWidget(btn_rem)
        
        layout.addSpacing(10)
        layout.addWidget(QtWidgets.QLabel("<b>Widok 3D:</b>"))
        layout.addWidget(btn_3d)

    def _build_tab_analysis(self):
        l = QtWidgets.QVBoxLayout(self.tab_analysis); l.setAlignment(QtCore.Qt.AlignTop)
        
        l.addWidget(QtWidgets.QLabel("<b>Raster (GDAL):</b>"))
        for t, f in [("⛰ Slope", self.compute_slope_action), ("🧭 Aspect", self.compute_aspect_action),
                     ("🌑 Hillshade", self.compute_hillshade_action), ("〰 Warstwice", self.generate_contours_action)]:
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
        
        # Sekcja PDF
        layout.addWidget(QtWidgets.QLabel("<b>Eksport Statyczny:</b>"))
        btn_pdf = QtWidgets.QPushButton("📄 Eksport PDF")
        btn_pdf.clicked.connect(self.export_pdf_action)
        layout.addWidget(btn_pdf)
        
        layout.addSpacing(15)
        
        # Sekcja WebGIS (Rozdzielona)
        layout.addWidget(QtWidgets.QLabel("<b>Eksport Interaktywny (Web):</b>"))
        
        # Przycisk 1: Generowanie (Ciężka praca)
        btn_update_web = QtWidgets.QPushButton("🔄 Aktualizuj treść mapy (HTML)")
        btn_update_web.clicked.connect(self.update_web_map_content_action)
        btn_update_web.setStyleSheet("background-color: #ffaa00; font-weight: bold;") # Wyróżniamy go
        layout.addWidget(btn_update_web)
        
        # Przycisk 2: Otwieranie (Tylko link)
        btn_open_web = QtWidgets.QPushButton("🌍 Otwórz w przeglądarce")
        btn_open_web.clicked.connect(self.open_web_map_url_action)
        layout.addWidget(btn_open_web)
        
        layout.addSpacing(15)
        
        # Sekcja GeoServer
        layout.addWidget(QtWidgets.QLabel("<b>Serwer OGC:</b>"))
        btn_gs = QtWidgets.QPushButton("🌐 Publikuj GeoServer")
        btn_gs.clicked.connect(self.publish_current_postgis_layer_action)
        layout.addWidget(btn_gs)
    def _build_tab_benchmark(self):
        l = QtWidgets.QVBoxLayout(self.tab_benchmark)
        ctrl = QtWidgets.QHBoxLayout()
        
        self.combo_test = QtWidgets.QComboBox()
        self.combo_test.addItems([
            "1. I/O Odczyt (Wektor)", "2. Geometria - Bufor", "3. Topologia - Spatial Join",
            "4. Projekcje - Transformacja", "5. Atrybuty - Filtrowanie", "6. Iteracja vs Wektoryzacja",
            "7. Raster - Resampling", "8. Raster - Algebra", "9. Raster - Statystyki",
            "10. DB - Import", "11. DB - Eksport", 
            "12. LiDAR - Info", "13. LiDAR - Filtracja"
        ])
        
        self.spin_iter = QtWidgets.QSpinBox(); self.spin_iter.setRange(1,10); self.spin_iter.setValue(3)
        
        # --- NOWE: Przełącznik Metryki ---
        self.combo_metric = QtWidgets.QComboBox()
        self.combo_metric.addItems(["Czas [s]", "RAM [MB]"])
        # ---------------------------------

        btn = QtWidgets.QPushButton("🚀 Test"); btn.clicked.connect(self.run_benchmark_action)
        
        ctrl.addWidget(QtWidgets.QLabel("Test:")); ctrl.addWidget(self.combo_test)
        ctrl.addWidget(QtWidgets.QLabel("Metryka:")); ctrl.addWidget(self.combo_metric)
        ctrl.addWidget(QtWidgets.QLabel("Powt.:")); ctrl.addWidget(self.spin_iter)
        ctrl.addWidget(btn)
        l.addLayout(ctrl)
        
        if HAS_PLOTS:
            self.fig = Figure(figsize=(5,4), dpi=100); self.chart_canvas = FigureCanvas(self.fig); l.addWidget(self.chart_canvas)
        else: l.addWidget(QtWidgets.QLabel("Brak matplotlib."))
        
        self.res_table = QtWidgets.QTableWidget(); l.addWidget(self.res_table)
        self.txt_bench_results = QtWidgets.QTextEdit(); self.txt_bench_results.setMaximumHeight(100); l.addWidget(self.txt_bench_results)

    def update_benchmark_charts(self, data):
        if not data or not HAS_PLOTS: return
        typ, df = data
        if df is None or df.empty: self.txt_bench_results.append("Brak danych."); return
        
        # Tabela (pokazuje wszystko)
        self.res_table.setRowCount(len(df)); self.res_table.setColumnCount(len(df.columns))
        self.res_table.setHorizontalHeaderLabels(df.columns)
        for i, row in df.iterrows():
            for j, val in enumerate(row): 
                t = f"{val:.4f}" if isinstance(val, (int, float)) else str(val)
                self.res_table.setItem(i, j, QtWidgets.QTableWidgetItem(t))
        
        # Wykres (zależny od wyboru użytkownika)
        metric = self.combo_metric.currentText() # "Czas [s]" lub "RAM [MB]"
        
        self.fig.clear(); ax = self.fig.add_subplot(111)
        
        # Kolory: Niebieski dla czasu, Czerwony dla RAMu
        bar_color = '#4e79a7' if "Czas" in metric else '#e15759'
        
        if "Nazwa" in df.columns and metric in df.columns:
            bars = ax.bar(df["Nazwa"], df[metric], color=bar_color)
            ax.set_ylabel(metric)
            ax.set_title(f"Porównanie: {metric}")
            ax.bar_label(bars, fmt='%.2f')
        
        elif "Liczba Obiektów" in df.columns: # Skalowalność
            ax.plot(df["Liczba Obiektów"], df["Czas [s]"], marker='o')

        self.chart_canvas.draw()
        self.txt_bench_results.append("Zakończono.")
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

    # --- LOGIKA TERMINALA ---
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
                # Wyłączamy logowanie do konsoli, żeby nie śmiecić
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
        if not PerformanceTester: return
        
        test_idx = self.combo_test.currentIndex() # 0-based index (0 = Test 1)
        iters = self.spin_iter.value()
        
        # Detekcja warstwy
        target_layer = None
        
        # Wektory: 0,1,2,3,4,5,9,10
        if test_idx in [0, 1, 2, 3, 4, 5, 9, 10]:
            target_layer = self.get_target_layer(QgsVectorLayer)
            if not target_layer: QtWidgets.QMessageBox.warning(self, "Info", "Wymagany Wektor."); return
            
        # Rastry: 6,7,8
        elif test_idx in [6, 7, 8]:
            target_layer = self.get_target_layer(QgsRasterLayer)
            if not target_layer: QtWidgets.QMessageBox.warning(self, "Info", "Wymagany Raster."); return
            
        # LiDAR: 11,12
        elif test_idx in [11, 12]:
            target_layer = self.get_target_layer(QgsPointCloudLayer)
            if not target_layer: QtWidgets.QMessageBox.warning(self, "Info", "Wymagana Chmura (LAS)."); return

        src = target_layer.source().split("|")[0]
        self.txt_bench_results.append(f"🚀 Start testu {test_idx+1}: {os.path.basename(src)}...")
        QtWidgets.QApplication.processEvents()
        
        conn = self.db.conn_string if self.db else None

        def run_test():
            t = PerformanceTester(conn)
            
            if test_idx == 0: return "bar", t.bench_vector_io_read(src)
            elif test_idx == 1: return "bar", t.bench_vector_buffer(src)
            elif test_idx == 2: return "bar", t.bench_vector_spatial_join(src)
            elif test_idx == 3: return "bar", t.bench_vector_reprojection(src)
            elif test_idx == 4: return "bar", t.bench_vector_attribute_filter(src)
            elif test_idx == 5: return "bar", t.bench_vector_iteration(src)
            
            elif test_idx == 6: return "bar", t.bench_raster_resample(src)
            elif test_idx == 7: return "bar", t.bench_raster_slope(src)
            elif test_idx == 8: return "bar", t.bench_raster_stats(src)
            
            elif test_idx == 9: return "bar", t.bench_db_import(src)
            elif test_idx == 10: return "bar", t.bench_db_export(src)
            
            elif test_idx == 11: return "bar", t.bench_lidar_info(src)
            elif test_idx == 12: return "bar", t.bench_lidar_filter(src)
            
        self.start_worker(run_test, result_callback=self.update_benchmark_charts)

    def update_benchmark_charts(self, data):
        if not data or not HAS_PLOTS: return
        typ, df = data
        if df is None or df.empty: 
            self.txt_bench_results.append("Brak danych do wykresu.")
            return
        
        # 1. Aktualizacja Tabeli
        self.res_table.setRowCount(len(df))
        self.res_table.setColumnCount(len(df.columns))
        self.res_table.setHorizontalHeaderLabels(df.columns)
        for i, row in df.iterrows():
            for j, val in enumerate(row):
                # Ładne formatowanie liczb
                txt = f"{val:.4f}" if isinstance(val, (float, int)) and not isinstance(val, bool) else str(val)
                self.res_table.setItem(i, j, QtWidgets.QTableWidgetItem(txt))
        
        # 2. Aktualizacja Wykresu
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        
        # Kolory dla wykresów
        colors = ['#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f']

        # --- LOGIKA RYSOWANIA ---
        # Obsługujemy uniwersalny typ "bar" wysyłany przez nowe testy
        
        if typ == "bar" or typ == "engine" or typ == "format":
            # Wykres słupkowy
            # Zakładamy, że 1. kolumna to Nazwa, 2. to Czas
            x_col = df.columns[0]
            y_col = df.columns[1]
            
            bars = ax.bar(df[x_col], df[y_col], color=colors[:len(df)])
            
            ax.set_ylabel(y_col)
            ax.set_title(f"Wyniki: {x_col} vs {y_col}")
            ax.grid(axis='y', linestyle='--', alpha=0.7)
            
            # Dodanie etykiet z wartościami nad słupkami
            try:
                ax.bar_label(bars, fmt='%.3f')
            except: pass # Starsze wersje matplotlib tego nie mają

        elif typ == "scale" or typ == "line":
            # Wykres liniowy (dla skalowalności)
            x_col = df.columns[0]
            y_col = df.columns[1]
            
            ax.plot(df[x_col], df[y_col], marker='o', linestyle='-', color='#e15759', linewidth=2)
            ax.set_xlabel(x_col)
            ax.set_ylabel(y_col)
            ax.set_title("Test Skalowalności")
            ax.grid(True)

        self.fig.tight_layout()
        self.chart_canvas.draw()
        
        self.txt_bench_results.append("✅ Wykres zaktualizowany.")
    def add_layer_smart(self, layer):
        """Dodaje warstwę i bezpiecznie ustawia widok."""
        if not layer.isValid():
            print("Błąd: Warstwa niepoprawna (isValid=False)")
            return False
        
        # Fix dla LiDAR
        if isinstance(layer, QgsPointCloudLayer) and not layer.crs().isValid():
            layer.setCrs(QgsCoordinateReferenceSystem("EPSG:2180"))

        # Dodanie do projektu (To powinno sprawić, że pojawi się w legendzie)
        self.project.addMapLayer(layer)
        
        # Aktualizacja stanu
        if isinstance(layer, QgsRasterLayer): self.last_raster_layer = layer
        elif isinstance(layer, QgsVectorLayer): self.last_vector_layer = layer
        elif isinstance(layer, QgsPointCloudLayer): self.last_point_cloud_layer = layer

        # Próba Zoomu (zabezpieczona)
        try:
            # Jeśli warstwa jest WFS, jej extent może być pusty na początku
            extent = layer.extent()
            
            # Sprawdzamy czy extent jest poprawny matematycznie
            if extent.isEmpty() or not extent.isFinite():
                # Nie robimy zoomu, zostawiamy widok tam gdzie jest (użytkownik musi sam przybliżyć)
                print("Info: Warstwa ma pusty zasięg (WFS?), pomijam auto-zoom.")
            else:
                # Standardowy zoom z transformacją
                tc = self.canvas.mapSettings().destinationCrs()
                if layer.crs() != tc:
                    tr = QgsCoordinateTransform(layer.crs(), tc, self.project)
                    ext = tr.transformBoundingBox(extent)
                    if ext.isFinite(): self.canvas.setExtent(ext)
                else:
                    self.canvas.setExtent(extent)
        except Exception as e:
            print(f"Błąd zoomu: {e}")
            # Nie robimy nic, żeby nie zepsuć widoku

        self.canvas.refresh()
        return True

    def load_default_basemap(self):
        """Ładuje podkład OpenStreetMap i ustawia widok na Polskę."""
        uri = "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&zmax=19&zmin=0"
        osm = QgsRasterLayer(uri, "OpenStreetMap", "wms")
        
        if osm.isValid():
            self.project.addMapLayer(osm)
            
            # --- USTAWIENIE WIDOKU NA POLSKĘ (EPSG:3857) ---
            # Współrzędne: xMin, yMin, xMax, yMax (w metrach Mercatora)
            poland_extent = QgsRectangle(1500000, 6250000, 2700000, 7450000)
            self.canvas.setExtent(poland_extent)
            # -----------------------------------------------
            
            self.canvas.refresh()
        else:
            print("Błąd: Nie udało się pobrać podkładu mapowego")
    def change_basemap_action(self):
        """Pozwala wybrać jeden z popularnych podkładów mapowych."""
        
        # Słownik dostępnych map (Nazwa : URI)
        # type=xyz oznacza kafelki (szybkie), context... to WMS (Geoportal)
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
            
            # Tworzenie warstwy
            # Dla XYZ i WMS provider to zawsze "wms" w QGIS
            layer = QgsRasterLayer(uri, name, "wms")
            
            if layer.isValid():
                self.project.addMapLayer(layer)
                
                # Przesuwamy warstwę na sam dół (żeby nie zasłoniła Twoich danych)
                root = self.project.layerTreeRoot()
                node = root.findLayer(layer.id())
                clone = node.clone()
                root.addChildNode(clone)
                root.removeChildNode(node)
                
                self.status.showMessage(f"Wczytano podkład: {name}", 3000)
            else:
                QtWidgets.QMessageBox.warning(self, "Błąd", "Nie udało się wczytać podkładu (sprawdź internet).")
    # --- AKCJE DANYCH ---

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

        # Usuwamy foldery _copc i pliki .inf, żeby QGIS musiał przeliczyć je od nowa
        # z naszym wymuszonym układem.
        folder = os.path.dirname(path)
        filename = os.path.basename(path)
        base_name = os.path.splitext(filename)[0]
        
        # Lista potencjalnych śmieci tworzonych przez QGIS
        junk_paths = [
            os.path.join(folder, base_name + "_copc"),      # Folder COPC
            os.path.join(folder, base_name + "_ept"),       # Folder EPT
            os.path.join(folder, filename + ".inf")       # Plik info  
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
    # --- USŁUGI SIECIOWE (WMS/WFS) ---

    def load_wms_action(self):
        """
        1. Pyta o URL.
        2. Worker pobiera listę warstw przez OWSLib.
        3. Użytkownik wybiera.
        4. QGIS ładuje warstwę.
        """
        if not OWSClient:
            QtWidgets.QMessageBox.critical(self, "Błąd", "Brak modułu ows_client (lub biblioteki OWSLib).")
            return

        # Przykładowy URL (Geoportal Ortofotomapa)
        default_url = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/StandardResolution"
        url, ok = QtWidgets.QInputDialog.getText(self, "WMS", "Podaj adres URL usługi WMS:", text=default_url)
        
        if not ok or not url: return

        # Funkcja dla workera
        def fetch_layers():
            return OWSClient.get_wms_layers(url)

        # Callback po pobraniu listy
        def on_layers_fetched(layers):
            if not layers:
                QtWidgets.QMessageBox.warning(self, "Info", "Usługa nie zwróciła żadnych warstw.")
                return
            
            # Lista do wyświetlenia: "Tytuł (Nazwa Techniczna)"
            display_list = [f"{title} ({name})" for name, title in layers]
            item, ok = QtWidgets.QInputDialog.getItem(self, "Wybierz Warstwę", "Dostępne warstwy WMS:", display_list, 0, False)
            
            if ok and item:
                idx = display_list.index(item)
                layer_name = layers[idx][0] # Nazwa techniczna
                layer_title = layers[idx][1]
                
                # Konstrukcja URI dla QGIS WMS Provider
                # QGIS sam ogarnie resztę parametrów (bbox, width, height)
                uri = f"url={url}&layers={layer_name}&format=image/png&styles="
                
                # Tworzenie warstwy
                rlayer = QgsRasterLayer(uri, layer_title, "wms")
                self.add_layer_smart(rlayer)
                self.status.showMessage(f"Dodano WMS: {layer_title}", 5000)

        self.status.showMessage("Pobieranie Capabilities serwera WMS...")
        self.start_worker(fetch_layers, result_callback=on_layers_fetched)

    def load_wfs_action(self):
        if not OWSClient: 
            QtWidgets.QMessageBox.critical(self, "Błąd", "Brak modułu OWSClient.")
            return

        # Domyślny URL (iKERG Kalisz)
        default_url = "https://ikerg.um.kalisz.pl/kalisz-egib"
        url_input, ok = QtWidgets.QInputDialog.getText(self, "WFS", "Adres usługi WFS:", text=default_url)
        
        if not ok or not url_input: return

        # 1. Czyszczenie URL (usuwamy ?request=...)
        base_url = url_input.split("?")[0]

        # 2. Pobieranie listy w tle
        def fetch(): 
            return OWSClient.get_wfs_layers(base_url)
        
        def done(layers):
            if not layers:
                QtWidgets.QMessageBox.warning(self, "Info", "Nie znaleziono warstw (lub błąd sieci).")
                return
            
            # Wyświetl listę: Tytuł (Nazwa_Techniczna)
            display_list = [f"{t} ({n})" for n, t in layers]
            item, ok = QtWidgets.QInputDialog.getItem(self, "Wybierz Warstwę", "Dostępne warstwy:", display_list, 0, False)
            
            if ok and item:
                idx = display_list.index(item)
                layer_name = layers[idx][0] # To jest 'typename' (np. 'egib:dzialki')
                layer_title = layers[idx][1]
                
                # --- PROFESJONALNA KONSTRUKCJA URI ---
                # Używamy klasy QgsDataSourceUri, która sformatuje to tak, jak robi to QGIS Desktop.
                
                uri = QgsDataSourceUri()
                uri.setParam("url", base_url)
                uri.setParam("typename", layer_name)
                
                # Wersja 1.0.0 jest najbezpieczniejsza dla polskich geoportali (unika problemu zamiany X/Y)
                uri.setParam("version", "1.0.0") 
                
                # Wymuszenie układu 2180 (Kluczowe dla Polski)
                uri.setParam("srsname", "EPSG:2180")
                
                # WAŻNE: Nie dodajemy pustych parametrów sql= ani table="", bo iKERG tego nie lubi!
                
                print(f"Próba ładowania WFS: {uri.uri()}")
                
                # Tworzenie warstwy
                vlayer = QgsVectorLayer(uri.uri(), layer_title, "WFS")
                
                if self.add_layer_smart(vlayer):
                    self.status.showMessage(f"Dodano WFS: {layer_title}", 5000)
                    QtWidgets.QMessageBox.information(self, "Sukces", 
                        f"Warstwa '{layer_title}' dodana.\n\n"
                        "Jeśli jest pusta, przybliż mapę do obszaru Kalisza i przesuń widok.")
                else:
                    # Jeśli się nie uda, spróbujmy bez wymuszania wersji (niech QGIS negocjuje)
                    print("Próba nr 2 (Auto-negocjacja)...")
                    uri = QgsDataSourceUri()
                    uri.setParam("url", base_url)
                    uri.setParam("typename", layer_name)
                    vlayer2 = QgsVectorLayer(uri.uri(), layer_title, "WFS")
                    
                    if self.add_layer_smart(vlayer2):
                         self.status.showMessage(f"Dodano WFS (Auto): {layer_title}", 5000)
                    else:
                         QtWidgets.QMessageBox.warning(self, "Błąd", "Nie udało się załadować warstwy. Serwer może wymagać autoryzacji.")

        self.status.showMessage("Pobieranie metadanych WFS...")
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

    def auto_style_action(self):
        # Jeśli zaznaczono w drzewku, użyj zaznaczonej, inaczej ostatniej dodanej
        idx = self.layer_tree_view.selectionModel().selectedRows()
        layer = None
        if idx:
            layer = self.layer_tree_view.index2node(idx[0]).layer()
        
        if not layer:
            # Fallback
            if self.last_vector_layer: layer = self.last_vector_layer
            elif self.last_raster_layer: layer = self.last_raster_layer
        
        if not layer: return

        if isinstance(layer, QgsVectorLayer) and apply_basic_style:
            colors = ["red", "blue", "green", "orange", "magenta", "black"]
            c, ok = QtWidgets.QInputDialog.getItem(self, "Styl", "Kolor:", colors, 0, False)
            if ok: apply_basic_style(layer, c)
        elif isinstance(layer, QgsRasterLayer) and apply_raster_colormap:
            ramps = ["Spectral", "Viridis", "Magma", "RdYlGn", "Grayscale"]
            r, ok = QtWidgets.QInputDialog.getItem(self, "Styl", "Paleta:", ramps, 0, False)
            if ok: apply_raster_colormap(layer, r)

    # --- AKCJE ANALIZ ---

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
        l = self.get_target_layer(QgsVectorLayer)
        if not l: 
            QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę rastrową.")
            return
        src = l.source().split("|")[0]
        out, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Zapisz", "", "GPKG (*.gpkg)")
        if out:
            i, ok = QtWidgets.QInputDialog.getDouble(self, "Interwał", "Metry:", 10, 0.1, 10000, 2)
            if ok: self.start_worker(generate_contours, src, out, interval=i, result_path=out)
    def validate_geometry_action(self):
        l = self.get_target_layer(QgsVectorLayer)
        if not l: 
             QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę wektorową.")
             return
        
        src = l.source().split("|")[0]
        
        self.status.showMessage("Trwa walidacja topologii...", 0)
        QtWidgets.QApplication.processEvents()
        
        # Uruchamiamy to synchronicznie (szybkie) lub w workerze
        # Tu zrobimy prosto, bo chcemy wyświetlić tekst
        from core.processing import validate_geometry # Local import for safety
        report = validate_geometry(src)
        
        # Wyświetlamy raport w oknie
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
        l = self.get_target_layer(QgsVectorLayer)
        if not l: 
            QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę wektorową.")
            return
        m, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Maska", "", "Wektor (*.shp *.gpkg)")
        if m:
            s = l.source().split("|")[0]
            o, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Zapisz", "", "SHP (*.shp)")
            if o: self.start_worker(clip_vector_geopandas, s, m, o, result_path=o)

    def compute_centroids_action(self):
        l = self.get_target_layer(QgsVectorLayer)
        if not l: 
            QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę wektorową.")
            return
        s = l.source().split("|")[0]
        o, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Zapisz", "", "SHP (*.shp)")
        if o: self.start_worker(centroids_geopandas, s, o, result_path=o)
    def extract_feature_action(self):
        # 1. Pobierz warstwę wektorową
        layer = self.get_target_layer(QgsVectorLayer)
        if not layer: 
            QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę z dzielnicami.")
            return
        
        # 2. Pobierz listę pól (kolumn)
        fields = layer.fields()
        field_names = [f.name() for f in fields]
        
        if not field_names:
            QtWidgets.QMessageBox.warning(self, "Info", "Ta warstwa nie ma atrybutów.")
            return

        # 3. Zapytaj użytkownika o Kolumnę (np. "nazwa_dzielnicy")
        col_name, ok = QtWidgets.QInputDialog.getItem(self, "Krok 1/2", "Wybierz kolumnę (atrybut):", field_names, 0, False)
        if not ok: return
        
        # 4. Pobierz unikalne wartości z tej kolumny (żeby zrobić listę wyboru)
        # Używamy indeksu pola
        idx = fields.indexFromName(col_name)
        unique_values = layer.uniqueValues(idx)
        # Sortujemy i konwertujemy na napisy
        values_str = sorted([str(v) for v in unique_values])
        
        # 5. Zapytaj użytkownika o Wartość (np. "Śródmieście")
        val_str, ok = QtWidgets.QInputDialog.getItem(self, "Krok 2/2", f"Wybierz wartość z '{col_name}':", values_str, 0, False)
        if not ok: return
        
        # 6. Wybierz plik zapisu
        src = layer.source().split("|")[0]
        out, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Zapisz wynik", "", "SHP (*.shp);;GPKG (*.gpkg)")
        
        if out:
            # Uruchom worker
            self.start_worker(extract_by_attribute, src, out, column=col_name, value=val_str, result_path=out)
    
    # --- AKCJE PDAL (LiDAR) ---

    def compute_dsm_action(self):
        """Generuje Model Powierzchni (drzewa, budynki)."""
        # Sprawdzamy czy mamy chmurę punktów
        # Może być w last_point_cloud_layer, a jeśli nie, to może użytkownik ma zaznaczone w drzewku
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
                
                # --- PRÓBA 1: Czytanie ze statystyk (dokładne) ---
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

                # --- PRÓBA 2: Czytanie z summary ---
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

                # Wyświetlenie w ładnym oknie
                box = QtWidgets.QMessageBox(self)
                box.setWindowTitle("PDAL Info")
                box.setTextFormat(QtCore.Qt.RichText)
                box.setText(msg)
                box.exec()

            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Błąd parsowania", f"{e}\n\nRaw: {str(json_str)[:200]}")

        self.status.showMessage("Skanowanie pliku LAS... (To może chwilę potrwać)", 0)
        self.start_worker(get_info, result_callback=show_info)

    # --- DB & EXPORT ---

    def connect_db_action(self):
        if not PostGISConnector: return
        
        # Domyślny string
        default_conn = f"postgresql://{PG_USER}:{PG_PASS}@localhost:5432/{PG_DB}"
        conn, ok = QtWidgets.QInputDialog.getText(self, "DB", "Conn String:", text=default_conn)
        
        if ok:
            try:
                # Wyciągamy nazwę bazy
                try: dbname = conn.rsplit("/", 1)[-1]
                except: dbname = "gismooth"
                
                self.status.showMessage(f"Łączenie z DB: {dbname}...", 0)
                QtWidgets.QApplication.processEvents()
                
                self.db = PostGISConnector(conn)
                # Upewniamy się, że baza istnieje
                self.db.ensure_database(dbname)
                self.db.connect()
                
                # --- NOWOŚĆ: SPRAWDZANIE MOŻLIWOŚCI ---
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

        if not HAS_OPEN3D:
            QtWidgets.QMessageBox.critical(self, "Błąd", 
                "Brakuje bibliotek 'open3d' i 'laspy'.\n"
                "Zainstaluj je komendą:\npip install open3d laspy")
            return

        # 1. Pobierz aktywną warstwę
        layer = self.get_target_layer(QgsPointCloudLayer)
        
        if not layer:
             QtWidgets.QMessageBox.warning(self, "Info", "Zaznacz warstwę chmury punktów (LiDAR), aby wyświetlić ją w 3D.")
             return
             
        # 2. Pobierz ścieżkę do pliku
        file_path = layer.source().split("|")[0]
        
        if not os.path.exists(file_path):
            QtWidgets.QMessageBox.critical(self, "Błąd", "Nie znaleziono pliku źródłowego.")
            return

        self.status.showMessage(f"Ładowanie chmury punktów do Open3D: {os.path.basename(file_path)}...", 0)
        QtWidgets.QApplication.processEvents()

        # 3. Uruchomienie wizualizacji w osobnym procesie (bezpieczne dla GUI)
        # Używamy prostego skryptu inline, żeby nie blokować okna QGIS
        try:
            las = laspy.read(file_path)
            
            # Pobranie współrzędnych
            # Centrujemy dane (Open3D nie lubi wielkich współrzędnych geodezyjnych)
            points = np.vstack((las.x, las.y, las.z)).transpose()
            center = np.mean(points, axis=0)
            points = points - center 

            # Tworzenie obiektu Open3D
            pcd = o3d.geometry.PointCloud()
            pcd.points = o3d.utility.Vector3dVector(points)

            # Kolory (jeśli są w pliku LAS)
            if hasattr(las, 'red'):
                # LAS zapisuje kolory jako 16-bit (0-65535), Open3D chce 0.0-1.0
                colors = np.vstack((las.red, las.green, las.blue)).transpose()
                # Normalizacja
                if np.max(colors) > 255:
                    colors = colors / 65535.0
                else:
                    colors = colors / 255.0
                pcd.colors = o3d.utility.Vector3dVector(colors)
            
            # Jeśli nie ma kolorów, kolorujemy wg wysokości (Z)
            else:
                z_vals = points[:, 2]
                # Prosta normalizacja 0-1 dla koloru
                z_norm = (z_vals - np.min(z_vals)) / (np.max(z_vals) - np.min(z_vals))
                # Mapa kolorów (niebieski -> czerwony)
                colors = plt.get_cmap("jet")(z_norm)[:, :3] # Wymaga matplotlib
                pcd.colors = o3d.utility.Vector3dVector(colors)

            self.status.showMessage("Otwieranie okna 3D...", 5000)
            
            # Uruchomienie okna
            o3d.visualization.draw_geometries([pcd], window_name=f"Podgląd 3D: {layer.name()}", width=1024, height=768)
            
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Błąd Open3D", f"Nie udało się wyświetlić chmury:\n{str(e)}")
            self.status.clearMessage()

    def upload_layer_to_postgis_action(self):
        if not self.last_vector_layer or not self.db: return
        tbl, ok = QtWidgets.QInputDialog.getText(self, "Tabela", "Nazwa:")
        if not ok: return
        items = ["EPSG:3857", "EPSG:4326", "EPSG:2180", "Bez zmian"]
        item, ok = QtWidgets.QInputDialog.getItem(self, "Układ", "Reprojekcja:", items, 0, False)
        if not ok: return
        srid = int(item.split(":")[0].replace("EPSG:", "")) if "EPSG" in item else None
        src = self.last_vector_layer.source().split("|")[0]
        self.start_worker(self.db.import_with_ogr2ogr, src, table_name=tbl, target_srid=srid)

    def load_layer_from_postgis_action(self):
        if not self.db: return
        try:
            ls = self.db.get_available_layers()
            if not ls: 
                QtWidgets.QMessageBox.information(self, "Info", "Brak warstw w DB.")
                return
            display = [f"{r[0]}.{r[1]} ({r[2]})" for r in ls]
            item, ok = QtWidgets.QInputDialog.getItem(self, "Wybierz", "Warstwa:", display, 0, False)
            if ok:
                idx = display.index(item)
                schema, table, geom, srid = ls[idx]
                
                uri_str = self.db.conn_string.replace("postgresql://", "")
                up, hd = uri_str.split("@")
                u, p = up.split(":")
                if "/" in hd: h, db = hd.rsplit("/", 1)
                else: h = hd; db = "gismooth"
                
                hp = h.split(":") if ":" in h else (h, "5432")
                
                uri = QgsDataSourceUri()
                uri.setConnection(hp[0], hp[1], db, u, p)
                uri.setDataSource(schema, table, geom)
                
                vl = QgsVectorLayer(uri.uri(), table, "postgres")
                self.add_layer_smart(vl)
                self.status.showMessage(f"Pobrano: {table}", 5000)
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
    # --- AKCJE WEBGIS (ROZDZIELONE) ---

    def update_web_map_content_action(self):
        if not HAS_FOLIUM: return
        
        valid_layers = self.canvas.layers()[::-1]
        if not valid_layers: 
            QtWidgets.QMessageBox.warning(self, "Info", "Brak warstw.")
            return

        out_html = os.path.join(self.data_dir, "index.html")
        # Folder cache do zrzucania WFS
        cache_dir = os.path.join(self.data_dir, "web_cache")
        if not os.path.exists(cache_dir): os.makedirs(cache_dir)

        self.status.showMessage("Aktualizacja mapy...", 0)
        QtWidgets.QApplication.processEvents()

        try:
            web_gen = WebMapGenerator(self.data_dir)
            colors = ['blue', 'green', 'red', 'purple', 'orange']
            count = 0
            
            for i, layer in enumerate(valid_layers):
                if not layer.isValid(): continue
                name = layer.name()
                provider = layer.providerType()
                
                # --- A. WMS ---
                if provider == "wms":
                    try:
                        uri = QgsDataSourceUri(layer.source())
                        url = uri.param("url")
                        layers_id = uri.param("layers")
                        fmt = uri.param("format") or "image/png"
                        
                        # Fallback parsowania
                        if not url and "url=" in layer.source():
                            url = layer.source().split("url=")[1].split("&")[0]
                        if not layers_id and "layers=" in layer.source():
                            layers_id = layer.source().split("layers=")[1].split("&")[0]

                        if url and layers_id:
                            if web_gen.add_wms_layer(url, layers_id, name, fmt):
                                count += 1
                    except: pass

                # --- B. WEKTOR (PLIK LOKALNY) ---
                elif isinstance(layer, QgsVectorLayer) and provider == "ogr":
                    src_path = layer.source().split("|")[0]
                    color = colors[i % len(colors)]
                    if web_gen.add_vector_layer(src_path, name, color):
                        count += 1

                # --- C. WEKTOR (WFS) - NOWOŚĆ! ---
                elif isinstance(layer, QgsVectorLayer) and provider.lower() == "wfs":
                    try:
                        self.status.showMessage(f"Pobieranie WFS: {name}...", 0)
                        QtWidgets.QApplication.processEvents()
                        
                        # Zrzucamy WFS do pliku GeoJSON
                        safe_name = "".join([c for c in name if c.isalnum()])
                        temp_geojson = os.path.join(cache_dir, f"wfs_{safe_name}.geojson")
                        
                        # Usuń stary jeśli jest
                        if os.path.exists(temp_geojson): os.remove(temp_geojson)
                        
                        # Zapisz warstwę do pliku
                        err = QgsVectorFileWriter.writeAsVectorFormat(
                            layer,
                            temp_geojson,
                            "UTF-8",
                            QgsCoordinateReferenceSystem("EPSG:4326"), # Wymuszamy WGS84 dla WebMapy
                            "GeoJSON"
                        )
                        
                        if err[0] == QgsVectorFileWriter.NoError:
                            color = colors[i % len(colors)]
                            # Dodajemy nowo powstały plik
                            if web_gen.add_vector_layer(temp_geojson, name, color):
                                count += 1
                        else:
                            print(f"Błąd zapisu WFS: {err}")
                    except Exception as e:
                        print(f"Wyjątek WFS: {e}")

                # --- D. RASTER (PLIK) ---
                elif isinstance(layer, QgsRasterLayer) and provider == "gdal":
                    src_path = layer.source().split("|")[0]
                    if src_path.lower().endswith(('.tif', '.tiff', '.asc')):
                        if web_gen.add_raster_layer(src_path, name):
                            count += 1

            if count > 0:
                web_gen.save_map(out_html)
                self.status.showMessage(f"Zaktualizowano {count} warstw.", 5000)
            else:
                QtWidgets.QMessageBox.warning(self, "Pusto", "Nie udało się wyeksportować żadnej warstwy.")

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Błąd", str(e))
            self.status.clearMessage()

    def open_web_map_url_action(self):
        """Otwiera localhost w domyślnej przeglądarce."""
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
        except ImportError:
            QtWidgets.QMessageBox.critical(self, "Błąd", "Brak modułu GeoServerPublisher.")
            return

        gs_url, ok = QtWidgets.QInputDialog.getText(self, "GeoServer", "URL:", text="http://localhost:8080/geoserver")
        if not ok: return
        gs_user, ok = QtWidgets.QInputDialog.getText(self, "User", "User:", text="admin")
        if not ok: return
        gs_pass, ok = QtWidgets.QInputDialog.getText(self, "Pass", "Password:", text="geoserver")
        if not ok: return

        default_conn = f"postgresql://{PG_USER}:{PG_PASS}@localhost:5432/{PG_DB}"
        if self.db and self.db.conn_string: default_conn = self.db.conn_string
        conn, ok = QtWidgets.QInputDialog.getText(self, "PostGIS", "Conn string:", text=default_conn)
        if not ok: return

        table, ok = QtWidgets.QInputDialog.getText(self, "Tabela", "Nazwa tabeli w PostGIS:")
        if not ok or not table: return

        items = ["EPSG:3857 (Web Mercator)", "EPSG:4326 (WGS 84)", "EPSG:2180 (PUWG 92)"]
        item, ok = QtWidgets.QInputDialog.getItem(self, "Układ danych", "W jakim układzie jest tabela w bazie?", items, 0, False)
        if not ok: return
        srs_code = item.split(" ")[0] 

        workspace, ok = QtWidgets.QInputDialog.getText(self, "Workspace", "Workspace:", text="gismooth_ws")
        if not ok: return
        store, ok = QtWidgets.QInputDialog.getText(self, "Store", "Datastore:", text="main_db")
        if not ok: return

        try:
            self.status.showMessage("Łączenie z GeoServerem...")
            QtWidgets.QApplication.processEvents()

            gp = GeoServerPublisher(gs_url, gs_user, gs_pass)
            gp.create_workspace(workspace)
            gp.create_postgis_datastore(workspace, store, "localhost", "5432", "gismooth", PG_USER, PG_PASS) 
            gp.publish_table_as_layer(workspace, store, table, native_srs=srs_code)
            
            QtWidgets.QMessageBox.information(self, "Sukces", f"Warstwa opublikowana!\nLink WMS:\n{gs_url}/{workspace}/wms")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Błąd publikacji", str(e))

    # --- WORKER SYSTEM ---
    
    def start_worker(self, func, *args, **kwargs):
        # Obsługa specjalnych parametrów
        result_path = kwargs.pop('result_path', None)
        result_callback = kwargs.pop('result_callback', None) # <--- NOWOŚĆ
        
        worker = Worker(func, *args, **kwargs)
        
        def on_success(res):
            self.status.showMessage("Zadanie zakończone.", 5000)
            
            # Scenariusz 1: Funkcja zwraca dane (np. DataFrame do wykresu)
            if result_callback:
                result_callback(res)
            
            # Scenariusz 2: Funkcja generuje plik (np. TIF/SHP)
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