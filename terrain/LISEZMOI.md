# Villages de l'observatoire de la sécheresse — Marahoué 2027

Proposition à valider avec l'ANADER et les services agricoles (accès, relais, sécurité).

| Fichier | Contenu |
|---|---|
| `villages_observatoire.csv` | 15 villages titulaires (MAR01 à MAR15) et 5 remplaçants (MAR-R1 à MAR-R5) : sous-préfecture, coordonnées, classe d'aléa SMADI, part de LST manquante en juillet–octobre |
| `carte_villages_observatoire.png` / `.pdf` | Carte A4 pour les agents, sur fond d'aléa SMADI |
| `releves_2027.csv` | Fichier de saisie pré-rempli : une ligne par village et par mois (avril → novembre 2027, 120 lignes) |
| `sources/` | Extraits utilisés : villages OpenStreetMap candidats, sous-préfectures geoBoundaries |

Régénérer : `python scripts/choisir_villages.py <osm_villages.json> <geoBoundaries-CIV-ADM3.geojson>`
(villages : requête Overpass `node["place"~"village|town|hamlet|city"]` sur l'emprise de la
région ; sous-préfectures : geoBoundaries CIV ADM3, 2021).

## Méthode de choix
- **Candidats** : 220 villages OpenStreetMap (`place=village`) dans la Marahoué, à plus de
  3 km de la limite de la région.
- **Critères** : moins de 25 % de mois sans LST en juillet–octobre (tous les candidats y
  satisfont après comblement par Aqua) ; au plus un titulaire par sous-préfecture ; au moins
  12 km entre deux villages (≈ une maille ERA5-Land, pour des observations indépendantes).
- **Quotas par aléa SMADI** (mode anomalie, z ≥ 1,0) : 3 « faible », 6 « moyen », 6 « fort ».
- **Répartition** : à chaque tour, le village le plus éloigné de ceux déjà choisis. Résultat :
  15 sous-préfectures différentes, du nord (Zérifla, 7,68° N) au sud (Lahorefla, 6,54° N).
- **Remplaçants** : à utiliser si un titulaire est inaccessible ou si l'observateur manque ;
  garder alors le code du remplaçant dans les relevés.

## À vérifier sur place
- Noms et positions OSM : certains villages peuvent porter un autre nom localement
  (ex. « Kouame gbohoufla »), ou être décalés de quelques centaines de mètres.
- Les sous-préfectures viennent de geoBoundaries (2021) ; noter sur la fiche le département
  officiel (Bouaflé, Bonon, Gohitafla, Sinfra ou Zuénoula).
- Prévoir pour chaque village un relais (agent ANADER, chef de village, coopérative).

## Pourquoi l'aléa et pas le risque
Ni MODIS MCD12Q1 (97 % de la région en « savane ») ni ESA WorldCover (0,2 % de cultures,
plantations de cacao et d'anacarde comptées comme des arbres) ne décrivent correctement les
cultures de la Marahoué. La page n'affiche donc plus de carte de risque, et les villages sont
répartis selon l'aléa. Les relevés de terrain (parcelles visitées autour de chaque village)
serviront à construire une vulnérabilité fiable.
