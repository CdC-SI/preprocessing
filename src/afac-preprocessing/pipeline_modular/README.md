# pipeline_multietape_modular.py — Référence CLI
Script unifié qui remplace pipeline_multietape.py (stage 1) et export_table_docling.py (stage 2).

Une seule conversion Docling produit tous les formats demandés — pas de double passage EasyOCR.

## Commande complète (tous les paramètres)

```
uv run python pipeline_modular/simple_extraction/pipeline_multietape_modulaire.py \
  --input     data/input_files/MonDoc.pdf \
  --output-dir ./data/output_files/MonDoc \
  --formats   json md txt doctags \ # pour spécifier quels fichiers doivent être générés
  --lang      fr en \
  --threads   4 \
  --device    cuda \
  --no-tables \ # Désactive la génération des tables html csv (de base activée)
  --dotenv    .env.test
```

## Référence des paramètres
| Paramètre | Alias | Par défaut | Description |
| ------- | ----- | -------- | --------- |
| --input | -i | (voir note) | Chemin vers le PDF à traiter.|
| --output-dir | -o | data/output_files/<nom_doc>/ | Dossier de sortie. Créé automatiquement.|
| --formats | -f | tous | Un ou plusieurs formats parmi json md txt doctags .|
| --lang | -l | fr | Code(s) de langue EasyOCR. Plusieurs valeurs possibles : fr en ar.|
| --threads | -t | 4 | Nombre de threads CPU alloués à Docling.|
| --device |   | cuda (Nvidia) | Accélérateur matériel : cuda ou cpu.|
| --no-ocr |   | désactivé | Désactive l'OCR (EasyOCR). Utile pour les PDFs avec texte natif.|
| --no-tables |   | désactivé | Désactive la détection de structure des tableaux. Ignore csv et html.|
| --dotenv |   | (aucun sélectionné) | 	Fichier `.env` à charger avant la résolution de `DOC_NAME`. Ignoré si `--input` est fourni.|

*Note: `--input` absent : le script lit DOC_NAME depuis l'environnement et résout automatiquement `data/input_files/<DOC_NAME>.pdf`. Utiliser `--dotenv .env.test` pour charger ce fichier depuis un `.env`.*

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
```
uv run python pipeline_modular/simple_extraction/pipeline_multietape_modulaire.py --dotenv .env.test
```

## Extraction rapide, seulement Markdown + JSON
```
uv run python pipeline_modular/simple_extraction/pipeline_multietape_modulaire.py \
  --input data/input_files/MonDoc.pdf --formats md json
```

## PDF natif (pas de scan) — désactiver l'OCR pour aller plus vite
```
uv run python pipeline_modular/simple_extraction/pipeline_multietape_modulaire.py \
  --input data/input_files/MonDoc.pdf --no-ocr
```

## Seulement les tables, sortie personnalisée
```
uv run python pipeline_modular/simple_extraction/pipeline_multietape_modulaire.py \
  --input data/input_files/MonDoc.pdf \
  --formats csv html \
  --output-dir ./exports/tables
```

## Document multilingue (français + anglais)
```
uv run python pipeline_modular/simple_extraction/pipeline_multietape_modulaire.py \
  --input data/input_files/MonDoc.pdf --lang fr en
```

# Script opencv_checker_modulaire.py — Validation visuelle des DocTags
Superpose les bounding boxes Docling sur chaque page du PDF et exporte les images PNG. Outil de validation uniquement — les PNG ne sont pas utilisés par les étapes suivantes.

## Chemins explicites
```
uv run python pipeline_modular/simple_extraction/opencv_checker_modulaire.py \
  --pdf      data/input_files/MonDoc.pdf \
  --doctags  data/output_files/MonDoc/MonDoc.doctags \
  --output-dir data/output_files/MonDoc/opencv_validation
```

## Ancien workflow .env.test
```
uv run python pipeline_modular/simple_extraction/opencv_checker_modulaire.py --dotenv .env.test
```

## DPI réduit pour aller plus vite (validation rapide)
```
uv run python pipeline_modular/simple_extraction/opencv_checker_modulaire.py \
  --pdf data/input_files/MonDoc.pdf --dpi 150
```

## Paramètres :
| Paramètre | Alias | Défaut | Description |
| ------- | ----- | -------- | -------------|
| --pdf | -p | (voir note) | Chemin vers le PDF source. |
| --doctags | -d | `data/output_files/<nom_pdf>/<nom_pdf>.doctags` | Fichier .doctags produit par `pipeline_multietape_modulaire.py`. |
| --output-dir | -o | `data/output_files/<nom_pdf>/opencv_validation`/ | Dossier de sortie pour les `PNG` |
| --dpi |  | 300 | Résolution de rendu. Réduire pour accélérer (150), augmenter pour plus de précision (600). |
| --dotenv |  | (aucun) | Fichier `.env` à charger pour résoudre DOC_NAME. Ignoré si `--pdf` est fourni. |

