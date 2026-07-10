# automate_pipeline_example — Orchestrateurs du pipeline

Ce dossier contient les scripts d'automatisation de haut niveau. Ils ne font aucun traitement eux-mêmes : ils coordonnent l'exécution des scripts individuels de `pipeline_modular/` et gèrent les cas d'usage courants (un document, tous les documents, plusieurs générations).

> **Point de départ :** tous les exemples supposent que vous êtes positionné à la racine du projet `afac-preprocessing/`.

---

## Vue d'ensemble

| Script | Cas d'usage |
|--------|-------------|
| `pipeline_extraction.py` | **Recommandé** — pipeline complet (13 étapes), un document, contrôle fin |
| `fullpipeline_modular_v3.py` | Variante v2 : tables markdown natives (pas de conversion JSON-lines), descriptions d'images activées par défaut. Sort dans `data/output_files_v3/`, jamais `data/output_files_preprocessing/`. Réutilise les questions HyQ déjà générées par v2 (v2 doit avoir tourné au préalable pour le document). |
| `fullpipeline_modular.py` | Version simplifiée (12 étapes) — maintenu pour compatibilité, préférer v2 |
| `batch_pipeline_all_pdfs.py` | Traitement automatique de **tous les PDFs** sous `data/input_files/` ou un sous-dossier ciblé (`--input-dir`) — v2 uniquement, pas de batch runner dédié pour v3 (boucle shell, cf. son fichier). |
| `multi_gen_consistency_test.py` | Test de cohérence — même document, **N générations** successives (GEN_ID = 1..N) |
| `audit_pipeline_output.py` | Contrôle santé read-only d'un arbre de sortie (`--stage5 data/output_files_preprocessing`, `_v3`, `_baseline`, ...) — détecte les documents où une étape a échoué silencieusement (marqueurs `[[[IMAGE_DESC:N]]]` non remplacés, `_final.md` vide/absent, etc.). Ne modifie rien. |

## Démarrage rapide

```bash
# 1 document — pipeline complet
uv run python pipeline_modular/automate_pipeline_example/pipeline_extraction.py \
    --dotenv .env.test \
    --input "data/input_files/afac/Adhésion/Adhésion traitement.pdf"

# Tous les docs d'un thème — vérifier d'abord avec --dry-run
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py \
    --dotenv .env.test \
    --input-dir data/input_files/afac/Adhésion \
    --dry-run

# Tous les docs d'un thème — lancer le batch
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py \
    --dotenv .env.test \
    --input-dir data/input_files/afac/Adhésion

# Tous les docs AFAC (136 documents)
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py \
    --dotenv .env.test
```

---

# pipeline_extraction.py — Orchestrateur principal

Lance les 13 étapes du pipeline en séquence pour un seul document. Chaque étape reçoit le `--dotenv` résolu, ce qui garantit que `DOC_NAME` est cohérent tout au long du run.

## Étapes exécutées

| # | Script | Rôle |
|---|--------|------|
| 01 | `pipeline_multietape_modular.py` | Extraction Docling + export images PNG |
| 02 | `reordered_doctags_modular.py` | Réordonnancement des blocs DocTags |
| 03 | `opencv_checker_modular.py` | Validation visuelle bounding boxes *(optionnel)* |
| 04 | `csv_to_jsonlines_modular.py` | Conversion tables CSV → JSONL |
| 05 | `load_jsonline_doctags_modular.py` | Injection tables dans les DocTags |
| 06 | `description_image_context_modular.py` | Descriptions images via VLM *(lent)* |
| 07 | `url_extaction_modular.py` | Extraction des liens hypertextes |
| 08 | `url_tuning_vlm_modular.py` | Intégration liens + corrections OCR via VLM |
| 09 | `docling_markdown_converter_modular.py` | Conversion DocTags → Markdown paginé |
| 10 | `markdown_control_vlm_modular.py` | Contrôle qualité Markdown via VLM |
| 11 | `inject_image_descriptions_modular.py` | Injection descriptions images → `_final.md` |
| 12 | `metadata_generation_modular.py` | Génération métadonnées + CSV final |
| 13 | `hyq_embedding_doc_modular.py` | Embeddings des questions hypothétiques |

## Commandes types

