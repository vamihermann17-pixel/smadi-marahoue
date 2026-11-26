/*******************************************************************************
 * SMADI mensuel à 1 km — application Google Earth Engine
 *
 * Soil Moisture Agricultural Drought Index (Sánchez et al., 2016) :
 *   SMADI(m) = SMCI(m) × MLST(m−1) / NDVI(m−1)
 *   SMCI = (SMmax − SM) / (SMmax − SMmin)      (bornes par pixel sur toute la période)
 *   MLST = LST / LSTmax                           (LST en Kelvin, LSTmax par pixel)
 *
 * Données
 *   NDVI  : MODIS MOD13A3 v061 (mensuel, 1 km)
 *   LST   : MODIS MOD11A2 v061 (8 jours, 1 km, LST de jour), moyenne mensuelle
 *   SM    : au choix ERA5-Land, FLDAS Noah, TerraClimate, GLDAS 2.1 (depuis 2000),
 *           SMAP L4 (depuis 2015) ou votre propre asset (TAMSAT-SM préparé avec
 *           prepare_tamsat_sm.py, SMOS, ESA CCI…), rééchantillonnée en bilinéaire à 1 km
 *   Occupation du sol (vulnérabilité) : MODIS MCD12Q1 v061, IGBP, dernière année
 *           disponible en chaque pixel (mosaïque des années)
 *
 * Période : mars 2000 → dernier mois où les trois jeux de données sont disponibles
 * (ERA5-Land et FLDAS ont 1 à 3 mois de retard).
 *
 * Produits
 *   Fréquence  : % des mois où SMADI ≥ seuil de sécheresse
 *   Durée      : durée moyenne des épisodes (mois consécutifs ≥ seuil)
 *   Intensité  : SMADI moyen des mois en sécheresse
 *   Aléa       : matrice fréquence (5 classes) × intensité (5 classes) → 5 classes
 *   Risque     : aléa × vulnérabilité agricole (occupation du sol) → 5 classes
 *   Tendance   : pente de Sen du SMADI moyen annuel (années complètes), par décennie
 *
 * Utilisation
 *   1. Coller ce script dans le Code Editor (code.earthengine.google.com) → Run.
 *   2. Pour publier : Apps → New App → choisir ce script.
 *   3. « Mon fichier de limites » : importer d'abord le shapefile/GeoJSON en asset
 *      (Assets → New → Shape files), puis coller son identifiant. Pour une App
 *      publique, partager l'asset en lecture (« Anyone can read »).
 *
 * Tous les seuils et pondérations sont dans CFG et VULN ci-dessous.
 ******************************************************************************/

// ============================ PARAMÈTRES ===================================
var CFG = {
  scale: 1000,                              // résolution de calcul (m)
  droughtThreshold: 1.0,                    // SMADI ≥ seuil → mois en sécheresse
  smadiBreaks: [0.5, 1.0, 2.0, 3.0],        // classes SMADI (bornes inférieures)
  freqBreaks: [5, 10, 20, 30],              // % de mois en sécheresse → classes 1..5
  intensityBreaks: [1.5, 2.0, 2.5, 3.0],    // SMADI moyen des mois secs → classes 1..5
  // Mode anomalie : z ≥ 1,0 par défaut. La validation en Marahoué (2005–2026, contre l'eau
  // du sol TAMSAT-ALERT) donne le meilleur score de détection à z ≥ 1,0 ; z ≥ 1,5 manque
  // trois mois secs sur quatre et ne repère que les sécheresses sévères (VALIDATION.md du
  // dépôt smadi-marahoue).
  droughtThresholdZ: 1.0,                    // z ≥ seuil → mois en sécheresse
  // Bornes des classes de fréquence et d'intensité selon le seuil : quantiles nationaux
  // 20/40/60/80 de la Côte d'Ivoire. 1,0 : ERA5-Land + LST Terra/Aqua, 0,05°, mars 2000 →
  // oct. 2026 (calibrer_seuils_z.py) ; 1,5 : TAMSAT-SM, pixels de 5 km, même période.
  // Pour un autre seuil, les bornes du seuil le plus proche sont utilisées (avertissement).
  breaksZ: {
    '1.0': {freq: [14.7, 15.5, 16.2, 16.9], intensity: [1.61, 1.67, 1.72, 1.77], vis: [13, 18]},
    '1.5': {freq: [7.5, 8.1, 8.6, 9.2], intensity: [2.03, 2.09, 2.14, 2.21], vis: [6, 10]}
  },
  aleaBreaks: [3, 5, 9, 15],                // score fréquence×intensité (1..25) → 1..5
  riskBreaks: [4, 7, 12, 18],               // score aléa×vulnérabilité (1..25) → 1..5
  ndviMin: 0.1,                             // masque sol nu / eau
  minValidMonths: 24                        // nb minimal de mois valides par pixel
};

// Vulnérabilité agricole (0 = non concerné … 5 = très vulnérable), classes IGBP
var VULN = {
  1: 1, 2: 1, 3: 1, 4: 1, 5: 1,   // forêts
  6: 2, 7: 2,                     // fourrés fermés / ouverts
  8: 3, 9: 3,                     // savanes arborées / savanes
  10: 3,                          // prairies
  11: 1,                          // zones humides
  12: 5,                          // cultures
  13: 0,                          // urbain
  14: 4,                          // mosaïque cultures / végétation naturelle
  15: 0, 16: 0, 17: 0             // neige, sol nu, eau
};

