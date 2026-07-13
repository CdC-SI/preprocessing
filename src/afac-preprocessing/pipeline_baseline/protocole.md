# Protocole — Baseline (docling brut) vs Pipeline de prétraitement

Guide pas-à-pas pour comparer, sur un corpus complet, la qualité de retrieval de deux
représentations d'un même document :
- **baseline** : markdown produit directement par Docling (OCR EasyOCR), sans aucun
  enrichissement.
- **pipeline** : markdown enrichi (descriptions d'images VLM, tuning des URLs, contrôle
  markdown VLM, structuration des tables) — actuellement la version « v2 »
  (`pipeline_extraction.py`), sortie dans `data/output_files_preprocessing/`.

Les deux représentations sont évaluées avec **les mêmes questions HyQ** et **les mêmes
métriques** (Recall/Precision/nDCG/MRR@k) — seule la représentation du document change.
Le delta mesure donc directement l'apport (ou le coût) du prétraitement.

Toutes les commandes ci-dessous s'exécutent depuis la racine du projet :
`preprocessing/src/afac-preprocessing/`.

## 0. Prérequis

- `uv` installé, dépendances du projet résolues (`uv sync` si besoin).
- Un fichier `.env.test` à la racine du projet contenant au minimum : `VLM_URL`,
  `VLM_MODEL_NAME`, `VLM_CA_PEM`, `EMBEDDING_URL`, `EMBEDDING_MODEL_NAME`,
  `RERANKER_URL`, `RERANKER_MODEL_NAME`. Sans ces variables, les étapes VLM/embedding
  échouent immédiatement (message explicite du type `VLM_URL not set`).
- Le corpus PDF à tester, présent sous `data/input_files/<source>/<thème>/*.pdf`
  (exemple utilisé ici : `data/input_files/afac/Adhésion/`, 20 documents).

Le pipeline fait de vrais appels VLM/embedding (coût + temps). Sur 20 documents,
compter plusieurs dizaines de minutes pour l'étape 1.

## 1. Générer le pipeline de prétraitement sur tout le corpus

```bash
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py \
  --dotenv .env.test \
  --input-dir "data/input_files/afac/Adhésion"
```

Ce script boucle sur chaque PDF trouvé sous `--input-dir` et lance
`pipeline_extraction.py` (13 étapes : extraction Docling/EasyOCR → réordonnancement
→ enrichissement VLM → metadata + embedding). Sortie par document :
`data/output_files_preprocessing/<doc>/` (dont `<doc>.md` = markdown brut, `<doc>_final.md`
= markdown enrichi, `metadata/<doc>_final.csv` = CONTENT + METADATA + EMBEDDING,
`metadata/hyq.json` + `metadata/hyq_<doc>/question_N.csv` = questions HyQ + leurs
embeddings).

**Si un document échoue** : le batch continue sur les suivants et liste les échecs à la
fin (`Batch finished with N failure(s)`). Pour rejouer un seul document après
correction :
```bash
uv run python pipeline_modular/automate_pipeline_example/pipeline_extraction.py \
  --dotenv .env.test --input "data/input_files/afac/Adhésion/<doc>.pdf" --from-step N
```
(`--from-step` = numéro de la première étape en échec, cf. l'en-tête de
`pipeline_extraction.py` pour la liste des 13 étapes — inutile de refaire
l'extraction Docling si elle a déjà réussi).

Vérifier que tous les documents ont bien produit `<doc>.md`, `metadata/hyq.json` et au
moins un `metadata/hyq_<doc>/question_*.csv` avant de passer à l'étape suivante (ce sont
les prérequis des étapes 2 et 3) :
```bash
for d in data/output_files_preprocessing/*/; do
  doc=$(basename "$d")
  test -f "$d/$doc.md" && test -f "$d/metadata/hyq.json" \
    && ls "$d/metadata/hyq_$doc"/question_*.csv >/dev/null 2>&1 \
    && echo "OK   $doc" || echo "MANQUE $doc"
done
```

## 2. Générer la baseline (docling brut) à partir des mêmes documents

```bash
uv run python single_retrieval_nopreprocessing/single_docling_baseline.py --dotenv .env.test
```

