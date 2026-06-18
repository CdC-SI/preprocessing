# pipeline_multietape_modulaire.py — Référence CLI
Script unifié qui remplace pipeline_multietape.py (stage 1) et export_table_docling.py (stage 2).

Une seule conversion Docling produit tous les formats demandés — pas de double passage EasyOCR.

## Commande complète (tous les paramètres)

uv run python stage1_modulaire/pipeline_multietape_modulaire.py \
  --input     data/input_files/MonDoc.pdf \
  --output-dir ./data/output_files/MonDoc \
  --formats   json md txt doctags csv html \
  --lang      fr en \
  --threads   4 \
  --device    cuda \
  --dotenv    .env.test

## Référence des paramètres
| Paramètre | Alias | Par défaut | Description |
| ------- | ----- | -------- | --------- |
| --input | -i | (voir note) | Chemin vers le PDF à traiter.|
| --output-dir | -o | data/output_files/<nom_doc>/ | Dossier de sortie. Créé automatiquement.|
| --formats | -f | tous | Un ou plusieurs formats parmi json md txt doctags csv html.|
| --lang | -l | fr | Code(s) de langue EasyOCR. Plusieurs valeurs possibles : fr en ar.|
| --threads | -t | 4 | Nombre de threads CPU alloués à Docling.|
| --device |   | cuda (Nvidia) | Accélérateur matériel : cuda ou cpu.|
| --no-ocr |   | désactivé | Désactive l'OCR (EasyOCR). Utile pour les PDFs avec texte natif.|
| --no-tables |   | désactivé | Désactive la détection de structure des tableaux. Ignore csv et html.|
| --dotenv |   | (aucun sélectionné) | 	Fichier .env à charger avant la résolution de DOC_NAME. Ignoré si --input est fourni.|

*Note: --input absent : le script lit DOC_NAME depuis l'environnement et résout automatiquement data/input_files/<DOC_NAME>.pdf. Utiliser --dotenv .env.test pour charger ce fichier depuis un .env.*

## Formats de sortie :
| Format | Type | Fichier produit |
| ------- | ----- | -------- |
| json | Texte | <doc>.json — structure complète Docling |
| md | Texte | <doc>.md — Markdown avec tableaux intégrés |
| txt | Texte | <doc>.txt — texte brut sans syntaxe Markdown |
| doctags | Texte | <doc>.doctags — format DocTags Docling |
| csv | Texte | tables/<doc>-table-N.csv par tableau détecté |
| html | Texte | tables/<doc>-table-N.html par tableau détecté |

## Ancien workflow .env.test (DOC_NAME défini dans le fichier)
uv run python stage1_modulaire/pipeline_multietape_modulaire.py --dotenv .env.test

## Extraction rapide, seulement Markdown + JSON
uv run python stage1_modulaire/pipeline_multietape_modulaire.py \
  --input data/input_files/MonDoc.pdf --formats md json

## PDF natif (pas de scan) — désactiver l'OCR pour aller plus vite
uv run python stage1_modulaire/pipeline_multietape_modulaire.py \
  --input data/input_files/MonDoc.pdf --no-ocr

## Seulement les tables, sortie personnalisée
uv run python stage1_modulaire/pipeline_multietape_modulaire.py \
  --input data/input_files/MonDoc.pdf \
  --formats csv html \
  --output-dir ./exports/tables

## Document multilingue (français + anglais)
uv run python stage1_modulaire/pipeline_multietape_modulaire.py \
  --input data/input_files/MonDoc.pdf --lang fr en

# Script opencv_checker_modulaire.py — Validation visuelle des DocTags
Superpose les bounding boxes Docling sur chaque page du PDF et exporte les images PNG. Outil de validation uniquement — les PNG ne sont pas utilisés par les étapes suivantes.

## Chemins explicites
uv run python stage1_modulaire/opencv_checker_modulaire.py \
  --pdf      data/input_files/MonDoc.pdf \
  --doctags  data/output_files/MonDoc/MonDoc.doctags \
  --output-dir data/output_files/MonDoc/opencv_validation

## Ancien workflow .env.test
uv run python stage1_modulaire/opencv_checker_modulaire.py --dotenv .env.test

## DPI réduit pour aller plus vite (validation rapide)
uv run python stage1_modulaire/opencv_checker_modulaire.py \
  --pdf data/input_files/MonDoc.pdf --dpi 150

## Paramètres :
| Paramètre | Alias | Défaut | Description |
| ------- | ----- | -------- | -------------|
| --pdf | -p | (voir note) | Chemin vers le PDF source. |
| --doctags | -d | data/output_files/<nom_pdf>/<nom_pdf>.doctags | Fichier .doctags produit par pipeline_multietape_modulaire.py. |
| --output-dir | -o | data/output_files/<nom_pdf>/opencv_validation/ | Dossier de sortie pour les PNG |
| --dpi |  | 300 | Résolution de rendu. Réduire pour accélérer (150), augmenter pour plus de précision (600). |
| --dotenv |  | (aucun) | Fichier .env à charger pour résoudre DOC_NAME. Ignoré si --pdf est fourni. |

*Note : --pdf absent : lit DOC_NAME depuis l'environnement et résout data/input_files/<DOC_NAME>.pdf.*

**Sorties :**
data/output_files/MonDoc/opencv_validation/
- page_1_doctags_boxes.png
- page_2_doctags_boxes.png
- ...

**Exit codes**
| Code | Explication | 
| ------- | ----- |
| 0 | Toutes les pages sont exportées avec succès |
| 1 | Une ou plusieurs pages en erreur (voir logs) |

# Script csv_to_jsonlines_modulaire.py — Conversion tables CSV -> JSONL

