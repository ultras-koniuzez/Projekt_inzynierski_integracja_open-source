# Integracja wybranych aplikacji typu open source umożliwiających opracowanie map numerycznych.
 
## 📌 Opis projektu

Repozytorium zawiera eksperymentalną, modułową architekturę systemu GIS przeznaczoną do przetwarzania, analizy, wizualizacji oraz publikacji danych przestrzennych z wykorzystaniem narzędzi open source.  
Projekt koncentruje się przede wszystkim na **możliwościach integracyjnych**, **charakterystyce wydajnościowej** oraz **elastyczności łączenia narzędzi niskopoziomowych z bibliotekami wysokiego poziomu**, a nie na bezwzględnej dokładności produktów kartograficznych.

Architektura łączy środowisko GIS desktop, programistyczne przetwarzanie danych, przestrzenną bazę danych, usługi sieciowe oraz wizualizację 3D w środowisku desktopowym i webowym.
--------------------------------------------
Aby aplikacja integracji działała, należy zainstalować programy:
- QGIS,
- OsGeo4w,
- Geoserver,
- PostgreSQL z rozszerzeniem PostGIS,

W celu inicjalizacji aplikacji należy otworzyć wiersz poleceń (CMD) oraz stosując komendy 'cd' dotrzeć do ścieżki folderu głównego pobranego programu
Aby uruchomić plikację okienkową należy sprawdzić folder, w którym zlokalizowany jest QGIS oraz jaką wersją dysponujemy, po czym podmienić tę ścieżki w plikach: 
- run.bat
- app.py

Aby połączyć się do bazy danych poprzez button w aplikacji w pliku .env, należy ustawić zmienne środowiskowe do pobierania 
Jeśli aplikacja nie startuje należy w tym samym folderze uruchomić polecenie:
- instalacja_bibliotek.bat 

Gdzie znajdują się wszystkie potrzebne biblioteki do użytkowania aplikacji.
-------------------------------------------

## 🎯 Cele projektu

Główne cele realizowane w ramach projektu:

- zaprojektowanie **modułowej architektury przetwarzania GIS** opartej na rozwiązaniach open source,
- porównanie **wysokopoziomowych bibliotek Pythona** z **binarnymi silnikami GIS** pod kątem wydajności i zapotrzebowania na pamięć,
- demonstracja **zautomatyzowanych potoków ETL** dla danych wektorowych, rastrowych oraz LiDAR,
- implementacja **wizualizacji 2D i 3D**, zarówno w środowisku desktopowym, jak i webowym,
- publikacja danych przestrzennych z wykorzystaniem **standardów OGC**.

---

## 🧱 Architektura systemu

System składa się z następujących warstw logicznych:

### 1. Warstwa danych
- dane wektorowe, rastrowe oraz chmury punktów LiDAR,
- dane lokalne oraz usługi sieciowe (WMS, WCS).

### 2. Warstwa przetwarzania
- biblioteki wysokiego poziomu:
  - GeoPandas  
  - Rasterio  
  - Laspy  
  - NumPy  
- binarne silniki GIS:
  - GDAL / OGR  
  - PDAL  

### 3. Warstwa bazodanowa
- PostgreSQL + PostGIS,
- składowanie danych przestrzennych,
- wydajne procesy ETL z wykorzystaniem narzędzi binarnych (`ogr2ogr`, `raster2pgsql`).

### 4. Warstwa usługowa
- GeoServer,
- publikacja danych przestrzennych w postaci:
  - WMS,
  - WFS,
  - WCS,
- automatyzacja konfiguracji (workspace, datastore, warstwy) poprzez REST API.

### 5. Warstwa wizualizacji
- QGIS (2D/3D),
- wizualizacja desktopowa 3D (Open3D),
- wizualizacja webowa 3D (PyDeck).

---

## 🧰 Wykorzystane technologie

### GIS i bazy danych
- **QGIS**
- **PostgreSQL + PostGIS**
- **GeoServer**

### Przetwarzanie i automatyzacja
- **Python 3**
- **GDAL / OGR**
- **PDAL**

### Biblioteki Python
- GeoPandas
- Rasterio
- Laspy
- PyProj
- NumPy
- Pandas
- Requests

