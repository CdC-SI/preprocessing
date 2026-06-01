import os
from typing import List
import base64
import fitz  # PyMuPDF
from PIL import Image
from io import BytesIO
import datetime
from pydantic import BaseModel
from openai import AsyncOpenAI
import httpx
import asyncio
from dotenv import load_dotenv

from prompts.prompts import OCR_PROMPT, LLM_PROMPT

import logging
logger = logging.getLogger("pdf-ocr-pipeline")

load_dotenv()

VLM_URL = os.environ.get("VLM_URL")
VLM_MODEL_NAME = os.environ.get("VLM_MODEL_NAME")
LLM_URL = os.environ.get("LLM_URL")
LLM_MODEL_NAME = os.environ.get("LLM_MODEL_NAME")
EMBEDDING_URL = os.environ.get("EMBEDDING_URL")
EMBEDDING_MODEL_NAME = os.environ.get("EMBEDDING_MODEL_NAME")
TOKENIZER_URL = os.environ.get("TOKENIZER_URL")
TOKENIZER_MODEL_NAME = os.environ.get("TOKENIZER_MODEL_NAME")
MAX_EMBEDDING_TOKENS = int(os.environ.get("MAX_EMBEDDING_TOKENS", 32768))

sem = asyncio.Semaphore(4)

http_client = httpx.AsyncClient(
    timeout=300.0,
    verify=False,
)

vlm_client = AsyncOpenAI(
    base_url=VLM_URL,
    api_key="",
    http_client=http_client,
)

llm_client = AsyncOpenAI(
    base_url=LLM_URL,
    api_key="",
    http_client=http_client,
)

embedding_client = AsyncOpenAI(
    base_url=EMBEDDING_URL,
    api_key="",
    http_client=http_client,
)

class VLMResponseModel(BaseModel):
    ocr_content: str = ""
    
class LLMResponseModel(BaseModel):
    language: str = ""
    summary: str = ""


def render_pdf_to_data_urls(pdf_bytes: bytes, dpi: int) -> List[str]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    data_urls = []

    for page in doc:
        pix = page.get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        buf = BytesIO()
        img.save(buf, format="PNG")
        page_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        data_urls.append(f"data:image/png;base64,{page_b64}")

    logger.info("Rendered %d page(s) at %d DPI", len(data_urls), dpi)
    return data_urls


def make_doc(page_nums, dpi, llm_response, doc_title, user_uuid, chunk, embedding):
    metadata = {
        "page_num": ",".join(str(num) for num in page_nums),
        "dpi": dpi,
        "language": llm_response.language,
        "summary": llm_response.summary,
        "title": doc_title,
        "source": "user_pdf_upload",
        "privacy": "private",
        "doctype": "rag",
        "last_modification": datetime.datetime.now().strftime("%d.%m.%Y"),
        "user_uuid": user_uuid,
    }

    formatted_embedding = str(embedding).replace("[", "").replace("]", "")
    contextualized_chunk = llm_response.summary + "\n\n" + chunk
    doc = {
        "content": contextualized_chunk,
        "metadata": metadata,
        "embedding": formatted_embedding,
    }
    
    return doc

async def tokenize(text: str):
    """
    Sends a single text prompt to the vLLM /tokenize API (OpenAI-compatible).
    Returns the token count and token ids.
    """
    payload = {
        "model": TOKENIZER_MODEL_NAME,
        "messages": [{"role": "user", "content": text}]
    }
    try:
        resp = await http_client.post(TOKENIZER_URL, json=payload)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.exception("Tokenizer call failed: %s", e)
    
async def get_embedding(text: str):
    """
    Sends a single string to vLLM's /v1/embeddings endpoint (OpenAI-compatible)
    and returns the raw JSON response, which includes the embedding vector.
    """
    try:
        res = await embedding_client.embeddings.create(
            model=EMBEDDING_MODEL_NAME,
            input=text
        )
        embedding = res.data[0].embedding
        
    except Exception as e:
        embedding = []
        logger.exception("Embedding call failed")
        
    return embedding

