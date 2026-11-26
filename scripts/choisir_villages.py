"""
Choix des villages de l'observatoire de la sécheresse (vérification du SMADI au champ, 2027).

Candidats : villages OpenStreetMap (place=village) situés dans la Marahoué, à plus de 3 km
de la limite de la région (la comparaison utilise un rayon de 3 km).

Pour chaque candidat (pixels SMADI dans un rayon d'environ 3 km) :
  - classe de risque du mode anomalie (z ≥ seuil de CFG) au pixel du village ;
  - part des mois de juillet à octobre sans LST après comblement par Aqua (nuages) ;
  - sous-préfecture (geoBoundaries CIV ADM3).

Sélection : villages avec moins de 25 % de LST manquante ; au plus un village par
sous-préfecture ; au moins 12 km entre deux villages (≈ une maille ERA5-Land) ; quotas par
classe de risque (faible, moyen, fort) ; dans chaque quota, on ajoute à chaque tour le village
le plus éloigné de ceux déjà choisis. 5 villages de remplacement sont tirés de la même façon.

Lancer : python choisir_villages.py <osm_villages.json> <geoBoundaries-CIV-ADM3.geojson>
Sorties (dossier ../terrain) : villages_observatoire.csv, carte_villages_observatoire.png/.pdf,
releves_2027.csv (relevés pré-remplis), sources/ (extraits utilisés)
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

OUT = os.path.join(ICI, "..", "terrain")
QUOTAS = {"Faible": 3, "Moyen": 6, "Fort": 6}       # 15 villages
N_REMPLACANTS = 5
DIST_MIN_KM = 12.0
LST_MAX = 0.25
BORD_KM = 3.0
MOIS_2027 = [f"2027-{m:02d}" for m in range(4, 12)]
NOM_RISQUE = {1: "Très faible", 2: "Faible", 3: "Moyen", 4: "Fort", 5: "Très fort"}


def repare(t):
    """Noms geoBoundaries encodés deux fois (« ZuÃ©noula » → « Zuénoula »)."""
    try:
        return t.encode("latin-1").decode("utf-8") if "Ã" in t else t
    except (UnicodeEncodeError, UnicodeDecodeError):
        return t


def km(a, b):
    """Distance approchée (km) entre deux points (lon, lat)."""
    dx = (a[0] - b[0]) * 111.32 * np.cos(np.radians((a[1] + b[1]) / 2))
    return float(np.hypot(dx, (a[1] - b[1]) * 110.57))


def dans(pt, anneaux):
    from matplotlib.path import Path
    d = False
    for a in anneaux:
        d ^= bool(Path(a).contains_point(pt))
    return d


def dist_bord(pt, anneaux):
    best = np.inf
    for a in anneaux:
        p, q = a[:-1], a[1:]
        v = q - p
        t = np.clip(((pt - p) * v).sum(1) / np.maximum((v ** 2).sum(1), 1e-12), 0, 1)
        proj = p + t[:, None] * v
        best = min(best, min(km(pt, x) for x in proj))
    return best


def anneaux_de(g):
    cs = g["coordinates"] if g["type"] == "Polygon" else [r for p in g["coordinates"] for r in p]
    return [np.asarray(r, float) for r in cs]


def produits_locaux():
    geom, ann = S.charger_zone()
    G = S.grille(ann)
    zone = S.masque_zone(G, ann)
    f = sorted(glob.glob(os.path.join(ICI, "cache_smadi", "entrees_era5_*.npz")))
    Z = [np.load(x) for x in f]
    sm, lst, lsta, ndvi = (np.concatenate([z[v] for z in Z]) for v in ("sm", "lst", "lsta", "ndvi"))
    mois = [int(i) for i in np.concatenate([z["mois"] for z in Z])]
    lst, _ = S.combler_lst(lst, lsta, mois, zone)
    smadi = S.calcul_smadi(sm, lst, ndvi)
    z = np.where(zone, S.anomalies(smadi, mois), np.nan)
    vuln = S.vulnerabilite(G)["v"]
    P = S.produits(z, S.CFG["droughtThresholdZ"], S.CFG["freqBreaksZ"], S.CFG["intensityBreaksZ"], vuln, zone)
    cal = np.array([S.ym(i)[1] for i in mois])
    saison = (cal >= 7) & (cal <= 10)
    lst_manque = np.isnan(lst[saison]).mean(0)
    return G, ann, zone, P["risque"], vuln, lst_manque


def fenetre(x, G, lon, lat, r=3):
    i = int((G["y1"] - lat) / G["res"]); j = int((lon - G["x0"]) / G["res"])
    return x[max(0, i - r):i + r + 1, max(0, j - r):j + r + 1], x[i, j]


def main():
    if len(sys.argv) < 3:
        sys.exit("Usage : python choisir_villages.py <osm_villages.json> <geoBoundaries-CIV-ADM3.geojson>")
    osm = json.load(open(sys.argv[1], encoding="utf-8"))
    adm3 = json.load(open(sys.argv[2], encoding="utf-8"))
    G, ann, zone, risque, vuln, lst_manque = produits_locaux()

    sp = []
    for f in adm3["features"]:
        a = anneaux_de(f["geometry"])
        pts = np.concatenate(a)
        if pts[:, 0].max() < G["x0"] or pts[:, 0].min() > G["x0"] + G["w"] * G["res"] \
                or pts[:, 1].max() < G["y1"] - G["h"] * G["res"] or pts[:, 1].min() > G["y1"]:
            continue
        sp.append((repare(f["properties"]["shapeName"]), a, f["geometry"]))

    cands = []
    for e in osm["elements"]:
        t = e.get("tags", {})
        if t.get("place") != "village" or not t.get("name"):
            continue
        pt = np.array([e["lon"], e["lat"]])
        if not dans(pt, ann) or dist_bord(pt, ann) < BORD_KM:
            continue
        wr, rc = fenetre(risque.astype(float), G, *pt)
        wl, _ = fenetre(lst_manque, G, *pt)
        if rc == 0:
            continue
        nom_sp = next((n for n, a, _ in sp if dans(pt, a)), "")
        cands.append({"nom": t["name"], "osm_id": e["id"], "lon": round(e["lon"], 4), "lat": round(e["lat"], 4),
                      "risque": int(rc), "classe": NOM_RISQUE[int(rc)],
                      "lst_manquante": round(float(np.nanmean(wl)), 3), "sous_prefecture": nom_sp})
    print(f"{len(cands)} villages candidats dans la Marahoué")
    ok = [c for c in cands if c["lst_manquante"] < LST_MAX]
    print(f"{len(ok)} avec moins de {int(LST_MAX * 100)} % de LST manquante en juillet–octobre")

    # Les classes « très faible » et « très fort » rejoignent les quotas voisins
    groupe = lambda c: "Faible" if c["risque"] <= 2 else ("Moyen" if c["risque"] == 3 else "Fort")
    rng = np.random.default_rng(2027)
    choisis, sp_pris = [], set()

    def tirer(n, pool, liste):
        for _ in range(n):
            libres = [c for c in pool if c not in choisis + liste and c["sous_prefecture"] not in sp_pris
                      and all(km((c["lon"], c["lat"]), (d["lon"], d["lat"])) >= DIST_MIN_KM for d in choisis + liste)]
            if not libres:
                return
            deja = choisis + liste
            if deja:
                c = max(libres, key=lambda c: min(km((c["lon"], c["lat"]), (d["lon"], d["lat"])) for d in deja))
            else:
                c = libres[rng.integers(len(libres))]
            liste.append(c); sp_pris.add(c["sous_prefecture"])

    for g, n in QUOTAS.items():
        sel = []
        tirer(n, [c for c in ok if groupe(c) == g], sel)
        choisis += sel
        print(f"  {g} : {len(sel)} villages (demandés {n})")
    manque = sum(QUOTAS.values()) - len(choisis)
    if manque > 0:                                  # compléter si une classe est trop rare
        sel = []; tirer(manque, ok, sel); choisis += sel
    remplacants = []
    sp_pris.clear()                                 # remplaçants : une sous-préfecture peut se répéter
    tirer(N_REMPLACANTS, ok, remplacants)

    os.makedirs(os.path.join(OUT, "sources"), exist_ok=True)
    choisis.sort(key=lambda c: -c["lat"])
    for k, c in enumerate(choisis, 1):
        c["code"] = f"MAR{k:02d}"; c["statut"] = "titulaire"
    for k, c in enumerate(sorted(remplacants, key=lambda c: -c["lat"]), 1):
        c["code"] = f"MAR-R{k}"; c["statut"] = "remplaçant"
    champs = ["code", "statut", "nom", "sous_prefecture", "lat", "lon", "classe", "lst_manquante", "osm_id"]
    with open(os.path.join(OUT, "villages_observatoire.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=champs, delimiter=";", extrasaction="ignore")
        w.writeheader(); w.writerows(choisis + remplacants)

    modele = open(os.path.join(ICI, "..", "docs", "releves_terrain_modele.csv"), encoding="utf-8-sig").read().strip().split(";")
    with open(os.path.join(OUT, "releves_2027.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=modele, delimiter=";")
        w.writeheader()
        for c in choisis:
            for m in MOIS_2027:
                w.writerow({**dict.fromkeys(modele, ""), "village": c["nom"], "code_village": c["code"],
                            "lat": c["lat"], "lon": c["lon"], "mois": m})

    # Extraits des sources (traçabilité)
    with open(os.path.join(OUT, "sources", "osm_villages_marahoue.json"), "w", encoding="utf-8") as f:
        json.dump({"source": "© contributeurs OpenStreetMap (ODbL), Overpass, place=village",
                   "candidats": cands}, f, ensure_ascii=False, indent=0)
    with open(os.path.join(OUT, "sources", "sous_prefectures_marahoue.geojson"), "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "source": "geoBoundaries CIV ADM3 (2021)",
                   "features": [{"type": "Feature", "properties": {"nom": n}, "geometry": g} for n, _, g in sp]},
                  f, ensure_ascii=False)
    carte(G, ann, zone, risque, sp, cands, choisis, remplacants)
    print("\nVillages retenus :")
    for c in choisis + remplacants:
        print(f"  {c['code']:7} {c['nom'][:24]:24} {c['sous_prefecture'][:18]:18} {c['classe']:9} LST manquante {c['lst_manquante']:.0%}")


def carte(G, ann, zone, risque, sp, cands, choisis, remplacants):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    pal = ["#1a9850", "#a6d96a", "#fee08b", "#fc8d59", "#d73027"]
    r = np.where(zone & (risque > 0), risque, np.nan)
    ext = [G["x0"], G["x0"] + G["w"] * G["res"], G["y1"] - G["h"] * G["res"], G["y1"]]
    fig, ax = plt.subplots(figsize=(8.27, 10.5))
    ax.imshow(r, extent=ext, cmap=ListedColormap(pal), vmin=0.5, vmax=5.5, interpolation="nearest", alpha=.85)
    for n, a, _ in sp:
        for x in a:
            ax.plot(x[:, 0], x[:, 1], color="#777", lw=.5)
    for x in ann:
        ax.plot(x[:, 0], x[:, 1], color="#111", lw=1.4)
    ax.scatter([c["lon"] for c in cands], [c["lat"] for c in cands], s=3, c="#555", label="autres villages OSM")
    for c in choisis:
        ax.scatter(c["lon"], c["lat"], s=60, c="#08306b", edgecolor="white", zorder=5)
        ax.annotate(f"{c['code']} {c['nom']}", (c["lon"], c["lat"]), xytext=(5, 4), textcoords="offset points",
                    fontsize=7.5, weight="bold", bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=.8))
    for c in remplacants:
        ax.scatter(c["lon"], c["lat"], s=40, facecolor="white", edgecolor="#08306b", lw=1.5, zorder=5)
        ax.annotate(c["code"] + " " + c["nom"], (c["lon"], c["lat"]), xytext=(6, -12), textcoords="offset points",
                    fontsize=6.5, color="#08306b")
    for n, x, y in S.VILLES:
        ax.scatter(x, y, marker="s", s=14, c="#333", zorder=4)
        ax.annotate(n, (x, y), xytext=(0, -11), textcoords="offset points", fontsize=7.5,
                    style="italic", color="#333", ha="center")
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    h = [Patch(color=pal[k], label=f"Risque {NOM_RISQUE[k + 1].lower()}") for k in range(5)]
    h += [Line2D([], [], marker="o", ls="", mfc="#08306b", mec="white", ms=8, label="Village de l'observatoire"),
          Line2D([], [], marker="o", ls="", mfc="white", mec="#08306b", ms=7, label="Remplaçant"),
          Line2D([], [], color="#777", lw=.8, label="Sous-préfectures (geoBoundaries 2021)")]
    ax.legend(handles=h, loc="upper right", fontsize=7.5, framealpha=.95)
    ax.set_title("Observatoire de la sécheresse agricole — villages 2027\n"
                 f"Marahoué · fond : risque SMADI (anomalie z ≥ {S.CFG['droughtThresholdZ']:.1f}, 2000–2026)", fontsize=11)
    ax.set_xlabel("Longitude (°)"); ax.set_ylabel("Latitude (°)")
    ax.set_aspect(1 / np.cos(np.radians(7.1)))
    ax.set_xlim(ext[0] - 0.05, ext[1] + 0.25); ax.set_ylim(ext[2] - 0.05, ext[3] + 0.05)
    ax.text(0.99, 0.005, "Villages : © contributeurs OpenStreetMap (ODbL). Limites : geoBoundaries.",
            transform=ax.transAxes, ha="right", va="bottom", fontsize=6, color="#444")
    fig.tight_layout()
    for ext_ in ("png", "pdf"):
        fig.savefig(os.path.join(OUT, f"carte_villages_observatoire.{ext_}"), dpi=200, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        main()
