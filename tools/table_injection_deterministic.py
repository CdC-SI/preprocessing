"""
table_injection_deterministic.py — Harnais de comparaison pour les variantes
corrigees des etapes 04 (csv-to-jsonlines) et 05 (load-jsonline-doctags).

La logique corrigee vit dans `src/afac_preprocessing/steps/table_injection_fixed.py`
(une etape de pipeline appartient a `src/`, pas a `tools/`). Ce script ne fait que
la rejouer a cote des sorties existantes et comparer, sans rien ecraser.

Ce qu'il mesure — le defaut corrige n'est pas une *perte* de contenu mais une
*substitution* : sur `TE - Revenu determinant`, le document contient deux fois les
donnees de `table-04`, dont une a la place de `table-03`. Voir le docstring de
`table_injection_fixed.py` pour la chaine de causalite complete.

⚠ Les JSONL sont ecrits dans `tables_det/`, jamais dans `tables/` : l'etape 05 de
production fait un `glob("*.jsonl")` sur `tables/`, et y deposer des fichiers
supplementaires corromprait le vrai pipeline. Le doctags sort en
`<doc>_reordered_with_tables_det.doctags`, a cote de la sortie actuelle.

Usage :
    uv run python tools/table_injection_deterministic.py \\
        --doc "afac/Taxation/TE/TE - Revenu déterminant"

    # Sur tout le corpus (tous les documents qui ont des tables) :
    uv run python tools/table_injection_deterministic.py

Pour executer reellement le pipeline avec ces etapes, ce script n'est pas
necessaire : la CLI porte un drapeau opt-in.

    uv run afac-preprocess run --input <pdf> --from-step reorder-doctags --fixed-tables
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from pathlib import Path

from afac_preprocessing.steps.load_jsonline_doctags import (
    _find_otsl_blocks,
    _parse_table_coords,
    jsonl_rows_to_block,
)
from afac_preprocessing.steps.table_injection_fixed import (
    CSV_GLOB,
    JSONL_GLOB,
    OtslTableInjector,
    Table,
    TableJsonlBuilder,
)

_log = logging.getLogger(__name__)


@dataclass
class DocTableReport:
    """Bilan d'integrite des tables pour un document."""

    doc_ref: str
    error: str | None = None
    csv_count: int = 0
    tables_built: int = 0
    tables_built_by_pipeline: int = 0
    otsl_blocks: int = 0
    replaced: int = 0
    preserved: int = 0
    unused_tables: list[str] = field(default_factory=list)
    duplicated_tables: list[str] = field(default_factory=list)
    output_doctags: Path | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.duplicated_tables and not self.unused_tables

    def format_line(self) -> str:
        if self.error is not None:
            return f"[{self.doc_ref}] {self.error}"
        status = "OK" if self.ok else "ATTENTION"
        recovered = self.tables_built - self.tables_built_by_pipeline
        gain = f", +{recovered} table(s) recuperee(s)" if recovered else ""
        return (
            f"[{self.doc_ref}] {status} — {self.csv_count} CSV, "
            f"{self.tables_built} table(s) construite(s){gain}, "
            f"{self.otsl_blocks} bloc(s) <otsl> : "
            f"{self.replaced} remplace(s), {self.preserved} conserve(s)"
        )