### Wizualizacja 3D
- **Open3D** – wizualizacja desktopowa,
- **PyDeck** – interaktywne mapy 3D w przeglądarce,
- **OpenStreetMap** – podkład mapowy (bez klucza API).

---

## 🗺️ Obsługiwane typy danych

- **Dane wektorowe**
  - punkty, linie, poligony,
  - formaty: GeoPackage, Shapefile,
- **Dane rastrowe**
  - NMT / NMPT / DSM,
  - GeoTIFF,
- **Dane LiDAR**
  - LAS / LAZ.

---

## ⚙️ Funkcjonalności

- automatyczna reprojekcja i transformacje układów współrzędnych,
- konwersja rastrów wysokościowych do postaci chmur punktów 3D,
- subsampling i optymalizacja dużych zbiorów danych LiDAR,
- hipsometryczna koloryzacja danych wysokościowych,
- automatyczna publikacja danych w GeoServerze,
- analiza czasu wykonania i zużycia pamięci RAM,
- modularna architektura umożliwiająca łatwą rozbudowę systemu.

---

## 🚀 Przykładowe scenariusze

- przetwarzanie danych wektorowych i rastrowych (reprojekcja, analiza),
- szybka obsługa danych LiDAR w aplikacjach interaktywnych,
- wydajne zasilanie bazy PostGIS,
- wizualizacja 3D danych wektorowych, rastrowych i chmur punktów,
- publikacja danych przestrzennych jako usługi sieciowe.

---

## 📊 Analiza wydajności

Projekt obejmuje porównanie:

- bibliotek wysokiego poziomu Pythona i narzędzi binarnych GIS,
- czasu wykonania operacji [s],
- przyrostu zużycia pamięci RAM [MB].

Wnioski:
- biblioteki wysokiego poziomu są wygodne w analizach interaktywnych,
- narzędzia binarne zapewniają lepszą skalowalność i minimalne zużycie pamięci,
- procesy ETL zdecydowanie faworyzują podejście binarne.

---

## ⚠️ Ograniczenia

- pełne przypisywanie atrybutów wysokościowych do danych wektorowych w wizualizacji 3D wymaga dalszych badań,
- wydajność wizualizacji webowej ograniczona jest możliwościami przeglądarki,
- część procesów zależna jest od środowiska QGIS i systemu operacyjnego.

---

## 🔮 Kierunki dalszego rozwoju

- poprawa obsługi wysokości obiektów wektorowych w środowiskach 3D,
- integracja modeli terenu z ekstruzją obiektów,
- rozwój potoków przetwarzania po stronie serwera,
- konteneryzacja architektury (Docker),
- obsługa danych czasowych i dynamicznych.

---

## 📄 Licencja

Projekt realizowany w celach **akademickich i badawczych**.  
Wszystkie wykorzystane narzędzia są rozwiązaniami open source i podlegają swoim licencjom.

---

## 👤 Autor

**Igor Koniusz**  
Systemy informacji geograficznej (GIS)  
Projekt akademicki – modułowa architektura GIS i analiza wydajności


# EN

# Modular GIS Architecture for Processing and Visualization of Spatial Data

## 📌 Project Overview

This repository contains an experimental and modular GIS architecture designed for processing, analyzing, visualizing, and publishing spatial data using open-source tools.  
The project focuses on **integration capabilities**, **performance characteristics**, and **flexibility** of combining low-level GIS engines with high-level Python libraries, rather than on the absolute accuracy of geospatial products.

The architecture integrates desktop GIS, programmatic processing, spatial databases, web services, and both desktop and web-based 3D visualization environments.

--- 

To initialize the application, open the command line (CMD) and use the ‘cd’ command to navigate to the root folder of the downloaded program
To run the window application, check the folder where QGIS is located and which version you have, then replace this path in the files 
    - run.bat
    - app.py
To connect to the database via the button in the application in the .env file, set the environment variables for downloading. 
If the application does not start, run the command in the same folder:
- instalacja_bibliotek.bat 
Where all the libraries needed to use the application are located.
---

## 🎯 Objectives

The main goals of the project are:

