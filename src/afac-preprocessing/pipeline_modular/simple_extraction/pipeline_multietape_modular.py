"""
Pipeline unifié Docling — OCR + export formats + export tables en une seule conversion.

Usage :
    uv run python pipeline_multietape_modulaire.py --input doc.pdf [options]

Remplace pipeline_multietape.py (stage1) + export_table_docling.py (stage2) :
un seul appel DocumentConverter.convert() produit tous les formats demandés.
"""
import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

import pandas as pd
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    EasyOcrOptions,
    PdfPipelineOptions,
    TableStructureOptions,
)
from docling.document_converter import ConversionResult, DocumentConverter, PdfFormatOption

_log = logging.getLogger(__name__)

TEXT_FORMATS = frozenset({"json", "md", "txt", "doctags"})
TABLE_FORMATS = frozenset({"csv", "html"})

DEVICE_MAP: dict[str, AcceleratorDevice] = {
    "cuda": AcceleratorDevice.CUDA,
    "cpu": AcceleratorDevice.CPU,
    # "mps": AcceleratorDevice.MPS, # MPS (Apple Silicon)
}


# Construction du convertisseur, pipeline docling avec options configurables
def build_converter(
    ocr: bool,
    lang: list[str],
    tables: bool,
    threads: int,
    device: AcceleratorDevice,
) -> DocumentConverter:
    """
    Docstring for build_converter
    Paramétrage du convertisseur Docling avec options OCR, tables, threads et device.

    :param ocr: Description
    :type ocr: bool
    :param lang: Description
    :type lang: list[str]
    :param tables: Description
    :type tables: bool
    :param threads: Description
    :type threads: int
    :param device: Description
    :type device: AcceleratorDevice
    :return: Description
    :rtype: DocumentConverter
    """
    opts = PdfPipelineOptions()
    opts.do_ocr = ocr
    opts.do_table_structure = tables
    if tables:
        opts.table_structure_options = TableStructureOptions(do_cell_matching=True)
    if ocr:
        opts.ocr_options = EasyOcrOptions(lang=lang)
    opts.accelerator_options = AcceleratorOptions(num_threads=threads, device=device)
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


# Export formats texte, ancienne fonction pipeline_multietape.py (stage 1)
def export_text_formats(conv_result: ConversionResult, output_dir: Path, formats: frozenset[str]) -> None:
    """
    Docstring for export_text_formats
    Exporte les formats texte demandés (json, md, txt, doctags) à partir du résultat de conversion Docling.

    :param conv_result: Description
    :param output_dir: Description
    :type output_dir: Path
    :param formats: Description
    :type formats: frozenset[str]
    """
    stem = conv_result.input.file.stem
    doc = conv_result.document

    if "json" in formats:
        path = output_dir / f"{stem}.json"
        path.write_text(json.dumps(doc.export_to_dict(), ensure_ascii=False), encoding="utf-8")
        _log.info("JSON exporté : %s", path)

    if "md" in formats:
        path = output_dir / f"{stem}.md"
        path.write_text(doc.export_to_markdown(), encoding="utf-8")
        _log.info("Markdown exporté : %s", path)

    if "txt" in formats:
        path = output_dir / f"{stem}.txt"
        path.write_text(doc.export_to_text(), encoding="utf-8")
        _log.info("Texte brut exporté : %s", path)

    if "doctags" in formats:
        path = output_dir / f"{stem}.doctags"
        path.write_text(doc.export_to_doctags(), encoding="utf-8")
        _log.info("DocTags exporté : %s", path)


# Export tables, ancienne fonction export_table_docling.py (stage 2)
def export_tables(conv_result: ConversionResult, output_dir: Path, formats: frozenset[str]) -> None:
    """
    Docstring for export_tables
    Extrait et exporte les tables détectées dans le document Docling vers les formats demandés (csv, html).

    :param conv_result: Description
    :param output_dir: Description
    :type output_dir: Path
    :param formats: Description
    :type formats: frozenset[str]
    """
    stem = conv_result.input.file.stem
    doc = conv_result.document
    tables = doc.tables

    if not tables:
        _log.info("Aucune table détectée dans le document.")
        return

    tables_dir = output_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    for i, table in enumerate(tables, start=1):
        if "csv" in formats:
            df: pd.DataFrame = table.export_to_dataframe(doc=doc)
            path = tables_dir / f"{stem}-table-{i}.csv"
            df.to_csv(path, index=False)
            _log.info("Table %d CSV : %s", i, path)

        if "html" in formats:
            path = tables_dir / f"{stem}-table-{i}.html"
            path.write_text(table.export_to_html(doc=doc), encoding="utf-8")
            _log.info("Table %d HTML : %s", i, path)

    _log.info("%d table(s) exportée(s) dans %s", len(tables), tables_dir)



