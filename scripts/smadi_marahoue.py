"""
SMADI mensuel à 1 km sur la Marahoué (Côte d'Ivoire), mis à jour chaque mois.

Version automatique de l'application Earth Engine « SMADI mensuel à 1 km »
(smadi_gee_app.js) : mêmes données, mêmes formules, mêmes seuils (CFG, VULN).

    SMADI(m) = SMCI(m) × MLST(m−1) / NDVI(m−1)
    SMCI = (SMmax − SM) / (SMmax − SMmin)   MLST = LST / LSTmax   (bornes par pixel)

Déroulement
  1. Earth Engine (API Python) : pour chaque année, extraction des entrées
     mensuelles sur une grille de 0,01° (≈ 1,1 km) couvrant la Marahoué :
     humidité du sol (ERA5-Land par défaut, rééchantillonnée en bilinéaire),
     LST MODIS MOD11A2 et NDVI MODIS MOD13A3 du mois précédent.
     Les années déjà téléchargées sont gardées dans cache_smadi/ ; seules les
     années récentes sont relues (données provisoires remplacées, nouveaux mois).
  2. Calcul local (numpy) du SMADI, des anomalies mensuelles z et des produits :
     fréquence, durée, intensité, aléa, risque (× vulnérabilité MODIS MCD12Q1),
     tendance (pente de Sen par décennie), pour les deux modes de l'application
     (seuil fixe SMADI ≥ 1 et anomalie z ≥ 1,5).
  3. Écriture de docs/smadi_marahoue.json (page web) et docs/serie_smadi_marahoue.csv.

Connexion à Earth Engine
  - GitHub Actions : secret EE_SERVICE_ACCOUNT_KEY = contenu JSON de la clé d'un
    compte de service enregistré pour Earth Engine (voir README).
  - Sur un PC : `earthengine authenticate` une fois, puis EE_PROJECT=mon-projet.

Variables d'environnement
  SM_SOURCE   era5 (défaut), fldas, terraclimate, gldas, smap, ou l'identifiant
              d'un asset image multibande nommé …_AAAAMM_AAAAMM (TAMSAT-SM préparé
              avec prepare_tamsat_sm.py)
  EE_PROJECT  projet Google Cloud enregistré pour Earth Engine
  OUTDIR      dossier de sortie (défaut ../docs)
"""

import base64
import csv
import datetime as dt
import json
import math
import os
import sys
import time
import warnings

import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ICI = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ICI, "cache_smadi")
OUTDIR = os.path.abspath(os.path.join(ICI, os.environ.get("OUTDIR", "../docs")))
ZONE_FICHIER = os.path.join(ICI, "marahoue.geojson")

# ============================ PARAMÈTRES (identiques à l'application GEE) ====
CFG = {
    "res": 0.01,                          # grille de calcul en degrés (≈ 1,1 km)
    "droughtThreshold": 1.0,              # SMADI ≥ seuil → mois en sécheresse
    "smadiBreaks": [0.5, 1.0, 2.0, 3.0],
    "freqBreaks": [5, 10, 20, 30],
    "intensityBreaks": [1.5, 2.0, 2.5, 3.0],
    "droughtThresholdZ": 1.5,
    "freqBreaksZ": [7.5, 8.1, 8.6, 9.2],
    "intensityBreaksZ": [2.03, 2.09, 2.14, 2.21],
    "aleaBreaks": [3, 5, 9, 15],
    "riskBreaks": [4, 7, 12, 18],
    "ndviMin": 0.1,
    "minValidMonths": 24,
    "moisCartes": 36,                     # nombre de derniers mois cartographiés dans la page
}

VULN = {1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 2, 7: 2, 8: 3, 9: 3, 10: 3, 11: 1,
        12: 5, 13: 0, 14: 4, 15: 0, 16: 0, 17: 0}

