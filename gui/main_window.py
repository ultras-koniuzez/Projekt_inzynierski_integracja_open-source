import os
import sys
import subprocess
import matplotlib
import matplotlib.pyplot as plt
matplotlib.use('Qt5Agg')
import pandas as pd
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import shutil
from qgis.gui import QgsProjectionSelectionDialog
from qgis.PyQt import QtWidgets, QtGui, QtCore

# --- IMPORTY QGIS ---
from qgis.core import (
    QgsProject, 
    QgsVectorLayer, 
    QgsRasterLayer, 
    QgsLayerTreeModel, 
    QgsPointCloudLayer,
    QgsCoordinateReferenceSystem, 
    QgsCoordinateTransform,
    QgsDataSourceUri
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
        pdal_generate_dsm, pdal_generate_dtm, pdal_info
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

        self.load_default_basemap()

    # --- BUILDERS ---
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
                # Sprawdź czy typ się zgadza (np. czy to Raster)
                if isinstance(layer, layer_type):
                    return layer
        
        # 2. Jeśli nic nie zaznaczono, weź ostatnią dodaną
        if layer_type == QgsRasterLayer: return self.last_raster_layer
        if layer_type == QgsVectorLayer: return self.last_vector_layer
        if layer_type == QgsPointCloudLayer: return self.last_point_cloud_layer
        
        return None
    def _build_tab_data(self):
        layout = QtWidgets.QVBoxLayout(self.tab_data)
        layout.setAlignment(QtCore.Qt.AlignTop)
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
                     ("📍 Centroidy", self.compute_centroids_action)]:
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
        btn_pdf = QtWidgets.QPushButton("📄 Eksport PDF")
        btn_pdf.clicked.connect(self.export_pdf_action)
        btn_gs = QtWidgets.QPushButton("🌐 Publikuj GeoServer")
        btn_gs.clicked.connect(self.publish_current_postgis_layer_action)
        layout.addWidget(btn_pdf)
        layout.addWidget(btn_gs)
    def _build_tab_benchmark(self):
        layout = QtWidgets.QVBoxLayout(self.tab_benchmark)
        
        # Panel sterowania
        ctrl = QtWidgets.QHBoxLayout()
        self.combo_test_type = QtWidgets.QComboBox()
        self.combo_test_type.addItems(["1. Porównanie Silników (Bar Chart)", "2. Porównanie Formatów (Bar Chart)", "3. Skalowalność (Line Chart)"])
        
        btn = QtWidgets.QPushButton("🚀 Uruchom Test")
        btn.clicked.connect(self.run_benchmark_action)
        
        ctrl.addWidget(QtWidgets.QLabel("Wybierz test:"))
        ctrl.addWidget(self.combo_test_type)
        ctrl.addWidget(btn)
        layout.addLayout(ctrl)
        
        # Wykres
        self.fig = Figure(figsize=(5, 4), dpi=100)
        self.chart_canvas = FigureCanvas(self.fig)
        layout.addWidget(self.chart_canvas)
        
        # Tabela
        self.res_table = QtWidgets.QTableWidget()
        layout.addWidget(self.res_table)
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
    def run_benchmark_action(self):
        if not self.last_vector_layer:
            QtWidgets.QMessageBox.warning(self, "Info", "Wczytaj warstwę wektorową.")
            return
            
        if not Benchmarker: # Sprawdza czy moduł załadowany
             QtWidgets.QMessageBox.critical(self, "Błąd", "Moduł Benchmark nieaktywny.")
             return

        test_idx = self.combo_test_type.currentIndex()
        src = self.last_vector_layer.source().split("|")[0]
        
        # Conn string
        conn = None
        if self.db: conn = f"postgresql://{PG_USER}:{PG_PASS}@localhost:5432/{PG_DB}"

        # Definicja zadania w tle
        def run_test():
            from core.analytics import PerformanceTester # Lokalny import
            tester = PerformanceTester(conn)
            
            if test_idx == 0:
                return "engine", tester.run_engine_benchmark(src)
            elif test_idx == 1:
                return "format", tester.run_format_benchmark(src)
            elif test_idx == 2:
                return "scale", tester.run_scalability_test(src)
        
        self.start_worker(run_test, result_callback=self.update_benchmark_charts)

    def update_benchmark_charts(self, data):
        test_type, df = data
        if df is None or df.empty: return
        
        # 1. Tabela
        self.res_table.setRowCount(len(df))
        self.res_table.setColumnCount(len(df.columns))
        self.res_table.setHorizontalHeaderLabels(df.columns)
        for i, row in df.iterrows():
            for j, val in enumerate(row):
                self.res_table.setItem(i, j, QtWidgets.QTableWidgetItem(str(val)))

        # 2. Wykres
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        
        if test_type == "engine":
            ax.bar(df["Nazwa"], df["Czas [s]"], color=['#4c72b0', '#55a868', '#c44e52'])
            ax.set_title("Wydajność Silników (Buforowanie)")
            ax.set_ylabel("Czas [s]")
            
        elif test_type == "format":
            ax.barh(df["Nazwa"], df["Czas [s]"], color='#8172b3')
            ax.set_title("Czas Odczytu Formatów")
            ax.set_xlabel("Czas [s]")
            
        elif test_type == "scale":
            ax.plot(df["Liczba Obiektów"], df["Czas [s]"], marker='o', linestyle='-', color='#c44e52')
            ax.set_title("Skalowalność (Czas vs Ilość danych)")
            ax.set_xlabel("Liczba obiektów")
            ax.set_ylabel("Czas obliczeń [s]")
            ax.grid(True)

        self.fig.tight_layout()
        self.chart_canvas.draw()
    def add_layer_smart(self, layer):
        if not layer.isValid(): return False
        
        # --- FIX DLA LIDAR (LAS) ---
        # Jeśli to chmura, ZAWSZE ustawiamy PUWG 1992 (EPSG:2180)
        if isinstance(layer, QgsPointCloudLayer):
            layer.setCrs(QgsCoordinateReferenceSystem("EPSG:2180"))
        
        self.project.addMapLayer(layer)
        
        if isinstance(layer, QgsRasterLayer): self.last_raster_layer = layer
        elif isinstance(layer, QgsVectorLayer): self.last_vector_layer = layer
        elif isinstance(layer, QgsPointCloudLayer): self.last_point_cloud_layer = layer

        # Zoom
        try:
            tc = self.canvas.mapSettings().destinationCrs()
            # Jeśli warstwa jest w 2180, a mapa w 3857, musimy przeliczyć zasięg
            if layer.crs() != tc:
                tr = QgsCoordinateTransform(layer.crs(), tc, self.project)
                ext = tr.transformBoundingBox(layer.extent())
                if ext.isFinite(): 
                    self.canvas.setExtent(ext)
                else:
                    self.canvas.zoomToFullExtent()
            else:
                self.canvas.setExtent(layer.extent())
        except: 
            self.canvas.zoomToFullExtent()
        
        self.canvas.refresh()
        return True

    def load_default_basemap(self):
        uri = "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&zmax=19&zmin=0"
        osm = QgsRasterLayer(uri, "OpenStreetMap", "wms")
        if osm.isValid():
            self.project.addMapLayer(osm)
            self.canvas.refresh()

    # --- AKCJE DANYCH ---

    def load_vector_action(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Otwórz", self.data_dir, "Wektor (*.shp *.gpkg *.geojson)")
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
        """
        Analogicznie dla WFS (Wektory).
        """
        if not OWSClient: return

        # Przykładowy URL (Geoportal - Państwowy Rejestr Granic)
        default_url = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/PRG/WFS/Granice"
        url, ok = QtWidgets.QInputDialog.getText(self, "WFS", "Podaj adres URL usługi WFS:", text=default_url)
        
        if not ok or not url: return

        def fetch_layers():
            return OWSClient.get_wfs_layers(url)

        def on_layers_fetched(layers):
            if not layers:
                QtWidgets.QMessageBox.warning(self, "Info", "Brak warstw WFS.")
                return
            
            display_list = [f"{title} ({name})" for name, title in layers]
            item, ok = QtWidgets.QInputDialog.getItem(self, "Wybierz Warstwę", "Dostępne warstwy WFS:", display_list, 0, False)
            
            if ok and item:
                idx = display_list.index(item)
                layer_name = layers[idx][0]
                layer_title = layers[idx][1]
                
                # URI dla WFS jest prostsze
                uri = f"{url}?service=WFS&version=1.0.0&request=GetFeature&typename={layer_name}"
                
                # Ładujemy jako Wektor ("WFS")
                vlayer = QgsVectorLayer(uri, layer_title, "WFS")
                self.add_layer_smart(vlayer)
                self.status.showMessage(f"Dodano WFS: {layer_title}", 5000)

        self.status.showMessage("Pobieranie Capabilities serwera WFS...")
        self.start_worker(fetch_layers, result_callback=on_layers_fetched)
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
        if not PostGISConnector: 
            QtWidgets.QMessageBox.critical(self, "Błąd", "Brak modułu DB.")
            return
        
        # Domyślny string
        default_conn = f"postgresql://{PG_USER}:{PG_PASS}@localhost:5432/{PG_DB}"
        conn, ok = QtWidgets.QInputDialog.getText(self, "DB", "Conn String:", text=default_conn)
        
        if ok:
            # Wyciągamy nazwę bazy z linku 
            try:
                dbname = conn.rsplit("/", 1)[-1]
            except:
                dbname = "gismooth"

            self.status.showMessage("Łączenie z bazą...", 0)
            QtWidgets.QApplication.processEvents()

            try:
                # 1. Inicjalizacja obiektu (jeszcze bez łączenia)
                self.db = PostGISConnector(conn)
                
                # Ta funkcja sprytnie podłączy się do 'postgres', stworzy bazę i się rozłączy.
                self.db.ensure_database(dbname)
                
                # 3. Teraz bezpiecznie łączymy się do naszej bazy
                self.db.connect()
                
                # 4. Włączamy rozszerzenie przestrzenne (PostGIS)
                self.db.enable_postgis()
                
                self.lbl_db_status.setText("Status: POŁĄCZONO ✅")
                self.lbl_db_status.setStyleSheet("color: green; font-weight: bold;")
                self.status.showMessage(f"Połączono z bazą: {dbname}", 5000)
                
            except Exception as e:
                self.lbl_db_status.setText("Status: BŁĄD ❌")
                QtWidgets.QMessageBox.critical(self, "Błąd Bazy Danych", 
                    f"Nie udało się połączyć lub utworzyć bazy.\n\nSzczegóły:\n{str(e)}")
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