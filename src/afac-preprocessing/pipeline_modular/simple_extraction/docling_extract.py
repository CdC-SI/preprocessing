"""
Pipeline unifié Docling — OCR + export formats + export tables en une seule conversion.

Usage :
    uv run python docling_extract.py --input doc.pdf [options]

Remplace pipeline_multietape.py (stage1) + export_table_docling.py (stage2) :
un seul appel DocumentConverter.convert() produit tous les formats demandés.
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd

from utils.paths import project_root, resolve_doc_name, resolve_input_pdf
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
    extract_images: bool = False,
    images_scale: float = 2.0,
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
    :param extract_images: Active generate_picture_images pour exporter les PNGs via Docling.
    :type extract_images: bool
    :param images_scale: Facteur d'échelle pour les images Docling (base 72 DPI). Ex: 2.08 ≈ 150 DPI.
    :type images_scale: float
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
    if extract_images:
        opts.generate_picture_images = True
        opts.images_scale = images_scale
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def export_docling_images(conv_result, output_dir: Path) -> int:
    """Sauvegarde les images extraites par Docling (pil_image) en PNG, nommées par leurs
    coordonnées doctags (x0,y0,x1,y1) via pic.get_location_tokens(doc) — identiques à celles
    du <picture> tag correspondant dans l'export doctags. Nommer par coordonnées plutôt que
    par index de position évite un désalignement si reordered_doctags.py change
    ensuite l'ordre relatif des images sur une page (son rôle même) : un matching par index
    de position dans le doctags réordonné ne pointerait alors plus vers le bon fichier."""
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = conv_result.document
    saved = 0
    for i, pic in enumerate(doc.pictures, start=1):
        img = getattr(pic, "image", None)
        pil = getattr(img, "pil_image", None) if img else None
        if pil is None:
            _log.warning("[docling] Image %d : pil_image absent (generate_picture_images activé ?)", i)
            continue
        if not pic.prov:
            _log.warning("[docling] Image %d : provenance absente — export ignoré", i)
            continue
        page = pic.prov[0].page_no
        # get_location_tokens() concatène 4 <loc_*> par entrée de prov — un élément qui
        # franchit un saut de page en a plusieurs. On ne garde que les 4 premiers (prov[0],
        # cohérent avec `page` ci-dessus) pour rester robuste aux images multi-provenance.
        x0, y0, x1, y1 = re.findall(r"<loc_(\d+)>", pic.get_location_tokens(doc))[:4]
        path = output_dir / f"pic_page{page}_x{x0}_y{y0}_x{x1}_y{y1}.png"
        pil.save(str(path))
        _log.info("[docling] Exporté : %s", path.name)
        saved += 1
    _log.info("%d image(s) exportée(s) via Docling → %s", saved, output_dir)
    return saved


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
    stem = conv_result.input.file.stem.strip()
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
    Extrait et exporte les tables détectées dans le document Docling vers les formats demandés
    (csv, html). Nommées <stem>-table-{i:02d}_page{page}_x{x0}_y{y0}_x{x1}_y{y1} — les
    coordonnées (via table.get_location_tokens(doc), identiques à celles du <otsl> tag
    correspondant dans le doctags) permettent à load_jsonline_doctags.py de matcher
    chaque JSONL au bon <otsl> même si reordered_doctags.py a changé l'ordre relatif
    des tables sur une page. L'index {i:02d} n'est là que pour la lisibilité humaine du
    dossier — jamais utilisé pour le matching en aval.

    :param conv_result: Description
    :param output_dir: Description
    :type output_dir: Path
    :param formats: Description
    :type formats: frozenset[str]
    """
    stem = conv_result.input.file.stem.strip()
    doc = conv_result.document
    tables = doc.tables

    if not tables:
        _log.info("Aucune table détectée dans le document.")
        return

    tables_dir = output_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    for i, table in enumerate(tables, start=1):
        if not table.prov:
            _log.warning("Table %d : provenance absente — export ignoré", i)
            continue
        page = table.prov[0].page_no
        # cf. export_docling_images() : ne garder que les 4 premiers <loc_*> (prov[0]) pour
        # rester robuste aux tables dont les prov multiples (saut de page) en produisent plus.
        x0, y0, x1, y1 = re.findall(r"<loc_(\d+)>", table.get_location_tokens(doc))[:4]
        base_name = f"{stem}-table-{i:02d}_page{page}_x{x0}_y{y0}_x{x1}_y{y1}"

        if "csv" in formats:
            df: pd.DataFrame = table.export_to_dataframe(doc=doc)
            path = tables_dir / f"{base_name}.csv"
            df.to_csv(path, index=False)
            _log.info("Table %d CSV : %s", i, path)

        if "html" in formats:
            path = tables_dir / f"{base_name}.html"
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
            "  uv run python docling_extract.py --input doc.pdf\n"
            "  uv run python docling_extract.py --input doc.pdf "
            "--formats doctags json\n"
            "  uv run python docling_extract.py --input doc.pdf "
            "--formats doctags --no-tables\n"
            "  uv run python docling_extract.py --input doc.pdf "
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
            "Défaut : data/output_files_preprocessing/<nom_du_doc>/ (relatif à la racine du projet)."
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
    parser.add_argument(
        "--extract-images",
        action="store_true",
        default=False,
        help="Active generate_picture_images pour exporter les PNGs Docling (utilisés par description_image_context.py). Défaut : désactivé.",
    )
    parser.add_argument(
        "--images-scale",
        type=float,
        default=2.08, # environ 150 DPI (base 72 DPI) https://docling-project.github.io/docling/reference/pipeline_options/#docling.datamodel.pipeline_options.KserveV2OcrOptions.model_name
        metavar="F",
        help="Facteur d'échelle Docling pour les images (base 72 DPI). Ex: 2.08≈150dpi, 4.17≈300dpi. Défaut : 2.08.",
    )
    parser.add_argument(
        "--images-dir",
        type=Path,
        default=None,
        metavar="DOSSIER",
        help="Dossier de sortie pour les PNGs Docling. Défaut : used_images/ dans le dossier de sortie.",
    )
    return parser.parse_args()


