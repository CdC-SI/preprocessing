# Documentation utilisée :
# https://docling-project.github.io/docling/_generated/examples/custom_convert/

import pandas as pd
import json
import time
import os
import json
import time
import logging
import certifi
from pathlib import Path
from dotenv import load_dotenv

from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    TableStructureOptions,
    EasyOcrOptions,  # explicit EasyOCR
)
from docling.document_converter import DocumentConverter, PdfFormatOption

_log = logging.getLogger(__name__)

# Chargement de .env.test
dotenv_path = Path(__file__).resolve().parent.parent / ".env.test"
print("Loading dotenv from:", dotenv_path.resolve(), "exists:", dotenv_path.exists())
load_dotenv(dotenv_path=dotenv_path)

# Certificat CA
custom_ca = os.environ.get("VLM_CA_PEM")
if custom_ca:
    os.environ.setdefault("SSL_CERT_FILE", custom_ca)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", custom_ca)
else:
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

VLM_URL = os.environ.get("VLM_URL", "")
VLM_MODEL_NAME = os.environ.get("VLM_MODEL_NAME", "")
if not VLM_URL:
    raise RuntimeError(
        f"VLM_URL not set. Ensure {dotenv_path} exists and contains VLM_URL."
    )
print(f"VLM_URL: {VLM_URL}, \nVLM_MODEL_NAME: {VLM_MODEL_NAME}")

def main():
    logging.basicConfig(level=logging.INFO)

    DOC_NAME = os.environ.get("DOC_NAME", "")
    project_root = Path(__file__).resolve().parent.parent
    data_folder = project_root / "data" / "input_files"
    input_doc_path = data_folder / f"{DOC_NAME}.pdf"

    # OCR options: force line break preservation
    ocr_options = EasyOcrOptions(lang=["fr"])

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True
    pipeline_options.do_table_structure = True
    pipeline_options.table_structure_options = TableStructureOptions(
        do_cell_matching=True
    )
    pipeline_options.ocr_options = ocr_options
    pipeline_options.accelerator_options = AcceleratorOptions(
        num_threads=4, device=AcceleratorDevice.CUDA
    )

    doc_converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )

    start_time = time.time()
    conv_result = doc_converter.convert(input_doc_path)
    end_time = time.time() - start_time

    _log.info(f"Document converted in {end_time:.2f} seconds.")

    ## Export results
    output_dir = project_root / "data" / "output_files" / "stage1_test" / DOC_NAME # CHANGER CHEMIN POUR CHAQUE TEST
    output_dir.mkdir(parents=True, exist_ok=True)

    doc_filename = conv_result.input.file.stem

    # Export Docling document JSON format:
    with (output_dir / f"{doc_filename}.json").open("w", encoding="utf-8") as fp:
        fp.write(json.dumps(conv_result.document.export_to_dict()))

    # Export Text format (plain text via Markdown export):
    with (output_dir / f"{doc_filename}.txt").open("w", encoding="utf-8") as fp:
        fp.write(conv_result.document.export_to_markdown(strict_text=True))

    # Export Markdown format:
    with (output_dir / f"{doc_filename}.md").open("w", encoding="utf-8") as fp:
        fp.write(conv_result.document.export_to_markdown())

    # Export Document Tags format:
    with (output_dir / f"{doc_filename}.doctags").open("w", encoding="utf-8") as fp:
        fp.write(conv_result.document.export_to_doctags())

if __name__ == "__main__":
    main()