// how : 'first' = une image par mois ; 'mean' = moyenne des images infra-journalières
// start : [année, mois] du premier mois SMADI possible avec cette source
// L'unité n'a pas d'importance : le SMCI normalise par le min/max de chaque pixel.
var SM_SOURCES = {
  era5:  {label: 'ERA5-Land (0–7 cm, ~9 km, 2000 →)', id: 'ECMWF/ERA5_LAND/MONTHLY_AGGR',
          band: 'volumetric_soil_water_layer_1', how: 'first', start: [2000, 3]},
  fldas: {label: 'FLDAS Noah (0–10 cm, ~11 km, 2000 →)', id: 'NASA/FLDAS/NOAH01/C/GL/M/V001',
          band: 'SoilMoi00_10cm_tavg', how: 'first', start: [2000, 3]},
  terraclimate: {label: 'TerraClimate (bilan hydrique, ~4 km, 2000 →)', id: 'IDAHO_EPSCOR/TERRACLIMATE',
          band: 'soil', how: 'first', start: [2000, 3]},
  gldas: {label: 'GLDAS 2.1 Noah (0–10 cm, ~25 km, 2000 →, plus lent)', id: 'NASA/GLDAS/V021/NOAH/G025/T3H',
          band: 'SoilMoi0_10cm_inst', how: 'mean', start: [2000, 3]},
  smap:  {label: 'SMAP L4 (0–5 cm, ~9 km, 2015 →, plus lent)', id: 'NASA/SMAP/SPL4SMGP/007',
          band: 'sm_surface', how: 'mean', start: [2015, 4]},
  // Source importée par l'utilisateur (TAMSAT-SM préparé avec prepare_tamsat_sm.py, SMOS, ESA CCI…)
  // - image multibande nommée …_AAAAMM_AAAAMM (une bande par mois), ou
  // - collection d'images mensuelles datées (system:time_start), première bande utilisée
  asset: {label: 'Ma collection (asset) : TAMSAT-SM, autre…', how: 'first', start: [2000, 3], custom: true}
};
var SM_KEYS = ['era5', 'fldas', 'terraclimate', 'gldas', 'smap', 'asset'];

var PAL = {
  smadi: ['3f8f5f', 'b9b23a', 'd98a2b', 'c4512f', '7d1f2a'],
  level: ['1a9850', 'a6d96a', 'fee08b', 'fc8d59', 'd73027'],
  freq:  ['ffffcc', 'fed976', 'fd8d3c', 'e31a1c', '800026'],
  dur:   ['f7fbff', '9ecae1', '4292c6', '08519c', '08306b'],
  trend: ['2166ac', '67a9cf', 'f7f7f7', 'ef8a62', 'b2182b']
};
var LEVEL_NAMES = ['Très faible', 'Faible', 'Moyen', 'Fort', 'Très fort'];
var SMADI_NAMES = ['Pas de sécheresse', 'Légère', 'Modérée', 'Sévère', 'Extrême'];

// ============================ DONNÉES ======================================
var NDVI_COL = ee.ImageCollection('MODIS/061/MOD13A3').select('NDVI');
var LST_COL  = ee.ImageCollection('MODIS/061/MOD11A2').select('LST_Day_1km');
// Mosaïque de toutes les années, la plus récente au-dessus : la dernière année
// publiée peut être incomplète (vide sur une partie du globe, ex. Côte d'Ivoire).
// Les zones sont appliquées par masque (zoneMaskOf) et les calculs régionaux
// précisent crs: 'EPSG:4326', pour éviter les erreurs de clip() en grille sinusoïdale.
var LC = ee.ImageCollection('MODIS/061/MCD12Q1').select('LC_Type1')
  .sort('system:time_start').mosaic();

var G0 = ee.FeatureCollection('FAO/GAUL_SIMPLIFIED_500m/2015/level0');
var G1 = ee.FeatureCollection('FAO/GAUL_SIMPLIFIED_500m/2015/level1');
var G2 = ee.FeatureCollection('FAO/GAUL_SIMPLIFIED_500m/2015/level2');

// Image mensuelle (première image ou moyenne) ; image masquée si le mois est vide
function monthlyImage(col, date, how, name) {
  var c = col.filterDate(date, date.advance(1, 'month'));
  var empty = ee.Image.constant(0).updateMask(ee.Image.constant(0));
  var img = ee.Image(ee.Algorithms.If(c.size().gt(0),
    how === 'first' ? c.first() : c.mean(), empty));
  return img.rename(name);
}

// Classes 1..n+1 à partir de bornes inférieures
function toClasses(img, breaks) {
  var c = ee.Image.constant(1);
  for (var k = 0; k < breaks.length; k++) c = c.add(img.gte(breaks[k]));
  return c.updateMask(img.mask()).int();
}

// Collection SMADI mensuelle
function buildSmadi(smKey, monthMillis) {
  var src = SM_SOURCES[smKey];
  var smAll = src.col ? src.col : ee.ImageCollection(src.id).select(src.band);
  // Projection native, pour que le rééchantillonnage bilinéaire s'applique aussi aux moyennes
  var smProj = ee.Image(smAll.first()).projection();

  var inputs = ee.ImageCollection(ee.List(monthMillis).map(function (t) {
    var d = ee.Date(t);
    var p = d.advance(-1, 'month');
    var sm = monthlyImage(smAll, d, src.how, 'sm')
      .setDefaultProjection(smProj).resample('bilinear');
    var lst = monthlyImage(LST_COL, p, 'mean', 'lst').multiply(0.02);        // K
    var ndvi = monthlyImage(NDVI_COL, p, 'first', 'ndvi').multiply(0.0001);
    return sm.addBands(lst).addBands(ndvi).set('system:time_start', d.millis());
  }));

  var smMin = inputs.select('sm').min();
  var smMax = inputs.select('sm').max();
  var lstMax = inputs.select('lst').max();

  return inputs.map(function (img) {
    var smci = smMax.subtract(img.select('sm'))
      .divide(smMax.subtract(smMin)).clamp(0, 1);
    var mlst = img.select('lst').divide(lstMax);
    var ndvi = img.select('ndvi');
    return smci.multiply(mlst).divide(ndvi)
      .updateMask(ndvi.gt(CFG.ndviMin))
      .rename('smadi')
      .set('system:time_start', img.get('system:time_start'));
  });
}

// Ajoute à chaque mois l'anomalie standardisée z = (SMADI − moyenne du mois) / écart-type
// du mois, calculés par pixel sur toute la période (climatologie mensuelle)
function withAnomalies(smadi) {
  var clim = ee.ImageCollection(ee.List.sequence(1, 12).map(function (m) {
    var c = smadi.filter(ee.Filter.calendarRange(m, m, 'month'));
    return c.mean().rename('mu').addBands(c.reduce(ee.Reducer.stdDev()).rename('sd'))
      .set('month', ee.Number(m).int());
  }));
  return smadi.map(function (img) {
    var m = ee.Date(img.get('system:time_start')).get('month');
    var c = ee.Image(clim.filter(ee.Filter.eq('month', m)).first());
    var sd = c.select('sd');
    var z = img.subtract(c.select('mu')).divide(sd).updateMask(sd.gt(0)).rename('z');
    return img.addBands(z).set('system:time_start', img.get('system:time_start'));
  });
}