*Note : `--pdf absent` : lit DOC_NAME depuis l'environnement et résout `data/input_files/<DOC_NAME>.pdf`.*

**Sorties :**
`data/output_files/MonDoc/opencv_validation/`
- page_1_doctags_boxes.png
- page_2_doctags_boxes.png
- ...

**Exit codes**
| Code | Explication | 
| ------- | ----- |
| 0 | Toutes les pages sont exportées avec succès |
| 1 | Une ou plusieurs pages en erreur (voir logs) |

# Script csv_to_jsonlines_modular.py — Conversion tables CSV -> JSONL

Convertit les fichiers CSV extraits par pipeline_multietape_modulaire.py en fichiers JSONL. Un `.jsonl` par CSV, une ligne JSON par ligne du tableau.

## Dossier explicite (mode Tekton)
```
uv run python pipeline_modular/simple_extraction/csv_to_jsonlines_modulaire.py \
  --input-dir data/output_files/MonDoc/tables
```

## JSONL dans un dossier séparé
```
uv run python pipeline_modular/simple_extraction/csv_to_jsonlines_modulaire.py \
  --input-dir  data/output_files/MonDoc/tables \
  --output-dir data/output_files/MonDoc/jsonlines
```

## Ancien workflow .env.test
```
uv run python pipeline_modular/simple_extraction/csv_to_jsonlines_modulaire.py --dotenv .env.test
```

## Paramètres :
| Paramètre | Alias | Défaut | Description |
| ------- | ----- | -------- | -------------|
| --input-dir | -i | (voir note) | Dossier contenant les fichiers `*.csv` à convertir. |
| --output-dir | -o | même dossier que `--input-dir` | Dossier de sortie pour les `.jsonl`. |
| --dotenv |  | (aucun) | Fichier .env à charger pour résoudre `DOC_NAME`. Ignoré si `--input-dir` est fourni. |

*Note : `--input-dir` absent : résout automatiquement `data/output_files/<DOC_NAME>/tables/ depuis la variable DOC_NAME`.*

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

**Exit codes**
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
```
uv run python pipeline_modular/simple_extraction/reordered_doctags.py \
  --input data/output_files/MonDoc/MonDoc.doctags
```

## Sortie personnalisée
```
uv run python pipeline_modular/simple_extraction/reordered_doctags.py \
  --input  data/output_files/MonDoc/MonDoc.doctags \
  --output data/output_files/MonDoc/MonDoc_reordered.doctags
```

## Ancien workflow .env.test
```
uv run python pipeline_modular/simple_extraction/reordered_doctags.py --dotenv .env.test
```

## Paramètres :
| Paramètre | Alias | Défaut | Description |
| ------- | ----- | -------- | -------------|
| --input | -i | (voir note) | Fichier .doctags source à réordonner. |
| --output | -o | `<même dossier>/<stem>_reordered.doctags` | Fichier .doctags de sortie. |
| --dotenv |  | (aucun) | Fichier `.env` à charger pour résoudre DOC_NAME. Ignoré si `--input` est fourni. |

*Note : `--input` absent : résout automatiquement `data/output_files/<DOC_NAME>/<DOC_NAME>.doctags`.*

**Sorties :**
data/output_files/MonDoc/
- MonDoc.doctags               <- entrée (produit par pipeline_multietape_modulaire.py)
- MonDoc_reordered.doctags     <- sortie

## Logique de tri
Le tri s'applique page par page (séparées par <page_footer> ou <page_break>) :

| Cas | Comportement | 
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
└──  
split_pages(blocks)             # sépare par page_footer / page_break
└── 
sort_page(blocks)               # tri stable y0 → x0 → index d'origine
└──  
render_blocks(blocks)           # sérialise + réenveloppe les unordered_list
└──    
reorder_doctags(input, output)  # I/O fichier

Toutes les fonctions intermédiaires sont pures (sans effet de bord) et importables indépendamment.

**Exit codes**
| Code | Explication | 
| ------- | ----- |
| 0 | Fichier réordonné avec succès |
| 1 | Erreur de traitement (fichier malformé, encodage, etc.) — traceback dans les logs |

# Script load_jsonline_doctags_modular.py — Injection des tables JSONL dans les DocTags

Remplace chaque balise `<otsl>…</otsl>` d'un fichier `.doctags` par un bloc `<text>` contenant le contenu JSONL de la table correspondante (ordre d'apparition = ordre alphabétique des fichiers).

