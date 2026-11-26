# Sécheresse agricole (SMADI) — Marahoué (Côte d'Ivoire)

Page web (après publication) : https://vamihermann17-pixel.github.io/smadi-marahoue/

Version automatique de l'application Earth Engine **SMADI mensuel à 1 km**
(`scripts/smadi_gee_app.js`), sur le modèle de la page des fenêtres de semis
(`semis-marahoue`) : un calcul planifié sur GitHub Actions, une page statique
sur GitHub Pages, des données en cache dans le dépôt.

## Ce que montre la page
- Le **dernier mois calculé** : SMADI moyen de la région, anomalie z par rapport au même
  mois des autres années, part de la région en sécheresse, part en risque fort ou très fort.
- Une **carte à 0,01° (≈ 1,1 km)** au choix : SMADI ou anomalie z de l'un des 36 derniers
  mois, risque, aléa, fréquence, durée, intensité, tendance, vulnérabilité ; avec les
  surfaces par classe en km².
- L'**évolution mensuelle** depuis 2000 (SMADI moyen + % de la région en sécheresse),
  la climatologie mois par mois et les années les plus sèches.
- Les deux critères de l'application : « SMADI ≥ 1 » (seuil fixe) et « anomalie z ≥ 1,5 ».

## Méthode (identique à l'application GEE)
SMADI(m) = SMCI(m) × LST(m−1)/LSTmax ÷ NDVI(m−1), SMCI = (SMmax − SM)/(SMmax − SMmin),
bornes par pixel sur toute la période, NDVI ≤ 0,1 masqué (Sánchez et al., 2016).

- `scripts/smadi_marahoue.py`
  1. Earth Engine (API Python, `computePixels`) : entrées mensuelles sur la grille de la
     Marahoué — humidité du sol ERA5-Land (0–7 cm) en bilinéaire, LST de jour MODIS MOD11A2
     (moyenne mensuelle, K) et NDVI MODIS MOD13A3 du mois précédent ; vulnérabilité à
     partir de MODIS MCD12Q1 (IGBP).
     Écart avec l'application GEE : les mois où la LST Terra manque (nuages) sont comblés par
     la LST Aqua MYD11A2, dont on retire l'écart moyen Aqua − Terra du pixel (≈ 2,7 K).
     Pixels sans LST de juillet à octobre : 38–79 % → 6–28 %.
  2. Calcul local (numpy) du SMADI, des anomalies z et des produits, avec les seuils de
     `CFG` et `VULN` repris de l'application.
  3. Sorties : `docs/smadi_marahoue.json` (page) et `docs/serie_smadi_marahoue.csv`
     (série mensuelle de la région).
- Les entrées déjà téléchargées sont gardées par année dans `scripts/cache_smadi/` ; à
  chaque mise à jour, seules les années récentes sont relues (ERA5-Land provisoire
  remplacé, nouveaux mois).
- Zone : limite de la région de la Marahoué (geoBoundaries, `scripts/marahoue.geojson`).
  Pour une autre zone, remplacer ce fichier (un polygone en EPSG:4326).

## Connexion à Earth Engine (une seule fois)
Le calcul tourne sur les serveurs GitHub, qui doivent pouvoir se connecter à votre projet
Earth Engine.

### Méthode utilisée : sans clé (Workload Identity Federation)
Aucun fichier de clé ni secret : Google Cloud fait confiance au seul dépôt
`vamihermann17-pixel/smadi-marahoue`, avec des jetons valables environ une heure.

1. Console Google Cloud, projet `smadi-2026` → **Cloud Shell**, puis :
   `bash <(curl -sL https://raw.githubusercontent.com/vamihermann17-pixel/smadi-marahoue/main/scripts/config_gcp_sans_cle.sh)`
   (compte de service `smadi-github`, rôles Earth Engine, pool `github` limité au dépôt).
2. Dépôt GitHub → **Settings → Secrets and variables → Actions → Variables** : reporter
   `GCP_WIF_PROVIDER` et `GCP_SERVICE_ACCOUNT` affichés à la fin du script, et `EE_PROJECT`.

### Autre méthode : clé JSON
Avec un **compte de service** et sa clé :

1. Console Google Cloud, projet enregistré pour Earth Engine (ex. `smadi-2026`) :
   **IAM et administration → Comptes de service → Créer**. Rôles :
   « Earth Engine Resource Viewer » (ou Writer) et « Service Usage Consumer ».
2. Sur ce compte : **Clés → Ajouter une clé → JSON**. Un fichier `.json` est téléchargé.
3. Dépôt GitHub : **Settings → Secrets and variables → Actions**
   - onglet *Secrets* : `EE_SERVICE_ACCOUNT_KEY` = tout le contenu du fichier JSON ;
   - onglet *Variables* : `EE_PROJECT` = identifiant du projet (facultatif si c'est le
     projet de la clé) ; `SM_SOURCE` (facultatif, voir plus bas).
4. Ne mettez **jamais** le fichier JSON dans le dépôt.

Si vous utilisez un asset privé (par exemple TAMSAT-SM), partagez-le en lecture avec
l'adresse du compte de service.

## Publication
1. Créer un dépôt public `smadi-marahoue` et y pousser le contenu de ce dossier.
2. **Settings → Pages → Source : GitHub Actions**.
3. Ajouter le secret (ci-dessus), puis **Actions → « Mise à jour du SMADI » → Run workflow**.
   Le premier calcul télécharge 26 ans de données : compter 15 à 40 minutes.

Ensuite, le calcul repart seul le 12 de chaque mois.

## Source d'humidité du sol
Variable `SM_SOURCE` : `era5` (défaut), `fldas`, `terraclimate`, `gldas`, `smap`, ou
l'identifiant d'un asset image multibande nommé `…_AAAAMM_AAAAMM`, par exemple
`projects/smadi-2026/assets/tamsat_sm_surface_200001_202610` (préparé avec
`prepare_tamsat_sm.py`). Attention : un asset importé une fois n'est pas mis à jour seul ;
la page s'arrête alors au dernier mois de l'asset.

## Lancer sur un PC
```
python -m pip install -r requirements.txt
earthengine authenticate
cd scripts
set EE_PROJECT=smadi-2026
python smadi_marahoue.py
```
Puis ouvrir `docs/index.html` via un petit serveur (`python -m http.server -d docs`).

## Validation
Voir [VALIDATION.md](VALIDATION.md) (`scripts/validation_smadi.py`) : le SMADI suit l'eau du sol
TAMSAT-ALERT (r ≈ −0,6 à −0,7) et la pluie, surtout de septembre à mars, mal d'avril à juin ;
seuil conseillé pour une veille z ≥ 1,0 (z ≥ 1,5 = sécheresses sévères).

## Limites
Seuils de classes indicatifs (ceux du mode anomalie ont été calés sur toute la Côte
d'Ivoire) ; humidité du sol de modèle à ~9 km ; derniers mois provisoires ; pas de
validation au champ. Résultats indicatifs.

## Sources
NASA MODIS MOD13A3, MOD11A2, MCD12Q1 v061 (LP DAAC) ; ERA5-Land (Copernicus C3S/ECMWF) ;
geoBoundaries ; Google Earth Engine. Sánchez N. et al. (2016), *A new Soil Moisture
Agricultural Drought Index (SMADI) integrating MODIS and SMOS products*, Int. J. Appl.
Earth Obs. Geoinf. 51, 18–29.
