import requests
import json

class GeoServerPublisher:
    def __init__(self, base_url, user, passwd):
        self.base = base_url.rstrip("/")
        self.auth = (user, passwd)
        self.headers_json = {"Content-Type": "application/json"}

    def create_workspace(self, workspace):
        url = f"{self.base}/rest/workspaces"
        payload = {"workspace": {"name": workspace}}
        r = requests.post(url, auth=self.auth, headers=self.headers_json, json=payload)
        return r.status_code in (200, 201, 409)

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
                    "Expose primary keys": "true"
                }
            }
        }
        r = requests.post(url, auth=self.auth, headers=self.headers_json, json=payload)
        return r.status_code in (200, 201, 409)

    def publish_table_as_layer(self, workspace, store_name, table_name, native_srs="EPSG:2180"):

        url_base = f"{self.base}/rest/workspaces/{workspace}/datastores/{store_name}/featuretypes"
        
        payload = {
            "featureType": {
                "name": table_name,
                "nativeName": table_name,
                "srs": native_srs,
                "enabled": True
            }
        }

        r_post = requests.post(url_base, auth=self.auth, headers=self.headers_json, json=payload)
        

        url_recalc = f"{url_base}/{table_name}"
        params = {"recalculate": "nativebbox,latlonbbox"}
        
        r_put = requests.put(
            url_recalc, 
            auth=self.auth, 
            headers=self.headers_json, 
            params=params, 
            json={"featureType": {"enabled": True}}
        )
        
        if r_put.status_code == 200:
            print(f"✅ Warstwa {table_name} gotowa z poprawnym zasięgiem.")
            return True
        return False