# Résolution des chemins
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
    doc_name = resolve_doc_name(args, primary_flag="--input")
    return resolve_input_pdf(doc_name)


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
    return project_root() / "data" / "output_files_preprocessing" / input_path.stem.strip()


# Point d'entrée
def main() -> None:
    # Importer build_converter depuis un autre script ne configure plus le logging global silencieusement.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    args = parse_args()

    input_path = resolve_input(args)  # charge le dotenv si --dotenv fourni
    if not input_path.exists():
        raise SystemExit(f"Erreur : fichier PDF introuvable — {input_path}")

    output_dir = resolve_output(args, input_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    text_fmts = frozenset(args.formats)
    do_table_structure = not args.no_tables
    do_table_export = not args.no_tables

    # --extract-images ou ENABLE_IMAGE_EXTRACTION=true dans le .env
    extract_images = args.extract_images or os.environ.get("ENABLE_IMAGE_EXTRACTION", "false").strip().lower() == "true"

    converter = build_converter(
        ocr=not args.no_ocr,
        lang=args.lang,
        tables=do_table_structure,
        threads=args.threads,
        device=DEVICE_MAP[args.device],
        extract_images=extract_images,
        images_scale=args.images_scale,
    )

    _log.info("Conversion de : %s", input_path)
    t0 = time.time()
    conv_result = converter.convert(input_path)
    _log.info("Conversion terminée en %.2fs.", time.time() - t0)

    if text_fmts:
        export_text_formats(conv_result, output_dir, text_fmts)

    if do_table_export:
        export_tables(conv_result, output_dir, TABLE_FORMATS)

    if extract_images:
        images_dir = args.images_dir.resolve() if args.images_dir else output_dir / "used_images"
        export_docling_images(conv_result, images_dir)

    _log.info("Résultats dans : %s", output_dir)
    sys.exit(0)  # Exit code explicite pour Tekton


if __name__ == "__main__":
    main()
