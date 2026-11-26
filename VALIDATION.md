# Validation du SMADI — Marahoué (novembre 2026)

Script : `scripts/validation_smadi.py` · résultats : `validation/validation_smadi.json`.

## Données de comparaison
Indépendantes des entrées du SMADI (ERA5-Land, MODIS LST et NDVI) :

- **Pluie TAMSAT v3.2** journalière → indice de pluie standardisé sur 1 et 3 mois
  (SPI-1, SPI-3, calculés par rangs, par mois de l'année).
- **beta TAMSAT-ALERT** : disponibilité en eau du sol sur 1 m (modèle JULES forcé par la pluie
  TAMSAT), anomalie standardisée par mois de l'année.
- **EVI MODIS août–octobre** de la végétation non arborée (champs, jachères, savanes),
  2005–2024, anomalie sans tendance. Test **à moitié indépendant** : le SMADI contient le NDVI
  MODIS du mois précédent.

Le SMADI (pixels de 1 km) est moyenné sur les 14 mailles de 0,25° bien couvertes par la région.
Période commune : mars 2005 → octobre 2026 (257 mois). Corrélations de Spearman ; un SMADI
élevé = sec, donc une **corrélation négative** est attendue.

## 1. Le SMADI suit-il la pluie et l'eau du sol, mois par mois ?

| Anomalie z du SMADI contre | Saison culturale (avr.–oct.) | Saison sèche (nov.–mars) | Toute l'année |
|---|---|---|---|
| SPI-1 (pluie du mois) | −0,30 / −0,40 | −0,29 / −0,42 | −0,30 / −0,40 |
| SPI-3 (pluie sur 3 mois) | −0,34 / −0,41 | −0,48 / −0,58 | −0,40 / −0,48 |
| beta TAMSAT-ALERT | −0,54 / −0,62 | −0,59 / −0,73 | −0,56 / −0,66 |

(maille × mois, n ≈ 2 100 / moyenne de la région, n ≈ 150)

- Le signe est toujours le bon et les liens sont nets, surtout avec l'eau du sol (beta).
- **Selon le mois** (région, contre SPI-3 / beta) : très bon en **septembre–octobre**
  (−0,79 / −0,78 ; −0,69 / −0,90) et en saison sèche ; **faible d'avril à juin**
  (SPI-3 : −0,08 à −0,27). En début de saison, le SMADI rend mal les faux départs des pluies :
  il ne faut pas s'en servir seul pour décider des semis (voir la page des fenêtres de semis).

## 2. Détecte-t-il les mois secs ? Choix du seuil
Saison culturale, maille × mois. Mois sec observé = SPI-3 ≤ −1 ou beta ≤ −1 (≈ 13 % des mois).

| Seuil du SMADI | Détection (beta) | Fausses alertes (beta) | Heidke (beta) | Heidke (SPI-3) | Biais |
|---|---|---|---|---|---|
| z ≥ 0,5 | 61 % | 69 % | 0,29 | 0,17 | 2,0 |
| **z ≥ 1,0** | **42 %** | **58 %** | **0,33** | **0,12** | **1,0** |
| z ≥ 1,5 (actuel) | 25 % | 50 % | 0,27 | 0,05 | 0,5 |
| z ≥ 2,0 | 14 % | 36 % | 0,19 | 0,01 | 0,2 |
| SMADI ≥ 1 (seuil fixe) | 9 % | 65 % | 0,09 | 0,04 | 0,3 |

- **z ≥ 1,0** donne le meilleur score de Heidke et autant d'alertes que de mois secs observés
  (biais 1,0). **z ≥ 1,5** est plus sûr (une alerte sur deux est juste) mais manque trois mois
  secs sur quatre : il convient pour les sécheresses **sévères**, pas pour une veille.
- Le **seuil fixe SMADI ≥ 1** ne détecte presque rien d'anormal : il signale surtout la saison
  sèche normale. À éviter en Marahoué.
- Avec la pluie seule (SPI-3), les scores sont faibles : le SMADI réagit à l'état du sol et de
  la végétation, pas directement au déficit de pluie.

## 3. Les années sèches
SMADI z moyen de juin à octobre, moyenne de la région, 2005–2024 (20 ans, test par permutation) :

| Comparé à | r | p |
|---|---|---|
| Pluie juin–septembre (SPI-1 moyen) | −0,56 | 0,014 |
| beta août–octobre | −0,54 | 0,014 |
| EVI août–octobre (à moitié indépendant) | −0,47 | 0,035 |

Années les plus sèches selon le SMADI : **2015**, 2024, 2009, 2023, 2022 ; selon la pluie :
**2015**, 2005, 2013, 2024, 2009 (3 années communes sur 5, 2015 en tête des deux).

Pour comparaison, l'eau du sol 0–30 cm du bilan hydrique riz suit l'EVI avec r = 0,79 : à
l'échelle de la saison, le bilan hydrique décrit mieux la végétation que le SMADI.

## En résumé
- Le SMADI de la Marahoué **suit bien l'eau du sol** (r ≈ −0,6 à −0,7) et repère les années
  sèches connues (2015), surtout de **septembre à mars**.
- Il est **peu fiable en avril–juin**, au moment des semis.
- **Seuil conseillé pour une veille : z ≥ 1,0** ; garder z ≥ 1,5 pour les sécheresses sévères.
- Limites : références elles-mêmes issues de satellites et de modèles (pas de mesures au sol),
  mailles de 28 km, 20 saisons. Une vérification avec les observations des agents agricoles
  (rendements, dates de flétrissement) reste à faire.
