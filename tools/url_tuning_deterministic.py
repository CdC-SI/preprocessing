"""
url_tuning_deterministic.py — Harnais de comparaison pour la variante
deterministe (sans VLM) de l'etape 08 (url-tuning).

La logique reutilisable vit dans
`src/afac_preprocessing/steps/url_tuning_fixed.py` (une etape de pipeline
appartient a `src/`, pas a `tools/`). Ce script ne fait que la rejouer a
cote des sorties existantes et comparer, sans rien ecraser.

Contexte : url-tuning (etape 08) rend chaque page en image et demande a un
VLM de retrouver visuellement ou placer chaque lien dans le doctags. Or
url-extraction (etape 07, sans VLM) a deja calcule, pour chaque lien, sa
page, son texte-ancre exact et son URI par matching spatial PyMuPDF — cf.
get_link_text() dans url_extraction.py. Voir le docstring de
url_tuning_fixed.py pour le detail des bugs traces (balises cassees, liens
perdus) sur "TN - Fortune et revenu acquis sous forme de rente".

⚠ Les sorties de ce script (`_url_det.doctags`, `_url_det.md`) sont ecrites
a cote de l'existant, jamais a sa place. Pour executer reellement le
pipeline avec cette etape, ce script n'est pas necessaire :

    uv run afac-preprocess run --input <pdf> --from-step url-tuning --deterministic-urls

Usage :
    uv run python tools/url_tuning_deterministic.py --stage5 data/output_files_preprocessing \\
        --doc "afac/Taxation/TN/TN - Fortune et revenu acquis sous forme de rente"

    # Sur tout le corpus (tous les docs qui ont des liens) :
    uv run python tools/url_tuning_deterministic.py --stage5 data/output_files_preprocessing
"""
from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from afac_preprocessing.steps.markdown_convert import convert_doctags_to_markdown
from afac_preprocessing.steps.url_tuning import load_jsonl_links
from afac_preprocessing.steps.url_tuning_fixed import DeterministicLinkInjector
from afac_preprocessing.utils.markdown_utils import repair_escaped_link_destinations
from afac_preprocessing.utils.pdf_utils import pdf_page_count

_log = logging.getLogger(__name__)

#: `]` et `(` sont exclus au meme titre que `)` : quand le texte-ancre est
#: lui-meme une URL visible, le markdown produit `[https://a](https://a)` et
#: un pattern qui accepte ces caracteres deborde a travers le `](` en
#: fabriquant une URL parasite qui n'existe nulle part.
URL_PATTERN = re.compile(r'(?:https?://|mailto:)[^\s")(\[\]<>]+')


@dataclass
class DocLinkReport:
    """Resultat de comparaison pour un document : liens attendus vs liens
    effectivement retrouves dans le markdown final."""

    doc_ref: str
    error: str | None = None
    pages: int = 0
    links_total: int = 0
    links_unmatched: int = 0
    unmatched_texts: list[str] = field(default_factory=list)
    expected_unique_urls: int = 0
    found_unique_urls: int = 0
    missing_urls: list[str] = field(default_factory=list)
    output_doctags: Path | None = None
    output_markdown: Path | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.missing_urls

    def format_line(self) -> str:
        if self.error is not None:
            return f"[{self.doc_ref}] {self.error}"
        status = "OK" if self.ok else "INCOMPLET"
        return (
            f"[{self.doc_ref}] {status} — {self.links_total} lien(s), "
            f"{self.links_unmatched} non-apparie(s), "
            f"{self.found_unique_urls}/{self.expected_unique_urls} URL(s) unique(s) "
            f"dans le markdown final"
        )


