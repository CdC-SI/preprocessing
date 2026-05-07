# uv pip install -r requirements.txt
# cp .env.example .env  # and fill in the values
# Note: you might need to define proxy settings to access the vlm inference endpoint

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

load_dotenv()

VLM_URL = os.environ.get("VLM_URL", "")
VLM_MODEL_NAME = os.environ.get("VLM_MODEL_NAME", "")

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

    INPUT_PATH = Path("path/to/your/input/files")
    OUTPUT_PATH = Path("path/to/your/output/files")
    fp = "example.pdf"

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
    pd.DataFrame(data).to_csv(OUTPUT_PATH / "af.csv", encoding="utf-8", sep=",", index=None)

    # view extraction markdown
    print(result.document.export_to_markdown())
