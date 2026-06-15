"""
Stage 2 - Script de description des images avec contexte textuel via VLM (ON/OFF global et par document)
Script 4 : description_image_context.py

Ce script parse les balises <picture> du doctags pour extraire les coordonnées des images, 
puis crop les images correspondantes à partir du PDF source, 
construit un prompt contextualisé avec les éléments textuels avant/après l'image, 
et envoie le tout à un VLM pour générer une description de l'image.
"""
import logging
import os
import re
import base64
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
import fitz # PyMuPDF
import requests
import sys

# Appel des fonctions de configuration pour récupérer les chemins et paramètres nécessaires
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prompts.prompts import WIKI_PROMPT_TEMPLATE
from utils.config import load_vlm_config

config = load_vlm_config()
CA_PATH = config["CA_PATH"]
VLM_URL = config["VLM_URL"]
VLM_MODEL_NAME = config["VLM_MODEL_NAME"]
_log = logging.getLogger(__name__)

# Switch per-document
# True  = descriptions générées via VLM
# False = descriptions désactivées → chaîne vide (balises <picture> conservées)
# Peut aussi être surchargé via .env : ENABLE_IMAGE_DESCRIPTION=false
_ENV_SWITCH = os.environ.get("ENABLE_IMAGE_DESCRIPTION", "true").strip().lower()
_GLOBAL_SWITCH: bool = _ENV_SWITCH not in ("false", "0", "no")

DOC_IMAGE_DESCRIPTION: dict[str, bool] = {
    # Ajouter ici les documents pour lesquels désactiver les descriptions
    # "Nom du document": False,
    # "Adhésion traitement": False,   # disabled for this document
    # "Autre document": True,         # enabled
}

def is_image_description_enabled(doc_name: str) -> bool:
    """
    Docstring for is_image_description_enabled
    - Retourne True si la description VLM est activée pour ce document.

    :param doc_name: Description
    :type doc_name: str
    :return: Description
    :rtype: bool
    """
    return DOC_IMAGE_DESCRIPTION.get(doc_name, _GLOBAL_SWITCH)

# Constantes
NORM = 500
DPI = 150 # Norme Qwen3.5
N_BEFORE = 5 # Nombre de éléments textuels à inclure dans le contexte avant l'image
N_AFTER = 5 # Nombre de éléments textuels à inclure dans le contexte après l'image

language = "french"

# Prompt voir fichier Word pour les autres prompts
# {context_before} et {context_after} sont injectés dynamiquement par image

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


def remove_picture_tags(content: str) -> str:
    """
    Docstring for remove_picture_tags
    - Supprime les balises <picture> du contenu pour ne pas les inclure dans le contexte textuel. si on skip la description d'image

    :param content: Description
    :type content: str
    :return: Description
    :rtype: str
    """
    return re.sub(r'<picture><loc_\d+><loc_\d+><loc_\d+><loc_\d+></picture>', '', content)


# ÉTAPE 1 — Parsing du doctags
def parse_picture_tags(doctags_path: Path) -> tuple[list[dict], str]:
    """
    Docstring for parse_picture_tags
    - Retourne la liste des <picture> et le contenu brut du doctags. 
    - Cette fonction parse les balises du doctags pour extraire les éléments textuels et les images avec leurs coordonnées, 
    en conservant l'ordre d'apparition pour le contexte.
   

    :param doctags_path: Description
    :type doctags_path: Path
    :return: Description
    :rtype: tuple[list[dict], str]
    """
    content = doctags_path.read_text(encoding="utf-8") # Vériier le type d'encodage du fichier source UTF-8, Unicode ou autre
    pictures = []
    page = 0

    for line in content.splitlines(): # Parcours ligne par ligne pour trouver les balises <picture> et leurs coordonnées
        line_clean = re.sub(r"</?doctag>", "", line).strip() 
        if not line_clean:
            continue
        if "<page_footer>" in line_clean:
            page += 1
        for m in re.finditer(
            r"(<picture><loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)></picture>)", # le regex pour trouver les balises <picture> avec leurs coordonnées, m.groupe(1) contient la balise complète <picture>...</picture>, m.groupe(2) à m.groupe(5) contiennent respectivement x0, y0, x1, y1
            line_clean,
        ):
            x0, y0, x1, y1 = int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)) # Extraction des coordonnées de la balise <picture>, m.groupe(1) contient la balise complète <picture>...</picture>
            pictures.append({
                "page": page, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "raw_tag": m.group(1),
            })
            _log.debug("<picture> trouvée page=%d loc=(%d,%d,%d,%d)", page, x0, y0, x1, y1)

    _log.info("OK %d balise(s) <picture> trouvée(s)", len(pictures))
    return pictures, content


