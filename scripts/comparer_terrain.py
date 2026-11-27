"""
Compare les relevés de terrain (fiche « Observatoire de la sécheresse agricole ») au SMADI.

Entrée : fichier CSV au format de docs/releves_terrain_modele.csv (séparateur « ; »),
une ligne par village et par mois. Le SMADI est lu dans cache_smadi/ (entrées déjà
téléchargées par smadi_marahoue.py) : lancer d'abord la mise à jour si les mois observés
sont récents. Le SMADI a 1 à 3 mois de retard : les mois pas encore calculés sont ignorés
et comptés.

Pour chaque relevé : SMADI et anomalie z moyens dans un rayon d'environ 3 km autour du
village (7 × 7 pixels), et part des pixels en sécheresse (z ≥ seuil) dans un rayon de 5 km.

Scores
  - corrélations de rang (Spearman) entre l'anomalie z et chaque observation
    (comparaison à la normale, part des parcelles flétries, état des cultures, pluie perçue,
    savane, points d'eau) ;
  - alertes : SMADI z ≥ seuil contre « plus sec » ou « beaucoup plus sec » ;
  - récolte : SMADI z moyen de juin à octobre contre la récolte comparée à une année normale.

Lancer : python comparer_terrain.py ../terrain/releves_2027.csv
Sorties : ../validation/validation_terrain.json et <fichier>_avec_smadi.csv
"""
import csv
import glob
import json
import os
import sys
import warnings

import numpy as np

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import smadi_marahoue as S
from validation_smadi import r3, scores, spearman

SORTIE = os.path.join(ICI, "..", "validation", "validation_terrain.json")
MIN_N = 20      # en dessous, les scores sont donnés mais marqués comme peu fiables


def num(v):
    v = (v or "").strip().replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return np.nan


def lire_releves(chemin):
    with open(chemin, encoding="utf-8-sig", newline="") as f:
        lignes = list(csv.DictReader(f, delimiter=";"))
    if not lignes:
        sys.exit(f"{chemin} : aucune ligne de relevé.")
    manque = {"lat", "lon", "mois", "comparaison_normale"} - set(lignes[0])
    if manque:
        sys.exit(f"{chemin} : colonnes manquantes {sorted(manque)} (voir releves_terrain_modele.csv).")
    return lignes


def smadi_grille():
    geom, ann = S.charger_zone()
    G = S.grille(ann)
    zone = S.masque_zone(G, ann)
    f = sorted(glob.glob(os.path.join(ICI, "cache_smadi", "entrees_era5_*.npz")))
    if not f:
        sys.exit("cache_smadi/ est vide : lancer d'abord smadi_marahoue.py.")
    Z = [np.load(x) for x in f]
    sm, lst, lsta, ndvi = (np.concatenate([z[v] for z in Z]) for v in ("sm", "lst", "lsta", "ndvi"))
    mois = [int(i) for i in np.concatenate([z["mois"] for z in Z])]
    lst, _ = S.combler_lst(lst, lsta, mois, zone)
    smadi = S.calcul_smadi(sm, lst, ndvi)
    z = S.anomalies(smadi, mois)
    return G, {m: k for k, m in enumerate(mois)}, smadi, z


