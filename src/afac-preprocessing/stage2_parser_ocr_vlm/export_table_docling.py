"""
Stage 2 - Script d'export des tables extraites du document avec Docling
Script 1 : export_table_docling.py

Documentation utilisée : 
https://docling-project.github.io/docling/_generated/examples/export_tables/

Ce script utilise la bibliothèque Docling pour extraire les tables d'un document PDF 
et les exporter dans différents formats (CSV, HTML). 
"""
import logging
import time
from pathlib import Path
import os
import pandas as pd
from docling.document_converter import DocumentConverter
import sys

# Appel des fonctions de configuration pour récupérer les chemins et paramètres nécessaires
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_vlm_config
load_vlm_config()

_log = logging.getLogger(__name__)


def main():
    #Root
    DOC_NAME = os.environ.get("DOC_NAME", "")
    input_doc_path = PROJECT_ROOT / "data" / "input_files" / f"{DOC_NAME}.pdf"
    output_dir = PROJECT_ROOT / "data" / "output_files" / "stage2_test" / DOC_NAME / "tables"
    output_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()
    conv_res = DocumentConverter().convert(input_doc_path)
    doc_filename = conv_res.input.file.stem

    # Export les tables extraites du document dans différents formats (CSV, HTML) avec Docling
    for table_ix, table in enumerate(conv_res.document.tables):
        table_df: pd.DataFrame = table.export_to_dataframe(doc=conv_res.document)
        print(f"## Table {table_ix}")
        print(table_df.to_markdown())

        # Sauvegarde la table au format CSV
        element_csv_filename = output_dir / f"{doc_filename}-table-{table_ix + 1}.csv"
        _log.info(f"Saving CSV table to {element_csv_filename}")
        table_df.to_csv(element_csv_filename)

        # Sauvegarde la table au format HTML
        element_html_filename = output_dir / f"{doc_filename}-table-{table_ix + 1}.html"
        _log.info(f"Saving HTML table to {element_html_filename}")
        with element_html_filename.open("w") as fp:
            fp.write(table.export_to_html(doc=conv_res.document))

    end_time = time.time() - start_time
    _log.info(f"Document converted and tables exported in {end_time:.2f} seconds.")

if __name__ == "__main__":
    main()