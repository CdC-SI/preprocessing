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
uv run python pipeline_preprocessing/orchestrators/batch_pipeline_all_pdfs.py \
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
uv run python pipeline_preprocessing/orchestrators/pipeline_extraction.py \
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
uv run python pipeline_baseline/single_docling_baseline.py --dotenv .env.test
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
`data/pipeline_evaluation/` : `global_summary.csv` (moyennes par doc, colonnes
`sem_mean_<métrique>@<k>`) + CSV/graphiques par document.

## 4. Générer le rapport de comparaison

```bash
uv run python pipeline_baseline/compare_baseline_report.py --dotenv .env.test
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
- Vérifier les tables : le pipeline les convertit en JSON-lines (une ligne = un objet
  avec toutes les clés répétées), ce qui peut ajouter beaucoup de bruit tokenisé pour peu
  de contenu utile à l'embedding.

## 6. Pièges déjà rencontrés (corrigés, mais bon à savoir)

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
  variable — mais seulement si le script est appelé avec ce flag explicitement (le pipeline
  ne le passe jamais, il dépend donc entièrement de la variable).
- **`--from-step N`** suppose que les fichiers produits par les étapes précédentes sont
  encore sur disque. Si le dossier `data/output_files_.../<doc>/` a été nettoyé ou
  reconstruit entre deux essais, relancer sans `--from-step` (l'étape 01, extraction
  Docling, est déterministe et sans coût VLM).

## Résumé des commandes (pipeline vs baseline, corpus Adhésion)

```bash
# 1. Pipeline de prétraitement sur les 20 documents
uv run python pipeline_preprocessing/orchestrators/batch_pipeline_all_pdfs.py \
  --dotenv .env.test --input-dir "data/input_files/afac/Adhésion"

# 2. Baseline docling brut
uv run python pipeline_baseline/single_docling_baseline.py --dotenv .env.test

# 3. Évaluation du pipeline (Recall/Precision/nDCG/MRR@k)
uv run python retrieval_protocol_evaluation/evaluate_all_docs.py

# 4. Rapport de comparaison
uv run python pipeline_baseline/compare_baseline_report.py --dotenv .env.test
```

Rapport final : `data/baseline_evaluation/comparison_report.md`.
En cas d'erreur, voir §6 (pièges déjà rencontrés).