// Produits de synthèse. col : bandes 'smadi' et 'z' ; dBand : bande qui définit
// les mois en sécheresse ('smadi' en seuil fixe, 'z' en anomalie mensuelle)
function buildProducts(col, dBand, thr, freqBreaks, intensityBreaks, firstFullYear, lastFullYear) {
  var dcol = col.select(dBand);
  var smadi = col.select('smadi');
  var count = dcol.count();
  var valid = count.gte(CFG.minValidMonths);

  var droughtMonths = dcol.map(function (i) { return i.gte(thr); }).sum();
  var freq = droughtMonths.divide(count).multiply(100).rename('frequence');

  var intensity = dcol.map(function (i) { return i.updateMask(i.gte(thr)); })
    .mean().rename('intensite');

  // Nombre d'épisodes = nombre de débuts (mois sec précédé d'un mois non sec)
  var dl = dcol.sort('system:time_start').map(function (i) {
    return i.gte(thr).unmask(0).rename('d');
  }).toList(2000);
  var n = dl.size();
  var onsets = ee.ImageCollection(ee.List.sequence(1, n.subtract(1)).map(function (k) {
    k = ee.Number(k);
    return ee.Image(dl.get(k)).and(ee.Image(dl.get(k.subtract(1))).not()).rename('d');
  })).sum().add(ee.Image(dl.get(0)));
  var duration = droughtMonths.divide(onsets).updateMask(onsets.gt(0)).rename('duree');

  // Tendance : pente de Sen du SMADI moyen annuel, par décennie
  var years = ee.List.sequence(firstFullYear, lastFullYear);
  var annual = ee.ImageCollection(years.map(function (y) {
    var m = smadi.filter(ee.Filter.calendarRange(y, y, 'year')).mean();
    return ee.Image.constant(y).float().rename('t').addBands(m.rename('y'));
  }));
  var trend = annual.reduce(ee.Reducer.sensSlope()).select('slope')
    .multiply(10).rename('tendance');

  // Aléa et risque
  var freqC = toClasses(freq, freqBreaks);
  var intC = toClasses(intensity.unmask(0), intensityBreaks);
  var alea = toClasses(freqC.multiply(intC), CFG.aleaBreaks).updateMask(valid).rename('alea');

  var keys = Object.keys(VULN).map(Number);
  var vals = keys.map(function (k) { return VULN[k]; });
  var vuln = LC.remap(keys, vals, 0).rename('vulnerabilite');
  var risk = toClasses(alea.multiply(vuln), CFG.riskBreaks)
    .updateMask(vuln.gt(0)).updateMask(valid).rename('risque');

  return {
    freq: freq.updateMask(valid), duration: duration.updateMask(valid),
    intensity: intensity.updateMask(valid), trend: trend.updateMask(valid),
    alea: alea, vuln: vuln, risk: risk
  };
}

// ============================ INTERFACE ====================================
var map = ui.Map();
map.setOptions('HYBRID');
map.setCenter(10, 20, 3);
var drawing = map.drawingTools();
drawing.setShown(false);
drawing.setLinked(false);
drawing.setDrawModes(['polygon', 'rectangle']);

var panel = ui.Panel({style: {width: '400px', padding: '8px'}});
ui.root.clear();
ui.root.add(ui.SplitPanel(panel, map));

function title(t) { return ui.Label(t, {fontWeight: 'bold', fontSize: '15px', margin: '16px 8px 4px'}); }
function note(t) { return ui.Label(t, {fontSize: '11px', color: '#5d6a62', margin: '2px 8px'}); }
function show(w, on) { w.style().set('shown', on); }

panel.add(ui.Label('SMADI mensuel à 1 km', {fontSize: '22px', fontWeight: 'bold', margin: '6px 8px 2px'}));
panel.add(note('Soil Moisture Agricultural Drought Index (Sánchez et al., 2016). ' +
  'NDVI et LST MODIS, humidité du sol au choix (5 sources ou votre asset, ex. TAMSAT-SM). Mars 2000 (2015 pour SMAP) → dernier mois disponible.'));

// ---- Zone ----
panel.add(title('1. Zone d\'étude'));
var ZONE_GAUL = 'gaul', ZONE_ASSET = 'asset', ZONE_DRAW = 'draw';
var zoneMode = ui.Select({
  items: [
    {label: 'Pays / région / département', value: ZONE_GAUL},
    {label: 'Mon fichier de limites (asset GEE)', value: ZONE_ASSET},
    {label: 'Zone dessinée sur la carte', value: ZONE_DRAW}
  ],
  value: ZONE_GAUL, style: {stretch: 'horizontal'}
});
var ALL = '— Toute la zone —';
var selCountry = ui.Select({placeholder: 'Chargement des pays…', style: {stretch: 'horizontal'}});
var selRegion = ui.Select({placeholder: 'Région (facultatif)', style: {stretch: 'horizontal'}});
var selDept = ui.Select({placeholder: 'Département (facultatif)', style: {stretch: 'horizontal'}});
var gaulPanel = ui.Panel([selCountry, selRegion, selDept]);

var assetBox = ui.Textbox({placeholder: 'users/mon_compte/mes_limites', style: {stretch: 'horizontal'}});
var assetPanel = ui.Panel([assetBox,
  note('Importez d\'abord votre shapefile ou GeoJSON en asset (Assets → New). ' +
       'Toutes les entités sont fusionnées.')]);

var drawBtn = ui.Button({label: 'Dessiner un polygone', onClick: function () {
  drawing.setShape('polygon'); drawing.draw();
}});
var clearBtn = ui.Button({label: 'Effacer', onClick: function () {
  drawing.layers().forEach(function (l) { l.geometries().reset(); });
}});
var drawPanel = ui.Panel([ui.Panel([drawBtn, clearBtn], ui.Panel.Layout.flow('horizontal')),
  note('Cliquez sur la carte pour tracer, double-cliquez pour terminer.')]);

panel.add(zoneMode).add(gaulPanel).add(assetPanel).add(drawPanel);

function updateZoneMode() {
  var m = zoneMode.getValue();
  show(gaulPanel, m === ZONE_GAUL);
  show(assetPanel, m === ZONE_ASSET);
  show(drawPanel, m === ZONE_DRAW);
  drawing.setShown(m === ZONE_DRAW);
}
zoneMode.onChange(updateZoneMode);
updateZoneMode();