class DeterministicUrlTuningTester:
    """Orchestre l'injection deterministe sur un ou plusieurs documents deja
    traites par le pipeline, et compare le resultat aux liens attendus.

    N'ecrit que des fichiers nouveaux (suffixe _url_det), jamais les sorties
    existantes de url-tuning.
    """

    SOURCE_DOCTAGS_SUFFIXES = ("_reordered_with_tables_pictures.doctags", "_reordered_with_tables.doctags")

    def __init__(
        self, stage5_dir: Path, input_root: Path, injector: DeterministicLinkInjector | None = None
    ) -> None:
        self.stage5_dir = stage5_dir
        self.input_root = input_root
        self.injector = injector or DeterministicLinkInjector()

    def discover_docs(self) -> list[str]:
        """Chemins relatifs (a stage5_dir) de tous les documents deja traites
        qui possedent des liens extraits."""
        return sorted(
            str(p.relative_to(self.stage5_dir))
            for p in self.stage5_dir.rglob("*")
            if p.is_dir()
            and (p / f"{p.name}.doctags").exists()
            and any(p.glob("hyperlinks_data_*.jsonl"))
        )

    def _source_doctags(self, doc_dir: Path, doc_name: str) -> Path | None:
        """Meme ordre de preference que UrlTuningStep._source_doctags."""
        for suffix in self.SOURCE_DOCTAGS_SUFFIXES:
            candidate = doc_dir / f"{doc_name}{suffix}"
            if candidate.exists():
                return candidate
        return None

    def process_doc(self, doc_ref: str) -> DocLinkReport:
        """Traite un document, en isolant tout echec inattendu dans le rapport.

        Un document au doctags malforme ne doit pas faire tomber le lot :
        l'exception devient l'erreur du rapport, et les autres documents
        continuent d'etre traites.
        """
        try:
            return self._process_doc(doc_ref)
        except Exception as exc:  # noqa: BLE001 — outil de lot : aucun echec ne doit interrompre le run
            _log.exception("Echec inattendu sur %s", doc_ref)
            return DocLinkReport(doc_ref=doc_ref, error=f"echec inattendu — {type(exc).__name__}: {exc}")

    def _process_doc(self, doc_ref: str) -> DocLinkReport:
        """Injection deterministe + conversion Markdown de controle.
        Ecrit <doc>_url_det.doctags et _url_det.md."""
        report = DocLinkReport(doc_ref=doc_ref)
        doc_dir = self.stage5_dir / doc_ref
        doc_name = Path(doc_ref).name

        doctags_path = self._source_doctags(doc_dir, doc_name)
        jsonl_path = doc_dir / f"hyperlinks_data_{doc_name}.jsonl"
        pdf_path = self.input_root / f"{doc_ref}.pdf"

        if doctags_path is None:
            report.error = "pas de doctags source (_reordered_with_tables[_pictures].doctags absent)"
            return report
        if not jsonl_path.exists():
            report.error = "pas de hyperlinks_data_*.jsonl"
            return report

        links = load_jsonl_links(jsonl_path)
        if not links:
            report.error = "aucun lien pour ce document, ignore"
            return report

        n_pages = pdf_page_count(pdf_path) or len({link.get("page_number") for link in links})
        doctags = doctags_path.read_text(encoding="utf-8")
        new_doctags, unmatched = self.injector.inject_into_doctags(doctags, links, n_pages)

        out_doctags = doc_dir / f"{doc_name}_url_det.doctags"
        out_doctags.write_text(new_doctags, encoding="utf-8")

        markdown = convert_doctags_to_markdown(out_doctags, expected_pages=n_pages)
        markdown = repair_escaped_link_destinations(markdown)
        out_markdown = doc_dir / f"{doc_name}_url_det.md"
        out_markdown.write_text(markdown, encoding="utf-8")

        expected_urls = {link["hyperlink"] for link in links if link.get("hyperlink")}
        found_urls = set(URL_PATTERN.findall(markdown))

        report.pages = n_pages
        report.links_total = len(links)
        report.links_unmatched = len(unmatched)
        report.unmatched_texts = [link.get("text", "") for link in unmatched]
        report.expected_unique_urls = len(expected_urls)
        report.found_unique_urls = len(found_urls)
        report.missing_urls = sorted(expected_urls - found_urls)
        report.output_doctags = out_doctags
        report.output_markdown = out_markdown
        return report

    def run(self, doc_refs: list[str] | None = None) -> list[DocLinkReport]:
        refs = doc_refs if doc_refs is not None else self.discover_docs()
        return [self.process_doc(doc_ref) for doc_ref in refs]

    @staticmethod
    def print_summary(reports: list[DocLinkReport]) -> None:
        for report in reports:
            print(report.format_line())
            for text in report.unmatched_texts:
                print(f"    ancre introuvable : {text!r}")
            for url in report.missing_urls:
                print(f"    URL manquante : {url}")

        processed = [report for report in reports if report.error is None]
        n_ok = sum(report.ok for report in processed)
        print(f"\n{len(processed)} document(s) traite(s), {n_ok} avec toutes les URLs retrouvees.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Variante deterministe (sans VLM) de url-tuning : reinjecte les liens "
            "deja extraits par url-extraction via recherche de texte-ancre. Ecrit "
            "des fichiers _url_det.* separes, ne touche a rien d'existant."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--stage5", type=Path, default=Path("data/output_files_preprocessing"))
    parser.add_argument("--input-root", type=Path, default=Path("data/input_files"))
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

    tester = DeterministicUrlTuningTester(
        stage5_dir=args.stage5.resolve(),
        input_root=args.input_root.resolve(),
    )
    doc_refs = [args.doc] if args.doc else tester.discover_docs()
    if not doc_refs:
        raise SystemExit(f"Aucun document avec des liens trouve sous {tester.stage5_dir}")

    tester.print_summary(tester.run(doc_refs))


if __name__ == "__main__":
    main()