Ce script découvre automatiquement tous les documents sous
`data/output_files_preprocessing/` qui ont un `<doc>.md`, un `hyq.json` et des questions
HyQ déjà embeddées (produits à l'étape 1), embedde le markdown brut de chacun (nouvel
appel embedding, sur `CONTENT` = `<doc>.md` seul — les questions HyQ et leurs embeddings
sont réutilisés tels quels, jamais régénérés, pour garantir une comparaison à questions
identiques). Sorties :
- `data/baseline_evaluation/baseline_metadata.csv` — une ligne par doc (CONTENT/METADATA/HYQ/EMBEDDING).
- `data/baseline_evaluation/baseline_results.csv` — une ligne par (doc, question HyQ), avec Recall/Precision/nDCG/MRR@k.
- `data/output_files_baseline/<doc>/<doc>.md` — copie du markdown brut utilisé, pour inspection visuelle.

## 3. Évaluer le pipeline de prétraitement (mêmes métriques)

```bash
uv run python retrieval_protocol_evaluation/evaluate_all_docs.py
```

Calcule Recall/Precision/nDCG/MRR@k pour chaque document du pipeline (pipeline sémantique
seul + pipeline avec reranker), sur les mêmes questions HyQ. Sorties dans
`data/evaluation_results/` : `global_summary.csv` (moyennes par doc, colonnes
`sem_mean_<métrique>@<k>`) + CSV/graphiques par document.

## 4. Générer le rapport de comparaison

```bash
uv run python single_retrieval_nopreprocessing/compare_baseline_report.py --dotenv .env.test
```

Fusionne `baseline_results.csv` (étape 2) et `global_summary.csv` (étape 3), calcule le
delta par métrique@k (`delta = baseline − pipeline` : positif ⇒ la baseline fait mieux,
donc le prétraitement dégrade le retrieval ; négatif ⇒ le prétraitement améliore). Un
appel VLM optionnel (désactivable avec `--no-vlm-analysis`) ajoute un verdict textuel en
tête du rapport. Sortie : `data/baseline_evaluation/comparison_report.md` (+ graphiques
dans `data/baseline_evaluation/charts/`).

## 5. Lire les résultats

Le rapport contient, dans l'ordre :
1. **Verdict VLM** (si activé) — synthèse en français, sans code couleur (baseline /
   pipeline / équivalent).
2. **Graphiques** — comparaison globale, évolution par k, nDCG par document.
3. **Résumé global** — moyenne sur tous les documents, une table par métrique
   (Recall/Precision/nDCG/MRR), colonnes k=1/3/5/10/20, ligne `Delta`.
4. **Détail par document** (k=5) — trié par delta nDCG croissant, donc les documents où
   le pipeline dégrade le plus apparaissent en haut, ceux où il améliore le plus en bas.

Points de vigilance à vérifier en priorité sur un delta négatif marqué (pipeline moins
bon) pour un document donné :
- Comparer `data/output_files_preprocessing/<doc>/<doc>.md` (baseline) et
  `<doc>_final.md` (pipeline) : la longueur a-t-elle explosé (ratio mots
  final/brut) ? Une inflation forte dilue le signal sémantique du document dans
  l'embedding.
- Chercher des descriptions d'images quasi identiques d'un document à l'autre (logos,
  en-têtes institutionnels) — elles rapprochent artificiellement les embeddings des
  documents entre eux, ce qui nuit au retrieval (les documents courts sont les plus
  pénalisés, la description représente alors une plus grande part du texte embeddé).
- Vérifier les tables : le pipeline v2 les convertit en JSON-lines (une ligne = un objet
  avec toutes les clés répétées), ce qui peut ajouter beaucoup de bruit tokenisé pour peu
  de contenu utile à l'embedding (le pipeline v3, cf. §6, garde les tables en markdown
  natif pour éviter ça).

## 6. (Optionnel) Comparer aussi les variantes v3

`fullpipeline_modular_v3.py` diffère de v2 : tables markdown natives (pas de conversion
JSON-lines pour le doctags/`_final.md`), descriptions d'images activées par défaut,
prompts VLM adaptés (`--prompt-variant v3`). Il réutilise automatiquement les questions
HyQ déjà générées par v2 (comparabilité garantie) — v2 doit donc avoir tourné au
préalable pour chaque document (étape 1).

Pas de batch runner dédié pour v3 : boucle shell sur les PDF du corpus.
```bash
find "data/input_files/afac/Adhésion" -name "*.pdf" | while IFS= read -r pdf; do
    echo "### $pdf ###"
    uv run python pipeline_modular/automate_pipeline_example/fullpipeline_modular_v3.py \
      --dotenv .env.test --input "$pdf"
done
```
Sortie : `data/output_files_v3/` (jamais `data/output_files_preprocessing/`, pour ne
rien écraser).

### 6bis. Variante v3 sans descriptions d'images (v3-noimg)

