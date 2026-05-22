import logging
import os
import re
import base64
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
import fitz
import requests
import certifi
from dotenv import load_dotenv

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger(__name__)

# Environnement & certificats
dotenv_path = Path(__file__).resolve().parent.parent / ".env.test"
_log.info("Loading dotenv from: %s (exists: %s)", dotenv_path.resolve(), dotenv_path.exists())
load_dotenv(dotenv_path=dotenv_path)

custom_ca = os.environ.get("VLM_CA_PEM")
CA_PATH = custom_ca if custom_ca and Path(custom_ca).exists() else certifi.where()
_log.info("CA bundle : %s", CA_PATH)

VLM_URL = os.environ.get("VLM_URL", "")
VLM_MODEL_NAME = os.environ.get("VLM_MODEL_NAME", "")
if not VLM_URL:
    raise RuntimeError(f"VLM_URL not set. Ensure {dotenv_path} exists and contains VLM_URL.")
_log.info("VLM_URL : %s", VLM_URL)
_log.info("VLM_MODEL_NAME : %s", VLM_MODEL_NAME)

# Constantes
NORM = 500
DPI = 150 # Norme Qwen3.5
N_BEFORE = 5
N_AFTER  = 5

language = "french"

# Prompt
# {context_before} et {context_after} sont injectés dynamiquement par image
WIKI_PROMPT_TEMPLATE = """
Role
You are a multimodal document analysis assistant.

Objective
Analyse the image in the context of the associated document or conversation.

Your goal is NOT to provide a full visual description.
Your goal is to determine whether the image contributes useful operational, analytical, contextual, or decision-relevant information beyond the surrounding text.

## Context

### Text BEFORE the image
{context_before}

### Text AFTER the image
{context_after}

## Analysis Process

Step 1 — Context understanding
Understand the surrounding text and identify:
- key topics;
- business objectives;
- claims;
- decisions;
- operational context.

Step 2 — Visual inventory
Before judging usefulness, identify all potentially informative visual elements, including:
- text;
- numbers;
- tables;
- charts;
- diagrams;
- UI elements;
- labels;
- warnings;
- anomalies;
- relationships between elements;
- spatial organization;
- unexpected or secondary details.

Step 3 — Cross-analysis
Determine whether the image:
- adds information absent from the text;
- clarifies ambiguity;
- confirms or contradicts claims;
- provides operational detail;
- adds contextual understanding;
- reveals constraints, risks, or exceptions;
- contains unexpected but relevant information.

Pay attention to secondary details that may still be operationally important even if not explicitly referenced in the text.

Prefer inclusion when omission could lead to misunderstanding or loss of context.

If information is uncertain or partially visible, mention it cautiously rather than omitting it.

Step 4 — Contribution assessment

Classify the image contribution as:
- redundant;
- complementary;
- clarifying;
- critical;
- contradictory.

If redundant:
Do not provide an image description.

Otherwise:
Provide a concise business-oriented summary focused only on useful information.

## Rules
- Avoid aesthetic descriptions.
- Avoid exhaustive scene descriptions.
- Focus on operationally relevant details.
- Include contradictions or discrepancies when present.
- Be concise but information-dense.

## Output

If relevant ALWAYS START YOUR IMAGE REVIEW WITH:
[IMAGE DESCRIPTION] ...

Otherwise:
No additional relevant visual information.

Always respond in {language}.
"""

# Dataclass pour la queue des tâches d'images à traiter
@dataclass
class ImageTask:
    index: int
    total: int
    page: int
    x0: int
    y0: int
    x1: int
    y1: int
    image_b64: str
    prompt: str # prompt contextualisé pour cette image
    raw_tag: str # balise <picture>...</picture> originale

# Tags textuels à inclure dans le contexte
TEXT_TAGS = {
    "text", "section_header_level_1", "section_header_level_2",
    "section_header_level_3", "list_item", "caption", "footnote",
    "page_header", "page_footer",
}

# ÉTAPE 1 — Parsing du doctags
def parse_picture_tags(doctags_path: Path) -> tuple[list[dict], str]:
    # Retourne la liste des <picture> et le contenu brut du doctags.
    content  = doctags_path.read_text(encoding="utf-8")
    pictures = []
    page     = 0

    for line in content.splitlines():
        line_clean = re.sub(r"</?doctag>", "", line).strip()
        if not line_clean:
            continue
        if "<page_footer>" in line_clean:
            page += 1
        for m in re.finditer(
            r"(<picture><loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)></picture>)",
            line_clean,
        ):
            x0, y0, x1, y1 = int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
            pictures.append({
                "page": page, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "raw_tag": m.group(1),
            })
            _log.debug("<picture> trouvée page=%d loc=(%d,%d,%d,%d)", page, x0, y0, x1, y1)

    _log.info("OK %d balise(s) <picture> trouvée(s)", len(pictures))
    return pictures, content


