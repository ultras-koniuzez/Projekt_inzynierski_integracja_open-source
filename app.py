import sys
import os
from qgis.core import QgsApplication

from qgis.PyQt.QtWidgets import QApplication 
from gui.main_window import MainWindow
from gui.main_window import MainWindow

# Ścieżka musi pasować do tego, co jest w run_windows.bat
QGIS_PREFIX_PATH = r"C:\Program Files\QGIS 3.40.13\apps\qgis-ltr"

def main():
    # 1. Inicjalizacja środowiska QGIS 
    QgsApplication.setPrefixPath(QGIS_PREFIX_PATH, True)
    # Drugi parametr False oznacza, że nie ładujemy GUI QGIS-a, tylko silnik
    qgs = QgsApplication([], False)
    qgs.initQgis()

    # 2. Inicjalizacja aplikacji Qt (PySide6)
    app = QApplication(sys.argv)

    # 3. Utworzenie i wyświetlenie Twojego okna
    window = MainWindow()
    window.show()

    # 4. Uruchomienie pętli zdarzeń
    exit_code = app.exec()
    
    # 5. Bezpieczne zamknięcie QGIS po zamknięciu okna
    qgs.exitQgis()
    sys.exit(exit_code)

if __name__ == "__main__":
    main()