Convertit les fichiers CSV extraits par pipeline_multietape_modulaire.py en fichiers JSONL. Un .jsonl par CSV, une ligne JSON par ligne du tableau.

## Dossier explicite (mode Tekton)
uv run python stage1_modulaire/csv_to_jsonlines_modulaire.py \
  --input-dir data/output_files/MonDoc/tables

## JSONL dans un dossier séparé
uv run python stage1_modulaire/csv_to_jsonlines_modulaire.py \
  --input-dir  data/output_files/MonDoc/tables \
  --output-dir data/output_files/MonDoc/jsonlines

## Ancien workflow .env.test
uv run python stage1_modulaire/csv_to_jsonlines_modulaire.py --dotenv .env.test

## Paramètres :
| Paramètre | Alias | Défaut | Description |
| ------- | ----- | -------- | -------------|
| --input-dir | -i | (voir note) | Dossier contenant les fichiers *.csv à convertir. |
| --output-dir | -o | même dossier que --input-dir | Dossier de sortie pour les .jsonl. |
| --dotenv |  | (aucun) | Fichier .env à charger pour résoudre DOC_NAME. Ignoré si --input-dir est fourni. |

*Note : --input-dir absent : résout automatiquement data/output_files/<DOC_NAME>/tables/ depuis la variable DOC_NAME.*

**Sorties :**
data/output_files/MonDoc/tables/        <- par défaut, à côté des CSV
- MonDoc-table-1.csv
- MonDoc-table-1.jsonl                  <- produit
- MonDoc-table-2.csv
- MonDoc-table-2.jsonl                  <- produit
- ...

Chaque ligne du .jsonl correspond à une ligne du tableau :
- {"Colonne A": "valeur 1", "Colonne B": "42", "Colonne C": null}
- {"Colonne A": "valeur 2", "Colonne B": "17", "Colonne C": "texte"}

*Note : toutes les valeurs non-nulles sont converties en str. Les NaN pandas deviennent null JSON.*

**Détection automatique du header**
Docling peut produire des colonnes numériques (0, 1, 2…) quand le vrai header se trouve dans la première ligne de données. Le script détecte ce cas automatiquement et repositionne le header sans intervention manuelle. Les colonnes dupliquées reçoivent un suffixe numérique (col, col_2, col_3…).

**Exite codes**
| Code | Explication | 
| ------- | ----- |
| 0 | Tous les CSV convertis avec succès (tableaux vides ignorés sans erreur) |
| 1 | Au moins un CSV a provoqué une erreur (voir logs pour le traceback) |

Le log final détaille les trois catégories:</br>
Terminé — 3 converti(s), 1 ignoré(s) (vide), 0 erreur(s).

# Script reordered_doctags.py — Réordonnancement des blocs DocTags par position

Corrige l'ordre des blocs extraits par Docling dans un fichier .doctags. Docling peut mal ordonner des blocs ayant des coordonnées y0 similaires ou absentes. Ce script les retrie par position verticale (y0) puis horizontale (x0), page par page, avant les étapes VLM aval.

Place dans le pipeline : après pipeline_multietape_modulaire.py (qui produit le .doctags), avant les scripts de parsing VLM.

## Sortie auto : ajoute le suffixe _reordered au nom du fichier
uv run python stage1_modulaire/reordered_doctags.py \
  --input data/output_files/MonDoc/MonDoc.doctags

## Sortie personnalisée
uv run python stage1_modulaire/reordered_doctags.py \
  --input  data/output_files/MonDoc/MonDoc.doctags \
  --output data/output_files/MonDoc/MonDoc_reordered.doctags

## Ancien workflow .env.test
uv run python stage1_modulaire/reordered_doctags.py --dotenv .env.test

## Paramètres :
| Paramètre | Alias | Défaut | Description |
| ------- | ----- | -------- | -------------|
| --input | -i | (voir note) | Fichier .doctags source à réordonner. |
| --output | -o | dossier>/<stem>_reordered.doctags | Fichier .doctags de sortie. |
| --dotenv |  | (aucun) | Fichier .env à charger pour résoudre DOC_NAME. Ignoré si --input est fourni. |

*Note : --input absent : résout automatiquement data/output_files/<DOC_NAME>/<DOC_NAME>.doctags.*

**Sorties :**
data/output_files/MonDoc/
- MonDoc.doctags               <- entrée (produit par pipeline_multietape_modulaire.py)
- MonDoc_reordered.doctags     <- sortie

## Logique de tri
Le tri s'applique page par page (séparées par <page_footer> ou <page_break>) :

| Cas | Comprortement | 
| ------- | ----- |
| Bloc avec y0 | Trié par y0 croissant, puis x0 croissant pour les égalités |
| Bloc sans y0 | Conservé en tête de page dans son ordre d'origine |
| <ordered_list> | Traité comme un seul bloc — ses items ne sont pas réordonnés individuellement |
| <unordered_list> | Items extraits et triés individuellement, réenveloppés dans <unordered_list> après tri |

## Architecture interne
parse_blocks(content)
- _collect_until()        # accumule les lignes jusqu'au tag fermant
- _parse_ordered_list()   # ordered_list → 1 Block
- _parse_unordered_list() # unordered_list → N Blocks (list_item)
</br>         
split_pages(blocks)             # sépare par page_footer / page_break
</br>         
sort_page(blocks)               # tri stable y0 → x0 → index d'origine
</br>        
render_blocks(blocks)           # sérialise + réenveloppe les unordered_list
</br>        
reorder_doctags(input, output)  # I/O fichier

Toutes les fonctions intermédiaires sont pures (sans effet de bord) et importables indépendamment.

**Exite codes**
| Code | Explication | 
| ------- | ----- |
| 0 | Fichier réordonné avec succès |
| 1 | Erreur de traitement (fichier malformé, encodage, etc.) — traceback dans les logs |