def extract_document_elements(doctags_path: Path) -> list[dict]:
    # Parse tous les éléments (textes + images) dans l'ordre d'apparition.
    content = doctags_path.read_text(encoding="utf-8")
    elements = []
    page = 0

    for line in content.splitlines():
        line_clean = re.sub(r"</?doctag>", "", line).strip()
        if not line_clean:
            continue
        if "<page_footer>" in line_clean:
            page += 1

        # Éléments textuels
        for m in re.finditer(
            r"<(?!/)(\w+)><loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)>(.*?)(?=<(?!loc_)\w|$)",
            line_clean, re.DOTALL,
        ):
            tag = m.group(1)
            if tag == "picture":
                continue
            x0, y0, x1, y1 = int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
            raw_text = re.sub(r"<[^>]+>", "", m.group(6)).strip()
            elements.append({
                "type": "text" if tag in TEXT_TAGS else "other",
                "tag": tag, "page": page,
                "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "text": raw_text,
            })

        # Images
        for m in re.finditer(
            r"<picture><loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)></picture>",
            line_clean,
        ):
            x0, y0, x1, y1 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            elements.append({
                "type": "picture", "tag": "picture", "page": page,
                "x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": "",
            })

    _log.info("OK %d élément(s) total parsé(s) dans le doctags", len(elements))
    return elements

def build_context(pic: dict, doc_elements: list, n_before: int, n_after: int) -> tuple[str, str]:
    # Retourne le contexte textuel avant/après une image.
    pic_index = next((
        idx for idx, e in enumerate(doc_elements)
        if e["type"] == "picture"
        and e["page"] == pic["page"]
        and e["x0"]  == pic["x0"]
        and e["y0"]  == pic["y0"]
    ), None)

    if pic_index is not None:
        before = [e["text"] for e in doc_elements[:pic_index] if e["type"] == "text" and e["text"]][-n_before:]
        after = [e["text"] for e in doc_elements[pic_index + 1:] if e["type"] == "text" and e["text"]][:n_after]
    else:
        before, after = [], []

    ctx_before = "\n".join(f"> {t}" for t in before) if before else "> No context available before this image."
    ctx_after = "\n".join(f"> {t}" for t in after) if after else "> No context available after this image."
    return ctx_before, ctx_after

# ÉTAPE 2 — Export des PNG (nommés avec les coordonnées du doctags)
def export_picture_images(
    pdf_path: Path,
    pictures: list[dict],
    doc_name: str,
    output_dir: Path,
    norm: int = NORM,
    dpi: int = DPI,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    _log.info("Export des PNG dans : %s", output_dir)

    for i, pic in enumerate(pictures, start=1):
        page = doc[pic["page"]]
        pw, ph = page.rect.width, page.rect.height
        pix = page.get_pixmap(dpi=dpi, clip=fitz.Rect(
            pic["x0"] / norm * pw, pic["y0"] / norm * ph,
            pic["x1"] / norm * pw, pic["y1"] / norm * ph,
        ))
        # Nom basé sur les coordonnées du doctags (pas normalisées)
        img_path = output_dir / (
            f"{doc_name}_page{pic['page']+1}_"
            f"x{pic['x0']}_y{pic['y0']}_x{pic['x1']}_y{pic['y1']}.png"
        )
        img_path.write_bytes(pix.tobytes("png"))
        _log.info("  [%d/%d] PNG exporté : %s", i, len(pictures), img_path.name)

    doc.close()
    _log.info("OK %d PNG exporté(s)", len(pictures))

# ÉTAPE 3 — Crop en mémoire + mise en queue + appel VLM direct
def crop_to_b64(pdf_doc: fitz.Document, pic: dict, norm: int = NORM, dpi: int = DPI) -> str:
    # Crop une image du PDF et retourne le base64 en mémoire.
    page = pdf_doc[pic["page"]]
    pw, ph = page.rect.width, page.rect.height
    pix = page.get_pixmap(dpi=dpi, clip=fitz.Rect(
        pic["x0"] / norm * pw, pic["y0"] / norm * ph,
        pic["x1"] / norm * pw, pic["y1"] / norm * ph,
    ))
    return base64.b64encode(pix.tobytes("png")).decode("utf-8")


def describe_image_b64(image_b64: str, prompt: str) -> str:
    # Envoie une image (base64) + prompt au VLM et retourne la description.
    payload = {
        "model": VLM_MODEL_NAME,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }],
        "max_tokens": 3000, # Qwen 3.5 accepte plus si necessaire
    }
    try:
        response = requests.post(VLM_URL, json=payload, verify=CA_PATH, timeout=120)
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        _log.error("Erreur API VLM : %s", e)
        return ""


