from qgis.PyQt.QtCore import QThread, pyqtSignal

class Worker(QThread):
    """
    Uniwersalny robotnik do zadań w tle.
    """
    finished = pyqtSignal(object) # Sygnał sukcesu (zwraca wynik)
    error = pyqtSignal(str)       # Sygnał błędu
    
    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            # Uruchom przekazaną funkcję
            result = self.func(*self.args, **self.kwargs)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))