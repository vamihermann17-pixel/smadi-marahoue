"""
Calage national des classes de fréquence et d'intensité du mode « anomalie » (z ≥ seuil).

Comme dans l'application GEE, les bornes des classes 1..5 sont les quantiles 20/40/60/80 % de
la distribution observée sur toute la Côte d'Ivoire. Ici : SMADI de la chaîne automatique
(ERA5-Land, LST Terra comblée par Aqua, NDVI MODIS), grille de 0,05° (≈ 5,5 km), mars 2000 →
dernier mois disponible, pour les seuils z ≥ 1,0 et z ≥ 1,5.

Lancé par le workflow « Calage des seuils » (Earth Engine) ; écrit
../validation/calibration_seuils_z.json
"""
import json
import os
import sys
import warnings

import numpy as np

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import smadi_marahoue as S

S.CFG["res"] = 0.05
S.CACHE = os.path.join(ICI, "cache_calage_civ")
S.ZONE_FICHIER = os.path.join(ICI, "cote_ivoire.geojson")
SORTIE = os.path.join(ICI, "..", "validation", "calibration_seuils_z.json")


def main():
    cle = os.environ.get("SM_SOURCE", "era5").strip() or "era5"
    _, anneaux = S.charger_zone()
    G = S.grille(anneaux)
    zone = S.masque_zone(G, anneaux)
    print(f"Grille nationale {G['w']} × {G['h']} pixels de {G['res']}°, {zone.sum()} pixels en Côte d'Ivoire")
    src = S.Source(cle)
    debut, fin = S.periode_disponible(src)
    sm, lst, lsta, ndvi = S.entrees(src, debut, fin, G)
    mois = list(range(debut, fin + 1))
    lst, _ = S.combler_lst(lst, lsta, mois, zone)
    smadi = S.calcul_smadi(sm, lst, ndvi)
    z = np.where(zone, S.anomalies(smadi, mois), np.nan)

    q = [20, 40, 60, 80]
    res = {"source_sm": src.label, "periode": [S.lbl(debut), S.lbl(fin)], "grille_deg": G["res"],
           "pixels": int(zone.sum()), "quantiles": q, "seuils": {}}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        count = np.sum(~np.isnan(z), 0)
        valid = zone & (count >= S.CFG["minValidMonths"])
        for thr in (1.0, 1.5):
            sec = np.nan_to_num(z, nan=-np.inf) >= thr
            dm = sec.sum(0)
            freq = np.where(valid, dm / np.maximum(count, 1) * 100, np.nan)
            inten = np.where(valid & (dm > 0), np.where(sec, z, 0).sum(0) / np.maximum(dm, 1), np.nan)
            f, i = freq[valid], inten[valid & ~np.isnan(inten)]
            res["seuils"][str(thr)] = {
                "freqBreaksZ": [round(float(v), 1) for v in np.percentile(f, q)],
                "intensityBreaksZ": [round(float(v), 2) for v in np.percentile(i, q)],
                "freq_P5_P95": [round(float(v), 1) for v in np.percentile(f, [5, 95])],
                "intensite_P5_P95": [round(float(v), 2) for v in np.percentile(i, [5, 95])],
            }
    os.makedirs(os.path.dirname(SORTIE), exist_ok=True)
    with open(SORTIE, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