SM_SOURCES = {
    "era5": ("ERA5-Land, 0–7 cm, ~9 km", "ECMWF/ERA5_LAND/MONTHLY_AGGR",
             "volumetric_soil_water_layer_1", "first", (2000, 3)),
    "fldas": ("FLDAS Noah, 0–10 cm, ~11 km", "NASA/FLDAS/NOAH01/C/GL/M/V001",
              "SoilMoi00_10cm_tavg", "first", (2000, 3)),
    "terraclimate": ("TerraClimate, bilan hydrique, ~4 km", "IDAHO_EPSCOR/TERRACLIMATE",
                     "soil", "first", (2000, 3)),
    "gldas": ("GLDAS 2.1 Noah, 0–10 cm, ~25 km", "NASA/GLDAS/V021/NOAH/G025/T3H",
              "SoilMoi0_10cm_inst", "mean", (2000, 3)),
    "smap": ("SMAP L4, 0–5 cm, ~9 km", "NASA/SMAP/SPL4SMGP/007", "sm_surface", "mean", (2015, 4)),
}

NODATA = -9999.0
VILLES = [("Bouaflé", -5.745, 6.990), ("Sinfra", -5.912, 6.621), ("Zuénoula", -6.052, 7.430)]


# ============================ OUTILS =========================================
def midx(y, m):
    """Indice de mois (m de 1 à 12)."""
    return y * 12 + (m - 1)


def ym(i):
    return i // 12, i % 12 + 1


def lbl(i):
    y, m = ym(i)
    return f"{y}-{m:02d}"


def mois_de_ms(ms):
    d = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)
    return midx(d.year, d.month), d.day


def charger_zone():
    with open(ZONE_FICHIER, encoding="utf-8") as f:
        fc = json.load(f)
    g = fc["features"][0]["geometry"]
    anneaux = g["coordinates"] if g["type"] == "Polygon" else [r for p in g["coordinates"] for r in p]
    return g, [np.asarray(r, float) for r in anneaux]


def grille(anneaux):
    r = CFG["res"]
    pts = np.concatenate(anneaux)
    x0 = math.floor((pts[:, 0].min() - 2 * r) / r) * r
    x1 = math.ceil((pts[:, 0].max() + 2 * r) / r) * r
    y0 = math.floor((pts[:, 1].min() - 2 * r) / r) * r
    y1 = math.ceil((pts[:, 1].max() + 2 * r) / r) * r
    w, h = int(round((x1 - x0) / r)), int(round((y1 - y0) / r))
    return {"x0": round(x0, 6), "y1": round(y1, 6), "res": r, "w": w, "h": h}


def masque_zone(G, anneaux):
    """Pixels dont le centre est dans le polygone (règle pair-impair, trous compris)."""
    from matplotlib.path import Path
    r = G["res"]
    lon = G["x0"] + (np.arange(G["w"]) + 0.5) * r
    lat = G["y1"] - (np.arange(G["h"]) + 0.5) * r
    LON, LAT = np.meshgrid(lon, lat)
    pts = np.column_stack([LON.ravel(), LAT.ravel()])
    dedans = np.zeros(len(pts), bool)
    for a in anneaux:
        dedans ^= Path(a).contains_points(pts)
    return dedans.reshape(G["h"], G["w"])


# ============================ EARTH ENGINE ===================================
_ee = None


def init_ee():
    global _ee
    if _ee is not None:
        return _ee
    import ee
    key = os.environ.get("EE_SERVICE_ACCOUNT_KEY", "").strip()
    project = os.environ.get("EE_PROJECT", "").strip() or None
    if key:
        info = json.loads(key)
        creds = ee.ServiceAccountCredentials(info["client_email"], key_data=key)
        ee.Initialize(creds, project=project or info.get("project_id"))
    elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        # Connexion sans clé (Workload Identity Federation, google-github-actions/auth)
        import google.auth
        creds, _ = google.auth.default(scopes=[
            "https://www.googleapis.com/auth/earthengine",
            "https://www.googleapis.com/auth/cloud-platform"])
        ee.Initialize(creds, project=project)
    else:
        ee.Initialize(project=project)
    _ee = ee
    return ee