def extract_document_elements(doctags_path: Path) -> list[dict]:
    """
    Docstring for extract_document_elements
    - Parse tous les éléments (textes + images) dans l'ordre d'apparition.
    - Cette fonction est utilisée pour construire le contexte textuel autour de chaque image.

    :param doctags_path: Description
    :type doctags_path: Path
    :return: Description
    :rtype: list[dict]
    """
    content = doctags_path.read_text(encoding="utf-8") # Vériier le type d'encodage du fichier source UTF-8, Unicode ou autre
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
            r"<(?!/)(\w+)><loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)>([^<]*)", # Extraction des éléments textuels avec leurs coordonnées, m.groupe(1) contient le tag (ex: text, section_header_level_1, etc.) et m.groupe(6) contient le texte brut à l'intérieur de la balise
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
            r"<picture><loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)></picture>", # le regex pour trouver les balises <picture> avec leurs coordonnées, m.groupe(1) à m.groupe(4) contiennent respectivement x0, y0, x1, y1
            line_clean,
        ):
            x0, y0, x1, y1 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            elements.append({ # On ajoute les images à la liste des éléments avec un type "picture" pour les différencier des éléments textuels, et on laisse le champ "text" vide pour les images
                "type": "picture", "tag": "picture", "page": page,
                "x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": "",
            })

    _log.info("OK %d élément(s) total parsé(s) dans le doctags", len(elements))
    return elements


def build_context(pic: dict, doc_elements: list, n_before: int, n_after: int) -> tuple[str, str]:
    """
    Docstring for build_context
    - Retourne le contexte textuel avant/après une image.
    - Cette fonction construit le contexte textuel autour d'une image donnée en recherchant sa position dans la liste des éléments parsés du doctags, 
    puis en récupérant les éléments textuels qui la précèdent et la suivent.

    :param pic: Description
    :type pic: dict
    :param doc_elements: Description
    :type doc_elements: list
    :param n_before: Description
    :type n_before: int
    :param n_after: Description
    :type n_after: int
    :return: Description
    :rtype: tuple[str, str]
    """
    
    pic_index = next((
        idx for idx, e in enumerate(doc_elements) # On cherche l'index de l'image dans la liste des éléments parsés pour pouvoir récupérer les éléments textuels avant et après
        if e["type"] == "picture"
        and e["page"] == pic["page"]
        and e["x0"] == pic["x0"]
        and e["y0"] == pic["y0"]
    ), None)

    if pic_index is not None: # Si on trouve l'image dans les éléments parsés, on construit le contexte à partir des éléments textuels avant et après
        before = [e["text"] for e in doc_elements[:pic_index] if e["type"] == "text" and e["text"]][-n_before:]
        after = [e["text"] for e in doc_elements[pic_index + 1:] if e["type"] == "text" and e["text"]][:n_after]
    else: # Si on ne trouve pas l'image (cas rare), on retourne un contexte vide avec un message indiquant l'absence de contexte
        before, after = [], []

    ctx_before = "\n".join(f"> {t}" for t in before) if before else "> No context available before this image."
    ctx_after = "\n".join(f"> {t}" for t in after) if after else "> No context available after this image."
    return ctx_before, ctx_after


