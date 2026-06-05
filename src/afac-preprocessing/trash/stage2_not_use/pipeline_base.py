from docling.datamodel.pipeline_options import VlmPipelineOptions
from docling.pipeline.vlm_pipeline import VlmPipeline
from docling.datamodel.vlm_engine_options import (
    ApiVlmEngineOptions,
    VlmEngineType,
)

from docling.datamodel.pipeline_options import (
    VlmConvertOptions,
    VlmPipelineOptions,
    PictureDescriptionVlmEngineOptions,
)

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat

import os
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv

##### Matthias début
import certifi

# Load .env.test relative to this file

dotenv_path = Path(__file__).resolve().parent.parent / ".env.test" # deux niveaux au dessus .env et .envtest sont au même niveau
print("Loading dotenv from:", dotenv_path.resolve(), "exists:", dotenv_path.exists())
load_dotenv(dotenv_path=dotenv_path)

# Configure CA bundle if provided, else fall back to certifi
custom_ca = os.environ.get("VLM_CA_PEM")
if custom_ca:
    os.environ.setdefault("SSL_CERT_FILE", custom_ca)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", custom_ca)
else:
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

# Read and validate required env vars early
VLM_URL = os.environ.get("VLM_URL", "")
VLM_MODEL_NAME = os.environ.get("VLM_MODEL_NAME", "")
if not VLM_URL:
    raise RuntimeError(f"VLM_URL not set. Ensure {dotenv_path} exists and contains VLM_URL or export it in the environment.")

print(f"VLM_URL: {VLM_URL}, \nVLM_MODEL_NAME: {VLM_MODEL_NAME}")  # Contrôle l'URL chargée et le modèle

# charge les variables d'environnement à partir du fichier .env
# load_dotenv()

# charge les variables d'environnement à partir du fichier .env.test (pour les tests)
# load_dotenv(dotenv_path=".env.test")
# load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env.test")

##### Matthias fin

# VLM_URL = os.environ.get("VLM_URL", "")
# VLM_MODEL_NAME = os.environ.get("VLM_MODEL_NAME", "")

# define picture description options
picture_desc_options = PictureDescriptionVlmEngineOptions.from_preset(
    "qwen",
    engine_options=ApiVlmEngineOptions(
        runtime_type=VlmEngineType.API,
        url=VLM_URL,
        params={
            "model": VLM_MODEL_NAME,
            "max_tokens": 1000,
            "skip_special_tokens": True,
        },
        timeout=30,
    ),
)

# A modifier avec des instructions plus spécifiques
language = "french"
picture_desc_options.prompt = f"""You are processing images for a retrieval system.

Your task:
Extract ONLY information that is useful for understanding processes, workflows, tables, structured data, diagrams, or technical content.

IGNORE and DO NOT describe:
- Logos
- Decorative illustrations
- Stock photos
- Portraits of people
- Background images
- Icons without technical meaning
- Purely aesthetic graphics

If the image does NOT contain informative technical content:
- Respond with an **empty string**!

If the image contains:
- A table → extract its structured content in text form.
- A process/workflow → describe the steps clearly.
- A diagram → describe components and relationships.
- A chart → summarize axes, variables and key values.

Rules:
- Be concise.
- No speculation.
- No generic phrases like "This image shows".
- No decorative commentary.
- Always respond in **{language}**.
"""

# define vlm options
vlm_options = VlmConvertOptions.from_preset(
    "qwen",
    engine_options=ApiVlmEngineOptions(
        runtime_type=VlmEngineType.API,
        url=VLM_URL,
        params={
            "model": VLM_MODEL_NAME,
            "max_tokens": 30000,
            "skip_special_tokens": True,
        },
        timeout=90,
    ),
)

# A modifier avec des instructions plus spécifiques (ie AF de zas.admin.ch)
# vlm_options.model_spec.prompt = 'Convert this page to markdown. Do not miss any text and only output the bare markdown! Do not use Latex for tables.'

pdf_pipeline_options = VlmPipelineOptions(
    vlm_options=vlm_options,
    do_picture_description=True,
    picture_description_options=picture_desc_options,
    enable_remote_services=True,
)

# define converter with pipeline options
converter = DocumentConverter(
    allowed_formats=[
            InputFormat.PDF,
        ],
    format_options={
        InputFormat.PDF: PdfFormatOption(
            pipeline_cls=VlmPipeline,
            pipeline_options=pdf_pipeline_options,
        ),
    }
)

if __name__ == "__main__":

##### Matthias début

    # Vérifie la project root :
    PROJECT_ROOT = Path(__file__).resolve().parents[1] # chemin vers le dossier pipeline

    # INPUT_PATH = Path("data/input_files") # data/input_files
    # OUTPUT_PATH = Path("data/output_files") # data/output_files
    
    INPUT_PATH = PROJECT_ROOT / "data" / "input_files"
    OUTPUT_PATH = PROJECT_ROOT / "data" / "output_files"

    # check si la output_path existe, sinon la créer
    OUTPUT_PATH.mkdir(parents=True, exist_ok=True)

    fp = "Domicilié dans les DOM-TOM, UE.pdf" # fp prend en valeur le document à traiter, e.g. "test1.pdf"
    
    ##### control optionel #####
    if not (INPUT_PATH / fp).exists():
        raise FileNotFoundError(f"Input file {INPUT_PATH / fp} does not exist.")

##### Matthias fin

    # run extraction pipeline
    result = converter.convert(INPUT_PATH / fp)

    # csv export must have 3 cols: content, metadata, embedding (None at this stage)
    data = [
        {
            "content": result.document.export_to_markdown(), # export to md or other format as needed
            "metadata": {},
            "embedding": None,
        }
    ]

    # save to csv
    pd.DataFrame(data).to_csv(OUTPUT_PATH / "af_base.csv", encoding="utf-8", sep=",", index=None) #penser à changer le nom du csc selon les tests

    # view extraction markdown
    print(result.document.export_to_markdown())
