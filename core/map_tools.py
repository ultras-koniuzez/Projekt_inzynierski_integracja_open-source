import os
import math
from datetime import datetime

# --- IMPORTY ---
from qgis.core import (
    QgsLayout, 
    QgsLayoutExporter, 
    QgsReadWriteContext, 
    QgsProject,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutItemMapGrid,
    QgsLayoutItemScaleBar,
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem,
    QgsRectangle,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsUnitTypes,
    QgsLayoutMeasurement,
    # Stylizacja
    QgsSymbol, QgsSingleSymbolRenderer, QgsLineSymbol,
    QgsStyle, QgsColorRampShader, QgsRasterShader, 
    QgsSingleBandPseudoColorRenderer, QgsRasterBandStats,
    QgsGradientColorRamp
)
from qgis.PyQt.QtXml import QDomDocument
from qgis.PyQt import QtGui, QtWidgets

# =============================================================================
# SEKCJA 1: STYLIZACJA
# =============================================================================

def apply_basic_style(vlayer, color_name="black", width=0.4):
    if not vlayer.isValid(): return
    try:
        symbol = QgsSymbol.defaultSymbol(vlayer.geometryType())
        symbol.setColor(QtGui.QColor(color_name))
        if vlayer.geometryType() == 1: symbol.setWidth(width)
        elif vlayer.geometryType() == 0: symbol.setSize(width * 6)
        vlayer.setRenderer(QgsSingleSymbolRenderer(symbol))
        vlayer.triggerRepaint()
    except: pass

def apply_raster_colormap(rlayer, ramp_name="Spectral", invert=False):
    if not rlayer.isValid(): return
    provider = rlayer.dataProvider()
    band = 1
    try:
        stats = provider.bandStatistics(band, QgsRasterBandStats.All)
        min_val = stats.minimumValue if stats.minimumValue is not None else 0
        max_val = stats.maximumValue if stats.maximumValue is not None else 255
        
        style = QgsStyle.defaultStyle()
        ramp = style.colorRamp(ramp_name)
        if not ramp:
            c1 = QtGui.QColor(0, 0, 255); c2 = QtGui.QColor(255, 0, 0)
            ramp = QgsGradientColorRamp(c1, c2)
        if invert: ramp.invert()

        crs = QgsColorRampShader()
        crs.setColorRampType(QgsColorRampShader.Interpolated)
        try: crs.classifyColorRamp(255, band, ramp, min_val, max_val)
        except: 
            i1 = QgsColorRampShader.ColorRampItem(min_val, ramp.color(0.0), str(min_val))
            i2 = QgsColorRampShader.ColorRampItem(max_val, ramp.color(1.0), str(max_val))
            crs.setColorRampItemList([i1, i2])

        sh = QgsRasterShader(); sh.setRasterShaderFunction(crs)
        rlayer.setRenderer(QgsSingleBandPseudoColorRenderer(provider, band, sh))
        rlayer.triggerRepaint()
    except: pass

# =============================================================================
# SEKCJA 2: NARZĘDZIA MAPY
# =============================================================================

def calculate_nice_interval(width_in_units, is_geographic):
    if width_in_units <= 0 or math.isnan(width_in_units): 
        return 1000 if not is_geographic else 0.1
    target = width_in_units / 4.5
    try: exponent = math.floor(math.log10(target))
    except: exponent = 1
    fraction = target / (10 ** exponent)
    if fraction < 1.5: base = 1
    elif fraction < 3.5: base = 2
    elif fraction < 7.5: base = 5
    else: base = 10
    return base * (10 ** exponent)

def heuristic_fix_crs(extent, current_crs):
    cx = extent.center().x()
    if abs(cx) <= 180 and (not current_crs.isValid() or not current_crs.isGeographic()):
        return QgsCoordinateReferenceSystem("EPSG:4326")
    elif abs(cx) > 1000 and (not current_crs.isValid() or current_crs.isGeographic()):
        return QgsCoordinateReferenceSystem("EPSG:2180")
    return current_crs

def ensure_metadata_label(layout, text):
    """Tworzy metrykę TYLKO jeśli w szablonie jej brak."""
    item = layout.itemById('meta_label')
    if not item:
        for i in layout.items():
            if isinstance(i, QgsLayoutItemLabel) and "{AUTOR}" in i.text():
                item = i; break
    if not item:
        # Tworzymy nową w bezpiecznym miejscu, jeśli szablon jest pusty
        item = QgsLayoutItemLabel(layout)
        item.setId("meta_label")
        layout.addLayoutItem(item)
        item.attemptMove(QgsLayoutPoint(220, 140, QgsUnitTypes.LayoutMillimeters))
        item.attemptResize(QgsLayoutSize(70, 40, QgsUnitTypes.LayoutMillimeters))
        item.setFrameEnabled(True)
        item.setBackgroundEnabled(True)
        item.setBackgroundColor(QtGui.QColor("white"))
        item.setZValue(99)
    
    if hasattr(item, 'setText'):
        item.setText(text)
        if isinstance(item, QgsLayoutItemLabel):
            item.setMode(0); item.setMarginX(2.0)
        item.update()