**Place dans le pipeline :** après `reordered_doctags.py` (qui produit le `_reordered.doctags`) et `csv_to_jsonlines_modulaire.py` (qui produit les `.jsonl` dans `tables/`), avant les scripts VLM.

## Chemins explicites (mode Tekton)
```
uv run python pipeline_modular/simple_extraction/load_jsonline_doctags_modulaire.py \
  --doctags    data/output_files/MonDoc/MonDoc_reordered.doctags \
  --tables-dir data/output_files/MonDoc/tables
```

## Sortie personnalisée
```
uv run python pipeline_modular/simple_extraction/load_jsonline_doctags_modulaire.py \
  --doctags    data/output_files/MonDoc/MonDoc_reordered.doctags \
  --tables-dir data/output_files/MonDoc/tables \
  --output     data/output_files/MonDoc/MonDoc_reordered_with_tables.doctags
```

## Ancien workflow .env.test
```
uv run python pipeline_modular/simple_extraction/load_jsonline_doctags_modulaire.py --dotenv .env.test
```

## Paramètres :
| Paramètre | Alias | Défaut | Description |
| ------- | ----- | -------- | -------------|
| --doctags | -d | (voir note) | Fichier `.doctags` source produit par `reordered_doctags.py`. |
| --tables-dir | -t | `<dossier parent du doctags>/tables` | Dossier contenant les `.jsonl` produits par `csv_to_jsonlines_modulaire.py`. |
| --output | -o | `<même dossier>/<stem>_with_tables.doctags` | Fichier `.doctags` enrichi en sortie. |
| --dotenv | | (aucun) | Fichier `.env` à charger pour résoudre `DOC_NAME`. Ignoré si `--doctags` est fourni. |

*Note : `--doctags` absent : résout automatiquement `data/output_files/<DOC_NAME>/<DOC_NAME>_reordered.doctags`.*

**Sorties :**
```
data/output_files/MonDoc/
├── MonDoc_reordered.doctags                  ← entrée
├── tables/
│   ├── MonDoc-table-1.jsonl                  ← entrée
│   └── MonDoc-table-2.jsonl                  ← entrée
└── MonDoc_reordered_with_tables.doctags      ← sortie
```

Chaque `<otsl>…</otsl>` est remplacé dans l'ordre d'apparition par :
```
<text>
{"Colonne A": "valeur 1", "Colonne B": "42"}
{"Colonne A": "valeur 2", "Colonne B": "17"}
</text>
```

## Cas gérés automatiquement
| Situation | Comportement |
| ------- | ----- |
| Aucun fichier `.jsonl` dans `tables/` | Passthrough — fichier copié sans modification, `exit 0` |
| Tous les fichiers `.jsonl` sont vides | Passthrough — fichier copié sans modification, `exit 0` |
| Aucune balise `<otsl>` dans le `.doctags` | Passthrough — fichier copié sans modification, `exit 0` |
| Nombre de `<otsl>` ≠ nombre de tables JSONL | Warning dans les logs, remplacement jusqu'à épuisement |

*Dans tous les cas de passthrough, le fichier de sortie est toujours écrit — l'étape suivante du pipeline reçoit toujours un fichier valide.*

**Exit codes**
| Code | Explication |
| ------- | ----- |
| 0 | Injection réussie (ou passthrough sans erreur) |
| 1 | Erreur de traitement (fichier malformé, encodage, etc.) — traceback dans les logs |

Le log final indique le nombre de remplacements effectués :
```
Terminé — 3 remplacement(s) <otsl> effectué(s).
```

# Script url_extaction_modular.py — Extraction des liens hypertextes depuis un PDF

Extrait tous les liens externes (`http`, `https`, `mailto`) d'un PDF page par page via PyMuPDF et associe à chaque lien le texte des mots dont le centre se trouve dans le rectangle du lien. Produit un fichier JSONL — une ligne par lien trouvé.

**Place dans le pipeline :** indépendant — lit uniquement le PDF source. Peut se lancer **en parallèle** de `stage1-ocr-export`, sans attendre ses sorties.

## Extraction minimale
```
uv run python pipeline_modular/simple_extraction/url_extaction_modular.py \
  --pdf data/input_files/MonDoc.pdf
```

## Sortie personnalisée
```
uv run python pipeline_modular/simple_extraction/url_extaction_modular.py \
  --pdf    data/input_files/MonDoc.pdf \
  --output data/output_files/MonDoc/hyperlinks_data_MonDoc.jsonl
```

## Ancien workflow .env.test
```
uv run python pipeline_modular/simple_extraction/url_extaction_modular.py --dotenv .env.test
```

