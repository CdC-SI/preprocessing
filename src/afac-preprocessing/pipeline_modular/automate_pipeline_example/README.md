# automate_pipeline_example — Orchestrateurs du pipeline

Scripts d'automatisation de haut niveau : ils ne font aucun traitement eux-mêmes, ils
coordonnent l'exécution des scripts individuels de `pipeline_modular/` (voir
[../README.md](../README.md) pour la liste des 13 étapes).

> Tous les exemples supposent que vous êtes positionné à la racine `afac-preprocessing/`.

## Vue d'ensemble

| Script | Cas d'usage |
|--------|-------------|
| `pipeline_extraction.py` | **Recommandé** — pipeline complet, un document ou un dossier entier, contrôle fin des étapes |
| `batch_pipeline_all_pdfs.py` | Traite tous les PDFs sous `data/input_files/` (ou un sous-dossier via `--input-dir`) — équivalent à `pipeline_extraction.py --input <dossier>` avec en plus `--dry-run` |
| `multi_gen_consistency_test.py` | Test de cohérence — même document, **N générations** successives, pour mesurer la variabilité du VLM |
| `audit_pipeline_output.py` | Contrôle santé read-only d'un arbre de sortie (`--stage5 data/output_files_preprocessing`) — détecte les étapes ayant échoué silencieusement. Ne modifie rien. |
| `fullpipeline_modular.py` | Ancêtre (12 étapes, sans contrôle des étapes) — conservé pour compatibilité, ne pas utiliser pour du nouveau code |

---

# pipeline_extraction.py

Lance les 13 étapes en séquence pour un document, ou pour tous les PDFs d'un dossier
(`--input <dossier>`, traitement séquentiel, continue sur échec).

```bash
# Un document
uv run python pipeline_modular/automate_pipeline_example/pipeline_extraction.py \
    --dotenv .env.test --input "data/input_files/afac/Adhésion/Adhésion traitement.pdf"

# Tout un dossier
uv run python pipeline_modular/automate_pipeline_example/pipeline_extraction.py \
    --dotenv .env.test --input "data/input_files/afac/Adhésion"

# Reprendre après un échec, sélection par nom ou numéro
uv run python pipeline_modular/automate_pipeline_example/pipeline_extraction.py \
    --dotenv .env.test --input "data/input_files/MonDoc.pdf" --from-step markdown-control

# Table des étapes (nom ↔ numéro)
uv run python pipeline_modular/automate_pipeline_example/pipeline_extraction.py --list-steps
```

`--help` liste tous les paramètres (`--from-step`, `--to-step`, `--skip-steps`, `--only`,
`--with-opencv-check`, `--no-ocr`). En cas d'échec d'une étape, le pipeline s'arrête
immédiatement (sauf en mode dossier, qui continue sur les documents suivants).

---

# batch_pipeline_all_pdfs.py

Parcourt récursivement `data/input_files/` (ou `--input-dir <dossier>`) et lance
`pipeline_extraction.py` sur chaque PDF trouvé, séquentiellement. Un échec sur un document
n'interrompt pas le batch ; les échecs sont listés en fin d'exécution (`exit 1` si au moins un).

```bash
# Aperçu sans exécution — toujours vérifier avant un run complet
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py \
    --dotenv .env.test --input-dir data/input_files/afac/Adhésion --dry-run

# Traiter tout un thème
uv run python pipeline_modular/automate_pipeline_example/batch_pipeline_all_pdfs.py \
    --dotenv .env.test --input-dir data/input_files/afac/Adhésion
```

Sans `--input-dir`, scanne tout `data/input_files/` (tous thèmes AFAC confondus).
`--from-step`/`--to-step`/`--skip-steps` sont transmis tels quels à `pipeline_extraction.py`.

---

# multi_gen_consistency_test.py

Lance `pipeline_extraction.py` **N fois** sur le même document (`GEN_ID=1..N`), snapshot les
sorties dans `gen_runs/gen_<N>/` après chaque run — utile pour mesurer la variabilité du VLM
d'une génération à l'autre.

```bash
uv run python pipeline_modular/automate_pipeline_example/multi_gen_consistency_test.py \
    --dotenv .env.test --input "data/input_files/Adhésion/Adhésion traitement.pdf" --runs 5

# Tester uniquement une étape VLM (plus rapide)
uv run python pipeline_modular/automate_pipeline_example/multi_gen_consistency_test.py \
    --dotenv .env.test --input "data/input_files/Adhésion/Adhésion traitement.pdf" \
    --runs 5 --from-step markdown-control --to-step markdown-control
```

`--no-snapshot` désactive la copie (sorties écrasées à chaque run) ; `--continue-on-error`
poursuit les générations suivantes même après un échec (par défaut, arrêt au premier échec).
`--help` pour la liste complète des paramètres.