G0.aggregate_array('ADM0_NAME').distinct().sort().evaluate(function (list) {
  selCountry.items().reset(list || []);
  selCountry.setPlaceholder('Choisir un pays');
});
selCountry.onChange(function (c) {
  selRegion.items().reset([]); selDept.items().reset([]);
  selRegion.setValue(null, false); selDept.setValue(null, false);
  selRegion.setPlaceholder('Chargement…');
  G1.filter(ee.Filter.eq('ADM0_NAME', c)).aggregate_array('ADM1_NAME').distinct().sort()
    .evaluate(function (l) {
      selRegion.items().reset([ALL].concat(l || []));
      selRegion.setPlaceholder('Région (facultatif)');
    });
});
selRegion.onChange(function (r) {
  selDept.items().reset([]); selDept.setValue(null, false);
  if (!r || r === ALL) return;
  selDept.setPlaceholder('Chargement…');
  G2.filter(ee.Filter.and(ee.Filter.eq('ADM0_NAME', selCountry.getValue()),
                          ee.Filter.eq('ADM1_NAME', r)))
    .aggregate_array('ADM2_NAME').distinct().sort()
    .evaluate(function (l) {
      selDept.items().reset([ALL].concat(l || []));
      selDept.setPlaceholder('Département (facultatif)');
    });
});

// Renvoie {geom, label} ou null (avec message)
function getZone() {
  var m = zoneMode.getValue();
  if (m === ZONE_GAUL) {
    var c = selCountry.getValue(), r = selRegion.getValue(), d = selDept.getValue();
    if (!c) { setStatus('Choisissez un pays.', true); return null; }
    if (d && d !== ALL) {
      return {geom: G2.filter(ee.Filter.and(ee.Filter.eq('ADM0_NAME', c),
        ee.Filter.eq('ADM1_NAME', r), ee.Filter.eq('ADM2_NAME', d))).geometry(), label: d};
    }
    if (r && r !== ALL) {
      return {geom: G1.filter(ee.Filter.and(ee.Filter.eq('ADM0_NAME', c),
        ee.Filter.eq('ADM1_NAME', r))).geometry(), label: r};
    }
    return {geom: G0.filter(ee.Filter.eq('ADM0_NAME', c)).geometry(), label: c};
  }
  if (m === ZONE_ASSET) {
    var id = assetBox.getValue();
    if (!id) { setStatus('Indiquez l\'identifiant de votre asset.', true); return null; }
    return {geom: ee.FeatureCollection(id.trim()).geometry(), label: id.trim().split('/').pop()};
  }
  var geoms = [];
  drawing.layers().forEach(function (l) {
    if (l.geometries().length() > 0) geoms.push(l.toGeometry());
  });
  if (!geoms.length) { setStatus('Dessinez d\'abord une zone sur la carte.', true); return null; }
  var g = geoms[0];
  for (var i = 1; i < geoms.length; i++) g = g.union(geoms[i], 1);
  return {geom: g, label: 'Zone dessinée'};
}

// ---- Données et paramètres ----
panel.add(title('2. Données'));
var smSelect = ui.Select({
  items: SM_KEYS.map(function (k) { return {label: SM_SOURCES[k].label, value: k}; }),
  value: 'era5', style: {stretch: 'horizontal'}
});
var thrBox = ui.Textbox({value: String(CFG.droughtThreshold), style: {width: '70px'}});
var periodLabel = note('Recherche du dernier mois disponible…');
var smAssetBox = ui.Textbox({placeholder: 'projects/mon-projet/assets/tamsat_sm_surface_200001_202610',
  style: {stretch: 'horizontal'}});
var smAssetBtn = ui.Button({label: 'Charger', onClick: function () { loadAsset(); }});
var smAssetPanel = ui.Panel([
  ui.Panel([smAssetBox, smAssetBtn], ui.Panel.Layout.flow('horizontal')),
  note('Image multibande produite par prepare_tamsat_sm.py (nom en …_AAAAMM_AAAAMM), ' +
       'ou collection d\'images mensuelles datées. TAMSAT-SM ne couvre que l\'Afrique.')
]);
show(smAssetPanel, false);
var methodSelect = ui.Select({
  items: [{label: 'Seuil fixe : SMADI ≥ seuil', value: 'fixed'},
          {label: 'Anomalie mensuelle : z ≥ seuil', value: 'z'}],
  value: 'fixed', style: {stretch: 'horizontal'}
});
var thrLabel = ui.Label('Seuil (SMADI ≥)', {fontSize: '12px'});
var methodNote = note('');
function updateMethodNote() {
  var z = methodSelect.getValue() === 'z';
  thrLabel.setValue(z ? 'Seuil (z ≥)' : 'Seuil (SMADI ≥)');
  // Seuil par défaut propre à chaque mode, tant que l'utilisateur ne l'a pas modifié
  var v = String(thrBox.getValue());
  if (z && v === String(CFG.droughtThreshold)) thrBox.setValue(String(CFG.droughtThresholdZ));
  if (!z && v === String(CFG.droughtThresholdZ)) thrBox.setValue(String(CFG.droughtThreshold));
  methodNote.setValue(z
    ? 'Un mois est sec si son SMADI dépasse la normale de ce mois (moyenne sur toute la période) ' +
      'de plus de « seuil » écarts-types (1 = sécheresse anormale, conseillé ; 1,5 = sévère ; 2 = extrême). ' +
      'Supprime l\'effet de la saison sèche.'
    : 'Un mois est sec si son SMADI dépasse le seuil. En zone à saison sèche marquée, ' +
      'chaque saison sèche compte comme une sécheresse.');
}
methodSelect.onChange(updateMethodNote);
updateMethodNote();
panel.add(ui.Label('Humidité du sol', {fontSize: '12px', margin: '4px 8px 0'}))
  .add(smSelect)
  .add(smAssetPanel)
  .add(ui.Label('Détection des mois secs', {fontSize: '12px', margin: '6px 8px 0'}))
  .add(methodSelect)
  .add(ui.Panel([thrLabel, thrBox], ui.Panel.Layout.flow('horizontal')))
  .add(methodNote)
  .add(periodLabel);

var runBtn = ui.Button({label: 'Lancer l\'analyse', disabled: true,
  style: {stretch: 'horizontal', margin: '10px 8px'}});
var statusLabel = ui.Label('', {fontSize: '12px', margin: '0 8px', whiteSpace: 'pre-wrap'});
panel.add(runBtn).add(statusLabel);
function setStatus(t, isError) {
  statusLabel.setValue(t);
  statusLabel.style().set('color', isError ? '#b2182b' : '#2f6b4f');
}

