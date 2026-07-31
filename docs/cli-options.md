# Options et personnalisation

Le [README](../README.md) donne le chemin nominal : configurer, lancer, comparer.
Ce document recense **tout ce qui est paramétrable** au-delà de ce chemin.

Il n'existe pas de commande unique qui liste l'ensemble : le dépôt contient
**trois familles** d'exécutables, avec trois mécaniques d'aide distinctes. En cas
de doute, `--help` fait toujours autorité — cette page peut vieillir, pas lui.

| Famille | Forme | Aide |
|---|---|---|
| CLI du pipeline | `afac-preprocess <commande>` (Typer) | `afac-preprocess <commande> --help` |
| Scripts d'évaluation | `python -m afac_preprocessing.<module>` (argparse) | `… --help`, module par module |
| Outils hors pipeline | `python tools/<script>.py` (argparse) | `… --help`, script par script |

Pour retrouver un module exécutable oublié :

```bash
grep -rln "argparse" src/afac_preprocessing/ tools/ --include=*.py | grep -v __pycache__
```

---

## 1. La CLI du pipeline

```bash
uv run afac-preprocess --help
```

| Commande | Rôle |
|---|---|
| `run` | Traite un PDF ou tous les PDF d'un dossier |
| `aggregate` | Reconstruit le CSV global de chaque corpus (`<racine>/<racine>.csv`) |
| `steps` | Liste les 13 étapes — fonctionne sans `.env` |
| `doctor` | Diagnostique l'installation et dit quoi corriger |
| `version` | Version installée |

### `run`

| Option | Défaut | Effet |
|---|---|---|
| `--input`, `-i` | **requis** | PDF ou dossier, exploré récursivement (`rglob("*.pdf")`) |
| `--profile` | `default` | Voir la table des profils ci-dessous |
| `--from-step` | — | Première étape à exécuter (nom ou numéro) |
| `--to-step` | — | Dernière étape à exécuter |
| `--skip` | — | Étapes à sauter, séparées par des virgules |
| `--only` | — | N'exécuter que ces étapes |
| `--no-ocr` | `false` | Transmis à `docling-extract` uniquement |
| `--with-opencv-check` | `false` | Inclut `opencv-check`, désactivée par défaut |
| `--dry-run` | `false` | Affiche les étapes retenues sans rien exécuter |
| `--dotenv` | `.env` puis `.env.test` | Fichier de configuration |
| `--verbose`, `-v` | `0` | `-v` = DEBUG |

Les étapes s'adressent par **nom ou par numéro** (`06` ≡ `image-description`) ;
`afac-preprocess steps` donne la correspondance.

**Règles de priorité** (`cli/main.py::_build_pipeline`) :

- `--only` court-circuite tout le reste — `--from-step`, `--to-step`, `--skip` et
  le `skip` du profil sont ignorés ;
- sinon, le `skip` du profil et le `--skip` de la ligne de commande sont
  **concaténés**, pas substitués ;
- `--to-step` explicite l'emporte sur le `to` du profil (cas de `extract`) ;
- `--with-opencv-check` force `include_disabled`, comme le profil `full`.

`--dry-run` ne touche ni le réseau ni le disque : ni client VLM, ni agrégation
de fin de lot. C'est la vérification à réflexe avant un long batch.

```bash
uv run afac-preprocess run --input data/input_files/afac --dry-run
```

### Les profils

Définis dans `core/registry.py` — une constante, pas de la configuration.

| Profil | Effet | Étapes exécutées |
|---|---|---|
| `default` | Comportement standard | 12 (tout sauf `opencv-check`) |
| `full` | Inclut les étapes désactivées | 13 |
| `no-images` | Saute `image-description` | 11 |
| `no-vlm` | Saute toutes les étapes `requires_vlm` | 7 |
| `extract` | S'arrête à `markdown-convert` | 8 |