def autour(x, G, lat, lon, rayon_px):
    i = int((G["y1"] - lat) / G["res"])
    j = int((lon - G["x0"]) / G["res"])
    if not (0 <= i < G["h"] and 0 <= j < G["w"]):
        return None
    return x[max(0, i - rayon_px):i + rayon_px + 1, max(0, j - rayon_px):j + rayon_px + 1]


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage : python comparer_terrain.py <releves.csv>")
    chemin = sys.argv[1]
    lignes = lire_releves(chemin)
    G, idx, smadi, z = smadi_grille()
    thr = S.CFG["droughtThresholdZ"]
    hors_zone = pas_calcule = 0
    rows = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for L in lignes:
            lat, lon, m = num(L["lat"]), num(L["lon"]), (L["mois"] or "").strip()
            if lon > 0:
                lon = -lon                          # longitude saisie sans le signe moins
            try:
                k = idx[int(m[:4]) * 12 + int(m[5:7]) - 1]
            except (KeyError, ValueError):
                pas_calcule += 1
                continue
            w3 = autour(z[k], G, lat, lon, 3)
            if w3 is None or np.isnan(w3).all():
                hors_zone += 1
                continue
            w5 = autour(z[k], G, lat, lon, 5)
            s3 = autour(smadi[k], G, lat, lon, 3)
            vu, fl = num(L.get("parcelles_vues")), num(L.get("parcelles_fletries"))
            rows.append({**L, "smadi": float(np.nanmean(s3)), "z": float(np.nanmean(w3)),
                         "pct_z_5km": float(100 * np.mean(w5[~np.isnan(w5)] >= thr)),
                         "part_fletries": fl / vu if vu and vu > 0 else np.nan})

    res = {"fichier": os.path.basename(chemin), "releves": len(lignes), "compares": len(rows),
           "mois_pas_encore_calcules": pas_calcule, "hors_zone_ou_sans_donnee": hors_zone,
           "seuil_z": thr, "fiable": len(rows) >= MIN_N}
    if not rows:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    zz = np.array([r["z"] for r in rows])
    obs = {
        "comparaison_normale (1 humide → 5 sec)": (+1, [num(r["comparaison_normale"]) for r in rows]),
        "part des parcelles flétries": (+1, [r["part_fletries"] for r in rows]),
        "état des cultures (1 très bon → 5 très mauvais)": (+1, [num(r.get("etat_cultures")) for r in rows]),
        "pluie perçue (1 bien en dessous → 5 bien au-dessus)": (-1, [num(r.get("pluie_percue")) for r in rows]),
        "savane (1 verte → 4 brûlée)": (+1, [num(r.get("savane")) for r in rows]),
        "points d'eau (1 normal → 3 à sec)": (+1, [num(r.get("points_eau")) for r in rows]),
    }
    # Part cultivée estimée au village (pour tester une future couche de vulnérabilité)
    pc = {}
    for r in rows:
        v = num(r.get("part_cultivee_pct"))
        if np.isfinite(v):
            pc.setdefault(r.get("code_village") or r.get("village"), []).append(v)
    res["part_cultivee_estimee_pct"] = {k: round(float(np.mean(v)), 1) for k, v in pc.items()}
    res["correlations"] = {}
    for nom, (signe, v) in obs.items():
        r, n = spearman(zz, v)
        res["correlations"][nom] = {"r": r3(r), "n": n, "signe_attendu": "+" if signe > 0 else "−",
                                    "bon_sens": None if not np.isfinite(r) else bool(r * signe > 0)}
    comp = np.array(obs["comparaison_normale (1 humide → 5 sec)"][1])
    ok = ~np.isnan(comp)
    res["alertes_vs_plus_sec"] = scores(zz[ok] >= thr, comp[ok] >= 4)

    # Récolte : une valeur par village et par année (fiche de novembre)
    par_village = {}
    for r in rows:
        par_village.setdefault((r.get("code_village") or r.get("village"), r["mois"][:4]), []).append(r)
    zs, rec = [], []
    for (_, an), rs in par_village.items():
        saison = [x["z"] for x in rs if 6 <= int(x["mois"][5:7]) <= 10]
        recolte = [num(x.get("recolte_vs_normale")) for x in rs if np.isfinite(num(x.get("recolte_vs_normale")))]
        if saison and recolte:
            zs.append(np.mean(saison)); rec.append(recolte[-1])
    rr, nr = spearman(zs, rec)
    res["recolte_vs_smadi_juin_oct"] = {"r": r3(rr), "n": nr, "signe_attendu": "+"}

    os.makedirs(os.path.dirname(SORTIE), exist_ok=True)
    with open(SORTIE, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    sortie_csv = os.path.splitext(chemin)[0] + "_avec_smadi.csv"
    champs = list(lignes[0]) + ["smadi", "z", "pct_z_5km", "part_fletries"]
    with open(sortie_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=champs, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if not res["fiable"]:
        print(f"\nAttention : seulement {len(rows)} relevés comparés (au moins {MIN_N} conseillés).")
    print(f"Écrit : {SORTIE}\n        {sortie_csv}")


if __name__ == "__main__":
    main()