class Source:
    """Source d'humidité du sol : collection GEE ou asset image multibande."""

    def __init__(self, cle):
        ee = init_ee()
        self.cle = cle
        if cle in SM_SOURCES:
            self.label, cid, band, self.how, self.start = SM_SOURCES[cle]
            self.col = ee.ImageCollection(cid).select(band)
            self.last_ms = None
        else:
            import re
            name = cle.rstrip("/").split("/")[-1]
            mt = re.search(r"_(\d{4})(\d{2})_(\d{4})(\d{2})", name)
            if not mt:
                sys.exit("SM_SOURCE : le nom de l'asset doit contenir _AAAAMM_AAAAMM "
                         "(ex. tamsat_sm_surface_200001_202610).")
            y0, m0, y1, m1 = map(int, mt.groups())
            n = midx(y1, m1) - midx(y0, m0) + 1
            img = ee.Image(cle)
            nb = img.bandNames().size().getInfo()
            if nb != n:
                sys.exit(f"SM_SOURCE : l'image a {nb} bandes alors que son nom annonce {n} mois.")

            def bande(k):
                k = ee.Number(k).int()
                b = img.select(ee.List([k]))
                return (b.updateMask(b.gt(-9000)).rename("sm")
                        .set("system:time_start", ee.Date.fromYMD(y0, m0, 1).advance(k, "month").millis()))
            self.col = ee.ImageCollection(ee.List.sequence(0, n - 1).map(bande))
            self.label = f"asset {name}"
            self.how = "first"
            self.start = ym(max(midx(2000, 3), midx(y0, m0)))
            self.last_ms = int(dt.datetime(y1, m1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        self.proj = ee.Image(self.col.first()).projection()


def ndvi_col():
    return init_ee().ImageCollection("MODIS/061/MOD13A3").select("NDVI")


def lst_col():
    return init_ee().ImageCollection("MODIS/061/MOD11A2").select("LST_Day_1km")


def periode_disponible(src):
    """Premier et dernier mois SMADI calculables (même règle que l'application)."""
    ee = init_ee()

    def last(col):
        return ee.ImageCollection(col).limit(1, "system:time_start", False).first().get("system:time_start")
    d = {"ndvi": last(ndvi_col()), "lst": last(lst_col())}
    if src.last_ms is None:
        d["sm"] = last(src.col)
    L = ee.Dictionary(d).getInfo()
    sm_ms = src.last_ms if src.last_ms is not None else L["sm"]

    def complet(ms, min_day):
        i, day = mois_de_ms(ms)
        return i - (0 if day >= min_day else 1)
    lst_idx = complet(L["lst"], 20)
    sm_idx = complet(sm_ms, 25) if src.how == "mean" else mois_de_ms(sm_ms)[0]
    fin = min(sm_idx, mois_de_ms(L["ndvi"])[0] + 1, lst_idx + 1)
    return midx(*src.start), fin


def monthly_image(col, date, how, name):
    ee = init_ee()
    c = col.filterDate(date, date.advance(1, "month"))
    vide = ee.Image.constant(0).updateMask(ee.Image.constant(0))
    img = ee.Image(ee.Algorithms.If(c.size().gt(0), c.first() if how == "first" else c.mean(), vide))
    return img.rename(name)


def compute_pixels(img, G, essais=4):
    ee = init_ee()
    req = {
        "expression": img.toFloat().unmask(NODATA),
        "fileFormat": "NUMPY_NDARRAY",
        "grid": {
            "dimensions": {"width": G["w"], "height": G["h"]},
            "affineTransform": {"scaleX": G["res"], "shearX": 0, "translateX": G["x0"],
                                "shearY": 0, "scaleY": -G["res"], "translateY": G["y1"]},
            "crsCode": "EPSG:4326",
        },
    }
    for k in range(essais):
        try:
            return ee.data.computePixels(req)
        except Exception as e:  # quota, délai dépassé, panne passagère
            if k == essais - 1:
                raise
            print(f"    Earth Engine : {e} — nouvel essai dans {30 * (k + 1)} s")
            time.sleep(30 * (k + 1))


def telecharger_annee(src, mois, G):
    """Entrées des mois donnés (indices) : sm(m), lst(m−1) en K, ndvi(m−1)."""
    ee = init_ee()
    bandes = None
    for i in mois:
        y, m = ym(i)
        d = ee.Date.fromYMD(y, m, 1)
        p = d.advance(-1, "month")
        sm = monthly_image(src.col, d, src.how, f"sm_{m:02d}").setDefaultProjection(src.proj).resample("bilinear")
        lst = monthly_image(lst_col(), p, "mean", f"lst_{m:02d}").multiply(0.02)
        ndvi = monthly_image(ndvi_col(), p, "first", f"ndvi_{m:02d}").multiply(0.0001)
        b = sm.addBands(lst).addBands(ndvi)
        bandes = b if bandes is None else bandes.addBands(b)
    arr = compute_pixels(bandes, G)
    out = {}
    for v in ("sm", "lst", "ndvi"):
        x = np.stack([np.asarray(arr[f"{v}_{ym(i)[1]:02d}"], np.float32) for i in mois])
        x[x <= NODATA + 1] = np.nan
        out[v] = x
    return out


def telecharger_vulnerabilite(G):
    ee = init_ee()
    lc = ee.ImageCollection("MODIS/061/MCD12Q1").select("LC_Type1").sort("system:time_start").mosaic()
    keys = list(VULN)
    vuln = lc.remap(keys, [VULN[k] for k in keys], 0).rename("v")
    a = np.asarray(compute_pixels(vuln, G)["v"], np.float32)
    a[a <= NODATA + 1] = np.nan
    return a


# ============================ CACHE ==========================================
def cache_nom(cle, annee):
    s = cle if cle in SM_SOURCES else "asset_" + cle.rstrip("/").split("/")[-1]
    return os.path.join(CACHE, f"entrees_{s}_{annee}.npz")


def lire_cache(chemin, G):
    if not os.path.exists(chemin):
        return None
    z = np.load(chemin)
    if list(z["grille"]) != [G["x0"], G["y1"], G["res"], G["w"], G["h"]]:
        return None
    return {"mois": list(z["mois"]), "sm": z["sm"], "lst": z["lst"], "ndvi": z["ndvi"]}


def entrees(src, debut, fin, G, relire=4):
    """Entrées de debut à fin (indices de mois), depuis le cache ou Earth Engine.
    Les années touchant les `relire` derniers mois sont toujours relues."""
    os.makedirs(CACHE, exist_ok=True)
    blocs = []
    for annee in range(ym(debut)[0], ym(fin)[0] + 1):
        mois = [i for i in range(max(debut, midx(annee, 1)), min(fin, midx(annee, 12)) + 1)]
        chemin = cache_nom(src.cle, annee)
        c = lire_cache(chemin, G)
        recent = mois[-1] > fin - relire
        if c is None or c["mois"] != mois or recent:
            print(f"  {annee} : téléchargement de {len(mois)} mois")
            d = telecharger_annee(src, mois, G)
            np.savez_compressed(chemin, mois=np.array(mois), sm=d["sm"], lst=d["lst"], ndvi=d["ndvi"],
                                grille=np.array([G["x0"], G["y1"], G["res"], G["w"], G["h"]]))
            c = {"mois": mois, **d}
        blocs.append(c)
    return (np.concatenate([b["sm"] for b in blocs]), np.concatenate([b["lst"] for b in blocs]),
            np.concatenate([b["ndvi"] for b in blocs]))


def vulnerabilite(G):
    chemin = os.path.join(CACHE, "vulnerabilite_mcd12q1.npz")
    if os.path.exists(chemin):
        z = np.load(chemin)
        age = time.time() - os.path.getmtime(chemin)
        if list(z["grille"]) == [G["x0"], G["y1"], G["res"], G["w"], G["h"]] and age < 180 * 86400:
            return z["v"]
    os.makedirs(CACHE, exist_ok=True)
    v = telecharger_vulnerabilite(G)
    np.savez_compressed(chemin, v=v, grille=np.array([G["x0"], G["y1"], G["res"], G["w"], G["h"]]))
    return v


# ============================ CALCUL (numpy) =================================
def calcul_smadi(sm, lst, ndvi):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        smin, smax, lmax = np.nanmin(sm, 0), np.nanmax(sm, 0), np.nanmax(lst, 0)
        den = smax - smin
        smci = np.clip((smax - sm) / np.where(den > 0, den, np.nan), 0, 1)
        s = smci * (lst / lmax) / ndvi
    s[~(ndvi > CFG["ndviMin"])] = np.nan
    return s.astype(np.float32)


def anomalies(smadi, mois):
    """z = (SMADI − moyenne du même mois) / écart-type du même mois (par pixel)."""
    cal = np.array([ym(i)[1] for i in mois])
    z = np.full_like(smadi, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for m in range(1, 13):
            k = cal == m
            if not k.any():
                continue
            mu = np.nanmean(smadi[k], 0)
            sd = np.nanstd(smadi[k], 0)          # écart-type de population (ee.Reducer.stdDev)
            z[k] = (smadi[k] - mu) / np.where(sd > 0, sd, np.nan)
    return z


def classes(x, breaks):
    """Classes 1..n+1 à partir de bornes inférieures ; 0 = sans donnée."""
    c = np.ones(x.shape, np.uint8)
    for b in breaks:
        c += (np.nan_to_num(x, nan=-np.inf) >= b)
    c[np.isnan(x)] = 0
    return c


def pente_sen(t, y):
    """Pente de Sen par pixel ; y : (n_années, H, W) avec NaN."""
    n = len(t)
    i, j = np.triu_indices(n, 1)
    pentes = (y[j] - y[i]) / (np.asarray(t, float)[j] - np.asarray(t, float)[i])[:, None, None]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(pentes, 0)


def produits(D, thr, freq_b, int_b, vuln, zone):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        count = np.sum(~np.isnan(D), 0)
        valid = (count >= CFG["minValidMonths"]) & zone
        sec = np.nan_to_num(D, nan=-np.inf) >= thr
        dm = sec.sum(0).astype(float)
        freq = np.where(count > 0, dm / np.maximum(count, 1) * 100, np.nan)
        inten = np.where(dm > 0, np.where(sec, D, 0).sum(0) / np.maximum(dm, 1), np.nan)
        debuts = sec[0].astype(float) + np.sum(sec[1:] & ~sec[:-1], 0)
        duree = np.where(debuts > 0, dm / np.maximum(debuts, 1), np.nan)

        freq_c = classes(freq, freq_b).astype(float)
        freq_c[freq_c == 0] = np.nan
        int_c = classes(np.nan_to_num(inten, nan=0.0), int_b).astype(float)
        alea = classes(freq_c * int_c, CFG["aleaBreaks"])
        alea[~valid] = 0
        v = np.nan_to_num(vuln, nan=0.0)
        a = alea.astype(float)
        a[a == 0] = np.nan
        risque = classes(a * v, CFG["riskBreaks"])
        risque[(v <= 0) | ~valid] = 0

    def m(x):
        x = x.astype(np.float32).copy()
        x[~valid] = np.nan
        return x
    return {"freq": m(freq), "duree": m(duree), "intensite": m(inten), "alea": alea, "risque": risque}


def tendance(smadi, mois, zone):
    ans = sorted({ym(i)[0] for i in mois})
    complets = [a for a in ans if all(midx(a, mm) in mois for mm in range(1, 13))]
    idx = {i: k for k, i in enumerate(mois)}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        annuel = np.stack([np.nanmean(smadi[[idx[midx(a, mm)] for mm in range(1, 13)]], 0) for a in complets])
    t = pente_sen(complets, annuel) * 10
    count = np.sum(~np.isnan(smadi), 0)
    t[~((count >= CFG["minValidMonths"]) & zone)] = np.nan
    return t.astype(np.float32), complets


def moyenne_zone(x, zone):
    v = x[..., zone]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(v, -1)


def pct_zone(x, thr, zone):
    v = x[..., zone]
    ok = ~np.isnan(v)
    n = ok.sum(-1)
    s = (np.nan_to_num(v, nan=-np.inf) >= thr).sum(-1)
    return np.where(n > 0, 100 * s / np.maximum(n, 1), np.nan)


# ============================ SORTIES ========================================
def code(x, lo, step):
    """Quantification sur 1 octet (255 = sans donnée), encodée en base64."""
    q = np.round((np.asarray(x, float) - lo) / step)
    q = np.clip(np.nan_to_num(q, nan=255), 0, 254).astype(np.uint8)
    q[np.isnan(np.asarray(x, float))] = 255
    return {"lo": lo, "step": step, "b64": base64.b64encode(q.tobytes()).decode()}


def code_classes(c):
    q = np.asarray(c, np.uint8).copy()
    q[q == 0] = 255
    return {"lo": 0, "step": 1, "b64": base64.b64encode(q.tobytes()).decode()}


def r(v, n=3):
    return None if v is None or not np.isfinite(v) else round(float(v), n)


def main():
    cle = os.environ.get("SM_SOURCE", "era5").strip() or "era5"
    geom, anneaux = charger_zone()
    G = grille(anneaux)
    zone = masque_zone(G, anneaux)
    print(f"Grille {G['w']} × {G['h']} pixels de {G['res']}°, {zone.sum()} pixels dans la Marahoué")

    src = Source(cle)
    debut, fin = periode_disponible(src)
    print(f"Source {src.label} : {lbl(debut)} → {lbl(fin)} ({fin - debut + 1} mois)")
    sm, lst, ndvi = entrees(src, debut, fin, G)
    vuln = vulnerabilite(G)
    ecrire(cle, src.label, list(range(debut, fin + 1)), sm, lst, ndvi, vuln, G, zone, anneaux)


def ecrire(cle, label, mois, sm, lst, ndvi, vuln, G, zone, anneaux):
    smadi = calcul_smadi(sm, lst, ndvi)
    z = anomalies(smadi, mois)
    smadi_z = np.where(zone, smadi, np.nan)
    z_z = np.where(zone, z, np.nan)

    P = {
        "fixe": produits(smadi_z, CFG["droughtThreshold"], CFG["freqBreaks"],
                         CFG["intensityBreaks"], vuln, zone),
        "z": produits(z_z, CFG["droughtThresholdZ"], CFG["freqBreaksZ"],
                      CFG["intensityBreaksZ"], vuln, zone),
    }
    trend, complets = tendance(smadi_z, mois, zone)

    # Série de la zone
    s_mean = moyenne_zone(smadi, zone)
    z_mean = moyenne_zone(z, zone)
    p_fixe = pct_zone(smadi, CFG["droughtThreshold"], zone)
    p_z = pct_zone(z, CFG["droughtThresholdZ"], zone)
    serie = [{"m": lbl(i), "smadi": r(s_mean[k]), "z": r(z_mean[k], 2),
              "pct_fixe": r(p_fixe[k], 1), "pct_z": r(p_z[k], 1)} for k, i in enumerate(mois)]

    os.makedirs(OUTDIR, exist_ok=True)
    with open(os.path.join(OUTDIR, "serie_smadi_marahoue.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["mois", "smadi_moyen", "anomalie_z_moyenne", "pct_zone_smadi_ge_1", "pct_zone_z_ge_1_5"])
        for s in serie:
            w.writerow([s["m"], s["smadi"], s["z"], s["pct_fixe"], s["pct_z"]])

    # Cartes des derniers mois
    n = min(CFG["moisCartes"], len(mois))
    cartes_mois = [{"m": lbl(mois[k]), "smadi": code(smadi_z[k], 0, 0.025),
                    "z": code(np.clip(z_z[k], -4, 4.4), -4, 1 / 30)} for k in range(len(mois) - n, len(mois))]

    def lay(p):
        return {"freq": code(np.clip(p["freq"], 0, 100), 0, 0.4),
                "duree": code(np.clip(p["duree"], 0, 12.7), 0, 0.05),
                "intensite": code(np.clip(p["intensite"], 0, 12.7), 0, 0.05),
                "alea": code_classes(p["alea"]), "risque": code_classes(p["risque"])}

    def surfaces(c):
        lat = G["y1"] - (np.arange(G["h"]) + 0.5) * G["res"]
        a = (G["res"] * 111.32) ** 2 * np.cos(np.radians(lat))[:, None] * np.ones((1, G["w"]))
        return [round(float(a[(c == k) & zone].sum()), 1) for k in range(1, 6)]

    dern = len(mois) - 1
    out = {
        "meta": {
            "zone": "Marahoué (Côte d'Ivoire)", "source_sm": label, "cle_source": cle,
            "periode": [lbl(mois[0]), lbl(mois[-1])], "n_mois": len(mois),
            "annees_tendance": [complets[0], complets[-1]] if complets else None,
            "genere": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "cfg": CFG, "vuln": VULN,
        },
        "grille": {**G, "zone": code_classes(zone.astype(np.uint8))},
        "contour": [[[round(x, 4), round(y, 4)] for x, y in a[:: max(1, len(a) // 400)]] for a in anneaux],
        "villes": VILLES,
        "produits": {"fixe": lay(P["fixe"]), "z": lay(P["z"]),
                     "tendance": code(np.clip(trend, -1.27, 1.27), -1.27, 0.01),
                     "vulnerabilite": code(np.where(zone, np.nan_to_num(vuln, nan=0), np.nan), 0, 1)},
        "surfaces_km2": {"fixe": {"alea": surfaces(P["fixe"]["alea"]), "risque": surfaces(P["fixe"]["risque"])},
                         "z": {"alea": surfaces(P["z"]["alea"]), "risque": surfaces(P["z"]["risque"])}},
        "resume_zone": {
            m: {"freq": r(np.nanmean(P[m]["freq"][zone]), 1), "duree": r(np.nanmean(P[m]["duree"][zone]), 2),
                "intensite": r(np.nanmean(P[m]["intensite"][zone]), 2)} for m in ("fixe", "z")},
        "dernier_mois": {"m": lbl(mois[dern]), "smadi": r(s_mean[dern]), "z": r(z_mean[dern], 2),
                         "pct_fixe": r(p_fixe[dern], 1), "pct_z": r(p_z[dern], 1)},
        "serie": serie,
        "cartes_mois": cartes_mois,
    }
    chemin = os.path.join(OUTDIR, "smadi_marahoue.json")
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Écrit : {chemin} ({os.path.getsize(chemin) / 1e6:.2f} Mo)")
    d = out["dernier_mois"]
    print(f"Dernier mois {d['m']} : SMADI moyen {d['smadi']}, z {d['z']}, "
          f"{d['pct_fixe']} % de la zone avec SMADI ≥ 1, {d['pct_z']} % avec z ≥ 1,5")


if __name__ == "__main__":
    main()