```bash
# Pipeline complet — document défini dans le .env (DOC_NAME + DOC_PATH)
uv run python pipeline_modular/automate_pipeline_example/pipeline_extraction.py \
    --dotenv .env.test

# Pipeline complet — document passé directement en argument
uv run python pipeline_modular/automate_pipeline_example/pipeline_extraction.py \
    --dotenv .env.test \
    --input "data/input_files/Adhésion/Ahésion traitement.pdf"

# Reprendre après un échec à l'étape 8
uv run python pipeline_modular/automate_pipeline_example/pipeline_extraction.py \
    --dotenv .env.test --input "data/input_files/MonDoc.pdf" --from-step 8

# Seulement le contrôle markdown + injection + métadonnées (étapes 10 → 13)
uv run python pipeline_modular/automate_pipeline_example/pipeline_extraction.py \
    --dotenv .env.test --input "data/input_files/MonDoc.pdf" --from-step 10

# Extraction seulement, sans métadonnées (étapes 1 → 11)
uv run python pipeline_modular/automate_pipeline_example/pipeline_extraction.py \
    --dotenv .env.test --input "data/input_files/MonDoc.pdf" --to-step 11

# Ignorer opencv (lent/optionnel) et descriptions images (très lent) — étapes 3 et 6
uv run python pipeline_modular/automate_pipeline_example/pipeline_extraction.py \
    --dotenv .env.test --input "data/input_files/MonDoc.pdf" --skip-steps 3,6

# Rejouer uniquement les métadonnées
uv run python pipeline_modular/automate_pipeline_example/pipeline_extraction.py \
    --dotenv .env.test --input "data/input_files/MonDoc.pdf" --from-step 12 --to-step 13
```

## Paramètres

| Paramètre | Défaut | Description |
|-----------|--------|-------------|
| `--dotenv` | `.env.test` | Fichier `.env` transmis à chaque étape. |
| `--input` / `-i` | *(depuis .env)* | Chemin vers le PDF à traiter. Surcharge `DOC_NAME` et `DOC_PATH` du `.env`. Accepte les sous-dossiers de `data/input_files/`. |
| `--from-step` | `1` | Première étape à exécuter (1–13, inclus). |
| `--to-step` | `13` | Dernière étape à exécuter (1–13, inclus). |
| `--skip-steps` | *(aucun)* | Étapes à ignorer, séparées par virgule. Ex. `--skip-steps 3,6`. |

**Comportement en cas d'échec :** si une étape retourne un code ≠ 0, le pipeline s'arrête immédiatement et affiche `[FAILED] <script> exited with code N`. Les étapes suivantes ne sont pas exécutées.

**Note `--input` :** le script calcule automatiquement le chemin relatif depuis `data/input_files/`, ce qui permet de pointer un PDF dans un sous-dossier sans modifier le `.env`. Sans `--input`, `DOC_NAME` et `DOC_PATH` doivent être définis dans le `.env`.

**Note `--skip-steps` :** ignorer une étape ne supprime pas la dépendance sur ses fichiers de sortie. Si l'étape 1 est ignorée mais que `<doc>.doctags` n'existe pas, l'étape 2 échouera avec un message explicite.

---

# fullpipeline_modular.py — Version simplifiée *(compatibilité)*

Version antérieure de l'orchestrateur — 12 étapes, sans `--input`, sans contrôle des étapes (`--from-step`, `--to-step`, `--skip-steps`). Maintenu pour la compatibilité avec d'anciens scripts. **Préférer `pipeline_extraction.py`** pour tout nouveau run.

## Commande

```bash
# DOC_NAME et DOC_PATH doivent être définis dans le .env
uv run python pipeline_modular/automate_pipeline_example/fullpipeline_modular.py \
    --dotenv .env.test
```

## Paramètre

| Paramètre | Défaut | Description |
|-----------|--------|-------------|
| `--dotenv` | `.env.test` | Fichier `.env` transmis à chaque étape. `DOC_NAME` obligatoire. |

---

# batch_pipeline_all_pdfs.py — Traitement de tous les PDFs

Parcourt récursivement `data/input_files/` (ou un sous-dossier ciblé via `--input-dir`) et lance `pipeline_extraction.py` sur chaque PDF trouvé. Les PDFs sont traités **séquentiellement**.

En cas d'échec sur un document, le batch **continue** sur les suivants et liste tous les échecs en fin d'exécution.

## Commandes types

