import logging
import os
import re
import tempfile
from pathlib import Path
import fitz
import certifi
from docling_core.types.doc import PictureItem
from dotenv import load_dotenv
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    PictureDescriptionVlmEngineOptions,
)
from docling.datamodel.vlm_engine_options import ApiVlmEngineOptions, VlmEngineType
from docling.document_converter import (
    DocumentConverter,
    PdfFormatOption,
    ImageFormatOption,
)

_log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Certification et .env
dotenv_path = Path(__file__).resolve().parent.parent / ".env.test"
print("Loading dotenv from:", dotenv_path.resolve(), "exists:", dotenv_path.exists())
load_dotenv(dotenv_path=dotenv_path)

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
    raise RuntimeError(f"VLM_URL not set. Ensure {dotenv_path} exists and contains VLM_URL.")

print(f"VLM_URL: {VLM_URL}\nVLM_MODEL_NAME: {VLM_MODEL_NAME}")

# PROMPT — str classique (pas f-string)
# {context_before} et {context_after} injectés par image via .format()
language = "french"

WIKI_PROMPT = """
Role
You are a document analysis assistant.

Objective: Your task is to analyse an image in the context of the associated document or conversation.
You should not aim to provide a systematic description of the image.
Rather, you should determine whether the image provides additional useful information for a business user.


## Document Context
**Instructions**: Use this context to better understand the image.
If the surrounding text already explains the image fully, only add visual details that bring new information not present in the text.
Here are the guidelines to determine the usefulness of the image and how to describe it:

### Text appearing BEFORE this image in the document:
{context_before}

### Text appearing AFTER this image in the document:
{context_after}

In addition to the textual context, you can also consider the following elements if available:
Definition of useful information
Visual information is considered useful if:
- it provides new information absent from the text;
- it confirms or refutes important information;
- it aids professional understanding;
- it contains details necessary for decision-making;
- it reduces the risk of misinterpretation;
- it has operational or contextual value.

SITUATIONS WHERE IT IS NOT NECESSARY TO DESCRIBE THE IMAGE:
Do not describe the image if:
- it simply repeats the content of the text;
- it is decorative;
- it does not provide any new information;
- the visible details are not relevant to the user;
- a business user would not need this visual information.

Process of decision:
Step 1: analyse the textual context.
Step 2: analyse the image.
Step 3: Determine whether the image adds any business value.
Step 4:
  If yes, produce a targeted and useful description.
  If not, do not add any information about the image.

IMPORTANT RULES:
- Never provide generic descriptions.
- Never describe details that are not valuable.
- Be concise.
- Prioritise business relevance.
- Avoid purely aesthetic descriptions.

Output format:
If the image is relevant:
[VISUAL INFORMATION] Concise, business-oriented description.
Otherwise, do not include any additional relevant visual information.
Always respond in **{language}**.
"""

# HELPER : retrouve une description par coordonnées
def _find_description(pic: dict, descriptions: dict, tolerance: int = 10) -> str:
    # Lookup exact puis fallback avec tolérance.
    # Factorisé pour éviter la duplication entre replace et export.
    key = (pic["page"], pic["x0"], pic["y0"], pic["x1"], pic["y1"])
    if key in descriptions:
        return descriptions[key]
    for (pg, dx0, dy0, dx1, dy1), desc in descriptions.items():
        if pg != pic["page"]:
            continue
        if all(abs(a - b) <= tolerance for a, b in [
            (dx0, pic["x0"]), (dy0, pic["y0"]),
            (dx1, pic["x1"]), (dy1, pic["y1"]),
        ]):
            return desc
    return ""

# FACTORY : converter Docling avec prompt injecté
def make_converter(prompt: str) -> DocumentConverter:
    picture_desc_options = PictureDescriptionVlmEngineOptions.from_preset(
        "qwen",
        engine_options=ApiVlmEngineOptions(
            runtime_type=VlmEngineType.API,
            url=VLM_URL,
            params={
                "model": VLM_MODEL_NAME,
                "max_tokens": 5000, # ajusté pour éviter les coupures sur les descriptions longues avec contexte (à revoir selon les tests)
                "skip_special_tokens": True,
            },
            timeout=120,
        ),
    )
    picture_desc_options.prompt = prompt

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_picture_description      = True
    pipeline_options.picture_description_options = picture_desc_options
    pipeline_options.enable_remote_services      = True

    return DocumentConverter(
        format_options={
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
            InputFormat.PDF:   PdfFormatOption(pipeline_options=pipeline_options),
        }
    )