- To design a **modular GIS processing pipeline** based on open-source components
- To compare **high-level Python libraries** with **binary GIS engines** in terms of performance and memory usage
- To demonstrate **automated data workflows** (ETL) for vector, raster, and LiDAR datasets
- To implement **2D and 3D visualization pipelines**, including desktop and web environments
- To publish spatial data through **standard OGC web services**

---

## 🧱 Architecture Overview

The system is composed of the following logical layers:

1. **Data Acquisition**
   - Vector, raster, and LiDAR datasets
   - Local files and remote services (WCS, WMS)

2. **Processing Layer**
   - High-level Python libraries:
     - GeoPandas
     - Rasterio
     - Laspy
     - NumPy
   - Low-level binary engines:
     - GDAL / OGR
     - PDAL

3. **Database Layer**
   - PostgreSQL + PostGIS
   - Storage of processed vector and raster data
   - Efficient ETL workflows using binary tools (`ogr2ogr`, `raster2pgsql`)

4. **Service Layer**
   - GeoServer
   - Publication of spatial layers via:
     - WMS
     - WFS
     - WCS
   - Automated workspace, datastore, and layer creation via REST API

5. **Visualization Layer**
   - Desktop GIS (QGIS)
   - Desktop 3D viewer (Open3D)
   - Web-based 3D visualization (PyDeck)

---

## 🧰 Technologies Used

### Core GIS & Databases
- **QGIS**
- **PostgreSQL + PostGIS**
- **GeoServer**

### Processing & Automation
- **Python 3**
- **GDAL / OGR**
- **PDAL**

### Python Libraries
- GeoPandas
- Rasterio
- Laspy
- PyProj
- NumPy
- Pandas
- Requests

### 3D Visualization
- **Open3D** (desktop visualization)
- **PyDeck** (web-based interactive 3D maps)
- **OpenStreetMap** tile services (no API key required)

---

## 🗺️ Supported Data Types

- **Vector data**
  - Points, lines, polygons
  - GeoPackage, Shapefile
- **Raster data**
  - DEM / DTM / DSM
  - GeoTIFF
- **LiDAR**
  - LAS / LAZ point clouds

---

## ⚙️ Key Features

- Automated reprojection and spatial transformations
- Raster-to-point-cloud conversion for 3D visualization
- Subsampling and optimization of large LiDAR datasets
- Integrated hypsometric color mapping
- REST-based automation of GeoServer publishing
- Comparison of memory and time efficiency between processing approaches
- Fully modular design allowing independent component replacement

---

## 🚀 Example Workflows

### Vector & Raster Processing
- Reprojection and geometry operations using GeoPandas and OGR
- Raster reprojection and resampling using Rasterio and GDAL

### LiDAR Processing
- Fast interactive processing with Laspy
- Server-oriented pipelines using PDAL

### Database Deployment
- ETL workflows into PostGIS
- Comparison between SQLAlchemy-based and binary-based imports

### 3D Visualization
- Desktop 3D point cloud rendering with Open3D
- Web-based 3D maps using PyDeck with OSM background
- Integration of vector, raster, and LiDAR layers in a single scene

---

## 📊 Performance Analysis

The project includes a detailed performance comparison between:

- High-level Python libraries vs binary GIS engines
- Time execution [s]
- Memory usage [MB]

Key conclusions:
- High-level libraries offer faster development and interactive performance
- Binary tools provide superior memory efficiency and scalability
- ETL operations strongly favor low-level tools (up to ~75× faster)

---

## ⚠️ Known Limitations

- Full assignment of height attributes to vector data in 3D environments requires further research
- Web-based 3D visualization is limited by browser performance constraints
- Some workflows are platform-dependent (Windows/QGIS environment)

---

## 🔮 Future Work

- Improved handling of vector height attributes in 3D scenes
- Integration of true terrain-based extrusion
- Extension of server-side processing pipelines
- Deployment in containerized environments (Docker)
- Support for time-series and dynamic spatial data

---

## 📄 License

This project is intended for **academic and research purposes**.  
All used tools and libraries are open-source and distributed under their respective licenses.

---

## 👤 Author

**Igor Koniusz**  
GIS / Geospatial Systems  
Academic project – modular GIS architecture and performance analysis

