# Architecture

Le pipeline transforme un PDF en une ligne `CONTENT | METADATA | EMBEDDING`
prête pour le retrieval, en 13 étapes chaînées.

## Vue d'ensemble

```
data/input_files/<corpus>/<thème>/<doc>.pdf
        │
        ▼  afac-preprocess run --input <PDF ou dossier>
   ┌────────────────────────────────────────────┐
   │ Pipeline (core/pipeline.py)                │
   │   · sélection : profils, --only/--skip     │
   │   · exécution : InProcessRunner par défaut │
   └────────────────────────────────────────────┘
        │  un PipelineContext par document
        ▼
data/output_files_preprocessing/<doc>/
   ├── <doc>.doctags, <doc>.md, <doc>_final.md, …
   ├── tables/, used_images/
   └── metadata/<doc>_final.csv
```

## Les objets du noyau

| Objet | Fichier | Rôle |
|---|---|---|
| `Settings` | `settings.py` | Configuration immuable, **seul** lecteur de l'environnement. Validée à la construction : une URL malformée échoue tout de suite, pas au bout de 2 min. |
| `DocumentWorkspace` | `workspace.py` | **Unique propriétaire des conventions de chemins.** Toute sortie du pipeline est une propriété de cette classe — ne jamais reconstruire un nom de fichier ailleurs. |
| `PipelineContext` | `context.py` | État d'un run (settings + workspace + clients), immuable. Remplace la circulation d'état par `os.environ`. |
| `ClientBundle` | `clients/bundle.py` | Possède **une** boucle d'événements et **un** client par cible (VLM, embedding) pour tout le run. |
| `PipelineStep` | `core/step.py` | Contrat des 13 étapes : `inputs()`/`outputs()` déclaratifs, `validate_inputs()`, `execute()`. |
| `Pipeline` | `core/pipeline.py` | Registre, sélection (`select()`), exécution (`run`, `run_batch`), rapports. |
| `StepRunner` | `core/runner.py` | Seam in-process / subprocess. `InProcessRunner` par défaut. |

## Les 13 étapes

`afac-preprocess steps --graph` imprime le chaînage réel, déduit des
déclarations `inputs()`/`outputs()`.

| # | Nom | VLM | Produit |
|---|---|---|---|
| 01 | `docling-extract` | — | `.doctags`, `.json`, `.md`, `.txt`, `tables/`, `used_images/` |
| 02 | `reorder-doctags` | — | `_reordered.doctags` |
| 03 | `opencv-check` | — | PNG de QA visuelle (désactivée par défaut) |
| 04 | `csv-to-jsonlines` | — | `tables/*.jsonl` |
| 05 | `load-jsonline-doctags` | — | `_reordered_with_tables.doctags` |
| 06 | `image-description` | ✅ | `_image_descriptions.md`, `_reordered_with_tables_pictures.doctags` |
| 07 | `url-extraction` | — | `hyperlinks_data_<doc>.jsonl` |
| 08 | `url-tuning` | ✅ | `_url_vlm.doctags` |
| 09 | `markdown-convert` | — | `_url_vlm.md` |
| 10 | `markdown-control` | ✅ | `_vlm_check.md` |
| 11 | `inject-image-descriptions` | — | `_final.md` |
| 12 | `metadata-generation` | ✅ | `metadata/<doc>_final.csv`, `resume.md`, `intent.json`, `hyq.json`, `embedding.json` |
| 13 | `hyq-embedding` | ✅ | `metadata/hyq_<doc>/question_N.csv` |

Deux collaborateurs portent les appels modèle de l'étape 12 sans être des
étapes du registre : `MetadataEnhancer` (resume / intent / hyq) et
`DocumentEmbedder` (embedding du markdown).

## Deux règles non négociables

**Aucun cache de réponses VLM/embedding**, nulle part — ni disque, ni mémoire,
ni inter-run. Chaque appel atteint réellement l'endpoint. L'hermétisme des
tests vient des doubles de `clients/fake.py`, jamais d'un rejeu.

**Tous les appels modèle sont asynchrones**, sur **une seule** boucle
d'événements par run. Le contrat commun des étapes (`execute()`) reste
synchrone ; les 5 étapes VLM implémentent `_execute_async()` et délèguent :

```python
def execute(self, ctx):
    return ctx.run_async(self._execute_async(ctx))   # jamais asyncio.run()
```

`asyncio.run()` dans une étape créerait une boucle et un client par étape,
détruisant le pool de connexions HTTP — c'est précisément ce que
`ClientBundle` évite.

## Ce qui est interdit dans le code du noyau

`sys.exit`, `SystemExit`, `logging.basicConfig`, `os.environ`,
`sys.path.insert`, `argparse`, `asyncio.run`. Seul `cli/main.py` sort du
process et configure le logging. Les erreurs métier sont des exceptions de
`exceptions.py` ; la CLI les traduit en codes de sortie stables (2 = config,
3 = étape inconnue, 4 = VLM indisponible).

## Hors périmètre

`pipeline_baseline/`, `retrieval_protocol_evaluation/`,
`neo4j_graphrag_ontology/` et `graph_url/` vivent dans le package mais n'ont
pas encore été refactorés — ils sont exemptés des règles ruff via
`per-file-ignores` et migreront séparément.