`no-vlm` retire `image-description`, `url-tuning`, `markdown-control`,
`metadata-generation` et `hyq-embedding` : il produit un markdown, **pas** de
métadonnées ni d'embeddings. Aucun CSV de retrieval n'en sort.

#### Sauter une étape ne casse plus la chaîne

Les étapes déclarent leurs entrées, et le noyau refuse de démarrer si un fichier
déclaré manque (`core/step.py::validate_inputs`). Trois étapes savent désormais
remonter la chaîne jusqu'au dernier artefact réellement produit, ce qui rend
`no-images` et `no-vlm` exécutables sur un arbre vierge :

| Étape | Entrée nominale | Repli |
|---|---|---|
| `url-tuning` | `_reordered_with_tables_pictures.doctags` | `_reordered_with_tables.doctags` |
| `markdown-convert` | `_url_vlm.doctags` | `…_pictures.doctags`, puis `_reordered_with_tables.doctags` |
| `inject-image-descriptions` | `_vlm_check.md` | `_url_vlm.md` |

Le repli teste l'**existence** des fichiers. Relancer un profil réduit sur un
dossier de sortie déjà rempli reprend donc les artefacts du run précédent :
partir d'un arbre vide est la seule façon d'obtenir un résultat propre.

Quand aucun candidat n'existe, le résolveur renvoie l'entrée nominale, pour que
le message d'erreur nomme le fichier attendu et non le fichier de secours.

### Deux façons de se passer des descriptions d'images

Elles ne produisent **pas** le même markdown — le choix n'est pas neutre.

| | `--profile no-images` | `ENABLE_IMAGE_DESCRIPTION=false` |
|---|---|---|
| Étape `image-description` | sautée | exécutée |
| Balises `<picture>` | **conservées** dans les doctags | **retirées** (`remove_picture_tags`) |
| Fichier `_image_descriptions.md` | non créé | non créé |
| Appels VLM pour les images | aucun | aucun |
| Portée | un run | toute la configuration |

La variable d'environnement est le geste chirurgical : l'étape tourne, nettoie
les balises et alimente normalement la suite. Le profil est le geste large :
l'étape ne tourne pas du tout, et les balises `<picture>` restent dans le flux.

Pour une comparaison de retrieval, `ENABLE_IMAGE_DESCRIPTION=false` donne le
contraste le plus net — seul le contenu des images disparaît, sans résidu de
balisage.

### `aggregate`

| Option | Défaut | Effet |
|---|---|---|
| `--root` | toutes les racines | Ne recalculer qu'un corpus (ex. `afac`) |
| `--dotenv` | `.env` puis `.env.test` | |
| `--verbose`, `-v` | `0` | |

L'agrégation tourne déjà en fin de batch ; cette commande sert à la rejouer
sans relancer le pipeline.

### `steps` et `doctor`

```bash
uv run afac-preprocess steps            # les 13 étapes
uv run afac-preprocess steps --graph    # le chaînage entrées ← sorties
uv run afac-preprocess doctor           # diagnostic, avec la correction à appliquer
uv run afac-preprocess doctor --dotenv .env.test
```

`steps` fonctionne **sans `.env`** : utile pour inspecter le pipeline sur une
machine non configurée.

---

## 2. Variables d'environnement

`Settings` (`settings.py`) est le **seul** lecteur de l'environnement, et
valide à la construction : une URL malformée échoue immédiatement, pas au bout
de deux minutes.

