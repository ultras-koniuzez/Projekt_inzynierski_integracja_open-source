import ssl
import warnings
import urllib3
from owslib.wms import WebMapService
from owslib.wfs import WebFeatureService
from owslib.wcs import WebCoverageService

# =========================================================
# --- FIX SSL: WYŁĄCZENIE WERYFIKACJI CERTYFIKATÓW ---
# Naprawia błąd "CERTIFICATE_VERIFY_FAILED" na serwerach rządowych
# =========================================================
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.simplefilter('ignore', urllib3.exceptions.InsecureRequestWarning)
# =========================================================

class OWSClient:


    @staticmethod
    def get_wms_layers(url):

        print(f"[OWSLib] Łączenie z WMS (SSL Verify=False): {url}")
        try:
            # Wersja 1.3.0 jest standardem
            wms = WebMapService(url, version='1.3.0', timeout=30)
            
            layers = []
            for name in list(wms.contents):
                title = wms.contents[name].title
                layers.append((name, title))
            
            return layers
        except Exception as e:
            # Próba ze starszą wersją
            try:
                print("Próba WMS 1.1.1...")
                wms = WebMapService(url, version='1.1.1', timeout=30)
                layers = []
                for name in list(wms.contents):
                    title = wms.contents[name].title
                    layers.append((name, title))
                return layers
            except Exception as e2:
                raise RuntimeError(f"Błąd połączenia WMS: {e2}")

    @staticmethod
    def get_wfs_layers(url):

        print(f"[OWSLib] Łączenie z WFS (SSL Verify=False): {url}")
 
        
        try:
            # Wersja 2.0.0
            wfs = WebFeatureService(url, version='2.0.0', timeout=30)
            
            layers = []
            for name in list(wfs.contents):
                title = wfs.contents[name].title
                layers.append((name, title))
            
            return layers
        except Exception:
            try:
                # Fallback: Wersja 1.0.0 (Najbezpieczniejsza dla Polski)
                print("Próba WFS 1.0.0...")
                wfs = WebFeatureService(url, version='1.0.0', timeout=30)
                layers = []
                for name in list(wfs.contents):
                    title = wfs.contents[name].title
                    layers.append((name, title))
                return layers
            except Exception as e2:
                raise RuntimeError(f"Błąd połączenia WFS: {e2}\n(Serwer może być offline lub wymagać VPN)")
    @staticmethod
    def get_wcs_layers(url):

        print(f"[OWSLib] Łączenie z WCS (SSL Verify=False): {url}")

        try:
            wcs = WebCoverageService(url, version='1.0.0', timeout=30)
            
            layers = []
            for name in list(wcs.contents):

                title = wcs.contents[name].title or name
                layers.append((name, title))
            
            return layers
        except Exception as e:
            try:
                print("Próba WCS 1.1.0...")
                wcs = WebCoverageService(url, version='1.1.0', timeout=30)
                layers = []
                for name in list(wcs.contents):
                    title = wcs.contents[name].title or name
                    layers.append((name, title))
                return layers
            except Exception as e2:
                raise RuntimeError(f"Błąd połączenia WCS: {e2}")