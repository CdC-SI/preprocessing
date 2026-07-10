# Rapport de réorganisation — `preprocessing`

> Document de travail préparé le 2026-07-09 comme **contexte** avant la mise en œuvre des retours du collaborateur.
> Aucune modification de code n'a été faite. Chaque point reprend le retour, décrit l'état actuel constaté, propose une solution, et signale les pièges.

## Rappel de la structure actuelle

```
Dev_Python_Stage/                      ← racine (non versionnée)
├── .globalvenvmatt/                   ← venv global (hors git)
├── lib/                               ← libs vis.js / tom-select (viz graphe)
├── backup_zia/                        ← copies/backups multiples (fina_backup, backup_final, …)
└── preprocessing/                     ← LE dépôt git (branche courante : automate-afac-preprocessing)
    ├── README.md                      ← vision/objectifs du projet
    ├── examples/
    ├── graphify-out/                  (gitignore)
    ├── pdf-ocr-pipeline/              ← ancien pipeline (à sortir / brancher)
    └── src/
        └── afac-preprocessing/        ← LE package réel
            ├── .venv/                 ← venv DANS src (gitignore)
            ├── pyproject.toml, uv.lock, requirements.txt
            ├── README.md + readme_comparasison.md + readme_simplification.md
            ├── pipeline_modular/
            │   ├── README.md (825 lignes)
            │   ├── simple_extraction/       ← étapes 01-10 (le cœur)
            │   ├── description_image/       ← étape descriptions VLM
            │   ├── metadata/                ← étapes metadata + embeddings
            │   ├── docling_image_png/       (gitignore)
            │   └── automate_pipeline_example/
            │       ├── fullpipeline_modular.py      (90 l, obsolète ?)
            │       ├── fullpipeline_modular_v2.py   (181 l)
            │       ├── fullpipeline_modular_v3.py   (359 l)
            │       ├── batch_pipeline_all_pdfs.py   (150 l)
            │       ├── audit_pipeline_output.py
            │       └── multi_gen_consistency_test.py
            ├── utils/ (config.py, paths.py, vlm_client.py, multi_gen_test.py, …)
            ├── prompts/ (+ prompts_test_playground.py)
            ├── graph_url/                   ← graphe d'URLs (isolé)
            ├── single_retrieval_nopreprocessing/
            ├── retrieval_protocol_evaluation/
            ├── neo4j_graphrag_ontology/
            ├── unread_file/ (gitignore)
            └── data/                        (gitignore sauf skeleton .gitkeep)
```

---

## Traitement point par point des retours

### 1. Renommer `afac-preprocessing` → `preprocessing`
- **État actuel** : double niveau `preprocessing/src/afac-preprocessing/`. Le nom `afac-preprocessing` apparaît dans `pyproject.toml` (`name = "afac-preprocessing"`), dans les `.gitignore` (chemins en dur `src/afac-preprocessing/data/**`, etc.), et dans plusieurs docstrings/commentaires.
- **Proposition** : deux options, à trancher.
  - **(A) renommage simple** : `src/afac-preprocessing/` → `src/preprocessing/`. Layout `src/` conservé (bonne pratique packaging Python).
  - **(B) aplatir** : remonter le contenu de `src/afac-preprocessing/` d'un cran. Plus simple à naviguer mais on perd le layout `src/` (voir point 4).
- **⚠️ Points bloquants** :
  - Le `.gitignore` contient **~20 chemins en dur** `src/afac-preprocessing/...` à réécrire ensemble sinon les données ignorées reviennent.
  - `pyproject.toml` : `name` + `[tool.hatch.build.targets.wheel] packages = ["utils", "prompts"]` (ces packages sont relatifs au répertoire de build).
  - `git mv` recommandé pour préserver l'historique.