# CLI (voir README pour les exemples d'utilisation)
def parse_args() -> argparse.Namespace:
    """
    Docstring for parse_args
    Crée un parser d'arguments pour la ligne de commande, 
    permettant de spécifier le fichier d'entrée, 
    le répertoire de sortie, 
    les formats à exporter, 
    les langues OCR, 
    le nombre de threads et l'accélérateur matériel.

    :return: Description
    :rtype: Namespace
    """
    parser = argparse.ArgumentParser(
        description=(
            "Pipeline Docling : OCR + export formats texte + tables "
            "(une seule conversion pour tous les formats)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  uv run python pipeline_multietape_modulaire.py --input doc.pdf\n"
            "  uv run python pipeline_multietape_modulaire.py --input doc.pdf "
            "--formats doctags json\n"
            "  uv run python pipeline_multietape_modulaire.py --input doc.pdf "
            "--formats doctags --no-tables\n"
            "  uv run python pipeline_multietape_modulaire.py --input doc.pdf "
            "--no-ocr --formats json\n"
        ),
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=None,
        help=(
            "Chemin vers le PDF à traiter. "
            "Si absent, lit DOC_NAME depuis l'environnement et résout "
            "data/input_files/<DOC_NAME>.pdf."
        ),
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=None,
        help=(
            "Dossier de sortie. "
            "Défaut : data/output_files/<nom_du_doc>/ (relatif à la racine du projet)."
        ),
    )
    parser.add_argument(
        "--formats", "-f",
        nargs="+",
        default=sorted(TEXT_FORMATS),
        choices=sorted(TEXT_FORMATS),
        metavar="FORMAT",
        help=(
            f"Formats texte à exporter parmi : {sorted(TEXT_FORMATS)}. "
            "Défaut : tous (doctags json md txt). "
            "L'extraction des tables (csv, html) est contrôlée séparément par --no-tables."
        ),
    )
    parser.add_argument(
        "--lang", "-l",
        nargs="+",
        default=["fr"],
        metavar="LANG",
        help="Code(s) de langue EasyOCR (ex. : fr en). Défaut : fr.",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="Désactiver l'OCR (EasyOCR). Utile si le PDF contient du texte natif.",
    )
    parser.add_argument(
        "--no-tables",
        action="store_true",
        help=(
            "Désactiver l'extraction des tableaux (csv + html). "
            "Désactive aussi la détection de structure Docling pour accélérer la conversion."
        ),
    )
    parser.add_argument(
        "--threads", "-t",
        type=int,
        default=4,
        metavar="N",
        help="Nombre de threads CPU alloués à Docling. Défaut : 4.",
    )
    parser.add_argument(
        "--device",
        choices=sorted(DEVICE_MAP),
        default="cuda",
        help="Accélérateur matériel. Défaut : cuda.",
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FICHIER",
        help=(
            "Chemin vers un fichier .env à charger avant la résolution de DOC_NAME "
            "(ex. : .env.test, .env). Ignoré si --input est fourni."
        ),
    )
    return parser.parse_args()


# Résolution des chemins
def _project_root() -> Path:
    """
    Docstring for _project_root
    Root du projet, utilisé pour résoudre les chemins d'entrée et de sortie relatifs.

    :return: Description
    :rtype: Path
    """
    return Path(__file__).resolve().parent.parent.parent  # Correspond à preprocessing/src/afac-preprocessing


def resolve_input(args: argparse.Namespace) -> Path:
    """
    Docstring for resolve_input
    Vérifie et résout le chemin du fichier PDF d'entrée à partir des arguments de la ligne de commande.
    Si --input est fourni, il est utilisé directement. Sinon, DOC_NAME est lu depuis l'environnement (ou depuis un fichier .env si --dotenv est fourni)

    :param args: Description
    :type args: argparse.Namespace
    :return: Description
    :rtype: Path
    """
    if args.input:
        return args.input.resolve()
    if args.dotenv:
        dotenv_path = args.dotenv.resolve()
        if not dotenv_path.exists():
            raise SystemExit(f"Erreur : fichier .env introuvable — {dotenv_path}")
        load_dotenv(dotenv_path=dotenv_path)
        _log.info("Environnement chargé depuis : %s", dotenv_path)
    doc_name = os.environ.get("DOC_NAME", "").strip()
    if not doc_name:
        raise SystemExit(
            "Erreur : fournir --input <chemin>, --dotenv <fichier> avec DOC_NAME, "
            "ou définir la variable DOC_NAME dans l'environnement."
        )
    return _project_root() / "data" / "input_files" / f"{doc_name}.pdf"


def resolve_output(args: argparse.Namespace, input_path: Path) -> Path:
    """
    Docstring for resolve_output
    
    
    :param args: Description
    :type args: argparse.Namespace
    :param input_path: Description
    :type input_path: Path
    :return: Description
    :rtype: Path
    """
    if args.output_dir:
        return args.output_dir.resolve()
    return _project_root() / "data" / "output_files" / input_path.stem


# Point d'entrée
def main() -> None:
    # Importer build_converter depuis un autre script ne configure plus le logging global silencieusement.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    args = parse_args()

    input_path = resolve_input(args)
    if not input_path.exists():
        raise SystemExit(f"Erreur : fichier PDF introuvable — {input_path}")

    output_dir = resolve_output(args, input_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    text_fmts = frozenset(args.formats)
    do_table_structure = not args.no_tables
    do_table_export = not args.no_tables

    converter = build_converter(
        ocr=not args.no_ocr,
        lang=args.lang,
        tables=do_table_structure,
        threads=args.threads,
        device=DEVICE_MAP[args.device],
    )

    _log.info("Conversion de : %s", input_path)
    t0 = time.time()
    conv_result = converter.convert(input_path)
    _log.info("Conversion terminée en %.2fs.", time.time() - t0)

    if text_fmts:
        export_text_formats(conv_result, output_dir, text_fmts)

    if do_table_export:
        export_tables(conv_result, output_dir, TABLE_FORMATS)

    _log.info("Résultats dans : %s", output_dir)
    sys.exit(0)  # Exit code explicite pour Tekton


if __name__ == "__main__":
    main()