# PARSE DES BALISES <picture> + éléments textuels
TEXT_TAGS = {
    "text", "section_header_level_1", "section_header_level_2",
    "section_header_level_3", "list_item", "caption", "footnote",
    "page_header", "page_footer",
}

def parse_picture_tags(doctags_path: Path) -> tuple[list, str]:
    # Retourne la liste des <picture> et le contenu brut du doctags.
    content = doctags_path.read_text(encoding="utf-8")
    pictures = []
    current_page = 0

    for line in content.splitlines():
        line_clean = line.replace("<doctag>", "").replace("</doctag>", "").strip()
        if not line_clean:
            continue
        if "<page_footer>" in line_clean:
            current_page += 1
        for match in re.finditer(
            r'(<picture><loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)></picture>)',
            line_clean
        ):
            x0, y0, x1, y1 = int(match.group(2)), int(match.group(3)), \
                              int(match.group(4)), int(match.group(5))
            pictures.append({
                "page": current_page,
                "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "raw_tag": match.group(1),
            })
            _log.info("Found <picture> page=%d loc=(%d,%d,%d,%d)", current_page, x0, y0, x1, y1)

    print(f"→ {len(pictures)} balise(s) <picture> trouvée(s)")
    return pictures, content


def extract_document_elements(doctags_path: Path) -> list:
    # Parse tous les éléments (textes + images) dans l'ordre d'apparition.
    # Fix : une seule passe regex pour éviter les doublons <picture>.
    content = doctags_path.read_text(encoding="utf-8")
    elements = []
    current_page = 0

    for line in content.splitlines():
        line_clean = line.replace("<doctag>", "").replace("</doctag>", "").strip()
        if not line_clean:
            continue
        if "<page_footer>" in line_clean:
            current_page += 1

        # Textes avec coordonnées
        for match in re.finditer(
            r'<(?!/)(\w+)><loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)>(.*?)(?=<(?!loc_)\w|$)',
            line_clean, re.DOTALL,
        ):
            tag = match.group(1)
            if tag == "picture":
                continue  # géré séparément ci-dessous (pas de contenu textuel)
            x0, y0, x1, y1 = int(match.group(2)), int(match.group(3)), \
                              int(match.group(4)), int(match.group(5))
            raw_text = re.sub(r'<[^>]+>', '', match.group(6)).strip()
            elements.append({
                "type": "text" if tag in TEXT_TAGS else "other",
                "tag": tag, "page": current_page,
                "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "text": raw_text,
            })

        # Images (pas de contenu textuel → regex dédiée)
        for match in re.finditer(
            r'<picture><loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)></picture>',
            line_clean
        ):
            x0, y0, x1, y1 = int(match.group(1)), int(match.group(2)), \
                              int(match.group(3)), int(match.group(4))
            elements.append({
                "type": "picture", "tag": "picture", "page": current_page,
                "x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": "",
            })

    return elements