```bash
# Traiter tous les PDFs du dossier input_files/ (tous les documents AFAC)
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py \
    --dotenv .env.test

# Traiter uniquement un sous-dossier (ex. tous les docs du thème "Adhésion")
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py \
    --dotenv .env.test \
    --input-dir data/input_files/afac/Adhésion

# Aperçu des PDFs qui seraient traités — sans exécution
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py \
    --dotenv .env.test \
    --input-dir data/input_files/afac/Adhésion \
    --dry-run

# Reprendre à partir de l'étape 8 pour tous les PDFs d'un sous-dossier
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py \
    --dotenv .env.test \
    --input-dir data/input_files/afac/Adhésion \
    --from-step 8

# Rejouer uniquement les métadonnées + HyQ (étapes 12→13) sur tous les PDFs
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py \
    --dotenv .env.test --from-step 12 --to-step 13

# Ignorer opencv et descriptions images (étapes lentes) sur tous les PDFs
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py \
    --dotenv .env.test --skip-steps 3,6
```

## Paramètres

| Paramètre | Défaut | Description |
|-----------|--------|-------------|
| `--dotenv` | `.env.test` | Fichier `.env` transmis à chaque étape de chaque document. |
| `--input-dir` | `data/input_files/` | Dossier racine à scanner pour les PDFs. Accepte un chemin absolu ou relatif à la racine du projet. |
| `--dry-run` | *(désactivé)* | Affiche la liste des PDFs détectés sans lancer le pipeline. |
| `--from-step` | `1` | Forwarded à `pipeline_extraction.py` — première étape à exécuter. |
| `--to-step` | `13` | Forwarded à `pipeline_extraction.py` — dernière étape à exécuter. |
| `--skip-steps` | *(aucun)* | Forwarded à `pipeline_extraction.py` — étapes à ignorer. |

## Comportement de `--input-dir`

Sans `--input-dir`, le script scanne **tout** `data/input_files/` récursivement — tous les thèmes AFAC confondus (136 documents environ).

Avec `--input-dir`, seul ce dossier est scanné (récursivement) :

```
data/input_files/
└── afac/
    ├── Adhésion/          ← --input-dir data/input_files/afac/Adhésion  → 20 PDFs
    ├── Taxation/          ← --input-dir data/input_files/afac/Taxation  → N PDFs
    ├── Contentieux/       ← ...
    └── ...
```

**Conseil :** utiliser `--dry-run` avant tout batch réel pour vérifier la liste des PDFs détectés.

**Résilience :** un échec sur un PDF n'interrompt pas le batch. Le script retourne `exit 1` uniquement si au moins un PDF a échoué, avec la liste des documents concernés et leurs codes de retour.

---

# multi_gen_consistency_test.py — Test de cohérence multi-générations

Lance `pipeline_extraction.py` **N fois de suite** sur le même document, en incrémentant `GEN_ID` à chaque passe (GEN_ID = 1, 2, …, N). Après chaque run, les fichiers de sortie sont copiés dans un sous-dossier dédié, ce qui permet de comparer les sorties entre générations et de mesurer la variabilité du VLM sur un même document.

**Cas d'usage typique :** vérifier que `_vlm_check.md` ou `_final.md` sont stables d'une génération à l'autre, ou au contraire mesurer l'amplitude des variations.

## Fonctionnement

Pour chaque génération `N` :
1. `GEN_ID="N"` est écrit dans le fichier `.env` (remplace la valeur existante).
2. `GEN_ID=N` est injecté dans l'environnement du sous-processus — `load_dotenv` ne l'écrasera pas.
3. `pipeline_extraction.py` est lancé avec tous les paramètres forwarded.
4. Les fichiers produits dans `data/output_files_preprocessing/<DOC_NAME>/` sont copiés dans `gen_runs/gen_<N>/`.

## Structure des sorties

```
data/output_files_preprocessing/
├── <DOC_NAME>/                     ← dernière génération (overwrite à chaque run)
│   ├── <doc>_vlm_check.md
│   ├── <doc>_final.md
│   └── ...
├── <DOC_NAME>_gen1/                ← snapshot génération 1
│   ├── <doc>_vlm_check.md
│   ├── <doc>_final.md
│   └── ...
├── <DOC_NAME>_gen2/                ← snapshot génération 2
│   └── ...
└── <DOC_NAME>_gen5/                ← snapshot génération 5
    └── ...
```

## Commandes types

