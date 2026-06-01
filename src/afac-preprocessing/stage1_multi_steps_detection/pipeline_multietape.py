# Documentation utilisée :
# https://docling-project.github.io/docling/_generated/examples/custom_convert/
import sys
import json
import time
import os
import json
import time
import logging
from pathlib import Path
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    TableStructureOptions,
    EasyOcrOptions,  # explicit EasyOCR
)
from docling.document_converter import DocumentConverter, PdfFormatOption

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_vlm_config

config = load_vlm_config()
CA_PATH = config["CA_PATH"]
VLM_URL = config["VLM_URL"]
VLM_MODEL_NAME = config["VLM_MODEL_NAME"]
_log = logging.getLogger(__name__)

def main():

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

    # Export results
    output_dir = project_root / "data" / "output_files" / "stage1_test" / DOC_NAME
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