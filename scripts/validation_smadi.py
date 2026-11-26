"""
Validation du SMADI de la Marahoué contre des données indépendantes (2005 → 2026).

Références (calculées pour l'application des semis, dossier marahoue_semis) :
  - pluie TAMSAT v3.2 journalière, mailles de 0,25° → indice de pluie standardisé
    sur 1 et 3 mois (SPI-1, SPI-3, par rangs) ;
  - beta TAMSAT-ALERT (disponibilité en eau du sol sur 1 m, modèle JULES forcé par TAMSAT),
    anomalie standardisée par mois de l'année ;
  - EVI MODIS MOD13Q1 août–octobre de la végétation non arborée (2005–2024), anomalie
    sans tendance. Attention : le SMADI contient lui-même le NDVI MODIS du mois précédent ;
    ce test n'est donc qu'à moitié indépendant.

Le SMADI (pixels de 1 km) est moyenné sur les mailles de 0,25° de la région.
Le SMADI du mois m utilise l'humidité du sol de m et la LST et le NDVI de m − 1.

Tests
  1. Corrélations de rang (Spearman) mois par mois entre l'anomalie z du SMADI et SPI / beta,
     en saison culturale (avril–octobre) et en saison sèche (novembre–mars).
  2. Détection des mois secs (SPI-3 ≤ −1, puis beta ≤ −1) selon le seuil z du SMADI :
     probabilité de détection, taux de fausses alertes, score de Heidke.
  3. Années : SMADI z moyen juin–octobre contre pluie juin–septembre, beta et EVI août–octobre
     (moyenne de la région, 20 ans, test par permutation).

Lancer : python validation_smadi.py   (variable REF_DIR = dossier marahoue_semis)
Sortie : ../validation/validation_smadi.json
"""
import glob
import json
import os
import sys
from statistics import NormalDist

import numpy as np

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import smadi_marahoue as S

REF = os.environ.get("REF_DIR", os.path.join(ICI, "..", "..", "marahoue_semis"))
SORTIE = os.path.join(ICI, "..", "validation", "validation_smadi.json")
rng = np.random.default_rng(0)
ND = NormalDist()


# ---------------------------------------------------------------- outils
def rangs(x):
    r = np.empty(len(x))
    r[np.argsort(x, kind="mergesort")] = np.arange(len(x))
    return r