## Paramètres :
| Paramètre | Alias | Défaut | Description |
| ------- | ----- | -------- | -------------|
| --pdf | -p | (voir note) | Chemin vers le PDF source. |
| --output | -o | `data/output_files/<nom_pdf>/hyperlinks_data_<nom_pdf>.jsonl` | Fichier JSONL de sortie. |
| --dotenv | | (aucun) | Fichier `.env` à charger pour résoudre `DOC_NAME`. Ignoré si `--pdf` est fourni. |

*Note : `--pdf` absent : résout automatiquement `data/input_files/<DOC_NAME>.pdf` depuis la variable `DOC_NAME`.*

**Sorties :**
```
data/output_files/MonDoc/
└── hyperlinks_data_MonDoc.jsonl
```

Chaque ligne du `.jsonl` correspond à un lien trouvé :
```json
{"page_number": 3, "text": "cliquez ici", "hyperlink": "https://example.com", "type": "URI", "details": {"from": [72.0, 300.5, 180.0, 315.2], "uri": "https://example.com", "kind": 2}}
```

| Champ | Description |
| ------- | ----- |
| `page_number` | Numéro de page (commence à 1) |
| `text` | Texte affiché sur le lien (extrait par position dans le rectangle) |
| `hyperlink` | URI cible du lien |
| `type` | Toujours `"URI"` |
| `details` | Dict brut PyMuPDF — `from` (coordonnées rectangle) converti en liste |

*Seuls les liens externes sont extraits. Les ancres internes et liens de navigation PDF sont ignorés.*

**Exit codes**
| Code | Explication |
| ------- | ----- |
| 0 | Extraction réussie (fichier JSONL écrit, même si aucun lien trouvé) |
| 1 | Erreur de traitement (PDF corrompu, accès fichier, etc.) — traceback dans les logs |

Le log final indique le résultat :
```
Terminé — 5 lien(s) extrait(s) -> data/output_files/MonDoc/hyperlinks_data_MonDoc.jsonl
Terminé — aucun lien externe trouvé dans MonDoc.pdf
```

# Script description_image_context_modular.py — Description des images via VLM avec contexte

Parse les balises `<picture>` d'un fichier `.doctags`, crop les zones correspondantes depuis le PDF source, construit un prompt contextualisé (N éléments textuels avant/après l'image) et appelle un VLM pour générer une description. Remplace ensuite chaque balise `<picture>` par la description produite. Si la description est désactivée, les balises `<picture>` sont supprimées proprement.

**Place dans le pipeline :** après `load_jsonline_doctags_modulaire.py` (qui produit le `_reordered_with_tables.doctags`), avant les étapes de post-traitement aval.

## Variables d'environnement requises
| Variable | Obligatoire | Description |
| ------- | ----- | -------------|
| `VLM_URL` | Oui (si `--image-description`) | Endpoint de l'API VLM (ex. `https://vlm.host/v1/chat/completions`). |
| `VLM_MODEL_NAME` | Oui (si `--image-description`) | Nom du modèle à passer dans la requête. |
| `VLM_CA_PEM` | Non | Chemin vers un certificat CA custom. Si absent, fallback `certifi`. |
| `DOC_NAME` | Si `--doctags`/`--pdf` absents | Nom du document pour la résolution automatique des chemins. |

## Chemins explicites (mode Tekton)
```
uv run python pipeline_modular/simple_extraction/description_image_context_modulaire.py \
  --doctags data/output_files/MonDoc/MonDoc_reordered_with_tables.doctags \
  --pdf     data/input_files/MonDoc.pdf \
  --image-description
```

## Désactiver la description VLM (supprime les balises `<picture>`)
```
uv run python pipeline_modular/simple_extraction/description_image_context_modulaire.py \
  --doctags data/output_files/MonDoc/MonDoc_reordered_with_tables.doctags \
  --pdf     data/input_files/MonDoc.pdf \
  --no-image-description
```

## Sorties personnalisées
```
uv run python pipeline_modular/simple_extraction/description_image_context_modulaire.py \
  --doctags    data/output_files/MonDoc/MonDoc_reordered_with_tables.doctags \
  --pdf        data/input_files/MonDoc.pdf \
  --output     data/output_files/MonDoc/MonDoc_final.doctags \
  --markdown   data/output_files/MonDoc/descriptions.md \
  --images-dir data/output_files/MonDoc/images \
  --image-description
```

## Parallélisation et tuning avancé
```
uv run python pipeline_modular/simple_extraction/description_image_context_modulaire.py \
  --doctags    data/output_files/MonDoc/MonDoc_reordered_with_tables.doctags \
  --pdf        data/input_files/MonDoc.pdf \
  --image-description \
  --workers    4 \
  --timeout    60 \
  --dpi        200 \
  --n-before   3 \
  --n-after    3 \
  --language   french \
  --log-level  DEBUG
```

