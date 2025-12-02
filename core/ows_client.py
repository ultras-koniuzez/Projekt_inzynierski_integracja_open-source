from owslib.wms import WebMapService
from owslib.wfs import WebFeatureService

class OWSClient:
    """
    Klient do obsługi metadanych usług OGC (WMS, WFS) przy użyciu OWSLib.
    Służy tylko do pobrania listy warstw, nie do pobierania danych.
    """

    @staticmethod
    def get_wms_layers(url):
        """
        Łączy się z WMS i zwraca listę dostępnych warstw.
        Zwraca: lista krotek (name, title)
        """
        print(f"[OWSLib] Łączenie z WMS: {url}")
        try:
            # Wersja 1.1.1 lub 1.3.0 jest standardem
            wms = WebMapService(url, version='1.3.0')
            
            layers = []
            # Iterujemy po zawartości
            for name in list(wms.contents):
                title = wms.contents[name].title
                layers.append((name, title))
            
            return layers
        except Exception as e:
            # Próba z starszą wersją w razie błędu
            try:
                wms = WebMapService(url, version='1.1.1')
                layers = []
                for name in list(wms.contents):
                    title = wms.contents[name].title
                    layers.append((name, title))
                return layers
            except Exception as e2:
                raise RuntimeError(f"Błąd połączenia WMS: {e2}")

    @staticmethod
    def get_wfs_layers(url):
        """
        Łączy się z WFS i zwraca listę dostępnych warstw (FeatureTypes).
        """
        print(f"[OWSLib] Łączenie z WFS: {url}")
        try:
            # Wersja 2.0.0 jest preferowana, ale 1.1.0/1.0.0 też częste
            wfs = WebFeatureService(url, version='2.0.0')
            
            layers = []
            for name in list(wfs.contents):
                title = wfs.contents[name].title
                layers.append((name, title))
            
            return layers
        except Exception as e:
            try:
                # Fallback do starszej wersji
                wfs = WebFeatureService(url, version='1.1.0')
                layers = []
                for name in list(wfs.contents):
                    title = wfs.contents[name].title
                    layers.append((name, title))
                return layers
            except Exception as e2:
                raise RuntimeError(f"Błąd połączenia WFS: {e2}")