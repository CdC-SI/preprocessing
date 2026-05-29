import logging
import time
from pathlib import Path
import os
from dotenv import load_dotenv
import pandas as pd
import certifi

from docling.document_converter import DocumentConverter

_log = logging.getLogger(__name__)

#####
# Chargement de .env.test
dotenv_path = Path(__file__).resolve().parent.parent / ".env.test" # Je suis sur l'.env.test qui est le même que le .env
print("Loading dotenv from:", dotenv_path.resolve(), "exists:", dotenv_path.exists())
load_dotenv(dotenv_path=dotenv_path)

# Certificat CA personnalisé si fourni, sinon fallback sur certifi (VU avec M Gianelli, pour les autres machines, demander accès)
custom_ca = os.environ.get("VLM_CA_PEM")
if custom_ca:
    os.environ.setdefault("SSL_CERT_FILE", custom_ca)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", custom_ca)
else:
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

# Vérifcation de la présence des variables d'environnement nécessaires
VLM_URL = os.environ.get("VLM_URL", "")
VLM_MODEL_NAME = os.environ.get("VLM_MODEL_NAME", "")
if not VLM_URL:
    raise RuntimeError(
        f"VLM_URL not set. Ensure {dotenv_path} exists and contains VLM_URL or export it in the environment."
    )

print(f"VLM_URL: {VLM_URL}, \nVLM_MODEL_NAME: {VLM_MODEL_NAME}")# affiche dans la console les variables d'environnement chargées pour vérification
#####

### https://docling-project.github.io/docling/examples/export_tables/

def main():
    logging.basicConfig(level=logging.INFO)

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