def export_picture_images(
    pdf_path: Path,
    pictures: list[dict],
    doc_name: str,
    output_dir: Path,
    norm: int = NORM,
    dpi: int = DPI,
) -> None:
    """
    Docstring for export_picture_images
    - Export des PNG (nommés avec les coordonnées du doctags) pour chaque balise <picture> trouvée, à partir du PDF source.
    - Cette fonction ouvre le PDF source, parcourt la liste des balises <picture> avec leurs coordonnées, 
    extrait la zone correspondante de chaque page, et sauvegarde cette zone en tant qu'image PNG dans le répertoire de sortie, 
    avec un nom de fichier basé sur les coordonnées du doctags pour faciliter le mapping avec les balises <picture>.

    :param pdf_path: Description
    :type pdf_path: Path
    :param pictures: Description
    :type pictures: list[dict]
    :param doc_name: Description
    :type doc_name: str
    :param output_dir: Description
    :type output_dir: Path
    :param norm: Description
    :type norm: int
    :param dpi: Description
    :type dpi: int
    """
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
            f"{doc_name}_page{pic['page']+1}_" # +1 pour que les pages soient 1-based dans le nommage
            f"x{pic['x0']}_y{pic['y0']}_x{pic['x1']}_y{pic['y1']}.png" # nommage avec les coordonnées originales du doctags pour faciliter le mapping avec les balises <picture>
        )
        img_path.write_bytes(pix.tobytes("png")) # Sauvegarde du PNG exporté
        _log.info("[%d/%d] PNG exporté : %s", i, len(pictures), img_path.name)

    doc.close()
    _log.info("OK %d PNG exporté(s)", len(pictures))


# Crop en mémoire + mise en queue + appel VLM direct
def crop_to_b64(pdf_doc: fitz.Document, pic: dict, norm: int = NORM, dpi: int = DPI) -> str:
    """
    Docstring for crop_to_b64
    - Crop une image du PDF et retourne le base64 en mémoire.
    - https://docs.nvidia.com/nim/vision-language-models/latest/vision-content-safety.html

    :param pdf_doc: Description
    :type pdf_doc: fitz.Document
    :param pic: Description
    :type pic: dict
    :param norm: Description
    :type norm: int
    :param dpi: Description
    :type dpi: int
    :return: Description
    :rtype: str
    """
    page = pdf_doc[pic["page"]]
    pw, ph = page.rect.width, page.rect.height
    pix = page.get_pixmap(dpi=dpi, clip=fitz.Rect(
        pic["x0"] / norm * pw, pic["y0"] / norm * ph,
        pic["x1"] / norm * pw, pic["y1"] / norm * ph,
    ))
    return base64.b64encode(pix.tobytes("png")).decode("utf-8")

def describe_image_b64(image_b64: str, prompt: str) -> str:
    """
    Docstring for describe_image_b64
    - Envoie une image (base64) + prompt au VLM et retourne la description.
    - Cette fonction construit la payload pour l'API du VLM en incluant le prompt contextualisé et l'image encodée en base64, 
    puis envoie la requête POST à l'endpoint du VLM, et retourne la description générée par le modèle. 
    En cas d'erreur, elle log l'erreur et retourne une chaîne vide.

    :param image_b64: Description
    :type image_b64: str
    :param prompt: Description
    :type prompt: str
    :return: Description
    :rtype: str
    """
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
        "chat_template_kwargs": {"enable_thinking": False},  # désactive le mode thinking Qwen3 (évite content=null)
    }
    try:
        response = requests.post(VLM_URL, json=payload, verify=CA_PATH, timeout=120)
        data = response.json()
        message = data["choices"][0]["message"]
        content = message.get("content")
        if content is not None:
            return content.strip()
        _log.warning("content=null, full message: %s", message)
        return ""
    except Exception as e:
        _log.info("Erreur API VLM : %s", e)
        return ""

# Worker thread consommateur de la queue
_results: dict[int, dict] = {}
_results_lock = threading.Lock()