async def get_embeddings_batch(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []

    try:
        res = await embedding_client.embeddings.create(
            model=EMBEDDING_MODEL_NAME,
            input=texts
        )
        return [item.embedding for item in res.data]
    except Exception as e:
        logger.exception("Batch embedding call failed: %s", e)
        return [[] for _ in texts]


async def call_vlm(data_url: str):

    messages = [
        {"role": "system", "content": "You are an expert at parsing PDF documents."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": OCR_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": data_url
                    }
                }
            ]
        }
    ]
    try:
        res = await vlm_client.chat.completions.parse(
            model=VLM_MODEL_NAME,
            messages=messages,
            response_format=VLMResponseModel,
            top_p=0.8,
            temperature=0.7,
            presence_penalty=1.5,
            
        )
        return res.choices[0].message.parsed
        
    except Exception as e:
        logger.exception("VLM call failed: %s", e)
        return VLMResponseModel()

async def ocr_page(data_url):
    async with sem:
        return await call_vlm(data_url)

async def call_llm(text: str):

    messages = [
        {
            "role": "developer",
            "content": [
                {"type": "text", "text": LLM_PROMPT.format(
                    doc=text
                )},
            ]
        }
    ]
    try:
        res = await llm_client.chat.completions.parse(
            model=LLM_MODEL_NAME,
            messages=messages,
            response_format=LLMResponseModel,
            temperature=0.0,
            
        )
        return res.choices[0].message.parsed
        
    except Exception as e:
        logger.exception("LLM call failed: %s", e)
        return LLMResponseModel()

async def run_pipeline(
    b64_pdf: str,
    user_uuid: str,
    doc_title: str,
    dpi: int = 150,
):
    logger.info("Pipeline started (doc_title=%r, user_uuid=%s, dpi=%d)", doc_title, user_uuid, dpi)

    # Decode PDF
    pdf_bytes = base64.b64decode(b64_pdf)

    # Render PDF off event loop
    data_urls = await asyncio.to_thread(
        render_pdf_to_data_urls, pdf_bytes, dpi
    )

    if not data_urls:
        logger.warning("No pages rendered from PDF (doc_title=%r)", doc_title)
        return []

    # OCR
    logger.info("Running OCR on %d page(s)", len(data_urls))
    ocr_tasks = [ocr_page(url) for url in data_urls]
    ocr_results: List[VLMResponseModel] = await asyncio.gather(*ocr_tasks)
    ocr_results = [
        r if isinstance(r, VLMResponseModel) else VLMResponseModel()
        for r in ocr_results
    ]

    empty_pages = [i + 1 for i, r in enumerate(ocr_results) if not r.ocr_content.strip()]
    if empty_pages:
        logger.warning("OCR returned empty content for page(s): %s", empty_pages)

    # Chunking
    chunks: List[str] = []
    page_ranges: List[List[int]] = []

    chunk = ""
    page_nums = []

    for page_idx, page in enumerate(ocr_results, start=1):
        page_text = page.ocr_content + "\n\n"
        candidate = chunk + page_text

        tokens = await tokenize(candidate)

        if tokens is None:
            logger.error("Tokenizer returned None for page %d; skipping page", page_idx)
            continue

        if tokens["count"] <= MAX_EMBEDDING_TOKENS:
            chunk = candidate
            page_nums.append(page_idx)
        else:
            # flush previous chunk
            if chunk:
                chunks.append(chunk)
                page_ranges.append(page_nums)

            # start new chunk
            chunk = page_text
            page_nums = [page_idx]

    if chunk:
        chunks.append(chunk)
        page_ranges.append(page_nums)

    logger.info("Chunking produced %d chunk(s) from %d page(s)", len(chunks), len(ocr_results))

    # Single LLM call (doc-level metadata)
    full_text = "\n".join(chunks)
    llm_response = await call_llm(full_text)

    # Batch embeddings
    embeddings = await get_embeddings_batch(chunks)

    # Build documents
    documents = []
    for chunk_text, pages, embedding in zip(chunks, page_ranges, embeddings):
        documents.append(
            make_doc(
                page_nums=pages,
                dpi=dpi,
                llm_response=llm_response,
                doc_title=doc_title,
                user_uuid=user_uuid,
                chunk=chunk_text,
                embedding=embedding,
            )
        )

    logger.info("Pipeline finished: produced %d document(s) (doc_title=%r)", len(documents), doc_title)
    return documents