// ---- Carte affichée ----
panel.add(title('3. Carte affichée'));
var LAYER_META = {
  risk:  {name: 'Risque de sécheresse agricole', vis: {min: 1, max: 5, palette: PAL.level}, legend: LEVEL_NAMES},
  alea:  {name: 'Aléa (fréquence × intensité)', vis: {min: 1, max: 5, palette: PAL.level}, legend: LEVEL_NAMES},
  freq:  {name: 'Fréquence (% des mois en sécheresse)', vis: {min: 0, max: 40, palette: PAL.freq},
          legend: ['0 %', '10 %', '20 %', '30 %', '≥ 40 %']},
  dur:   {name: 'Durée moyenne des épisodes (mois)', vis: {min: 1, max: 5, palette: PAL.dur},
          legend: ['1', '2', '3', '4', '≥ 5']},
  trend: {name: 'Tendance du SMADI (par décennie)', vis: {min: -0.4, max: 0.4, palette: PAL.trend},
          legend: ['≤ −0,4 (moins sec)', '−0,2', '0', '+0,2', '≥ +0,4 (plus sec)']},
  month: {name: 'SMADI du mois', vis: {min: 1, max: 5, palette: PAL.smadi},
          legend: [
            '< ' + CFG.smadiBreaks[0] + '  ' + SMADI_NAMES[0],
            CFG.smadiBreaks[0] + '–' + CFG.smadiBreaks[1] + '  ' + SMADI_NAMES[1],
            CFG.smadiBreaks[1] + '–' + CFG.smadiBreaks[2] + '  ' + SMADI_NAMES[2],
            CFG.smadiBreaks[2] + '–' + CFG.smadiBreaks[3] + '  ' + SMADI_NAMES[3],
            '≥ ' + CFG.smadiBreaks[3] + '  ' + SMADI_NAMES[4]]},
  monthz: {name: 'Anomalie du mois (z)', vis: {min: -2, max: 2, palette: PAL.trend},
          legend: ['≤ −2 (plus humide que la normale)', '−1', '0 (normal)', '+1', '≥ +2 (plus sec que la normale)']}
};
var LAYER_ORDER = ['risk', 'alea', 'freq', 'dur', 'trend', 'month', 'monthz'];
var layerSelect = ui.Select({
  items: LAYER_ORDER.map(function (k) { return {label: LAYER_META[k].name, value: k}; }),
  value: 'risk', style: {stretch: 'horizontal'}
});
var monthSelect = ui.Select({placeholder: 'Mois', style: {stretch: 'horizontal'}});
panel.add(layerSelect)
  .add(ui.Panel([ui.Label('Mois :', {fontSize: '12px'}), monthSelect], ui.Panel.Layout.flow('horizontal')));

// Légende
var legend = ui.Panel({style: {position: 'bottom-left', padding: '8px 10px'}});
map.add(legend);
function setLegend(key) {
  var m = LAYER_META[key];
  legend.clear();
  legend.add(ui.Label(m.name, {fontWeight: 'bold', fontSize: '12px', margin: '0 0 6px'}));
  m.vis.palette.forEach(function (c, i) {
    legend.add(ui.Panel([
      ui.Label('', {backgroundColor: '#' + c, padding: '7px', margin: '0 6px 3px 0', border: '1px solid #999'}),
      ui.Label(m.legend[i], {fontSize: '11px', margin: '1px 0 0'})
    ], ui.Panel.Layout.flow('horizontal')));
  });
}
setLegend('risk');

// ---- Résultats ----
panel.add(title('4. Évolution mensuelle'));
var chartPanel = ui.Panel([note('Lancez l\'analyse pour afficher le graphique. ' +
  'Cliquez sur un point pour afficher la carte de ce mois.')]);
panel.add(chartPanel);

panel.add(title('5. Surfaces par classe'));
var areaSelect = ui.Select({
  items: [{label: 'Risque', value: 'risk'}, {label: 'Aléa', value: 'alea'},
          {label: 'SMADI du mois sélectionné', value: 'month'}],
  value: 'risk', style: {stretch: 'horizontal'}
});
var areaPanel = ui.Panel();
panel.add(areaSelect).add(areaPanel);

panel.add(title('6. Export'));
var exportBtn = ui.Button({label: 'Exporter vers Google Drive (Code Editor)', style: {stretch: 'horizontal'}});
panel.add(exportBtn)
  .add(note('Crée des tâches d\'export GeoTIFF à 1 km dans l\'onglet Tasks. ' +
            'Ne fonctionne que dans le Code Editor, pas dans une App publiée.'));

panel.add(title('Méthode'));
panel.add(note('SMADI(m) = SMCI(m) × LST(m−1)/LSTmax ÷ NDVI(m−1), avec SMCI = (SMmax − SM)/(SMmax − SMmin). ' +
  'Bornes min/max calculées par pixel sur toute la période. Pixels avec NDVI ≤ ' + CFG.ndviMin + ' masqués.'));
panel.add(note('Aléa : classe de fréquence (seuils ' + CFG.freqBreaks.join(', ') + ' %) × classe d\'intensité ' +
  '(SMADI moyen des mois secs, seuils ' + CFG.intensityBreaks.join(', ') + '), regroupé en 5 classes.'));
panel.add(note('Risque : aléa × vulnérabilité agricole (MODIS MCD12Q1 : cultures 5, mosaïque 4, ' +
  'prairies et savanes 3, fourrés 2, forêts 1, urbain/sol nu/eau exclus).'));
panel.add(note('Anomalie mensuelle : z = (SMADI − moyenne du même mois) / écart-type du même mois, ' +
  'par pixel sur toute la période. Mois sec si z ≥ ' + CFG.droughtThresholdZ + ' par défaut. ' +
  'Classes calées sur la Côte d\'Ivoire pour z ≥ 1 (fréquence ' + CFG.breaksZ['1.0'].freq.join(', ') +
  ' % ; intensité ' + CFG.breaksZ['1.0'].intensity.join(', ') + ') et z ≥ 1,5 (fréquence ' +
  CFG.breaksZ['1.5'].freq.join(', ') + ' % ; intensité ' + CFG.breaksZ['1.5'].intensity.join(', ') + ').'));
panel.add(note('Tendance : pente de Sen du SMADI moyen annuel (années complètes). Positive = assèchement.'));
panel.add(note('Seuils indicatifs, modifiables dans CFG en tête du script.'));

// ============================ ÉTAT ET CALCUL ===============================
var PERIODS = {};   // par source : {millis: [...], labels: [...], lastFullYear}
var CUR = null;     // résultats courants
var LAYERS = {};    // ui.Map.Layer par clé

