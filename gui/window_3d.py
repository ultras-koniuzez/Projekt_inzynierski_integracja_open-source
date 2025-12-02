from qgis.PyQt import QtWidgets, QtCore, QtGui
from qgis.core import QgsProject, QgsVectorLayer

# --- POPRAWIONE IMPORTY 3D ---
HAS_3D = False
try:
    # 1. Widget okna 3D znajduje się w qgis.gui!
    from qgis.gui import Qgs3DMapCanvas
    
    # 2. Ustawienia i obiekty 3D znajdują się w qgis._3d
    from qgis._3d import (
        Qgs3DMapSettings, 
        QgsCameraPose, 
        QgsVector3D,
        QgsDirectionalLightSettings,
        Qgs3DAxisSettings
    )
    HAS_3D = True
except ImportError as e:
    print(f"BŁĄD IMPORTU 3D: {e}")
    HAS_3D = False

class Visualizer3D(QtWidgets.QMainWindow):
    def __init__(self, layer, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Wizualizacja 3D: {layer.name()}")
        self.resize(1000, 700)
        
        if not HAS_3D:
            lbl = QtWidgets.QLabel("Błąd: Moduł qgis._3d lub qgis.gui.Qgs3DMapCanvas nie został załadowany.")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            self.setCentralWidget(lbl)
            return

        try:
            # 1. Tworzymy Płótno 3D
            self.canvas3d = Qgs3DMapCanvas()
            self.setCentralWidget(self.canvas3d)
            
            # 2. Konfiguracja Sceny
            self.map_settings = Qgs3DMapSettings()
            
            # Układ współrzędnych
            self.map_settings.setCrs(layer.crs())
            
            # Tło
            self.map_settings.setBackgroundColor(QtGui.QColor(30, 30, 30))
            
            # Dodajemy warstwę
            self.map_settings.setLayers([layer])
            
            # Oświetlenie
            light = QgsDirectionalLightSettings()
            light.setDirection(QgsVector3D(0, -1, -1))
            light.setIntensity(1.5)
            self.map_settings.setLightSources([light])
            
            # Przypisanie ustawień
            self.canvas3d.setMapSettings(self.map_settings)
            
            # 3. Kamera
            self.set_camera_to_layer(layer)
            
        except Exception as e:
            print(f"Błąd inicjalizacji widoku 3D: {e}")
            lbl = QtWidgets.QLabel(f"Błąd renderowania: {str(e)}")
            self.setCentralWidget(lbl)

    def set_camera_to_layer(self, layer):
        """Ustawia kamerę na środek warstwy."""
        extent = layer.extent()
        if extent.isEmpty(): return
        
        center = extent.center()
        width = extent.width()
        if width == 0: width = 100
        
        # Kamera
        camera = self.canvas3d.camera()
        camera.setCenterPoint(QgsVector3D(center.x(), center.y(), 0.0))
        camera.setDistanceFromCenterPoint(width * 1.5)
        camera.setPitch(45.0) 
        self.canvas3d.setCamera(camera)