def spearman(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    ok = ~np.isnan(a) & ~np.isnan(b)
    if ok.sum() < 8:
        return np.nan, int(ok.sum())
    return float(np.corrcoef(rangs(a[ok]), rangs(b[ok]))[0, 1]), int(ok.sum())


def p_perm(a, b, n=5000):
    a, b = np.asarray(a, float), np.asarray(b, float)
    ok = ~np.isnan(a) & ~np.isnan(b)
    a, b = a[ok], b[ok]
    r0 = spearman(a, b)[0]
    c = sum(abs(spearman(rng.permutation(a), b)[0]) >= abs(r0) for _ in range(n))
    return (c + 1) / (n + 1)


def z_rang(x, cal):
    """Standardisation par rangs, par mois de l'année (comme un SPI empirique). x : (T, ...)."""
    out = np.full(x.shape, np.nan)
    for m in range(1, 13):
        k = np.where(cal == m)[0]
        sub = x[k]
        for idx in np.ndindex(sub.shape[1:]):
            v = sub[(slice(None),) + idx]
            ok = ~np.isnan(v)
            n = ok.sum()
            if n < 8:
                continue
            p = (rangs(v[ok]) + 0.5) / n
            col = np.full(len(v), np.nan)
            col[ok] = [ND.inv_cdf(q) for q in p]
            out[(k,) + idx] = col
    return out


def mensuel(dates, x):
    """Somme et moyenne mensuelles d'une série journalière (T, 6, 6) → dict mois → tableaux."""
    cles = np.array([int(d[:4]) * 12 + int(d[5:7]) - 1 for d in dates])
    mois = np.unique(cles)
    somme = np.stack([np.nansum(x[cles == m], 0) for m in mois])
    moy = np.stack([np.nanmean(x[cles == m], 0) for m in mois])
    n = np.array([(cles == m).sum() for m in mois])
    return mois, somme, moy, n


def scores(prevu, obs):
    prevu, obs = np.asarray(prevu, bool), np.asarray(obs, bool)
    a = int((prevu & obs).sum()); b = int((prevu & ~obs).sum())
    c = int((~prevu & obs).sum()); d = int((~prevu & ~obs).sum())
    n = a + b + c + d
    pod = a / (a + c) if a + c else np.nan
    far = b / (a + b) if a + b else np.nan
    attendu = ((a + b) * (a + c) + (c + d) * (b + d)) / n
    hss = ((a + d) - attendu) / (n - attendu) if n != attendu else np.nan
    return {"detection": r3(pod), "fausses_alertes": r3(far), "heidke": r3(hss),
            "biais": r3((a + b) / (a + c)) if a + c else None, "n_obs_secs": a + c, "n": n}


def r3(v):
    return None if v is None or not np.isfinite(v) else round(float(v), 3)


def anomalie_sans_tendance(x):
    """x : (années, ...) → écart à la droite de tendance, par maille."""
    t = np.arange(x.shape[0], dtype=float)
    out = np.full(x.shape, np.nan)
    for idx in np.ndindex(x.shape[1:]):
        v = x[(slice(None),) + idx]
        ok = ~np.isnan(v)
        if ok.sum() < 8:
            continue
        a, b = np.polyfit(t[ok], v[ok], 1)
        out[(slice(None),) + idx] = v - (a * t + b)
    return out


def lire_evi():
    ans = np.arange(2005, 2025)
    out = np.full((len(ans), 6, 6), np.nan)          # EVI août–octobre
    for ligne in open(os.path.join(REF, "validation", "ndvi_saison_compact.txt"), encoding="utf-8"):
        if ligne.startswith("#") or ":" not in ligne:
            continue
        cle, val = ligne.strip().split(":", 1)
        i, j = int(cle[0]), int(cle[1])
        for a, bloc in enumerate(val.split(";")):
            parts = bloc.split(" ")
            if len(parts) == 4 and parts[1].strip():
                out[a, i, j] = int(parts[1]) / 1000
    return ans, out


# ---------------------------------------------------------------- données
def smadi_par_maille(lat, lon):
    geom, ann = S.charger_zone()
    G = S.grille(ann)
    zone = S.masque_zone(G, ann)
    f = sorted(glob.glob(os.path.join(ICI, "cache_smadi", "entrees_era5_*.npz")))
    Z = [np.load(x) for x in f]
    sm, lst, lsta, ndvi = (np.concatenate([z[v] for z in Z]) for v in ("sm", "lst", "lsta", "ndvi"))
    mois = [int(i) for i in np.concatenate([z["mois"] for z in Z])]
    lst, _ = S.combler_lst(lst, lsta, mois, zone)
    smadi = S.calcul_smadi(sm, lst, ndvi)
    z = S.anomalies(smadi, mois)

    plat = G["y1"] - (np.arange(G["h"]) + 0.5) * G["res"]
    plon = G["x0"] + (np.arange(G["w"]) + 0.5) * G["res"]
    T = len(mois)
    cz = np.full((T, 6, 6), np.nan)
    cs = np.full((T, 6, 6), np.nan)
    npix = np.zeros((6, 6), int)
    for i, la in enumerate(lat):
        for j, lo in enumerate(lon):
            m = zone & (np.abs(plat - la) <= 0.125)[:, None] & (np.abs(plon - lo) <= 0.125)[None, :]
            npix[i, j] = m.sum()
            if npix[i, j] < 150:                # mailles trop peu couvertes par la région
                continue
            v = z[:, m]; s = smadi[:, m]
            ok = np.sum(~np.isnan(v), 1) >= 0.3 * npix[i, j]
            with np.errstate(all="ignore"):
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    cz[ok, i, j] = np.nanmean(v[ok], 1)
                    cs[ok, i, j] = np.nanmean(s[ok], 1)
    return np.array(mois), cz, cs, npix


def main():
    b = np.load(os.path.join(REF, "donnees_bilan_marahoue.npz"))
    lat, lon = b["lat"], b["lon"]
    mp, pluie, _, njours = mensuel(b["dates"], b["pluie"])
    h = np.load(os.path.join(REF, "historique_beta_marahoue.npz"))
    mb, _, beta, _ = mensuel(h["dates"], h["beta"])

    mois_s, cz, cs, npix = smadi_par_maille(lat, lon)
    print(f"Mailles retenues : {(npix >= 150).sum()} sur 36 ; pixels par maille : {npix.max()} max")

    # Période commune (mois complets)
    complet = njours >= 28
    communs = sorted(set(mois_s) & set(mp[complet]) & set(mb))
    communs = [m for m in communs if m - 2 in set(mp)]
    k_s = [int(np.where(mois_s == m)[0][0]) for m in communs]
    idx_p = {m: k for k, m in enumerate(mp)}
    k_b = [int(np.where(mb == m)[0][0]) for m in communs]
    cal = np.array([m % 12 + 1 for m in communs])

    z_s = cz[k_s]
    p1 = pluie[[idx_p[m] for m in communs]]
    p3 = np.stack([pluie[idx_p[m]] + pluie[idx_p[m - 1]] + pluie[idx_p[m - 2]] for m in communs])
    spi1, spi3 = z_rang(p1, cal), z_rang(p3, cal)
    zb = z_rang(beta[k_b], cal)
    print(f"Période commune : {S.lbl(communs[0])} → {S.lbl(communs[-1])} ({len(communs)} mois)")

    res = {"periode": [S.lbl(communs[0]), S.lbl(communs[-1])], "n_mois": len(communs),
           "mailles": int((npix >= 150).sum())}

    # 1. Corrélations mois par mois
    saisons = {"culture_avr_oct": (cal >= 4) & (cal <= 10), "seche_nov_mars": (cal <= 3) | (cal >= 11),
               "toute_annee": np.ones(len(cal), bool)}
    zone_m = lambda x: np.array([np.nanmean(v) if np.isfinite(v).any() else np.nan for v in x])
    corr = {}
    for sn, s in saisons.items():
        corr[sn] = {}
        for nom, ref in (("SPI-1", spi1), ("SPI-3", spi3), ("beta", zb)):
            r_m, n_m = spearman(z_s[s].ravel(), ref[s].ravel())
            r_z, n_z = spearman(zone_m(z_s[s]), zone_m(ref[s]))
            corr[sn][nom] = {"r_maille_mois": r3(r_m), "n": n_m, "r_region": r3(r_z), "n_region": n_z}
    res["correlations"] = corr
    # Par mois de l'année (région)
    res["correlation_par_mois_SPI3"] = {
        str(m): r3(spearman(zone_m(z_s[cal == m]), zone_m(spi3[cal == m]))[0]) for m in range(1, 13)}
    res["correlation_par_mois_beta"] = {
        str(m): r3(spearman(zone_m(z_s[cal == m]), zone_m(zb[cal == m]))[0]) for m in range(1, 13)}

    # 2. Détection des mois secs (saison culturale, maille × mois)
    s = saisons["culture_avr_oct"]
    ok = s[:, None, None] & np.isfinite(z_s) & np.isfinite(spi3) & np.isfinite(zb)
    det = {}
    for nom_obs, obs in (("SPI-3 <= -1", spi3 <= -1), ("beta <= -1", zb <= -1)):
        det[nom_obs] = {f"z >= {t}": scores(z_s[ok] >= t, obs[ok]) for t in (0.5, 1.0, 1.5, 2.0)}
        cs_c = cs[k_s]
        det[nom_obs]["SMADI >= 1 (seuil fixe)"] = scores(cs_c[ok] >= 1.0, obs[ok])
    res["detection_saison_culturale"] = det

    # 3. Années
    ans = np.arange(2005, 2025)
    def moy_an(x, m0, m1):
        out = []
        for a in ans:
            k = [n for n, m in enumerate(communs) if m // 12 == a and m0 <= m % 12 + 1 <= m1]
            out.append(np.nanmean(x[k]) if k else np.nan)
        return np.array(out)
    a_smadi = moy_an(zone_m(z_s), 6, 10)
    a_pluie = moy_an(zone_m(spi1), 6, 9)
    a_beta = moy_an(zone_m(zb), 8, 10)
    _, evi = lire_evi()
    a_evi = np.nanmean(anomalie_sans_tendance(evi).reshape(len(ans), -1), 1)
    annees = {}
    for nom, ref in (("pluie juin–sept.", a_pluie), ("beta août–oct.", a_beta), ("EVI août–oct.", a_evi)):
        r, n = spearman(a_smadi, ref)
        annees[nom] = {"r": r3(r), "p": r3(p_perm(a_smadi, ref)), "n": n}
    res["annees_smadi_juin_oct"] = annees
    ordre = np.argsort(-a_smadi)
    res["annees_les_plus_seches_smadi"] = [int(ans[k]) for k in ordre[:5]]
    res["annees_les_plus_seches_pluie"] = [int(ans[k]) for k in np.argsort(a_pluie)[:5]]
    res["series_annuelles"] = {"annees": ans.tolist(), "smadi_z": [r3(v) for v in a_smadi],
                               "spi_juin_sept": [r3(v) for v in a_pluie], "beta": [r3(v) for v in a_beta],
                               "evi_anomalie": [r3(v) for v in a_evi]}

    os.makedirs(os.path.dirname(SORTIE), exist_ok=True)
    with open(SORTIE, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "series_annuelles"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
