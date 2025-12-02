# core/geoserver_publish.py
import requests
import json

class GeoServerPublisher:
    def __init__(self, base_url, user, passwd):
        """
        base_url: np. http://localhost:8080/geoserver
        """
        self.base = base_url.rstrip("/")
        self.auth = (user, passwd)
        self.headers_json = {"Content-Type": "application/json"}

    def create_workspace(self, workspace):
        url = f"{self.base}/rest/workspaces"
        payload = {"workspace": {"name": workspace}}
        # Tworzenie workspace
        r = requests.post(url, auth=self.auth, headers=self.headers_json, json=payload)
        
        if r.status_code in (200, 201):
            return True
        elif r.status_code == 409: # Już istnieje
            return True
        elif r.status_code == 401:
            raise RuntimeError("Błędne hasło do GeoServera!")
        else:
            raise RuntimeError(f"Błąd tworzenia workspace: {r.status_code} {r.text}")

    def create_postgis_datastore(self, workspace, store_name, host, port, db, user, password, schema="public"):
        url = f"{self.base}/rest/workspaces/{workspace}/datastores"
        payload = {
            "dataStore": {
                "name": store_name,
                "connectionParameters": {
                    "host": host,
                    "port": str(port),
                    "database": db,
                    "user": user,
                    "passwd": password,
                    "dbtype": "postgis",
                    "schema": schema,
                    "Expose primary keys": "true" # Ważne dla WFS-T
                }
            }
        }
        r = requests.post(url, auth=self.auth, headers=self.headers_json, json=payload)
        
        if r.status_code in (200, 201) or r.status_code == 409:
            return True
        else:
            raise RuntimeError(f"Błąd tworzenia datastore: {r.status_code} {r.text}")

    def publish_table_as_layer(self, workspace, store_name, table_name, native_srs="EPSG:3857", title=None):
        """
        Publikuje tabelę i AUTOMATYCZNIE przelicza Bounding Boxy.
        """
        # 1. Tworzenie warstwy (POST)
        url_create = f"{self.base}/rest/workspaces/{workspace}/datastores/{store_name}/featuretypes"
        
        payload = {
            "featureType": {
                "name": table_name,
                "nativeName": table_name,
                "srs": native_srs,        # Np. EPSG:3857
                "nativeCRS": native_srs,  # Wymuszamy definicję
                "title": title or table_name,
                "enabled": True
            }
        }
        
        print(f"Publikowanie warstwy {table_name}...")
        r = requests.post(url_create, auth=self.auth, headers=self.headers_json, json=payload)
        
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Błąd publikacji (Krok 1): {r.status_code} {r.text}")

        # 2. MAGICZNY KROK: Wymuszenie przeliczenia granic (PUT)
        # Parametr ?recalculate=nativebbox,latlonbbox mówi GeoServerowi:
        # "Policz granice na podstawie danych w bazie, nie zgaduj!"
        
        url_update = f"{self.base}/rest/workspaces/{workspace}/datastores/{store_name}/featuretypes/{table_name}"
        params = {"recalculate": "nativebbox,latlonbbox"}
        
        # Wysyłamy pusty payload lub enabled=true, ważne są parametry URL
        update_payload = {"featureType": {"enabled": True}}
        
        print("Przeliczanie granic (Bounding Box)...")
        r2 = requests.put(url_update, auth=self.auth, headers=self.headers_json, json=update_payload, params=params)
        
        if r2.status_code == 200:
            print("Sukces! Granice obliczone.")
            return True
        else:
            print(f"Ostrzeżenie: Nie udało się przeliczyć granic automatycznie ({r2.status_code}).")
            return True