function pad(n) { return (n < 10 ? '0' : '') + n; }
function monthIdx(ms) { var d = new Date(ms); return d.getUTCFullYear() * 12 + d.getUTCMonth(); }
// Dernier mois (presque) complet d'une série : un mois n'est retenu que si
// la dernière image commence au plus tôt le jour minDay
function completeMonthIdx(ms, minDay) {
  return monthIdx(ms) - (new Date(ms).getUTCDate() >= minDay ? 0 : 1);
}

function lastMillis(col) {
  return ee.ImageCollection(col).limit(1, 'system:time_start', false).first().get('system:time_start');
}
// Dates lues source par source : une source indisponible ne bloque pas les autres
var MODIS = null;   // dernières dates MODIS, utilisées aussi pour les assets
ee.Dictionary({ndvi: lastMillis(NDVI_COL), lst: lastMillis(LST_COL)}).evaluate(function (L, err) {
  if (err) { setStatus('Impossible de lire les dates MODIS : ' + err, true); return; }
  var lstIdx = completeMonthIdx(L.lst, 20);
  MODIS = {L: L, lstIdx: lstIdx};
  SM_KEYS.forEach(function (k) {
    if (SM_SOURCES[k].custom) return;
    ee.Number(lastMillis(SM_SOURCES[k].id)).evaluate(function (smLast, e2) {
      if (e2 || smLast === null) {
        SM_SOURCES[k].error = e2 || 'aucune image';
      } else {
        setPeriod(k, smLast, L, lstIdx);
      }
      if (k === smSelect.getValue()) updatePeriod();
    });
  });
});

function setPeriod(k, smLast, L, lstIdx) {
  var src = SM_SOURCES[k];
  var smIdx = src.how === 'mean' ? completeMonthIdx(smLast, 25) : monthIdx(smLast);
  var start = src.start[0] * 12 + (src.start[1] - 1);
  var end = Math.min(smIdx, monthIdx(L.ndvi) + 1, lstIdx + 1);
  var millis = [], labels = [];
  for (var i = start; i <= end; i++) {
    var y = Math.floor(i / 12), m = i % 12;
    millis.push(Date.UTC(y, m, 1));
    labels.push(y + '-' + pad(m + 1));
  }
  var endYear = Math.floor(end / 12);
  PERIODS[k] = {millis: millis, labels: labels,
                firstFullYear: src.start[1] === 1 ? src.start[0] : src.start[0] + 1,
                lastFullYear: (end % 12 === 11) ? endYear : endYear - 1};
}

// ---- Source importée (asset) ----
function assetFail(msg) {
  SM_SOURCES.asset.error = msg;
  updatePeriod();
}
function loadAsset() {
  var src = SM_SOURCES.asset;
  var id = String(smAssetBox.getValue() || '').trim();
  delete PERIODS.asset; src.col = null; src.error = null;
  if (!id) { assetFail('indiquez l\'identifiant de l\'asset.'); return; }
  if (!MODIS) { assetFail('les dates MODIS sont encore en cours de lecture, réessayez dans un instant.'); return; }
  runBtn.setDisabled(true);
  periodLabel.setValue('Lecture de l\'asset…');
  ee.data.getAsset(id, function (info, err) {
    if (err || !info) {
      assetFail('asset introuvable ou non partagé (' + (err || 'aucune réponse') + ').');
      return;
    }
    var type = String(info.type).toUpperCase();
    if (type.indexOf('COLLECTION') >= 0) {
      var col = ee.ImageCollection(id).map(function (i) {
        return i.select([0]).rename('sm');
      });
      ee.Dictionary({first: col.aggregate_min('system:time_start'),
                     last: col.aggregate_max('system:time_start')}).evaluate(function (d, e) {
        if (e || !d || d.first === null) { assetFail('collection vide ou sans dates (' + (e || '') + ').'); return; }
        src.col = col;
        finishAsset(d.first, d.last);
      });
    } else if (type === 'IMAGE') {
      var name = id.split('/').pop();
      var mt = name.match(/_(\d{4})(\d{2})_(\d{4})(\d{2})/);
      if (!mt) {
        assetFail('le nom de l\'image doit contenir _AAAAMM_AAAAMM, par exemple tamsat_sm_surface_200001_202610.');
        return;
      }
      var y0 = Number(mt[1]), m0 = Number(mt[2]), y1 = Number(mt[3]), m1 = Number(mt[4]);
      var n = (y1 * 12 + m1) - (y0 * 12 + m0) + 1;
      var img = ee.Image(id);
      img.bandNames().size().evaluate(function (nb, e) {
        if (e) { assetFail(String(e)); return; }
        if (nb !== n) {
          assetFail('l\'image a ' + nb + ' bandes alors que son nom annonce ' + n + ' mois.');
          return;
        }
        src.col = ee.ImageCollection(ee.List.sequence(0, n - 1).map(function (k) {
          k = ee.Number(k).int();
          var b = img.select(ee.List([k]));
          return b.updateMask(b.gt(-9000)).rename('sm')
            .set('system:time_start', ee.Date.fromYMD(y0, m0, 1).advance(k, 'month').millis());
        }));
        finishAsset(Date.UTC(y0, m0 - 1, 1), Date.UTC(y1, m1 - 1, 1));
      });
    } else {
      assetFail('type d\'asset non pris en charge : ' + info.type + '.');
    }
  });
}
function finishAsset(firstMs, lastMs) {
  var src = SM_SOURCES.asset;
  var f = new Date(firstMs);
  // Premier mois SMADI : mars 2000 au plus tôt, et pas avant le premier mois de l'asset
  var fi = Math.max(2000 * 12 + 2, f.getUTCFullYear() * 12 + f.getUTCMonth());
  src.start = [Math.floor(fi / 12), fi % 12 + 1];
  setPeriod('asset', lastMs, MODIS.L, MODIS.lstIdx);
  if (PERIODS.asset.millis.length < CFG.minValidMonths) {
    delete PERIODS.asset;
    assetFail('période trop courte : au moins ' + CFG.minValidMonths + ' mois communs avec MODIS sont nécessaires.');
    return;
  }
  updatePeriod();
}