## Workflow .env.test (dev local)
```
uv run python pipeline_modular/simple_extraction/description_image_context_modulaire.py --dotenv .env.test --image-description
uv run python pipeline_modular/simple_extraction/description_image_context_modulaire.py --dotenv .env.test --no-image-description
```

## Paramètres :
| Paramètre | Alias | Défaut | Description |
| ------- | ----- | -------- | -------------|
| --doctags | -d | (voir note) | Fichier `.doctags` source produit par `load_jsonline_doctags_modulaire.py`. |
| --pdf | -p | (voir note) | Fichier PDF source pour le crop des images. |
| --output | -o | `<même dossier>/<stem>_pictures.doctags` | Fichier `.doctags` enrichi en sortie. |
| --markdown | -m | `<même dossier>/<doc_name>_image_descriptions.md` | Fichier Markdown listant toutes les descriptions générées. |
| --images-dir | | `<même dossier>/used_images/` | Dossier de sortie pour les PNG cropés. Créé uniquement si des `<picture>` sont trouvées. |
| --doc-name | | déduit de `DOC_NAME` ou du nom du `--doctags` | Nom du document — utilisé dans les logs et le Markdown. |
| --image-description / --no-image-description | | `False` (désactivé) | Active ou désactive la description VLM. |
| --workers | -w | `1` | Threads VLM parallèles. Garder à 1 si l'API a un rate-limit. |
| --timeout | | `120` | Timeout en secondes pour chaque appel VLM. |
| --dpi | | `150` | Résolution DPI pour le crop des images PDF. |
| --n-before | | `5` | Nombre d'éléments textuels avant l'image inclus dans le contexte VLM. |
| --n-after | | `5` | Nombre d'éléments textuels après l'image inclus dans le contexte VLM. |
| --norm | | `500` | Facteur de normalisation des coordonnées DocTags. |
| --language | | `french` | Langue de la réponse VLM. |
| --log-level | | `INFO` | Niveau de log : `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| --dotenv | | (aucun) | Fichier `.env` à charger (`VLM_URL`, `VLM_CA_PEM`, `VLM_MODEL_NAME`, `DOC_NAME`). Toujours chargé pour la config VLM ; si absent, les variables sont lues depuis l'environnement. |

*Note : `--doctags` absent : résout automatiquement `data/output_files/<DOC_NAME>/<DOC_NAME>_reordered_with_tables.doctags`. `--pdf` absent : résout `data/input_files/<DOC_NAME>.pdf`.*

**Sorties :**
```
data/output_files/MonDoc/
├── MonDoc_reordered_with_tables.doctags              ← entrée
├── MonDoc_reordered_with_tables_pictures.doctags     ← sortie (balises <picture> remplacées)
├── MonDoc_image_descriptions.md                      ← rapport Markdown des descriptions
└── used_images/                                      ← créé uniquement si <picture> trouvées
    ├── MonDoc_page1_x12_y34_x56_y78.png             ← PNG cropés (nommés par coordonnées doctags)
    └── ...
