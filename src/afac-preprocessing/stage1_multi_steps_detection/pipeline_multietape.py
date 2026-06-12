"""
Stage 1 - Multi-étapes de détection : Pipeline de conversion de documents avec Docling
Script 1 : pipeline_multietape.py

Documentation utilisée :
https://docling-project.github.io/docling/_generated/examples/custom_convert/

Il s'agit du premier script du pipeline de conversion d'un document source, par exemple un PDF, 
en différents formats exportables (JSON, Markdown, texte brut, DocTags). 
Ce script utilise la bibliothèque Docling pour effectuer la conversion et l'extraction de la structure du document.
"""
import sys
import json
import time
import os
import logging
from pathlib import Path
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    TableStructureOptions,
    EasyOcrOptions,  # Choix de l'ocr ici : EasyOCR
)
from docling.document_converter import DocumentConverter, PdfFormatOption

# Appel des fonctions de configuration pour récupérer les chemins et paramètres nécessaires
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

_log = logging.getLogger(__name__)


def main():
    """
    Pipeline de conversion d'un document PDF en différents formats exportables (JSON, Markdown, texte brut, DocTags).
     - Utilise Docling pour la conversion et l'extraction de la structure du document.
     - Configure les options de pipeline pour inclure l'OCR (via EasyOCR) et la détection de la structure des tableaux.
     - Mesure le temps de conversion et exporte les résultats dans un dossier de sortie organisé par nom de document.
     - Les formats d'export incluent : JSON, Markdown, texte brut et DocTags.
    """
    # Récupère le nom du document à traiter depuis les variables d'environnement
    DOC_NAME = os.environ.get("DOC_NAME", "")
    project_root = Path(__file__).resolve().parent.parent
    input_doc_path = project_root / "data" / "input_files" / f"{DOC_NAME}.pdf"

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True
    pipeline_options.do_table_structure = True
    pipeline_options.table_structure_options = TableStructureOptions(do_cell_matching=True)
    pipeline_options.ocr_options = EasyOcrOptions(lang=["fr"])
    pipeline_options.accelerator_options = AcceleratorOptions(
        num_threads=4, device=AcceleratorDevice.CUDA
    )

    doc_converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )

    start_time = time.time()
    conv_result = doc_converter.convert(input_doc_path)
    _log.info(f"Document converti en {time.time() - start_time:.2f}s.")

    output_dir = project_root / "data" / "output_files" / "stage1_test" / DOC_NAME
    output_dir.mkdir(parents=True, exist_ok=True)
    doc_filename = conv_result.input.file.stem

    # Docling pour le json
    with (output_dir / f"{doc_filename}.json").open("w", encoding="utf-8") as fp:
        fp.write(json.dumps(conv_result.document.export_to_dict()))
        print(f"Document JSON exporté : {output_dir / f'{doc_filename}.json'}")

    # Docling pour le texte brut
    with (output_dir / f"{doc_filename}.txt").open("w", encoding="utf-8") as fp:
        fp.write(conv_result.document.export_to_markdown())
        print(f"Document texte exporté : {output_dir / f'{doc_filename}.txt'}")

    # Docling pour le markdown
    with (output_dir / f"{doc_filename}.md").open("w", encoding="utf-8") as fp:
        fp.write(conv_result.document.export_to_markdown())
        print(f"Document Markdown exporté : {output_dir / f'{doc_filename}.md'}")

    # Docling pour le DocTags
    with (output_dir / f"{doc_filename}.doctags").open("w", encoding="utf-8") as fp:
        fp.write(conv_result.document.export_to_doctags())
        print(f"DocTags exporté : {output_dir / f'{doc_filename}.doctags'}")


if __name__ == "__main__":
    main()