function updatePeriod() {
  var key = smSelect.getValue();
  var p = PERIODS[key];
  show(smAssetPanel, key === 'asset');
  runBtn.setDisabled(!p);
  if (!p) {
    periodLabel.setValue(SM_SOURCES[key].error
      ? 'Source indisponible : ' + SM_SOURCES[key].error
      : (key === 'asset' ? 'Indiquez l\'identifiant de votre asset, puis cliquez sur Charger.'
                         : 'Recherche du dernier mois disponible…'));
    return;
  }
  periodLabel.setValue('Période : ' + p.labels[0] + ' → ' + p.labels[p.labels.length - 1] +
    ' (' + p.labels.length + ' mois)' +
    (key === 'smap' ? '. Période plus courte : fréquence, durée et tendance ne sont pas ' +
                      'comparables avec une analyse depuis 2000.' : '') +
    (SM_SOURCES[key].how === 'mean' ? ' Calcul plus long (données toutes les 3 heures).' : ''));
  monthSelect.items().reset(p.labels.slice().reverse());
  monthSelect.setValue(p.labels[p.labels.length - 1], false);
}
smSelect.onChange(updatePeriod);

function monthClasses(label) {
  var d = ee.Date(label + '-01');
  var img = ee.Image(CUR.col.filterDate(d, d.advance(1, 'month')).first()).select('smadi');
  return toClasses(img, CFG.smadiBreaks).updateMask(CUR.zoneMask);
}
function monthAnomaly(label) {
  var d = ee.Date(label + '-01');
  return ee.Image(CUR.col.filterDate(d, d.advance(1, 'month')).first()).select('z')
    .updateMask(CUR.zoneMask);
}

// Masque de la zone : remplace clip(), qui échoue sur les images en grille
// sinusoïdale MODIS (occupation du sol) lors de la transformation des bords
function zoneMaskOf(g) {
  return ee.Image.constant(1).clip(g).mask();
}

// Bornes des classes du mode anomalie pour le seuil choisi (seuil calé le plus proche)
function breaksForZ(thr) {
  var best = null, d = Infinity;
  Object.keys(CFG.breaksZ).forEach(function (k) {
    var e = Math.abs(Number(k) - thr);
    if (e < d) { d = e; best = k; }
  });
  var b = CFG.breaksZ[best];
  b.note = d > 0.05 ? ' Attention : classes calées pour z ≥ ' + best.replace('.', ',') +
    ', pas pour z ≥ ' + String(thr).replace('.', ',') + '.' : '';
  return b;
}

function run() {
  var zone = getZone();
  if (!zone) return;
  var thr = parseFloat(String(thrBox.getValue()).replace(',', '.'));
  if (!(thr > 0)) { setStatus('Le seuil de sécheresse doit être un nombre positif.', true); return; }
  var smKey = smSelect.getValue();
  var p = PERIODS[smKey];
  if (!p) { setStatus('Cette source d\'humidité n\'est pas disponible pour le moment.', true); return; }

  setStatus('Calcul en cours pour « ' + zone.label + ' »… Les cartes s\'affichent ' +
            'progressivement ; le graphique et les surfaces peuvent prendre 1 à 3 minutes.');

  var method = methodSelect.getValue();
  var dBand = method === 'z' ? 'z' : 'smadi';
  var col = withAnomalies(buildSmadi(smKey, p.millis));
  var z = method === 'z';
  var bz = breaksForZ(thr);
  var P = buildProducts(col, dBand, thr,
    z ? bz.freq : CFG.freqBreaks,
    z ? bz.intensity : CFG.intensityBreaks, p.firstFullYear, p.lastFullYear);
  // Échelle de la carte de fréquence adaptée au mode et au seuil
  var v0 = bz.vis[0], v1 = bz.vis[1];
  LAYER_META.freq.vis.min = z ? v0 : 0;
  LAYER_META.freq.vis.max = z ? v1 : 40;
  LAYER_META.freq.legend = z ? ['≤ ' + v0 + ' %', String(v0 + (v1 - v0) / 4) + ' %',
                                String(v0 + (v1 - v0) / 2) + ' %', String(v0 + 3 * (v1 - v0) / 4) + ' %', '≥ ' + v1 + ' %']
                             : ['0 %', '10 %', '20 %', '30 %', '≥ 40 %'];
  setLegend(layerSelect.getValue());
  if (z && bz.note) setStatus(statusLabel.getValue() + bz.note, false);
  var g = zone.geom;
  var zm = zoneMaskOf(g);
  CUR = {geom: g, zoneMask: zm, col: col, dBand: dBand, method: method,
         thr: thr, label: zone.label, smKey: smKey,
         risk: P.risk.updateMask(zm), alea: P.alea.updateMask(zm), freq: P.freq.updateMask(zm),
         dur: P.duration.updateMask(zm), trend: P.trend.updateMask(zm),
         intensity: P.intensity.updateMask(zm)};

  // Couches
  map.layers().reset();
  LAYERS = {};
  var current = layerSelect.getValue();
  LAYER_ORDER.forEach(function (k) {
    var img = k === 'month' ? monthClasses(monthSelect.getValue())
      : k === 'monthz' ? monthAnomaly(monthSelect.getValue()) : CUR[k];
    var lyr = ui.Map.Layer(img, LAYER_META[k].vis, LAYER_META[k].name, k === current);
    LAYERS[k] = lyr;
    map.layers().add(lyr);
  });
  var outline = ee.Image().byte().paint(ee.FeatureCollection([ee.Feature(g)]), 1, 2);
  map.layers().add(ui.Map.Layer(outline, {palette: ['000000']}, 'Limite de la zone'));
  map.centerObject(g);

  updateChart();
  updateAreas();
}
runBtn.onClick(run);

layerSelect.onChange(function (k) {
  LAYER_ORDER.forEach(function (key) { if (LAYERS[key]) LAYERS[key].setShown(key === k); });
  setLegend(k);
});
monthSelect.onChange(function (label) {
  if (!CUR || !label) return;
  LAYERS.month.setEeObject(monthClasses(label));
  LAYERS.monthz.setEeObject(monthAnomaly(label));
  if (areaSelect.getValue() === 'month') updateAreas();
});
areaSelect.onChange(function () { if (CUR) updateAreas(); });