# DESCRIPTION DES IMAGES AVEC CONTEXTE
def describe_pictures_with_context(
    pdf_path: Path,
    pictures: list,
    doc_elements: list,
    n_before: int = 5,
    n_after: int = 5,
    norm: int = 500,
    dpi: int = 150, # Voir pour qwen 3.5 
) -> dict:
    doc = fitz.open(str(pdf_path))
    descriptions = {}

    for i, pic in enumerate(pictures, start=1):
        print(f"\n  [{i}/{len(pictures)}] Page {pic['page']+1} "
              f"loc=({pic['x0']},{pic['y0']},{pic['x1']},{pic['y1']})")

        # Crop
        page = doc[pic["page"]]
        pw, ph = page.rect.width, page.rect.height
        pix = page.get_pixmap(dpi=dpi, clip=fitz.Rect(
            pic["x0"] / norm * pw, pic["y0"] / norm * ph,
            pic["x1"] / norm * pw, pic["y1"] / norm * ph,
        ))
        image_bytes = pix.tobytes("png")

        # Contexte textuel
        pic_index = next((
            idx for idx, e in enumerate(doc_elements)
            if e["type"] == "picture"
            and e["page"] == pic["page"]
            and e["x0"] == pic["x0"]
            and e["y0"] == pic["y0"]
        ), None)

        if pic_index is not None:
            before_texts = [
                e["text"] for e in doc_elements[:pic_index]
                if e["type"] == "text" and e["text"]
            ][-n_before:]
            after_texts = [
                e["text"] for e in doc_elements[pic_index + 1:]
                if e["type"] == "text" and e["text"]
            ][:n_after]
        else:
            before_texts, after_texts = [], []

        print(f"  → Contexte : {len(before_texts)} texte(s) avant, {len(after_texts)} texte(s) après")
        if before_texts:
            print(f"    Avant  : « {before_texts[-1][:80]}... »")
        if after_texts:
            print(f"    Après  : « {after_texts[0][:80]}... »")

        # Prompt contextualisé
        context_before = "\n".join(f"> {t}" for t in before_texts) \
                         if before_texts else "> No context available before this image."
        context_after = "\n".join(f"> {t}" for t in after_texts) \
                         if after_texts  else "> No context available after this image."

        prompt = WIKI_PROMPT.format(
            context_before=context_before,
            context_after=context_after,
            language=language,
        )

        # Conversion via Docling
        converter = make_converter(prompt)
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(image_bytes)
                tmp_path = Path(tmp.name)

            result = converter.convert(str(tmp_path))
            tmp_path.unlink(missing_ok=True)

            description = next((
                annotation.text.strip()
                for element, _ in result.document.iterate_items()
                if isinstance(element, PictureItem)
                for annotation in element.annotations
                if hasattr(annotation, "text") and annotation.text
            ), "")

            if description:
                descriptions[(pic["page"], pic["x0"], pic["y0"], pic["x1"], pic["y1"])] = description
                print(f"  → Description ({len(description)} chars)")
            else:
                print("  → Aucune description retournée par le VLM")

        except Exception as e:
            _log.error("Erreur Docling pour image %d : %s", i, e)
            if 'tmp_path' in locals():
                tmp_path.unlink(missing_ok=True)

    doc.close()
    print(f"\n→ {len(descriptions)} image(s) décrite(s) avec contexte")
    return descriptions

# REMPLACEMENT DES BALISES <picture>
def replace_picture_tags_docling(
    content: str,
    pictures: list,
    descriptions: dict,
    tolerance: int = 10, # vérifier fallback avec tolérance pour éviter les tags non remplacés
) -> str:
    for pic in pictures:
        matched_desc = _find_description(pic, descriptions, tolerance)
        if not matched_desc:
            _log.warning("Pas de description pour <picture> page=%d loc=(%d,%d,%d,%d)",
                         pic["page"], pic["x0"], pic["y0"], pic["x1"], pic["y1"])
            continue
        new_tag = f"<text>\n{matched_desc}\n</text>"
        content = content.replace(pic["raw_tag"], new_tag, 1)
        _log.info("Replaced <picture> (%d chars)", len(matched_desc))
    return content