| Variable | Défaut | Rôle |
|---|---|---|
| `VLM_URL` | **requis** | Endpoint VLM |
| `VLM_MODEL_NAME` | `""` | Nom du modèle VLM |
| `VLM_CA_PEM` | certifi | Certificat CA ; ignoré si le chemin n'existe pas |
| `VLM_TEMPERATURE` | `0.0` | Température des générations VLM |
| `ENABLE_IMAGE_DESCRIPTION` | `true` | Voir la comparaison ci-dessus |
| `ENABLE_IMAGE_EXTRACTION` | `false` | Export des PNG par Docling (le `.env` du projet le met à `true`) |
| `EMBEDDING_URL` | — | Requis pour les métadonnées et l'évaluation |
| `EMBEDDING_MODEL_NAME` | `""` | |
| `RERANKER_URL` | — | Requis par le volet reranker de l'évaluation |
| `RERANKER_MODEL_NAME` | `""` | |
| `PROJECT_ROOT` | racine détectée | Surcharge la racine du dépôt |
| `DATA_ROOT` | `<project_root>/data` | Surcharge l'emplacement de `data/` |

`DATA_ROOT` déplace **à la fois** `input_files/` et `output_files_preprocessing/` :
c'est le levier prévu pour un conteneur ou la CI. Pour ne rediriger que la
sortie d'une expérience, un `mv` du dossier de sortie est plus simple. Surcharger
`PROJECT_ROOT` sans `DATA_ROOT` fait suivre `data_root` automatiquement.

`.env.example` contient des variables qu'aucun code de `src/` ni de `tools/` ne
lit aujourd'hui — `LLM_URL`, `LLM_MODEL_NAME`, `TOKENIZER_URL`,
`TOKENIZER_MODEL_NAME`, `MAX_EMBEDDING_TOKENS`, `VISUALISER_LE_GRAPH`. Les
`NEO4J_*` ne servent qu'au chantier graphe (`neo4j_graphrag_ontology/`), et
`DOC_NAME` n'est plus lu que par `tools/markdown_tables_to_jsonl.py`.
`Settings` est en `extra="ignore"` : leur présence est inoffensive.

---

## 3. Les scripts d'évaluation

Modules autonomes, lancés par `python -m`. C'est d'eux que viennent `--stage5`,
`--output-dir` et `--docs-output-dir` — **aucun n'est une option de
`afac-preprocess`**.

| Module | Options |
|---|---|
| `pipeline_baseline.single_docling_baseline` | `--stage5` `--output-dir` `--docs-output-dir` `--dotenv` `--top-ks` `--log-level` |
| `retrieval_protocol_evaluation.evaluate_all_docs` | `--stage5` `--output-dir` `--top-ks` `--canonical-k` `--no-reranker` `--log-level` |
| `pipeline_baseline.comparison_report_html` | `--baseline-results` `--pipeline-summary` `--output` `--top-ks` `--canonical-k` `--log-level` |
| `pipeline_baseline.compare_baseline_report` | idem + `--dotenv` `--no-vlm-analysis` `--charts-dir` `--no-charts` |
| `pipeline_baseline.single_doc_preview_report` | `--doc-name` `--stage5` `--baseline-metadata` `--output` |

Les options transversales :

| Option | Défaut | Effet |
|---|---|---|
| `--stage5` | `data/output_files_preprocessing` | Arbre de sortie à évaluer. **Le levier central** : il détermine à la fois les documents et les questions HyQ utilisées |
| `--output-dir` | `data/baseline_evaluation` ou `data/pipeline_evaluation` | Où écrire les CSV de résultats |
| `--docs-output-dir` | `data/output_files_baseline` | Copie des markdown bruts évalués, pour inspection |
| `--top-ks` | `1,3,5,10,20` | Valeurs de k. Sur 20 documents, `@20` renvoie tout le corpus : peu informatif |
| `--canonical-k` | `5` | k des graphiques ordonnés |
| `--no-reranker` | `false` | Évaluation sémantique seule — divise le temps et les appels réseau |
| `--no-vlm-analysis` | `false` | Rapport markdown sans verdict rédigé par le modèle |

Faire porter `--stage5` **et** les deux `--output-dir` sur des chemins dédiés est
ce qui permet de comparer plusieurs variantes sans qu'elles s'écrasent.

### Choisir entre les deux rapports