```

## Comportement selon le switch et le contenu

| Situation | Comportement |
| ------- | ----- |
| Aucune balise `<picture>` dans le `.doctags` | Passthrough — doctags copié sans modification, Markdown vide, dossier `used_images/` non créé, `exit 0` |
| `--no-image-description` | Balises `<picture>` supprimées, Markdown vide, PNG exportés, `exit 0` |
| `VLM_URL` ou `VLM_MODEL_NAME` non défini avec `--image-description` | Erreur immédiate, `exit 1` |
| VLM non joignable (`check_vlm_connection` échoue) | Erreur immédiate avant tout appel image, `exit 1` |
| `--image-description` — VLM répond correctement | Balises `<picture>` remplacées par `<text>description</text>` (ou inline dans `<list_item>`), `exit 0` |
| `--image-description` — VLM ne répond pas pour une image | Balise `<picture>` conservée, warning dans les logs, `exit 0` |
| Exception inattendue pendant le traitement VLM | Traceback dans les logs, `exit 1` |

## Règles de remplacement des balises `<picture>`

| Contexte de la balise | Remplacement |
| ------- | ----- |
| `<list_item><picture/></list_item>` | `<list_item>description inline</list_item>` |
| `<picture/>` entre des `<list_item>` | `<list_item>description inline</list_item>` |
| `<picture/>` standalone | `<text>\ndescription\n</text>` |

**Exit codes**
| Code | Explication |
| ------- | ----- |
| 0 | Traitement réussi (ou passthrough sans erreur) |
| 1 | VLM non joignable, `VLM_URL`/`VLM_MODEL_NAME` manquant, ou exception pendant le traitement |

Le log final indique le résultat par étape :
```
VLM OK — <VLM_URL>/v1/models | modèles disponibles : ['<VLM_MODEL_NAME>']
CA utilisée : <chemin_résolu> (VLM_CA_PEM)   ← ou (certifi fallback) si VLM_CA_PEM absent
ÉTAPE 1 — Parsing des balises <picture> + éléments du doctags
ÉTAPE 2 — Export des images PNG (coordonnées doctags)
ÉTAPE 3 — Description des images avec contexte textuel
ÉTAPE 4 — Remplacement des balises <picture> dans le doctags
ÉTAPE 5 — Export des descriptions en Markdown
3/3 image(s) décrite(s) avec contexte
```
# Script description_image_context_modular.py — Description des images via VLM avec contexte

## Rôle dans le pipeline
S'exécute après `load_jsonline_doctags_modulaire.py` (qui produit le `_reordered_with_tables.doctags`) et avant les étapes de post-traitement.
| Étape | Opération|
| ------- | ----- |
| 1-Parsing | Lit le `.doctags` et extrait deux structures : la liste des `<picture>` (page + coordonnées loc_x0_y0_x1_y1) et la liste ordonnée de tous les éléments textuels du document (DocElement) |
| 2-Export PNG | Pour chaque `<picture>`, crop la zone correspondante dans le PDF source via PyMuPDF (les coordonnées doctags sont normalisées sur 500 × 500, ramenées aux dimensions réelles de la page) et sauvegarde le PNG dans `used_images/` |
| 3-Descroption VLM | Pour chaque image, collecte les 5 éléments textuels précédents et les 5 suivants (`N_BEFORE / N_AFTER`), construit un prompt contextualisé (`WIKI_PROMPT_TEMPLATE`), encode l'image en base64 et envoie le tout au VLM via HTTP POST. Le traitement est parallélisable via un pool de threads (`queue.Queue` + `_vlm_worker`) |
| 2-Remplacement | Réécrit le `.doctags` en substituant chaque `<picture>` selon son contexte : standalone → `<text>description</text>` ; dans un `<list_item>` → texte inline ; entre des `<list_item>` → `<list_item>description</list_item>` |
| 2-Export Markdown | Produit un rapport `.md` listant toutes les descriptions avec leur page et coordonnées, plus un résumé OK / WARNING par image |

## Comportements importants
- Si `--no-image-description` : les balises `<picture>` sont simplement supprimées (via regex), les PNG sont quand même exportés, exit 0.
- Si aucune `<picture>` trouvée : passthrough — le `.doctags` est copié tel quel, exit 0.
- Si le VLM ne répond pas pour une image : la balise `<picture>` est conservée dans la sortie (warning dans les logs), le reste continue.
- Si une exception inattendue : exit 1 avec traceback

**Priorité du switch `--image-description`**
| Argument CLI | Résultat|
| ------- | ----- |
| `--image-description` | `True` - VLM active |
| `--no-image-description` | `False` - balises supprimées |
| Rien (vide) | `False` - balises supprimées (défaut argparse) |

## Example d'une command complète et documentation des arguments:
```
uv run python pipeline_modular/simple_extraction/description_image_context_modulaire.py \
  --doctags    data/output_files/MonDoc/MonDoc_reordered_with_tables.doctags \
  --pdf        data/input_files/MonDoc.pdf \
  --output     data/output_files/MonDoc/MonDoc_reordered_with_tables_pictures.doctags \
  --markdown   data/output_files/MonDoc/MonDoc_image_descriptions.md \
  --images-dir data/output_files/MonDoc/used_images \
  --doc-name   MonDoc \
  --language   french \
  --workers    1 \
  --image-description \
  --dotenv     .env.test
