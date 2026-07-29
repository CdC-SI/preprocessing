# Contribuer

## Mise en route

```bash
cp .env.example .env      # renseigner VLM_URL, EMBEDDING_URL
uv sync --all-extras
uv run afac-preprocess doctor
```

## Avant de pousser

```bash
uv run ruff check src/ tests/ tools/
uv run mypy
uv run pytest
```

C'est exactement ce que fait la CI (`.github/workflows/ci.yml`).

## Ajouter une étape au pipeline

1. **Une classe dans `src/afac_preprocessing/steps/`** qui hérite de
   `PipelineStep` : `name`, `description`, `requires_vlm`, puis `inputs()`,
   `outputs()` et `execute()`.
2. **L'enregistrer** dans `core/registry.py` — la fabrique `_converted_steps()`
   et la position voulue dans `STEP_ORDER`.
3. **Deux tests de contrat** dans `tests/unit/` : les sorties déclarées sont
   créées et non vides ; une entrée manquante lève `StepInputMissing`.

Si l'étape appelle un modèle, elle est **async** :

```python
def execute(self, ctx: PipelineContext) -> StepResult:
    return ctx.run_async(self._execute_async(ctx))

async def _execute_async(self, ctx: PipelineContext) -> StepResult:
    vlm = ctx.vlm()                       # client partagé du run
    ...
```

Concurrence par `asyncio.Semaphore` + `asyncio.gather` — le patron de
référence est [`steps/url_tuning.py`](src/afac_preprocessing/steps/url_tuning.py).
Ne jamais construire de client dans une étape, ne jamais appeler
`asyncio.run()`, ne jamais ajouter de cache de réponses.

## Chemins de fichiers

Tout nom de sortie est une propriété de `DocumentWorkspace`. Si un chemin
manque, on l'ajoute là — jamais un f-string dans une étape. Ces noms sont
**contractuels** : des consommateurs en aval en dépendent.

## Tests

Les tests n'appellent jamais le réseau : les doubles `FakeVlmClient` et
`FakeEmbeddingClient` (`clients/fake.py`) fournissent des réponses fixes.
Marqueurs disponibles pour ce qui ne peut pas tourner en CI :

```python
@pytest.mark.vlm     # nécessite un endpoint réel
@pytest.mark.slow    # trop lent pour la CI
```

## Non-régression

`tools/compare_outputs.py` compare un dossier de sortie à une référence gelée.
Après un changement censé ne rien modifier :

```bash
uv run afac-preprocess run --input "data/input_files/.../<doc>.pdf"
python tools/compare_outputs.py tests/reference data/output_files_preprocessing
```

Un échec `STRICT` est bloquant. Les artefacts VLM sont comparés
structurellement (présence, sections, schéma) — leur texte change à chaque run,
c'est attendu.
