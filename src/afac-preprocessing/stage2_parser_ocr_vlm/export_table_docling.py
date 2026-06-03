### https://docling-project.github.io/docling/examples/export_tables/
import logging
import time
from pathlib import Path
import os
import pandas as pd
from docling.document_converter import DocumentConverter
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_vlm_config
config = load_vlm_config()

_log = logging.getLogger(__name__)

def main():

    # Root
    DOC_NAME = os.environ.get("DOC_NAME", "") # CHANGER SELON LES TESTS
    project_root = Path(__file__).resolve().parent.parent
    data_folder = project_root / "data" / "input_files"
    input_doc_path = data_folder / f"{DOC_NAME}.pdf" # CHANGER CHEMIN POUR CHAQUE TEST
    output_dir = project_root / "data" / "output_files" / "stage2_test" / DOC_NAME / "tables" # CHANGER CHEMIN POUR CHAQUE TEST
    output_dir.mkdir(parents=True, exist_ok=True)
    doc_converter = DocumentConverter()

    start_time = time.time()
    conv_res = doc_converter.convert(input_doc_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    doc_filename = conv_res.input.file.stem

    # Export tables
    for table_ix, table in enumerate(conv_res.document.tables):
        table_df: pd.DataFrame = table.export_to_dataframe(doc=conv_res.document)
        print(f"## Table {table_ix}")
        print(table_df.to_markdown())

        # Save the table as CSV
        element_csv_filename = output_dir / f"{doc_filename}-table-{table_ix + 1}.csv"
        _log.info(f"Saving CSV table to {element_csv_filename}")
        table_df.to_csv(element_csv_filename)

        # Save the table as HTML
        element_html_filename = output_dir / f"{doc_filename}-table-{table_ix + 1}.html"
        _log.info(f"Saving HTML table to {element_html_filename}")
        with element_html_filename.open("w") as fp:
            fp.write(table.export_to_html(doc=conv_res.document))

    end_time = time.time() - start_time
    _log.info(f"Document converted and tables exported in {end_time:.2f} seconds.")

if __name__ == "__main__":
    main()