class DeterministicTableInjectionTester:
    """Rejoue les etapes 04-05 corrigees sur des documents deja traites, et
    compare le resultat aux sorties actuelles du pipeline.

    N'ecrit que des fichiers nouveaux : `tables_det/` pour les JSONL et
    `<doc>_reordered_with_tables_det.doctags` pour le doctags enrichi.
    """

    def __init__(
        self,
        stage5_dir: Path,
        builder: TableJsonlBuilder | None = None,
        injector: OtslTableInjector | None = None,
    ) -> None:
        self.stage5_dir = stage5_dir
        self.builder = builder or TableJsonlBuilder()
        self.injector = injector or OtslTableInjector()

    def discover_docs(self) -> list[str]:
        """Documents deja traites qui possedent un dossier `tables/`."""
        return sorted(
            str(p.relative_to(self.stage5_dir))
            for p in self.stage5_dir.rglob("*")
            if p.is_dir() and (p / "tables").is_dir() and any((p / "tables").glob(CSV_GLOB))
        )

    def _build_tables(self, tables_dir: Path, output_dir: Path) -> list[Table]:
        """Construit les tables depuis les CSV et ecrit les JSONL de la variante."""
        tables: list[Table] = []
        for csv_path in sorted(tables_dir.glob(CSV_GLOB)):
            rows = self.builder.build_rows(csv_path)
            if not rows:
                continue
            jsonl_path = output_dir / csv_path.with_suffix(".jsonl").name
            jsonl_path.write_text(jsonl_rows_to_block(rows) + "\n", encoding="utf-8")
            tables.append(
                Table(
                    name=csv_path.name,
                    coords=_parse_table_coords(csv_path.name),
                    rows=rows,
                )
            )
        return tables

    @staticmethod
    def _count_duplicates(doctags: str, tables: list[Table]) -> list[str]:
        """Tables dont le contenu apparait plus souvent que de raison dans le
        doctags — la signature exacte de la corruption recherchee, ou une table
        injectee a la place d'une autre y figure deux fois.

        Deux precautions, toutes deux tirees de faux positifs constates :

        - la signature est le bloc **complet** de la table, pas sa premiere
          ligne : une table peut legitimement contenir deux lignes identiques
          (`Tableau cas de sortie`, 5 lignes dont 4 distinctes) ;
        - le seuil est le **nombre de tables partageant ce contenu**, pas 1 :
          un document peut legitimement repeter la meme table a deux endroits
          (`Analyse des inscriptions`, le meme bareme en page 21 et en page 24,
          avec des bbox distinctes).
        """
        expected: dict[str, list[str]] = {}
        for table in tables:
            signature = jsonl_rows_to_block(table.rows)
            if signature:
                expected.setdefault(signature, []).append(table.name)

        duplicated: list[str] = []
        for signature, names in expected.items():
            if doctags.count(signature) > len(names):
                duplicated.extend(names)
        return duplicated

    def process_doc(self, doc_ref: str) -> DocTableReport:
        """Traite un document, en isolant tout echec inattendu dans le rapport."""
        try:
            return self._process_doc(doc_ref)
        except Exception as exc:  # noqa: BLE001 — outil de lot : aucun echec n'interrompt le run
            _log.exception("Echec inattendu sur %s", doc_ref)
            return DocTableReport(
                doc_ref=doc_ref, error=f"echec inattendu — {type(exc).__name__}: {exc}"
            )

    def _process_doc(self, doc_ref: str) -> DocTableReport:
        report = DocTableReport(doc_ref=doc_ref)
        doc_dir = self.stage5_dir / doc_ref
        doc_name = Path(doc_ref).name

        reordered = doc_dir / f"{doc_name}_reordered.doctags"
        tables_dir = doc_dir / "tables"

        if not reordered.exists():
            report.error = "pas de _reordered.doctags"
            return report

        report.csv_count = len(list(tables_dir.glob(CSV_GLOB)))
        report.tables_built_by_pipeline = len(
            [p for p in tables_dir.glob(JSONL_GLOB) if p.stat().st_size > 0]
        )

        output_dir = doc_dir / "tables_det"
        output_dir.mkdir(exist_ok=True)
        tables = self._build_tables(tables_dir, output_dir)
        report.tables_built = len(tables)

        doctags = reordered.read_text(encoding="utf-8")
        report.otsl_blocks = len(_find_otsl_blocks(doctags))

        outcome = self.injector.inject(doctags, tables)
        report.replaced = len(outcome.replaced)
        report.preserved = len(outcome.preserved)
        report.unused_tables = outcome.unused_tables
        report.duplicated_tables = self._count_duplicates(outcome.doctags, tables)

        out_path = doc_dir / f"{doc_name}_reordered_with_tables_det.doctags"
        out_path.write_text(outcome.doctags, encoding="utf-8")
        report.output_doctags = out_path
        return report

    def run(self, doc_refs: list[str] | None = None) -> list[DocTableReport]:
        refs = doc_refs if doc_refs is not None else self.discover_docs()
        return [self.process_doc(doc_ref) for doc_ref in refs]

    @staticmethod
    def print_summary(reports: list[DocTableReport]) -> None:
        for report in reports:
            if report.error is None and report.ok and report.preserved == 0:
                continue  # cas nominal : rien a signaler
            print(report.format_line())
            for name in report.duplicated_tables:
                print(f"    CORRUPTION : contenu de {name} present plusieurs fois")
            for name in report.unused_tables:
                print(f"    table construite mais jamais injectee : {name}")

        processed = [report for report in reports if report.error is None]
        recovered = sum(
            max(0, report.tables_built - report.tables_built_by_pipeline) for report in processed
        )
        corrupted = sum(1 for report in processed if report.duplicated_tables)
        print(
            f"\n{len(processed)} document(s) traite(s), "
            f"{recovered} table(s) recuperee(s), {corrupted} corruption(s) restante(s)."
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare les variantes corrigees des etapes 04-05 aux sorties actuelles, "
            "sans rien ecraser. Pour executer reellement le pipeline avec ces etapes, "
            "utiliser : afac-preprocess run --fixed-tables"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--stage5", type=Path, default=Path("data/output_files_preprocessing"))
    parser.add_argument(
        "--doc",
        type=str,
        default=None,
        help="Un seul document (chemin relatif a --stage5, sans extension).",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=args.log_level, format="%(levelname)s %(message)s")

    tester = DeterministicTableInjectionTester(stage5_dir=args.stage5.resolve())
    doc_refs = [args.doc] if args.doc else tester.discover_docs()
    if not doc_refs:
        raise SystemExit(f"Aucun document avec des tables trouve sous {tester.stage5_dir}")

    tester.print_summary(tester.run(doc_refs))


if __name__ == "__main__":
    main()
