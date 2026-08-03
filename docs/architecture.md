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
data/output_files_preprocessing/<corpus>/<thème>/<doc>/
   ├── <doc>.doctags, <doc>.md, <doc>_final.md, …
   ├── tables/, used_images/
   └── metadata/<doc>_final.csv
data/output_files_preprocessing/<corpus>/<corpus>.csv   ← agrégat de fin de batch
```

La sortie **reproduit l'arborescence de l'entrée** (lot F1) : deux documents
homonymes rangés dans des dossiers différents ne s'écrasent plus. En fin de
batch, `aggregate.py` reconstruit un CSV global par corpus (lot F2) — ce n'est
pas une étape du pipeline : une étape ne voit qu'un document, alors que
l'agrégat dépend de tous les autres.

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

### Comment ils s'assemblent

> **Diagramme :** [`core-objects.mmd`](core-objects.mmd) — les objets du
> noyau, qui construit qui, et par où passent les appels modèle.

Trois arêtes portent l'essentiel des règles du noyau : `ENV → Settings`
(personne d'autre ne lit l'environnement), `Step → ClientBundle` via
`ctx.run_async` (personne n'ouvre sa propre boucle), et `Step → disque`
toujours à travers `DocumentWorkspace` (personne ne reconstruit un chemin).
`aggregate` est branché en pointillés parce qu'il n'est pas une étape : il se
déclenche après le lot, quand tous les documents ont écrit leur CSV.

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

### Le chaînage réel

> **Diagramme :** [`pipeline-steps.mmd`](pipeline-steps.mmd) — le DAG des
> 13 étapes, étiqueté par le fichier qui circule sur chaque arête.

Il transcrit ce qu'imprime `afac-preprocess steps --graph`, lui-même déduit des
`inputs()`/`outputs()` déclarés (à une nuance près, signalée en fin de
section). Les traits pointillés sont les chemins de repli, empruntés quand une
étape a été sautée par un profil.

Quatre choses que le graphe rend visibles et que la liste ordonnée cachait :

- **Le PDF reste une entrée jusqu'à l'étape 10.** Six étapes le rouvrent —
  pour rendre les pages, découper les images, lire les liens natifs ou
  comparer le markdown à la page d'origine. Ce n'est pas une chaîne
  linéaire : c'est un DAG dont le PDF est une source permanente.
- **Deux branches parallèles se rejoignent en 08.** `url-extraction` (07) ne
  dépend que du PDF : elle est indépendante de toute la branche doctags et
  pourrait tourner n'importe quand avant l'étape 08.
- **Le raccourci 01 → 12.** `metadata-generation` relit le `doc.json` de
  Docling — pas seulement le markdown final — pour les métadonnées
  structurelles. Sauter l'étape 01 ne casse donc pas que le début du
  pipeline.
- **Les replis sont ce qui rend les profils exécutables.** Sans l'arête
  pointillée `05 ⇢ 08`, `--profile no-images` échouerait sur un fichier
  manquant ; sans `09 ⇢ 11`, ce serait `no-vlm`. Chaque repli remonte la
  chaîne jusqu'aux doctags ou au markdown réellement produits, au lieu
  d'exiger la version la plus enrichie.

Une réserve sur ce graphe, également notée en tête du `.mmd` : `steps --graph`
attribue `tables/` à
`docling-extract` pour l'étape 05, parce que 01 et 04 écrivent dans le **même
répertoire** et que la résolution producteur→consommateur se fait par chemin.
Le graphe ci-dessus rétablit la dépendance réelle (05 lit les `.jsonl` produits
par 04) ; c'est la limite d'un contrat déclaratif au niveau du répertoire.

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

## Les autres chantiers du package

`pipeline_baseline/`, `retrieval_protocol_evaluation/` et
`neo4j_graphrag_ontology/` ne sont pas des étapes du pipeline, mais depuis le
lot 9 ils suivent les mêmes règles : **zéro `sys.path.insert`**, imports
relatifs au package, configuration par `Settings`, et aucune exemption ruff.
Ils se lancent donc en `-m`, comme le reste :

```bash
uv run python -m afac_preprocessing.pipeline_baseline.single_docling_baseline --dotenv .env.test
uv run python -m afac_preprocessing.retrieval_protocol_evaluation.evaluate_all_docs
uv run python -m afac_preprocessing.neo4j_graphrag_ontology.graphrag.batch_build_kg --help
```

**Tous les appels VLM/embedding/reranker du dépôt sont asynchrones** (exigence
métier). Les variantes synchrones de `utils/vlm_client.py` n'ont pas été
restaurées : `text_completion_async` et `text_completion_thinking_async` ont
été ajoutées au noyau au lot 9, et les scripts qui les appelaient sont devenus
`async def` avec un `asyncio.run()` en point d'entrée. Le reranker et le banc
de test embedding sont passés de `requests` à `httpx.AsyncClient`.

Seule exception, documentée dans le code : `EmbedderFactory` construit un
`httpx.Client` synchrone, parce que `neo4j_graphrag.embeddings.OpenAIEmbeddings`
est synchrone par conception et que la bibliothèque l'appelle elle-même depuis
`SimpleKGPipeline`.

Reste connu : ces scripts gardent leur `argparse` et quelques `sys.exit` dans
leur propre `main()` — seul `cli/main.py` en est exempt côté noyau.

Les outils autonomes (hors pipeline, décision n°14) vivent dans `tools/` :
`audit_pipeline_output.py`, `markdown_tables_to_jsonl.py`,
`compare_outputs.py`.