// ---- Graphique mensuel ----
function updateChart() {
  chartPanel.clear();
  chartPanel.add(note('Calcul du graphique…'));
  var thr = CUR.thr, g = CUR.geom, dBand = CUR.dBand;
  var fc = CUR.col.map(function (i) {
    var r = i.select('smadi').addBands(i.select(dBand).gte(thr).multiply(100).rename('pct')).reduceRegion({
      reducer: ee.Reducer.mean(), geometry: g, scale: CFG.scale, crs: 'EPSG:4326',
      bestEffort: true, maxPixels: 1e9, tileScale: 4
    });
    return ee.Feature(null, {t: i.get('system:time_start'), smadi: r.get('smadi'), pct: r.get('pct')});
  });
  fc.reduceColumns(ee.Reducer.toList(3), ['t', 'smadi', 'pct']).get('list')
    .evaluate(function (rows, err) {
      chartPanel.clear();
      if (err) { chartPanel.add(note('Erreur du graphique : ' + err + '. Essayez une zone plus petite.')); return; }
      var dt = {
        cols: [{id: 'd', label: 'Mois', type: 'date'},
               {id: 's', label: 'SMADI moyen', type: 'number'},
               {id: 'p', label: '% de la zone en sécheresse', type: 'number'}],
        rows: rows.map(function (r) {
          var d = new Date(r[0]);
          // Milieu du mois : évite le décalage d'un jour dû au fuseau horaire du navigateur
          return {c: [{v: 'Date(' + d.getUTCFullYear() + ',' + d.getUTCMonth() + ',15)'},
                      {v: r[1] === null ? null : Math.round(r[1] * 1000) / 1000},
                      {v: r[2] === null ? null : Math.round(r[2] * 10) / 10}]};
        })
      };
      var chart = ui.Chart(dt, 'LineChart', {
        title: 'SMADI mensuel — ' + CUR.label,
        height: 280, legend: {position: 'bottom'},
        hAxis: {format: 'yyyy'},
        vAxes: {0: {title: 'SMADI', minValue: 0}, 1: {title: '% en sécheresse', minValue: 0, maxValue: 100}},
        series: {0: {targetAxisIndex: 0, color: '2f6b4f', lineWidth: 1.5},
                 1: {targetAxisIndex: 1, color: 'd98a2b', lineWidth: 1, lineDashStyle: [4, 3]}},
        chartArea: {left: 45, right: 45, top: 30, bottom: 50}
      });
      chart.onClick(function (x) {
        if (x === null || x === undefined) return;
        var d = new Date(x);
        var label = d.getFullYear() + '-' + pad(d.getMonth() + 1);
        monthSelect.setValue(label, true);
        layerSelect.setValue('month', true);
      });
      chartPanel.add(chart);
      chartPanel.add(note('Mois sec : ' + (CUR.method === 'z' ? 'anomalie z ≥ ' : 'SMADI ≥ ') + thr +
        '. Agrandir le graphique (↗) pour télécharger les valeurs en CSV.'));
    });
}

// ---- Surfaces par classe ----
function updateAreas() {
  areaPanel.clear();
  areaPanel.add(note('Calcul des surfaces…'));
  var which = areaSelect.getValue();
  var img, names, palette, titleTxt;
  if (which === 'month') {
    var label = monthSelect.getValue();
    img = monthClasses(label); names = SMADI_NAMES; palette = PAL.smadi;
    titleTxt = 'Classes SMADI — ' + label;
  } else {
    img = CUR[which]; names = LEVEL_NAMES; palette = PAL.level;
    titleTxt = (which === 'risk' ? 'Risque' : 'Aléa') + ' — ' + CUR.label;
  }
  ee.Image.pixelArea().divide(1e6).addBands(img.rename('c')).reduceRegion({
    reducer: ee.Reducer.sum().group({groupField: 1, groupName: 'c'}),
    geometry: CUR.geom, scale: CFG.scale, crs: 'EPSG:4326', maxPixels: 1e13, tileScale: 8
  }).get('groups').evaluate(function (groups, err) {
    areaPanel.clear();
    if (err) { areaPanel.add(note('Erreur du calcul des surfaces : ' + err)); return; }
    var byClass = {}, total = 0;
    (groups || []).forEach(function (g) { byClass[g.c] = g.sum; total += g.sum; });
    var rows = names.map(function (n, i) {
      var a = byClass[i + 1] || 0;
      return {c: [{v: n}, {v: Math.round(a)}, {v: 'color: #' + palette[i]},
                  {v: total ? (Math.round(a / total * 1000) / 10) + ' %' : '0 %'}]};
    });
    var chart = ui.Chart({
      cols: [{id: 'n', label: 'Classe', type: 'string'},
             {id: 'a', label: 'Surface (km²)', type: 'number'},
             {id: 's', role: 'style', type: 'string'},
             {id: 'p', role: 'annotation', type: 'string'}],
      rows: rows
    }, 'ColumnChart', {title: titleTxt, height: 240, legend: {position: 'none'},
                       vAxis: {title: 'km²', minValue: 0}, chartArea: {left: 60, right: 10, top: 30, bottom: 40}});
    areaPanel.add(chart);
    names.forEach(function (n, i) {
      var a = byClass[i + 1] || 0;
      areaPanel.add(ui.Label(n + ' : ' + Math.round(a).toLocaleString('fr-FR') + ' km² (' +
        (total ? (a / total * 100).toFixed(1) : '0.0') + ' %)', {fontSize: '12px', margin: '1px 8px'}));
    });
    areaPanel.add(note('Total classé : ' + Math.round(total).toLocaleString('fr-FR') + ' km². ' +
      (which === 'risk' ? 'Les zones urbaines, sols nus et eau ne sont pas classées.' : '')));
    setStatus('Analyse terminée pour « ' + CUR.label + ' ».');
  });
}

// ---- Export (Code Editor uniquement) ----
exportBtn.onClick(function () {
  if (!CUR) { setStatus('Lancez d\'abord l\'analyse.', true); return; }
  var base = ('SMADI_' + CUR.label + '_' + CUR.smKey + '_' + CUR.method).replace(/[^A-Za-z0-9_-]/g, '_');
  var items = {
    risque: CUR.risk, alea: CUR.alea, frequence: CUR.freq, duree: CUR.dur,
    tendance: CUR.trend, intensite: CUR.intensity,
    smadi_mensuel: CUR.col.select('smadi').toBands().updateMask(CUR.zoneMask).float()
  };
  try {
    Object.keys(items).forEach(function (k) {
      Export.image.toDrive({
        image: items[k].toFloat(), description: base + '_' + k, folder: 'SMADI',
        region: CUR.geom, scale: CFG.scale, crs: 'EPSG:4326', maxPixels: 1e13
      });
    });
    setStatus('Tâches d\'export créées : ouvrez l\'onglet Tasks et cliquez sur Run pour chacune.');
  } catch (e) {
    setStatus('L\'export n\'est disponible que dans le Code Editor.', true);
  }
});