```
| Paramètre |	Valeur dans l'exemple |	Note |
|-----------|-----------------------|------|
| `--doctags` |	`MonDoc_reordered_with_tables.doctags` |	Sortie de l'étape précédente |
| `--pdf` |	`data/input_files/MonDoc.pdf`	| PDF source pour le crop |
| `--output` |	`MonDoc_reordered_with_tables_pictures.doctags` |	Doctags enrichi (défaut = `<stem>_pictures.doctags`) |
| `--markdown` |	`MonDoc_image_descriptions.md` | Rapport des descriptions (défaut = `<doc_name>_image_descriptions.md`) |
| `--images-dir` |	`used_images/` | PNG cropés (défaut = used_images/ dans le dossier du doctags) |
| `--doc-name` |	`MonDoc` | Optionnel — déduit du nom fichier si absent |
| `--language` |	`french` | Langue de la réponse VLM |
| `--workers` |	`1`	| Threads VLM parallèles — garder à 1 si l'API a un rate-limit |
| `--image-description` |	(flag)	| **Obligatoire** pour activer le VLM, sinon les `<picture>` sont supprimées |
| `--dotenv` |	`.env.test`	| Charge `VLM_URL`, `CA_PATH`, `VLM_MODEL_NAME`, ignoré si `--doctags` et `--pdf` sont fournis |

*Note: Note : `--dotenv` et `--doctags/--pdf` explicites peuvent coexister, dans ce cas `--dotenv` sert uniquement à charger `VLM_URL` / `CA_PATH` / `VLM_MODEL_NAME`, la résolution des chemins utilise les valeurs CLI*

# Script url_tuning_vlm_modular.py — Intégration des liens hypertextes dans le doctags via VLM

Reconstruit le doctags page par page en y intégrant les liens hypertextes (`http`, `https`, `mailto`) extraits par `url_extaction_modular.py`. Pour chaque page, le VLM reçoit le fragment de doctags, la liste des liens de la page et une image PNG de la page, et produit un doctags enrichi où les liens sont intégrés au format markdown `[text](url)`. Les pages traitées sont ensuite réassemblées en un fichier doctags final.

**Place dans le pipeline :** après `url_extaction_modular.py` (qui produit le `.jsonl`) et après l'étape qui produit le `.doctags` d'entrée, avant les étapes de post-traitement aval.

## Variables d'environnement requises
| Variable | Obligatoire | Description |
| ------- | ----- | -------------|
| `VLM_URL` | Oui | Endpoint de l'API VLM (ex. `https://vlm.host/v1/chat/completions`). |
| `VLM_MODEL_NAME` | Oui | Nom du modèle à passer dans la requête. |
| `CA_PATH` | Non | Chemin vers un certificat CA custom pour la connexion HTTPS au VLM. |
| `DOC_NAME` | Si `--pdf` absent | Nom du document pour la résolution automatique des chemins. |

## Commande complète (tous les paramètres)
```
uv run python pipeline_modular/simple_extraction/url_tuning_vlm_modular.py \
  --pdf      data/input_files/MonDoc.pdf \
  --doctags  data/output_files/MonDoc/MonDoc.doctags \
  --jsonl    data/output_files/MonDoc/hyperlinks_data_MonDoc.jsonl \
  --output   data/output_files/MonDoc/MonDoc_url_vlm.doctags \
  --workers  1 \
  --dotenv   .env.test
```

## Chemins résolus automatiquement depuis le stem du PDF
```
uv run python pipeline_modular/simple_extraction/url_tuning_vlm_modular.py \
  --pdf data/input_files/MonDoc.pdf
```

## Ancien workflow .env.test
```
uv run python pipeline_modular/simple_extraction/url_tuning_vlm_modular.py --dotenv .env.test
```

## Doctags d'entrée personnalisé (ex. : issu de stage2)
```
uv run python pipeline_modular/simple_extraction/url_tuning_vlm_modular.py \
  --pdf     data/input_files/MonDoc.pdf \
  --doctags data/output_files/MonDoc/MonDoc_reordered_with_tables_pictures.doctags \
  --jsonl   data/output_files/MonDoc/hyperlinks_data_MonDoc.jsonl \
  --output  data/output_files/MonDoc/MonDoc_reordered_with_tables_pictures_url_vlm.doctags
```

## Paramètres :
| Paramètre | Alias | Défaut | Description |
| ------- | ----- | -------- | -------------|
| --pdf | -p | (voir note) | Chemin vers le PDF source — utilisé pour rendre chaque page en image. |
| --doctags | -d | `data/output_files/<stem>/<stem>.doctags` | Fichier `.doctags` d'entrée à enrichir avec les liens. |
| --jsonl | -j | `data/output_files/<stem>/hyperlinks_data_<stem>.jsonl` | Fichier JSONL produit par `url_extaction_modular.py`. |
| --output | -o | `data/output_files/<stem>/<stem>_url_vlm.doctags` | Fichier `.doctags` enrichi en sortie. |
| --workers | -w | `1` | Nombre de requêtes VLM simultanées. Garder à 1 si l'API a un rate-limit. |
| --dotenv | | (aucun) | Fichier `.env` à charger pour `VLM_URL`, `CA_PATH`, `VLM_MODEL_NAME` et `DOC_NAME`. Toujours chargé avant la config VLM. |

*Note : `--pdf` absent : résout automatiquement `data/input_files/<DOC_NAME>.pdf` depuis la variable `DOC_NAME`.*