### 2. `batch_pipeline_all_pdfs.py` → intégrer `--input <dossier ou fichier>` dans le fullpipeline
- **État actuel** : `fullpipeline_modular_v2.py` et `v3.py` acceptent déjà `--input <PDF>` (un seul fichier). `batch_pipeline_all_pdfs.py` (150 l) est un wrapper séparé qui boucle sur tous les PDF.
- **Proposition** : dans le fullpipeline unifié, faire que `--input` accepte fichier **ou** dossier :
  - fichier → une exécution ;
  - dossier → `for pdf in dir.rglob("*.pdf"): run_pipeline(pdf)`.
  - Garder `batch_pipeline_all_pdfs.py` en local seulement (déjà l'intention) ou le convertir en simple ré-export de la fonction batch.
- **Effort** : faible — la logique de résolution `DOC_NAME`/`DOC_PATH` existe déjà (`_resolve_doc_name` dans v3), il suffit d'ajouter une branche « si dossier, itérer ».

### 3. Nommage à retravailler (`v2` vs `v3`, `modular`, `pipeline_multietape_modular`, `automate_pipeline_example`…)
- **État actuel** : le suffixe `_modular` est présent sur **quasiment tous** les fichiers (`pipeline_multietape_modular.py`, `csv_to_jsonlines_modular.py`, …). Comme tout le package est « modular », le suffixe est redondant et bruite les noms.
- **Proposition** :
  - Retirer systématiquement le suffixe `_modular` (c'est le nom du package, pas de chaque fichier).
  - `automate_pipeline_example/` → `pipeline/` ou `runners/` (ce ne sont pas des « exemples », c'est le point d'entrée réel).
  - `pipeline_multietape_modular.py` → `docling_extract.py` (c'est l'extraction Docling, étape 01).
  - `automate_pipeline_example` fait doublon conceptuel avec « pipeline_modular » — voir aussi points 6 et 15.
- **⚠️** : renommages en masse → mettre à jour toutes les listes `STEPS` (chemins en dur dans les 3 fullpipeline), les READMEs, et les commandes d'exemple.

### 4. venv créé dans `src/` plutôt qu'à la racine du projet — est-ce courant ?
- **Constat** : **non**, ce n'est pas la convention. Le `.venv` est dans `src/afac-preprocessing/.venv`. La convention `uv`/PEP est un `.venv` **à la racine du dépôt** (ici `preprocessing/.venv`), à côté du `pyproject.toml`.
- **Complication** : le `pyproject.toml` lui-même est dans `src/afac-preprocessing/`, pas à la racine `preprocessing/`. Donc le venv « suit » le pyproject. Le vrai problème est que **le projet Python (pyproject) n'est pas à la racine du dépôt git**.
- **Proposition** : remonter `pyproject.toml` + `uv.lock` à la racine `preprocessing/`, adopter le layout `preprocessing/src/preprocessing/` standard, et laisser `uv` créer `.venv` à la racine. `.venv`, `.globalvenvmatt`, `.retrievalvenvmatt` sont déjà dans `.gitignore` — vérifier que le nouveau chemin l'est aussi.
- **À noter** : il existe **3 venvs** dans l'arbre (`.globalvenvmatt` racine, `.venv` src, `.retrievalvenvmatt` dans retrieval_protocol_evaluation). À rationaliser en un seul.

### 5. `pdf-ocr-pipeline` — à voir avec Kieran, éventuellement branche séparée
- **État actuel** : `preprocessing/pdf-ocr-pipeline/` est un pipeline distinct (dossiers `preprocessing/`, `prompts/`, `data/`, `manifests/`) coexistant avec `src/`.
- **Proposition** : action différée (attendre Kieran). Recommandation : **le sortir de la branche principale** vers une branche `legacy/pdf-ocr-pipeline` (ou un dépôt/dossier d'archive), pour ne pas polluer l'arborescence du package. Rien ne l'importe depuis `src/` (vérifié : aucun import croisé).
- **Décision à prendre avec Kieran** avant d'agir — noté comme *pending*.

### 6. Ne garder qu'un `fullpipeline_modular` (v2 et v3 ne devraient différer que par les paramètres ?)
- **⚠️ Constat important** : ce n'est **pas** qu'une différence de paramètres aujourd'hui. `v3` (359 l) contient de la **logique structurelle** absente de `v2` (181 l) :
  - routage explicite de chaque chemin d'entrée/sortie par étape (`build_steps()`), là où v2 s'appuie sur les défauts internes de chaque script ;
  - `seed_hyq()` : copie de `resume.md`/`intent.json`/`hyq.json`/embeddings depuis la sortie v2 pour préserver la comparabilité ;
  - auto-skip de l'étape 11 si les embeddings HyQ sont déjà seedés ;
  - `--output-root` avec garde-fou anti-écrasement ;
  - **liste d'étapes différente** : v3 supprime `opencv_checker`, `csv_to_jsonlines`, `load_jsonline_doctags` et ajoute `markdown_tables_to_jsonl` — les tables restent en `<otsl>` natif (mesuré : 373→1038 mots en JSON, ×2.8 de bruit d'embedding).
  - v3 active les descriptions d'images par défaut, v2 non ; v3 utilise `--prompt-variant v3`.
- **Proposition** : unification possible mais elle demande un vrai travail, pas un simple merge :
  1. adopter le routage explicite `build_steps()` de v3 comme base commune ;
  2. exprimer les différences v2/v3 comme un **profil/preset** (ex. `--profile v2|v3`, ou un fichier de config YAML — cf. vision du README) qui sélectionne : liste d'étapes, variante de prompt, descriptions d'images on/off, output-root ;
  3. `seed_hyq` devient une option (`--seed-from <dir>`), utile uniquement en mode comparaison.
  - Les lignes de commande spécifiques v2/v3 iraient dans `examples/` (cf. retour).
- **`fullpipeline_modular.py` (sans suffixe, 90 l)** semble être l'ancêtre obsolète des deux (steps 1-12, référence encore `fullpipeline_modular_v2.py` dans sa docstring). **À supprimer** une fois v2/v3 unifiés.

### 7. Enlever par défaut `Step 03/13 — opencv_checker_modular.py`
- **État actuel** : dans `v2`, l'étape 03 est `opencv_checker_modular.py` — QA visuelle uniquement, **ne produit rien en aval** (confirmé par le commentaire dans v3 : « QA visuelle uniquement, ne produit rien »). `v3` l'a **déjà retirée**.
- **Proposition** : la retirer de la séquence par défaut de v2 (ou du pipeline unifié). La rendre disponible en opt-in (`--with-opencv-check` ou l'appeler manuellement). C'est cohérent avec l'unification du point 6.

### 8. Ne pas créer `adhesion_traitement_image_descriptions` si vide
- **État constaté** : dans `description_image_context_modular.py` :
  - ligne 898 : `images_dir.mkdir(parents=True, exist_ok=True)` est appelé **avant** de savoir s'il y a des images à exporter → dossier vide si le doc n'a pas d'image ;
  - ligne 906 : quand les descriptions sont désactivées, `markdown_path.write_text("")` écrit un fichier `_image_descriptions.md` **vide**.
- **Proposition** : ne créer le dossier/fichier que si `pictures` est non vide (garder le `mkdir` juste avant l'écriture effective, et sauter l'écriture du `.md` vide). Petit patch localisé, faible risque.

### 9. Remplacer `stages` par dossiers d'input/output
- **⚠️ Constat** : les args `--stage1 … --stage5` de `metadata_generation_modular.py` (et `enhancement_/embedding_metadata`) **pointent tous vers la même valeur par défaut** (`DEFAULT_OUTPUT_FILES`, lignes 72-76). Dans `v3`, `build_steps()` passe d'ailleurs `output_root` aux 5 en même temps. Le découpage « stage1..5 » est donc **vestigial** et trompeur.
- **Proposition** (alignée avec le retour) : remplacer par des noms sémantiques explicites :
  ```
  --stage1 → --input-dir      (dossier source doctags/markdown)
  --stage2 → --image-dir      (images)
  --stage3 → --url-dir        (URLs/hyperliens)
  --stage4 → --markdown-dir   (markdown final, source du résumé/metadata)
  --stage5 → --output-dir     (metadata + embeddings)
  ```
  Comme ils convergent aujourd'hui vers un seul dossier, on peut probablement les **fusionner en `--input-dir` / `--output-dir`** et ne garder les autres qu'en override optionnel.
- **⚠️** : vérifier chaque usage de `args.stageN` dans les 3 scripts metadata avant de renommer.

### 10. Supprimer `prompts_test_playground.py` (garder en local)
- **État** : `prompts/prompts_test_playground.py` existe. Il n'est pas gitignoré actuellement.
- **Proposition** : le retirer du suivi git (`git rm --cached`) et l'ajouter au `.gitignore`, ou le déplacer dans un `scratch/`/`local/` ignoré. Vérifier d'abord qu'aucun script ne l'importe (recherche rapide : aucun import détecté).

### 11. Simplification des READMEs
- **État** : documentation **redondante et volumineuse** :
  - `preprocessing/README.md` (vision, 114 l)
  - `src/afac-preprocessing/README.md` (153 l)
  - `src/afac-preprocessing/readme_comparasison.md` + `readme_simplification.md` (déjà gitignorés — notes de travail)
  - `pipeline_modular/README.md` (**825 lignes**)
  - + READMEs dans metadata/, single_retrieval/, retrieval_protocol_evaluation/ (×2)
- **Proposition** :
  - un README racine court (quoi + quickstart + lien vers le reste) ;
  - un README par sous-module fonctionnel, concis ;
  - fusionner/supprimer `readme_comparasison.md` et `readme_simplification.md` (notes perso) ;
  - le README de 825 l → le réduire à l'essentiel opérationnel, déplacer les détails d'implémentation en commentaires de code ou docs/.

### 12. Renommer le dossier `simple_extraction`
- **État** : `pipeline_modular/simple_extraction/` contient en réalité **le cœur du pipeline** (étapes 01-10 : docling, réordonnancement, URL, markdown, contrôle VLM). Le nom « simple » est trompeur.
- **Proposition** : `extraction/` ou `steps/` ou `core/`. (`extraction/` reste cohérent avec le découpage `description_image/` + `metadata/`.)
- **⚠️** : chemin référencé en dur via `_SIMPLE = _PIPELINE_ROOT / "simple_extraction"` dans les 3 fullpipeline + le README.

### 13. Supprimer `graph_url` si plus utilisé
- **État constaté** : `graph_url/graph_url.py` — **aucun import** depuis le reste du package (vérifié). `graph_url/graph_output/` est gitignoré. Dépendances associées encore dans `pyproject.toml` (`networkx`, `pyvis`, `matplotlib`, `Jinja2`) + le dossier `lib/` racine (vis.js/tom-select) sert à ce rendu.
- **Proposition** : confirmer qu'il n'est plus utilisé, puis le supprimer (ou l'archiver hors branche principale). Si supprimé, **retirer aussi les dépendances viz** du `pyproject.toml` pour alléger l'environnement — sauf si `neo4j_graphrag_ontology` en dépend (à vérifier avant).

### 14. Convention de nommage anglais dans le code / français dans les READMEs
- **État** : mélange. Code majoritairement anglais mais docstrings/commentaires/logs souvent en français ; noms français persistants (`description_image` ok, mais aussi `pipeline_multietape`, variables/messages FR).
- **Proposition** :
  - **identifiants (fichiers, fonctions, variables, args CLI)** → anglais (`description_image` → `image_description`, etc.) ;
  - **READMEs** → français (conservé) ;
  - **docstrings/commentaires** : choisir une règle unique — pragmatiquement, garder le français est acceptable si c'est la langue de l'équipe, mais harmoniser (aujourd'hui c'est mélangé).
- **Note** : plusieurs docstrings sont des placeholders auto-générés (`"Docstring for parse_image_descriptions_md" / ":param descriptions_path: Description"`) — à nettoyer pendant cette passe.

### 15. Rendre `--from-step` / `--skip-steps` plus clairs (des noms ?)
- **État** : sélection d'étapes par **numéro** (`--from-step 8`, `--skip-steps 3,6`). Fragile : les numéros changent dès qu'on ajoute/retire une étape (v2 a 13 étapes, v3 en a 11 → même numéro = étape différente).
- **Proposition** (à discuter, cf. retour) : nommer les étapes et permettre la sélection par nom.
  - Passer d'une `list[Path]` à une structure `{name: str, script: Path, args: [...]}`.
  - `--from-step docling`, `--skip-steps opencv-check,image-description`, `--only markdown-control`.
  - Accepter aussi les numéros en compat, ou `--list-steps` pour afficher la table `nom → n°`.
  - Ça règle en même temps la fragilité des numéros entre profils v2/v3 (point 6).

---

## Points additionnels / bloquants relevés (hors retours)

- **A. `backup_zia/` à la racine** : contient 4 copies quasi-complètes du projet (`fina_backup/`, `backup_final/`, `matthias_guideline_preprocessing/`, `neo4j_graphrag_ontology/`), certaines avec leur propre `.venv`. Non versionné mais alourdit fortement l'arbre local et prête à confusion (on peut éditer la mauvaise copie). Recommandation : archiver ailleurs / supprimer une fois la réorg validée.
- **B. `neo4j_graphrag_ontology/examples/` gitignoré pour cause de données AFAC sensibles** : attention lors des renommages/déplacements à ne pas ré-inclure ces fichiers par erreur (le `.gitignore` cible des chemins en dur).
- **C. Double source de dépendances** : `pyproject.toml` + `uv.lock` **et** `requirements.txt`. Choisir une source de vérité (recommandé : `pyproject`+`uv.lock`, supprimer `requirements.txt` ou le générer automatiquement).
- **D. `fullpipeline_modular.py` (sans suffixe)** : sa docstring documente encore un usage `fullpipeline_modular_v2.py` et liste 12 étapes divergentes — probable code mort. À supprimer explicitement (point 6).
- **E. `.pyc` / `__pycache__` versionnés ?** : présents dans plusieurs dossiers. `*.pyc` est dans `.gitignore` mais vérifier qu'aucun `__pycache__` n'est déjà suivi (`git ls-files | grep pycache`).
- **F. `utils/multi_gen_test.py` vs `automate_pipeline_example/multi_gen_consistency_test.py`** : deux fichiers « multi_gen » à des endroits différents — clarifier lequel est canonique (le fichier ouvert dans ton éditeur est `utils/multi_gen_test.py`).
- **G. `unread_file/` et `single_retrieval_nopreprocessing/` vs `retrieval_protocol_evaluation/`** : trois zones « lecture/évaluation » aux frontières floues — à regrouper sous un `evaluation/` unique si le sens le permet.

---

## Ordre d'exécution suggéré (du moins au plus risqué)

1. **Nettoyage sans impact code** : supprimer/ignorer `prompts_test_playground.py` (10), fusionner les READMEs de notes (11), corriger le dossier/fichier vide d'images (8), retirer opencv du défaut (7). *Petits patchs isolés.*
2. **Renommages internes** : suffixe `_modular` (3), `simple_extraction` (12), args `--stageN` (9), noms d'étapes (15). *Mécaniques mais touchent les listes `STEPS` en dur.*
3. **Unification du pipeline** : fusionner v2/v3 en profils + `build_steps` commun, supprimer `fullpipeline_modular.py` (6), `--input` fichier/dossier (2). *Le vrai chantier — tester sur un doc de référence à chaque étape.*
4. **Restructuration dépôt** : remonter `pyproject`/venv à la racine, renommer `afac-preprocessing`→`preprocessing`, réécrire le `.gitignore` (1, 4). *Faire en un commit atomique, `git mv`.*
5. **Décisions en attente** : `pdf-ocr-pipeline` (5) et `graph_url` (13) → attendre Kieran / confirmer non-usage avant suppression.

## Garde-fous avant de commencer
- Travailler sur une branche dédiée (pas `automate-afac-preprocessing` directement) et commiter par lot du plan ci-dessus.
- Après chaque lot touchant le pipeline, **relancer un run de référence** sur un PDF connu et diffé la sortie — les chemins en dur (`_SIMPLE`, `STEPS`, `--stageN`) sont la principale source de casse silencieuse.
- Le `.gitignore` à chemins en dur est le point le plus fragile de la réorg de structure : le mettre à jour **dans le même commit** que chaque renommage de dossier.

En te basant sur ce fichier markdown qui est un rapport d'audit sur mon pipeline de preprocessing, nous allons implémenter les changements progressivement. Pour ce faire, je vais te dire point par point lequel ne faisons, ne change rien aux autres points. Dans ta réponse pour me dire que tu comprends dans qul point nous sommes, tu dois me donncer le texte du point à modifier avant de faire les changements :
