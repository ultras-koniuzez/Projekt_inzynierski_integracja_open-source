# core/db_iface.py
import subprocess
import os
import psycopg2
from sqlalchemy import create_engine, text  
import geopandas as gpd

class PostGISConnector:
    def __init__(self, conn_string):
        """
        conn_string: postgresql://user:pass@host:port/dbname
        """
        self.conn_string = conn_string
        self.engine = None

    def connect(self):
        """Tworzy SQLAlchemy engine i testuje połączenie."""
        try:
            self.engine = create_engine(self.conn_string)
            with self.engine.connect() as conn:
                # Używamy text() dla bezpieczeństwa w nowych wersjach SQLAlchemy
                conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            raise ConnectionError(f"Błąd połączenia z bazą: {e}")

    def ensure_database(self, dbname):
        """
        Tworzy bazę danych jeśli nie istnieje.
        Wymaga podłączenia do bazy 'postgres' w trybie AUTOCOMMIT.
        """
        # Budujemy URL do domyślnej bazy 'postgres'
        base_url = self.conn_string.rsplit("/", 1)[0] + "/postgres"
        
        # AUTOCOMMIT jest konieczny do CREATE DATABASE
        engine_admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
        
        try:
            with engine_admin.connect() as conn:
                # Sprawdź czy baza istnieje
                check_query = text(f"SELECT 1 FROM pg_database WHERE datname = '{dbname}'")
                exists = conn.execute(check_query).fetchone()
                
                if not exists:
                    print(f"Tworzenie bazy danych: {dbname}...")
                    # CREATE DATABASE nie może być w bloku transakcji 
                    conn.execute(text(f"CREATE DATABASE {dbname}"))
        except Exception as e:
            # Ignorujemy błąd jeśli baza już jest, w innym przypadku rzucamy wyjątek
            if "already exists" not in str(e):
                raise RuntimeError(f"Nie udało się utworzyć bazy danych: {e}")
        finally:
            engine_admin.dispose()

    def enable_postgis(self):
        """Włącza rozszerzenie PostGIS w bieżącej bazie."""
        if self.engine is None:
            self.connect()
            
        try:
            with self.engine.connect() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
                conn.commit() # Zatwierdź zmianę
        except Exception as e:
            raise RuntimeError(f"Nie udało się włączyć PostGIS: {e}")

    # ---- Metoda A: użycie ogr2ogr (rekomendowane) ----
    def import_with_ogr2ogr(self, layer_path, schema="public", table_name=None, srid=None, target_srid=None, overwrite=True):
        """
        Import do PostGIS z opcjonalną reprojekcją
        
        """
        if table_name is None:
            table_name = os.path.splitext(os.path.basename(layer_path))[0]

        # Parsowanie connection stringa (jak wcześniej)
        uri = self.conn_string.replace("postgresql://", "")
        try:
            userpass, hostdb = uri.split("@")
            user, password = userpass.split(":")
            hostport, dbname = hostdb.rsplit("/", 1)
            if ":" in hostport:
                host, port = hostport.split(":")
            else:
                host, port = hostport, "5432"
        except ValueError:
            raise ValueError("Błędny format connection string.")

        pgconn = f"PG:host={host} port={port} user={user} dbname={dbname} password={password}"

        cmd = [
            "ogr2ogr",
            "-f", "PostgreSQL",
            pgconn,
            layer_path,
            "-nln", f"{schema}.{table_name}",
            "-nlt", "PROMOTE_TO_MULTI",
            "-lco", "GEOMETRY_NAME=geom",
            "-lco", "FID=id"
        ]

        # --- LOGIKA REPROJEKCJI (PROJ) ---
        if target_srid:
            cmd += ["-t_srs", f"EPSG:{target_srid}"]
        elif srid:
            cmd += ["-a_srs", f"EPSG:{srid}"]

        if overwrite:
            cmd += ["-overwrite"]

        print(f"Uruchamiam import: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        return True
    def get_available_layers(self):
        """
        Pobiera listę tabel przestrzennych z bazy (schema, table, geometry_column).
        """
        if self.engine is None:
            self.connect()
            
        # Zapytanie do widoku systemowego PostGIS
        sql = text("""
            SELECT f_table_schema, f_table_name, f_geometry_column, srid 
            FROM geometry_columns 
            ORDER BY f_table_schema, f_table_name
        """)
        
        layers = []
        try:
            with self.engine.connect() as conn:
                result = conn.execute(sql)
                for row in result:
                    # Zwracamy krotkę: (schema, table, geom_col, srid)
                    layers.append(row)
            return layers
        except Exception as e:
            print(f"Błąd pobierania listy warstw: {e}")
            return []
    # ---- Metoda B: użycie GeoPandas (Python) ----
    def import_with_geopandas(self, layer_path, table_name=None, if_exists="replace"):
        if table_name is None:
            table_name = os.path.splitext(os.path.basename(layer_path))[0]

        print("Wczytywanie geopandas...")
        gdf = gpd.read_file(layer_path)

        gdf = gdf.rename_geometry("geom")
        
        # Zapis do bazy
        print(f"Zapis do tabeli {table_name}...")
        gdf.to_postgis(table_name, self.engine, if_exists=if_exists, index=False)
        return True