**Sorties :**
```
data/output_files/MonDoc/
├── MonDoc.doctags                       ← entrée (ou tout autre .doctags spécifié)
├── hyperlinks_data_MonDoc.jsonl         ← entrée (produit par url_extaction_modular.py)
└── MonDoc_url_vlm.doctags               ← sortie
```

## Comportement page par page

| Situation | Comportement |
| ------- | ----- |
| Page sans lien dans le JSONL | VLM appelé quand même — le doctags de la page reste inchangé (pas de lien à insérer) |
| Page absente du split doctags | Ignorée — non envoyée au VLM |
| VLM non joignable au démarrage | Arrêt immédiat avant tout appel, `exit 1` |
| Erreur VLM sur une page | Fallback : le doctags original de la page est conservé, le traitement continue |
| `<page_break>` présents dans le doctags | Split exact par `<page_break>` — méthode fiable |
| Aucun `<page_break>` trouvé | Fallback : distribution approximative des blocs par nombre total de tags |

## Assemblage du doctags final

Après traitement de toutes les pages, les contenus sont réassemblés dans l'ordre :
- Les balises `<doctag>` / `</doctag>` internes à chaque réponse VLM sont retirées
- Les pages sont rejointées avec `<page_break>` comme séparateur
- Le résultat final est enveloppé dans une seule paire `<doctag>…</doctag>`

**Exit codes**
| Code | Explication |
| ------- | ----- |
| 0 | Doctags enrichi produit avec succès |
| 1 | VLM inaccessible au démarrage, ou exception inattendue — traceback dans les logs |

Le log final indique le chemin du fichier produit :
```
Doctags final sauvegardé : data/output_files/MonDoc/MonDoc_url_vlm.doctags
```

# Script docling_markdown_converter_modular.py — Conversion des doctags en Markdown

Convertit un fichier `.doctags` enrichi en Markdown via Docling. Applique deux pré-traitements avant la conversion et deux post-traitements sur le Markdown produit.

**Place dans le pipeline :** dernière étape — lit le `.doctags` final (enrichi avec tables, images et URLs), produit le `.md` livrable.

## Pré-traitements appliqués avant conversion Docling

| Étape | Fonction | Rôle |
| ------- | ----- | -------------|
| 1 | `_split_pages` | Découpe un bloc `<doctag>` unique en un bloc par page. Essaie d'abord `</page_footer>` (doctags Docling natif), puis `<page_break>` (produit par `url_tuning_vlm_modular.py`). Sans ce découpage, Docling s'arrête à la première page. |
| 2 | `_hoist_misplaced_tags` | Extrait les `<section_header_level_N>` et `<unordered_list>` imbriqués dans un `<ordered_list>` et les repositionne juste après le `</ordered_list>`. Docling écraserait sinon les en-têtes et frontières de section. |

## Post-traitements appliqués sur le Markdown produit

| Balise source | Rendu final |
| ------- | -------------|
| `[[COLOR:red]]texte[[/COLOR]]` | `<span style="color:red">texte</span>` |
| `\_\_texte\_\_` | `<u>texte</u>` |

## Commande minimale
```
uv run python pipeline_modular/simple_extraction/docling_markdown_converter_modular.py \
  --input data/output_files/MonDoc/MonDoc.doctags
```

## Sortie personnalisée
```
uv run python pipeline_modular/simple_extraction/docling_markdown_converter_modular.py \
  --input  data/output_files/MonDoc/MonDoc.doctags \
  --output data/output_files/MonDoc/MonDoc.md
```

## Ancien workflow .env.test
```
uv run python pipeline_modular/simple_extraction/docling_markdown_converter_modular.py --dotenv .env.test
```

## Paramètres :
| Paramètre | Alias | Défaut | Description |
| ------- | ----- | -------- | -------------|
| --input | -i | (voir note) | Fichier `.doctags` à convertir. |
| --output | -o | `<même dossier que --input>/<stem>.md` | Fichier Markdown de sortie. Créé automatiquement si absent. |
| --dotenv | | (aucun) | Fichier `.env` à charger pour résoudre `DOC_NAME`. Ignoré si `--input` est fourni. |

*Note : `--input` absent : résout automatiquement `data/output_files/<DOC_NAME>/<DOC_NAME>.doctags` depuis la variable `DOC_NAME`.*

**Sorties :**
```
data/output_files/MonDoc/
├── MonDoc.doctags      ← entrée
└── MonDoc.md           ← sortie
```

**Exit codes**
| Code | Explication |
| ------- | ----- |
| 0 | Markdown généré avec succès |
| 1 | Fichier `.doctags` invalide, erreur Docling, ou exception inattendue — traceback dans les logs |

Le log final indique le chemin du fichier produit :
```
Markdown généré : data/output_files/MonDoc/MonDoc.md
```