# EXPORT MARKDOWN
def export_descriptions_to_markdown(
    pictures: list,
    descriptions: dict,
    doc_name: str,
    output_path: Path,
    tolerance: int = 10,
) -> None:
    matched_count = 0
    lines = [
        f"# Descriptions des images — *{doc_name}*\n",
        f"> Généré automatiquement par le pipeline VLM  ",
        f"> Document source : `{doc_name}.pdf`  ",
        f"> Nombre d'images détectées : **{len(pictures)}**  ",
        f"> Modèle VLM : `{VLM_MODEL_NAME}`\n",
        "---\n",
    ]

    for i, pic in enumerate(pictures, start=1):
        matched_desc = _find_description(pic, descriptions, tolerance)  # ← helper réutilisé
        status = "OK" if matched_desc else "Warning"
        lines.append(
            f"## {status} Image {i}/{len(pictures)} "
            f"— Page {pic['page'] + 1} "
            f"| `loc({pic['x0']}, {pic['y0']}, {pic['x1']}, {pic['y1']})`\n"
        )
        if matched_desc:
            lines.append(matched_desc)
            matched_count += 1
        else:
            lines.append(
                "> **Aucune description générée.**\n"
                "> *Vérifier le matching des coordonnées ou la réponse du VLM.*\n"
            )
        lines.append("\n---\n")

    lines += [
        "\n## Résumé\n",
        f"- Images détectées  : **{len(pictures)}**",
        f"- Images décrites   : **{matched_count}**",
        f"- Images manquantes : **{len(pictures) - matched_count}**\n",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"→ Markdown exporté ({matched_count}/{len(pictures)} images décrites) : {output_path}")

# EXPORT DES IMAGES CROPPÉES
def export_picture_images(
    pdf_path: Path,
    pictures: list,
    doc_name: str,
    output_dir: Path,
    norm: int = 500,
    dpi: int = 200,
):
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    for pic in pictures:
        page = doc[pic["page"]]
        pw, ph = page.rect.width, page.rect.height
        x0, y0 = pic["x0"] / norm * pw, pic["y0"] / norm * ph
        x1, y1 = pic["x1"] / norm * pw, pic["y1"] / norm * ph
        pix = page.get_pixmap(dpi=dpi, clip=fitz.Rect(x0, y0, x1, y1))
        img_path = output_dir / (
            f"{doc_name}_page{pic['page']+1}_"
            f"x{x0:.0f}_y{y0:.0f}_x{x1:.0f}_y{y1:.0f}.png"
        )
        img_path.write_bytes(pix.tobytes("png"))
        _log.info("Image exportée : %s", img_path)
    doc.close()

# PIPELINE PRINCIPAL
def main():
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    DOC_NAME = "Confirmer l'adhésion"

    pdf_path = PROJECT_ROOT / "data" / "input_files"  / f"{DOC_NAME}.pdf"
    doctags_path = PROJECT_ROOT / "data" / "output_files" / "stage1_test" / DOC_NAME / f"{DOC_NAME}.doctags"
    output_path = PROJECT_ROOT / "data" / "output_files" / "stage2_test" / DOC_NAME / f"{DOC_NAME}_with_pictures.doctags"
    markdown_path = PROJECT_ROOT / "data" / "output_files" / "stage2_test" / DOC_NAME / f"{DOC_NAME}_image_descriptions.md"
    images_dir = PROJECT_ROOT / "data" / "output_files" / "stage2_test" / DOC_NAME / "used_images"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 60)
    print("ÉTAPE 1 — Parsing des balises <picture> + éléments du doctags")
    print("=" * 60)
    pictures, content = parse_picture_tags(doctags_path)
    doc_elements = extract_document_elements(doctags_path)
    print(f"→ {len(doc_elements)} élément(s) total parsé(s) dans le doctags")
    export_picture_images(pdf_path, pictures, DOC_NAME, images_dir)

    if not pictures:
        print("Aucune balise <picture> trouvée, fin du script.")
        return

    print("\n" + "=" * 60)
    print("ÉTAPE 2 — Description des images avec contexte textuel")
    print("=" * 60)
    descriptions = describe_pictures_with_context(pdf_path, pictures, doc_elements)

    print("\n" + "=" * 60)
    print("ÉTAPE 3 — Remplacement des balises <picture> dans le doctags")
    print("=" * 60)
    enriched_content = replace_picture_tags_docling(content, pictures, descriptions)
    output_path.write_text(enriched_content, encoding="utf-8")
    print(f"Doctags enrichi sauvegardé : {output_path}")

    print("\n" + "=" * 60)
    print("ÉTAPE 4 — Export des descriptions en Markdown")
    print("=" * 60)
    export_descriptions_to_markdown(pictures, descriptions, DOC_NAME, markdown_path)
    print(f"Markdown sauvegardé : {markdown_path}")

if __name__ == "__main__":
    main()