| | `comparison_report_html` | `compare_baseline_report` |
|---|---|---|
| Sortie | HTML autoportant | Markdown + `charts/` |
| Verdict | calculé (signe du delta nDCG moyen) | rédigé par le VLM, sauf `--no-vlm-analysis` |
| Extra `viz` | non requis (SVG écrit à la main) | requis (matplotlib) |
| Reproductible | au bit près | non, si le verdict VLM est activé |

Les fonctions d'agrégation sont partagées : à données égales, les chiffres sont
identiques.

---

## 4. Les outils hors pipeline

| Script | Options | Rôle |
|---|---|---|
| `tools/audit_pipeline_output.py` | `--stage5` `--json` | Contrôle santé read-only : détecte les étapes échouées en silence |
| `tools/markdown_tables_to_jsonl.py` | `--doc-name` `--stage5` `--markdown`/`-m` `--output-dir`/`-o` `--embed-output` `--dotenv` `--log-level` | Exporte les tables Markdown d'un `_final.md` en JSONL |
| `tools/compare_outputs.py` | deux chemins positionnels | Compare deux arbres de sortie |

`--embed-output` produit le `_final_embed.md` que `metadata-generation` préfère
comme source de contenu et d'embedding **s'il existe**.

`compare_outputs.py` classe chaque fichier en STRICT (égalité après
normalisation), STRUCTUREL (présence et forme — pour les sorties VLM, qui
changent à chaque run), TOLERANT (CSV cellule à cellule) ou MANQUANT. Seuls les
échecs STRICT/TOLERANT et les manquants donnent un code de sortie non nul.

---

## 5. Recettes

**Reprendre après un échec, sans tout refaire**

```bash
uv run afac-preprocess run --input <…> --from-step markdown-convert
```

**Rejouer une seule étape** — utile après avoir édité un fichier intermédiaire
à la main :

```bash
uv run afac-preprocess run --input <…> --only hyq-embedding
```

**Comparer deux variantes du pipeline sans qu'elles s'écrasent**

La sortie est déterministe (`data/output_files_preprocessing/<corpus>/<thème>/<doc>/`),
sans horodatage : deux runs successifs occupent le même dossier, et rien n'est
nettoyé entre les deux. Il faut donc isoler explicitement chaque variante.

```bash
mv data/output_files_preprocessing data/output_variante_a
uv run afac-preprocess run --input <…> --profile no-images
mv data/output_files_preprocessing data/output_variante_b
```

puis pointer `--stage5` et les `--output-dir` sur des chemins distincts.

**Évaluer plus vite pendant la mise au point d'un protocole**

```bash
uv run python -m afac_preprocessing.retrieval_protocol_evaluation.evaluate_all_docs \
    --stage5 <…> --output-dir <…> --no-reranker --top-ks 1,5
```

**Évaluer avec des questions imposées**

Les scripts lisent les questions dans l'arbre `--stage5` : `metadata/hyq.json`
pour le texte, `metadata/hyq_<doc>/question_N.csv` pour les embeddings. La
pertinence est déterminée par le **dossier où la question est rangée**
(`metrics.py` : un unique document pertinent par question).

Substituer un jeu de questions revient donc à écrire ces fichiers dans le
`metadata/` du document censé répondre. Un document sans question est ignoré
comme source de requêtes mais **reste dans le vivier de candidats** : il continue
de jouer son rôle de distracteur.

Les embeddings de questions ne dépendent que du texte et du modèle, pas de la
représentation du document : ils se génèrent une fois et se copient d'un arbre
à l'autre.

---

## Voir aussi

- [architecture.md](architecture.md) — les 13 étapes, le contrat `PipelineStep`
- [../README.md](../README.md) — le chemin nominal
- [../CONTRIBUTING.md](../CONTRIBUTING.md) — ajouter une étape
- [../src/afac_preprocessing/pipeline_baseline/protocole.md](../src/afac_preprocessing/pipeline_baseline/protocole.md) — le protocole de comparaison en détail