# Worker thread consommateur de la queue
_results: dict[int, dict] = {}
_results_lock = threading.Lock()

def _vlm_worker(task_queue: queue.Queue) -> None:
    while True:
        task: ImageTask | None = task_queue.get()
        if task is None:
            _log.info("Worker VLM arrêté.")
            break

        _log.info(
            "  [%d/%d] -> Envoi au VLM — Page %d loc=(%d,%d,%d,%d)",
            task.index, task.total, task.page + 1,
            task.x0, task.y0, task.x1, task.y1,
        )
        description = describe_image_b64(task.image_b64, task.prompt)

        with _results_lock:
            _results[task.index] = {
                "page": task.page,
                "x0": task.x0, "y0": task.y0,
                "x1": task.x1, "y1": task.y1,
                "description": description,
                "raw_tag": task.raw_tag,
            }

        if description:
            _log.info(" [%d/%d] OK Description reçue (%d chars)", task.index, task.total, len(description))
        else:
            _log.warning(" [%d/%d] WARNING  Aucune description retournée par le VLM", task.index, task.total)

        task_queue.task_done()


def describe_all_pictures(
    pdf_path: Path,
    pictures: list[dict],
    doc_elements: list[dict],
    n_before: int = N_BEFORE,
    n_after:  int = N_AFTER,
) -> dict[int, dict]:
    
    # Crop chaque image en mémoire, construit le prompt contextualisé,
    # met en queue et envoie au VLM via requests (pas Docling).
    # Retourne un dict indexé par position (1-based).
    _results.clear()
    total = len(pictures)
    pdf_doc = fitz.open(str(pdf_path))
    task_q = queue.Queue()

    worker = threading.Thread(target=_vlm_worker, args=(task_q,), daemon=True)
    worker.start()
    _log.info("Mise en queue de %d image(s) (crop mémoire → base64)", total)

    for i, pic in enumerate(pictures, start=1):
        # Contexte textuel
        ctx_before, ctx_after = build_context(pic, doc_elements, n_before, n_after)
        _log.info(
            "  [%d/%d] Contexte — %d texte(s) avant / %d texte(s) après",
            i, total,
            ctx_before.count("\n> ") + 1 if ctx_before.startswith(">") else 0,
            ctx_after.count("\n> ")  + 1 if ctx_after.startswith(">")  else 0,
        )

        # Prompt contextualisé pour cette image
        prompt = WIKI_PROMPT_TEMPLATE.format(
            context_before=ctx_before,
            context_after=ctx_after,
            language=language,
        )

        # Crop en mémoire → base64
        image_b64 = crop_to_b64(pdf_doc, pic)
        _log.info("  [%d/%d] OK - Image croppée et encodée en base64", i, total)

        task_q.put(ImageTask(
            index=i, total=total,
            page=pic["page"],
            x0=pic["x0"], y0=pic["y0"],
            x1=pic["x1"], y1=pic["y1"],
            image_b64=image_b64,
            prompt=prompt,
            raw_tag=pic["raw_tag"],
        ))
        _log.info("  [%d/%d] -> Image mise en queue (page %d)", i, total, pic["page"] + 1)

    pdf_doc.close()

    _log.info("Attente de la fin du traitement VLM...")
    task_q.join()
    task_q.put(None) # signal d'arrêt du worker
    worker.join()

    described = sum(1 for r in _results.values() if r["description"])
    _log.info("OK %d/%d image(s) décrite(s) avec contexte", described, total)
    return dict(_results)

# ÉTAPE 4 — Remplacement des balises <picture> dans le doctags
def replace_picture_tags(content: str, results: dict[int, dict]) -> str:
    replaced = 0
    for idx in sorted(results.keys()):
        r = results[idx]
        if not r["description"]:
            _log.warning("Pas de description pour <picture> idx=%d — tag conservé", idx)
            continue

        if r["raw_tag"] not in content:
            _log.error("raw_tag introuvable : %s", r["raw_tag"])
            continue

        desc       = r["description"]
        raw_tag    = re.escape(r["raw_tag"])
        desc_inline = desc.replace("\n", " ").strip()

        # Cas 1 : <list_item><picture/></list_item>
        pattern_in_item = re.compile(
            r"<list_item>\s*" + raw_tag + r"\s*</list_item>",
            re.DOTALL
        )
        if pattern_in_item.search(content):
            content = pattern_in_item.sub(
                f"<list_item>{desc_inline}</list_item>",
                content, count=1
            )
            _log.info("[%d] <picture> dans <list_item> → texte inline", idx)

        # Cas 2 : <picture/> entre des <list_item> dans une liste
        elif re.search(
            r"<list_item[^>]*>.*?</list_item>\s*" + raw_tag,
            content, re.DOTALL
        ):
            content = content.replace(
                r["raw_tag"],
                f"<list_item>{desc_inline}</list_item>",
                1
            )
            _log.info("[%d] <picture> entre list_items → <list_item> texte inline", idx)

        # Cas 3 : <picture/> standalone (hors liste)
        else:
            content = content.replace(
                r["raw_tag"],
                f"<text>\n{desc}\n</text>",
                1
            )
            _log.info("[%d] <picture> standalone → <text>", idx)

        replaced += 1

    _log.info(" %d/%d balise(s) <picture> remplacée(s)", replaced, len(results))
    return content