def _vlm_worker(task_queue: queue.Queue) -> None:
    """
    Docstring for _vlm_worker
    - Fonction qui tourne dans un thread séparé pour consommer les tâches d'images à traiter, appeler le VLM et stocker les résultats.

    :param task_queue: Description
    :type task_queue: queue.Queue
    """
    while True:
        task: ImageTask | None = task_queue.get()
        if task is None:
            _log.info("Worker VLM arrêté.")
            break

        _log.info(
            "[%d/%d] -> Envoi au VLM — Page %d loc=(%d,%d,%d,%d)",
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
            _log.info("[%d/%d] OK Description reçue (%d chars)", task.index, task.total, len(description))
        else:
            _log.warning("[%d/%d] WARNING Aucune description retournée par le VLM", task.index, task.total)

        task_queue.task_done()

def describe_all_pictures(
    pdf_path: Path,
    pictures: list[dict],
    doc_elements: list[dict],
    doc_name: str = "",
    n_before: int = N_BEFORE,
    n_after: int = N_AFTER,
) -> dict[int, dict]:
    """
    Docstring for describe_all_pictures
    - Controle le switch global et par document pour activer ou désactiver la description des images.
    - Si désactivé, retourne un dict avec des descriptions vides (balises <picture> conservées). 
    - Si activé, lance le processus de description avec contexte et VLM.
    - Crop chaque image en mémoire, construit le prompt contextualisé,
    - met en queue et envoie au VLM via requests (pas Docling).
    - Retourne un dict indexé par position (1-based).
    
    :param pdf_path: Description
    :type pdf_path: Path
    :param pictures: Description
    :type pictures: list[dict]
    :param doc_elements: Description
    :type doc_elements: list[dict]
    :param doc_name: Description
    :type doc_name: str
    :param n_before: Description
    :type n_before: int
    :param n_after: Description
    :type n_after: int
    :return: Description
    :rtype: dict[int, dict]
    """
    if not is_image_description_enabled(doc_name):
        _log.warning("Descriptions VLM désactivées pour '%s' — balises <picture> conservées.", doc_name)
        return {
            i + 1: {
                "page": pic["page"],
                "x0": pic["x0"], "y0": pic["y0"],
                "x1": pic["x1"], "y1": pic["y1"],
                "description": "",   # empty string → tag preserved
                "raw_tag": pic["raw_tag"],
            }
            for i, pic in enumerate(pictures)
        }
    
    _results.clear()
    total = len(pictures)
    pdf_doc = fitz.open(str(pdf_path))
    task_q = queue.Queue()

    worker = threading.Thread(target=_vlm_worker, args=(task_q,), daemon=True)
    worker.start()
    _log.info("Mise en queue de %d image(s) (crop mémoire -> base64)", total)

    for i, pic in enumerate(pictures, start=1):
        ctx_before, ctx_after = build_context(pic, doc_elements, n_before, n_after) # Construit le contexte textuel autour de l'image à partir des éléments du doctags
        prompt = WIKI_PROMPT_TEMPLATE.format( # Injecte le contexte dans le prompt
            context_before=ctx_before,
            context_after=ctx_after,
            language=language,
        )
        image_b64 = crop_to_b64(pdf_doc, pic)
        task_q.put(ImageTask(
            index=i, total=total,
            page=pic["page"],
            x0=pic["x0"], y0=pic["y0"],
            x1=pic["x1"], y1=pic["y1"],
            image_b64=image_b64,
            prompt=prompt,
            raw_tag=pic["raw_tag"],
        ))

    pdf_doc.close()
    task_q.join()
    task_q.put(None)
    worker.join()

    described = sum(1 for r in _results.values() if r["description"])
    _log.info("OK %d/%d image(s) décrite(s) avec contexte", described, total)
    return dict(_results)


def replace_picture_tags(content: str, results: dict[int, dict]) -> str:
    """
    Docstring for replace_picture_tags
    - Remplacement des balises <picture> dans le doctags par les descriptions générées, en fonction des résultats du VLM.
    - Cette fonction parcourt les résultats des descriptions générées pour chaque image,
    et remplace les balises <picture> dans le contenu du doctags par les descriptions correspondantes.
    - Si une description est vide (cas de désactivation ou d'erreur), la balise <picture> est conservée et un warning est loggé.

    :param content: Description
    :type content: str
    :param results: Description
    :type results: dict[int, dict]
    :return: Description
    :rtype: str
    """
    replaced = 0
    for idx in sorted(results.keys()):
        r = results[idx]
        if not r["description"]:
            _log.warning("Pas de description pour <picture> idx=%d — tag conservé", idx)
            continue

        if r["raw_tag"] not in content:
            _log.error("raw_tag introuvable : %s", r["raw_tag"])
            continue

        desc = r["description"]
        raw_tag = re.escape(r["raw_tag"])
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


def export_descriptions_to_markdown(results: dict[int, dict], doc_name: str, output_path: Path) -> None:
    """
    Docstring for export_descriptions_to_markdown
    - Export Markdown

    :param results: Description
    :type results: dict[int, dict]
    :param doc_name: Description
    :type doc_name: str
    :param output_path: Description
    :type output_path: Path
    """
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


def main():
    DOC_NAME = os.environ.get("DOC_NAME", "")

    _log.info(
        "Descriptions VLM : %s pour '%s'",
        "ACTIVÉES" if is_image_description_enabled(DOC_NAME) else "DÉSACTIVÉES",
        DOC_NAME,
    )

    # Root
    pdf_path = PROJECT_ROOT / "data" / "input_files" / f"{DOC_NAME}.pdf"
    doctags_path = PROJECT_ROOT / "data" / "output_files" / "stage2_test" / DOC_NAME / f"{DOC_NAME}_reordered_with_tables.doctags"
    output_path = PROJECT_ROOT / "data" / "output_files" / "stage2_test" / DOC_NAME / f"{DOC_NAME}_reordered_with_tables_pictures.doctags"
    markdown_path = PROJECT_ROOT / "data" / "output_files" / "stage2_test" / DOC_NAME / f"{DOC_NAME}_image_descriptions.md"
    images_dir = PROJECT_ROOT / "data" / "output_files" / "stage2_test" / DOC_NAME / "used_images"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Parsing du doctags pour extraire les balises <picture> et les éléments textuels pour le contexte
    _log.info("ÉTAPE 1 — Parsing des balises <picture> + éléments du doctags")
    pictures, content = parse_picture_tags(doctags_path)
    doc_elements = extract_document_elements(doctags_path)

    if not pictures:
        _log.warning("Aucune balise <picture> trouvée, fin du script.")
        return

    # Extrait les images du PDF en PNG à partir des coordonnées des balises <picture> pour les sauvegarder
    _log.info("ÉTAPE 2 — Export des images PNG (coordonnées doctags)")
    export_picture_images(pdf_path, pictures, DOC_NAME, images_dir)

    # Description VLM via queue
    _log.info("ÉTAPE 3 — Description des images avec contexte textuel (queue → VLM direct)")
    results = describe_all_pictures(pdf_path, pictures, doc_elements, doc_name=DOC_NAME)

    # Remplacement des <picture> dans le doctags
    _log.info("ÉTAPE 4 — Remplacement des balises <picture> dans le doctags")
    if not is_image_description_enabled(DOC_NAME):
        _log.warning("Descriptions VLM désactivées pour '%s' — balises <picture> supprimées.", DOC_NAME)

        # Retire toutes les balises <picture>
        enriched_content = remove_picture_tags(content)
        output_path.write_text(enriched_content, encoding="utf-8")
        _log.info("OK - Doctags sans images sauvegardé : %s", output_path)

        # Optionnel : créer un Markdown vide pour la cohérence du pipeline, ou le supprimer
        markdown_path.write_text("", encoding="utf-8")
        return

    enriched_content = replace_picture_tags(content, results)
    output_path.write_text(enriched_content, encoding="utf-8")
    _log.info("OK - Doctags enrichi sauvegardé : %s", output_path)

    # Extrait les descriptions dans un Markdown
    _log.info("ÉTAPE 5 — Export des descriptions en Markdown")
    export_descriptions_to_markdown(results, DOC_NAME, markdown_path)

if __name__ == "__main__":
    main()