```bash
# 5 générations complètes sur un document
uv run python pipeline_modular/automate_pipeline_example/multi_gen_consistency_test.py \
    --dotenv .env.test \
    --input "data/input_files/Adhésion/Ahésion traitement.pdf" \
    --runs 5

# Tester uniquement le contrôle Markdown VLM (étape 10) — beaucoup plus rapide
uv run python pipeline_modular/automate_pipeline_example/multi_gen_consistency_test.py \
    --dotenv .env.test \
    --input "data/input_files/Adhésion/Ahésion traitement.pdf" \
    --runs 5 --from-step 10 --to-step 10

# Tester les étapes VLM (url tuning + markdown control) sans extraction complète
uv run python pipeline_modular/automate_pipeline_example/multi_gen_consistency_test.py \
    --dotenv .env.test \
    --input "data/input_files/Adhésion/Ahésion traitement.pdf" \
    --runs 3 --from-step 8 --to-step 10

# Exécuter sans snapshot (les sorties sont simplement écrasées à chaque run)
uv run python pipeline_modular/automate_pipeline_example/multi_gen_consistency_test.py \
    --dotenv .env.test \
    --input "data/input_files/Adhésion/Ahésion traitement.pdf" \
    --runs 5 --no-snapshot

# Continuer même si une génération échoue
uv run python pipeline_modular/automate_pipeline_example/multi_gen_consistency_test.py \
    --dotenv .env.test \
    --input "data/input_files/Adhésion/Ahésion traitement.pdf" \
    --runs 5 --continue-on-error
```

## Paramètres

| Paramètre | Défaut | Description |
|-----------|--------|-------------|
| `--dotenv` | `.env.test` | Fichier `.env` transmis à chaque étape. Mis à jour avec `GEN_ID` avant chaque run. |
| `--input` / `-i` | *(depuis .env)* | Chemin vers le PDF à traiter. Forwarded à `pipeline_extraction.py`. |
| `--runs` | `5` | Nombre de générations à exécuter (GEN_ID va de 1 à N). |
| `--from-step` | `1` | Première étape à exécuter par génération. Forwarded à `pipeline_extraction.py`. |
| `--to-step` | `13` | Dernière étape à exécuter par génération. Forwarded à `pipeline_extraction.py`. |
| `--skip-steps` | *(aucun)* | Étapes à ignorer par génération. Forwarded à `pipeline_extraction.py`. |
| `--no-snapshot` | *(désactivé)* | Ne copie pas les sorties après chaque run. Les fichiers sont écrasés à chaque génération. |
| `--continue-on-error` | *(désactivé)* | Continue les générations suivantes même si une génération échoue. Par défaut, le script s'arrête au premier échec. |

## Comparer les sorties entre générations

```bash
# Comparer le _vlm_check.md entre la génération 1 et la génération 2
diff \
  "data/output_files_preprocessing/Ahésion traitement_gen1/Ahésion traitement_vlm_check.md" \
  "data/output_files_preprocessing/Ahésion traitement_gen2/Ahésion traitement_vlm_check.md"

# Comparer tous les _vlm_check.md entre toutes les générations
for i in 2 3 4 5; do
  echo "=== gen_1 vs gen_$i ==="
  diff \
    "data/output_files_preprocessing/Ahésion traitement_gen1/Ahésion traitement_vlm_check.md" \
    "data/output_files_preprocessing/Ahésion traitement_gen${i}/Ahésion traitement_vlm_check.md" \
    | head -20
done
```

## Résumé affiché en fin d'exécution

```
============================================================
  MULTI-GENERATION SUMMARY
============================================================
  gen 01  [OK ]  → data/output_files_preprocessing/MonDoc_gen1
  gen 02  [OK ]  → data/output_files_preprocessing/MonDoc_gen2
  gen 03  [FAIL]
  gen 04  [OK ]  → data/output_files_preprocessing/MonDoc_gen4
  gen 05  [OK ]  → data/output_files_preprocessing/MonDoc_gen5

  Failed generations: [3]
============================================================
```

| Situation | Comportement |
|-----------|-------------|
| Génération réussie + snapshot activé | Sorties copiées dans `gen_runs/gen_<N>/`, run précédent écrasé si même numéro |
| Génération réussie + `--no-snapshot` | Sorties dans `data/output_files_preprocessing/<doc>/` seulement, pas de copie |
| Dossier de sortie introuvable après run | Warning dans les logs, snapshot ignoré pour cette génération |
| Génération échouée | Aucun snapshot, passage à la suivante si `--continue-on-error`, arrêt sinon |
| Toutes les générations échouées | `exit 1` |