def safe_set_text(layout, item_id, text):
    item = layout.itemById(item_id)
    if item and hasattr(item, 'setText'): item.setText(text)

def export_map_to_pdf(project, canvas, out_path, title_text, author, target_crs):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(base_dir, "template2.qpt") 
    if not os.path.exists(template_path): 
        template_path = os.path.join(base_dir, "template.qpt")
        if not os.path.exists(template_path): raise FileNotFoundError("Brak szablonu")
    QtWidgets.QApplication.processEvents()
    layout = QgsLayout(project)
    layout.initializeDefaults()
    with open(template_path, 'r', encoding='utf-8') as f:
        doc = QDomDocument(); doc.setContent(f.read())
    layout.loadFromTemplate(doc, QgsReadWriteContext())

    
    # --- 1. MAPA ---
    map_item = layout.itemById('main_map')
    
    if map_item and isinstance(map_item, QgsLayoutItemMap):
        map_item.setLayers(canvas.layers())
        map_item.setBackgroundColor(QtGui.QColor("white"))
        
        canvas_extent = canvas.extent()
        canvas_crs = heuristic_fix_crs(canvas_extent, canvas.mapSettings().destinationCrs())
        
        final_crs = target_crs
        final_extent = canvas_extent

        # Bezpieczna transformacja
        if canvas_crs != target_crs:
            try:
                tr = QgsCoordinateTransform(canvas_crs, target_crs, QgsProject.instance())
                test = tr.transformBoundingBox(canvas_extent)
                if test.isFinite() and not test.isEmpty():
                    test.normalize()
                    final_extent = test
                else:
                    print("Błąd transformacji - powrót do oryginału")
                    final_crs = canvas_crs
            except Exception as e:
                print(f"Wyjątek: {e}")
                final_crs = canvas_crs

        map_item.setCrs(final_crs)
        map_item.zoomToExtent(final_extent)
        
        # --- 2. SIATKA ---
        grids = map_item.grids()
        while grids.size() > 0: grids.removeGrid(grids.grid(0).id())
        grid = QgsLayoutItemMapGrid("Grid", map_item)
        grids.addGrid(grid)
        
        grid.setEnabled(True)
        grid.setCrs(final_crs)
        grid.setUnits(0)
        
        is_geo = final_crs.isGeographic()
        width = final_extent.width()
        interval = calculate_nice_interval(width, is_geo)
        
        grid.setIntervalX(interval); grid.setIntervalY(interval)
        grid.setAnnotationEnabled(True)
        for s in [0,1,2,3]: grid.setAnnotationDisplay(0, s)
        
        sym = QgsLineSymbol.createSimple({'color': 'black', 'width': '0.1'})
        grid.setLineSymbol(sym)
        grid.setStyle(0)
        grid.setFrameStyle(1)
        
        grid.setAnnotationFormat(1 if is_geo else 0)
        grid.setAnnotationPrecision(0)
        
        map_item.updateBoundingRect()
        map_item.refresh()

        # --- 3. TEKSTY ---
        safe_set_text(layout, 'title_label', title_text)
        crs_desc = f"{final_crs.authid()} - {final_crs.description()}"
        meta = f"PROJEKT\nAutor: {author}\nData: {datetime.now().strftime('%Y-%m-%d')}\nUkład: {crs_desc}\nŹródło: GUGiK/OSM"
        ensure_metadata_label(layout, meta)

        # --- 4. SKALA  ---
        scalebars = [i for i in layout.items() if isinstance(i, QgsLayoutItemScaleBar)]
        if scalebars:
            sb = scalebars[0]
            
            if is_geo:
                sb.setUnits(QgsUnitTypes.DistanceDegrees)
            else:
                sb.setUnits(QgsUnitTypes.DistanceMeters)

            sb.update()

    # --- 5. EKSPORT ---
    exporter = QgsLayoutExporter(layout)
    settings = QgsLayoutExporter.PdfExportSettings()
    settings.dpi = 210
    settings.rasterizeWholeImage = True
    res = exporter.exportToPdf(out_path, settings)
    
    if res == QgsLayoutExporter.Success: return True
    else: raise RuntimeError("Błąd zapisu PDF.")