# ÉTAPE 5 — Export Markdown
def export_descriptions_to_markdown(
    results: dict[int, dict],
    doc_name: str,
    output_path: Path,
) -> None:
    total = len(results)
    nb_described = sum(1 for r in results.values() if r["description"])
    nb_missing = total - nb_described
    sections = []

    for i in sorted(results.keys()):
        r = results[i]
        loc_str = f"loc({r['x0']}, {r['y0']}, {r['x1']}, {r['y1']})"
        page_str = f"Page {r['page'] + 1}"

        if r["description"]:
            sections.append(
                f"## OK - Image {i}/{total} — {page_str} | `{loc_str}`\n\n"
                f"{r['description']}\n"
            )
        else:
            sections.append(
                f"## WARNING - Image {i}/{total} — {page_str} | `{loc_str}`\n\n"
                f"> **Aucune description générée.**\n"
                f"> *Vérifier les coordonnées ou la réponse du VLM.*\n"
            )

    header = (
        f"# Descriptions des images — *{doc_name}*\n\n"
        f"> Généré automatiquement par le pipeline VLM  \n"
        f"> Document source : `{doc_name}.pdf`  \n"
        f"> Nombre d'images détectées : **{total}**  \n"
        f"> Modèle VLM : `{VLM_MODEL_NAME}`\n\n"
        f"---\n\n"
    )
    summary = (
        f"## Résumé\n\n"
        f"- Images détectées  : **{total}**\n"
        f"- Images décrites   : **{nb_described}**\n"
        f"- Images manquantes : **{nb_missing}**\n"
    )

    md_content = header + "\n\n---\n\n".join(sections) + "\n\n---\n\n" + summary
    output_path.write_text(md_content, encoding="utf-8")
    _log.info("OK - Markdown exporté (%d/%d images décrites) : %s", nb_described, total, output_path)

# PIPELINE PRINCIPAL
def main():
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    DOC_NAME = os.environ.get("DOC_NAME", "")

    pdf_path = PROJECT_ROOT / "data" / "input_files" / f"{DOC_NAME}.pdf"
    doctags_path = PROJECT_ROOT / "data" / "output_files" / "stage2_test" / DOC_NAME / f"{DOC_NAME}_reordered_with_tables.doctags"
    output_path = PROJECT_ROOT / "data" / "output_files" / "stage2_test" / DOC_NAME / f"{DOC_NAME}_reordered_with_tables_pictures.doctags"
    markdown_path = PROJECT_ROOT / "data" / "output_files" / "stage2_test" / DOC_NAME / f"{DOC_NAME}_image_descriptions.md"
    images_dir = PROJECT_ROOT / "data" / "output_files" / "stage2_test" / DOC_NAME / "used_images"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ÉTAPE 1 : Parsing
    _log.info("ÉTAPE 1 — Parsing des balises <picture> + éléments du doctags")
    pictures, content = parse_picture_tags(doctags_path)
    doc_elements = extract_document_elements(doctags_path)

    if not pictures:
        _log.warning("Aucune balise <picture> trouvée, fin du script.")
        return

    # ÉTAPE 2 : Export PNG

    _log.info("ÉTAPE 2 — Export des images PNG (coordonnées doctags)")
    export_picture_images(pdf_path, pictures, DOC_NAME, images_dir)

    # ÉTAPE 3 : Description VLM via queue
    _log.info("ÉTAPE 3 — Description des images avec contexte textuel (queue → VLM direct)")
    results = describe_all_pictures(pdf_path, pictures, doc_elements)

    # ÉTAPE 4 : Remplacement des <picture> dans le doctags
  
    _log.info("ÉTAPE 4 — Remplacement des balises <picture> dans le doctags")
    enriched_content = replace_picture_tags(content, results)
    output_path.write_text(enriched_content, encoding="utf-8")
    _log.info("OK - Doctags enrichi sauvegardé : %s", output_path)

    #ÉTAPE 5 : Export Markdown
    _log.info("ÉTAPE 5 — Export des descriptions en Markdown")
    export_descriptions_to_markdown(results, DOC_NAME, markdown_path)

if __name__ == "__main__":
    main()