Pour isoler l'effet des descriptions d'images (cf. §5 — elles peuvent rapprocher
artificiellement les embeddings des documents entre eux), variante v3 avec descriptions
désactivées et tables gardées en markdown même pour l'embedding (`--skip-steps 9` évite
la conversion JSON-lines de `markdown_tables_to_jsonl_modular.py`, qui ne sert qu'à
produire `_final_embed.md` — sans lui, `metadata_generation_modular.py` retombe sur
`_final.md`, markdown natif corrigé par VLM à l'étape 07) :
```bash
find "data/input_files/afac/Adhésion" -name "*.pdf" | while IFS= read -r pdf; do
    echo "### $pdf ###"
    uv run python pipeline_modular/automate_pipeline_example/fullpipeline_modular_v3.py \
      --dotenv .env.test --input "$pdf" \
      --no-image-description --skip-steps 9 \
      --output-root data/output_files_v3_noimg
done
```
Sortie dans `data/output_files_v3_noimg/` (dédiée, pour ne jamais écraser
`data/output_files_v3/`).

### Évaluer et comparer une variante v3 (ou v3-noimg)

```bash
uv run python retrieval_protocol_evaluation/evaluate_all_docs.py \
  --stage5 data/output_files_v3 --output-dir data/evaluation_results_v3
  # (ou --stage5 data/output_files_v3_noimg --output-dir data/evaluation_results_v3_noimg)

uv run python single_retrieval_nopreprocessing/compare_baseline_report.py --dotenv .env.test \
  --pipeline-summary data/evaluation_results_v3/global_summary.csv \
  --output data/baseline_evaluation/comparison_report_v3.md
  # (ou --pipeline-summary data/evaluation_results_v3_noimg/global_summary.csv
  #     --output data/baseline_evaluation/comparison_report_v3_noimg.md)
```
`compare_baseline_report.py` ne lit qu'un seul `global_summary.csv` à la fois
(`--pipeline-summary`) : chaque variante (v2/v3/v3-noimg) a son propre `--output` dédié,
comparée séparément à la même baseline (`baseline_results.csv`, inchangée quelle que
soit la variante testée — seules les questions HyQ et le markdown brut comptent, pas la
variante du pipeline).

## 7. Pièges déjà rencontrés (corrigés, mais bon à savoir)

- **`Incohérence : N page(s) dans le markdown mais M page(s) dans le PDF`** (étape 07 ou
  10) : Docling insère parfois un `<page_break>` mal placé (au milieu d'une page plutôt
  qu'à sa frontière), ou n'émet carrément aucun `<page_footer>` pour certains documents.
  `split_pages()` dans `reordered_doctags_modular.py` gère maintenant les deux cas
  (`<page_footer>` prioritaire quand il existe, repli sur `<page_break>` sinon) — mais si
  l'erreur revient sur un nouveau document, comparer le nombre de `<page_footer>` et
  `<page_break>` dans `<doc>.doctags`, `<doc>_reordered.doctags` et `<doc>_url_vlm.doctags`
  pour localiser l'étape qui perd/duplique une page.
- **`ENABLE_IMAGE_DESCRIPTION`** dans `.env.test` est un défaut global (actuellement
  `false`) lu par `description_image_context_modular.py`. Un flag CLI explicite
  (`--image-description` / `--no-image-description`) est toujours prioritaire sur cette
  variable — mais seulement si le script est appelé avec ce flag explicitement (v2 ne le
  passe jamais, il dépend donc entièrement de la variable ; v3 le passe toujours en dur).
- **`--from-step N`** suppose que les fichiers produits par les étapes précédentes sont
  encore sur disque. Si le dossier `data/output_files_.../<doc>/` a été nettoyé ou
  reconstruit entre deux essais, relancer sans `--from-step` (l'étape 01, extraction
  Docling, est déterministe et sans coût VLM).

## Résumé des commandes (v2 vs baseline, corpus Adhésion)

```bash
# 1. Pipeline de prétraitement (v2) sur les 20 documents
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py \
  --dotenv .env.test --input-dir "data/input_files/afac/Adhésion"

# 2. Baseline docling brut
uv run python single_retrieval_nopreprocessing/single_docling_baseline.py --dotenv .env.test

# 3. Évaluation du pipeline (Recall/Precision/nDCG/MRR@k)
uv run python retrieval_protocol_evaluation/evaluate_all_docs.py

# 4. Rapport de comparaison
uv run python single_retrieval_nopreprocessing/compare_baseline_report.py --dotenv .env.test
```

Rapport final : `data/baseline_evaluation/comparison_report.md`. Pour v3/v3-noimg, voir §6.
En cas d'erreur, voir §7 (pièges déjà rencontrés).