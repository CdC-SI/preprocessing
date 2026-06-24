import argparse
import base64
import logging
import os
import queue
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlparse, urlunparse
import fitz  # PyMuPDF
import requests

from prompts.prompts import WIKI_PROMPT_TEMPLATE
from utils.config import load_vlm_config
from utils.paths import project_root

_log = logging.getLogger(__name__)


# Constantes
NORM = 500
DPI_DEFAULT = 150
N_BEFORE = 5
N_AFTER = 5

TEXT_TAGS = {
    "text", "section_header_level_1", "section_header_level_2",
    "section_header_level_3", "list_item", "caption", "footnote",
    "page_header", "page_footer",
}

# Modèles de données est classes
class PictureTag(TypedDict):
    page: int
    x0: int
    y0: int
    x1: int
    y1: int
    raw_tag: str


class DocElement(TypedDict):
    type: str   # "text" | "picture" | "other"
    tag: str
    page: int
    x0: int
    y0: int
    x1: int
    y1: int
    text: str


class VLMResult(TypedDict):
    page: int
    x0: int
    y0: int
    x1: int
    y1: int
    description: str
    raw_tag: str


@dataclass
class VLMConfig:
    url: str
    ca_path: str
    model_name: str
    timeout: int = 120


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
    prompt: str
    raw_tag: str


# Logique métier (fonctions pures / sans état global)
def remove_picture_tags(content: str) -> str:
    """
    Docstring for remove_picture_tags
    Supprime les balises <picture> quand la description est désactivée.

    :param content: Description
    :type content: str
    :return: Description
    :rtype: str
    """
    return re.sub(r'<picture><loc_\d+><loc_\d+><loc_\d+><loc_\d+></picture>', '', content)


def parse_picture_tags(content: str) -> list[PictureTag]:
    """
    Docstring for parse_picture_tags
    Parse les balises <picture> depuis le contenu doctags. Retourne la liste des pics.

    :param content: Description
    :type content: str
    :return: Description
    :rtype: list[PictureTag]
    """
    pictures = []
    page = 0

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

    _log.info("%d balise(s) <picture> trouvée(s)", len(pictures))
    return pictures


def extract_document_elements(content: str) -> list[DocElement]:
    """
    Docstring for extract_document_elements
    Parse tous les éléments (textes + images) dans l'ordre d'apparition.

    :param content: Description
    :type content: str
    :return: Description
    :rtype: list[DocElement]
    """
    elements = []
    page = 0

    for line in content.splitlines():
        line_clean = re.sub(r"</?doctag>", "", line).strip()
        if not line_clean:
            continue
        if "<page_footer>" in line_clean:
            page += 1

        for m in re.finditer(
            r"<(?!/)(\w+)><loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)>([^<]*)",
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

        for m in re.finditer(
            r"<picture><loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)></picture>",
            line_clean,
        ):
            x0, y0, x1, y1 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            elements.append({
                "type": "picture", "tag": "picture", "page": page,
                "x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": "",
            })

    _log.info("%d élément(s) total parsé(s) dans le doctags", len(elements))
    return elements


def build_context(pic: PictureTag, doc_elements: list[DocElement], n_before: int, n_after: int) -> tuple[str, str]:
    """
    Docstring for build_context
    Retourne le contexte textuel (ctx_before, ctx_after) autour d'une image.

    :param pic: Description
    :type pic: PictureTag
    :param doc_elements: Description
    :type doc_elements: list[DocElement]
    :param n_before: Description
    :type n_before: int
    :param n_after: Description
    :type n_after: int
    :return: Description
    :rtype: tuple[str, str]
    """
    pic_index = next((
        idx for idx, e in enumerate(doc_elements)
        if e["type"] == "picture"
        and e["page"] == pic["page"]
        and e["x0"] == pic["x0"]
        and e["y0"] == pic["y0"]
    ), None)

    if pic_index is not None:
        before = [e["text"] for e in doc_elements[:pic_index] if e["type"] == "text" and e["text"]][-n_before:]
        after = [e["text"] for e in doc_elements[pic_index + 1:] if e["type"] == "text" and e["text"]][:n_after]
    else:
        before, after = [], []

    ctx_before = "\n".join(f"> {t}" for t in before) if before else "> No context available before this image."
    ctx_after = "\n".join(f"> {t}" for t in after) if after else "> No context available after this image."
    return ctx_before, ctx_after


def _clip_rect(page: fitz.Page, pic: PictureTag, norm: int) -> fitz.Rect:
    """Convertit les coordonnées DocTags normalisées en Rect PyMuPDF pour le crop."""
    pw, ph = page.rect.width, page.rect.height
    return fitz.Rect(
        pic["x0"] / norm * pw, pic["y0"] / norm * ph,
        pic["x1"] / norm * pw, pic["y1"] / norm * ph,
    )


def load_preextracted_b64(images_dir: Path, index: int, page: int) -> str | None:
    """Charge une image pré-extraite par Docling depuis le disque (pic{i:03d}_page{page+1}.png)."""
    path = images_dir / f"pic{index:03d}_page{page + 1}.png"
    if not path.exists():
        _log.warning("Image pré-extraite introuvable : %s", path)
        return None
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def crop_to_b64(pdf_doc: fitz.Document, pic: PictureTag, norm: int = NORM, dpi: int = DPI_DEFAULT) -> str:
    """
    Docstring for crop_to_b64
    Crop une image du PDF et retourne le base64 PNG en mémoire.

    :param pdf_doc: Description
    :type pdf_doc: fitz.Document
    :param pic: Description
    :type pic: PictureTag
    :param norm: Description
    :type norm: int
    :param dpi: Description
    :type dpi: int
    :return: Description
    :rtype: str
    """
    page = pdf_doc[pic["page"]]
    pix = page.get_pixmap(dpi=dpi, clip=_clip_rect(page, pic, norm))
    return base64.b64encode(pix.tobytes("png")).decode("utf-8")


def describe_image_b64(image_b64: str, prompt: str, vlm_cfg: VLMConfig) -> str:
    """
    Docstring for describe_image_b64
    Envoie image (base64) + prompt au VLM et retourne la description.

    :param image_b64: Description
    :type image_b64: str
    :param prompt: Description
    :type prompt: str
    :param vlm_cfg: Description
    :type vlm_cfg: VLMConfig
    :return: Description
    :rtype: str
    """
    payload = {
        "model": vlm_cfg.model_name,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }],
        "max_tokens": 3000,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        response = requests.post(vlm_cfg.url, json=payload, verify=vlm_cfg.ca_path or False, timeout=vlm_cfg.timeout)
        response.raise_for_status()
        data = response.json()
        message = data["choices"][0]["message"]
        content = message.get("content")
        if content is not None:
            return content.strip()
        _log.warning("content=null, full message: %s", message)
        return ""
    except Exception:
        _log.exception("Erreur API VLM")
        return ""


def check_vlm_connection(vlm_cfg: VLMConfig) -> bool:
    """
    Vérifie la connectivité au VLM via GET /v1/models avant tout traitement.
    Loggue OK avec les modèles disponibles, ou ERROR avec la cause.
    Retourne True si le VLM est joignable et le modèle configuré est présent.
    """
    parsed = urlparse(vlm_cfg.url)
    models_url = urlunparse((parsed.scheme, parsed.netloc, "/v1/models", "", "", ""))
    try:
        resp = requests.get(models_url, verify=vlm_cfg.ca_path or False, timeout=10)
        resp.raise_for_status()
        available = [m.get("id", "?") for m in resp.json().get("data", [])]
        _log.info("VLM OK — %s | modèles disponibles : %s", models_url, available or ["(aucun)"])
        if vlm_cfg.model_name and vlm_cfg.model_name not in available:
            _log.warning(
                "Modèle configuré '%s' absent de la liste VLM : %s",
                vlm_cfg.model_name, available,
            )
        return True
    except requests.exceptions.ConnectionError:
        _log.error("VLM non joignable — connexion refusée : %s", models_url)
    except requests.exceptions.Timeout:
        _log.error("VLM non joignable — timeout (10s) : %s", models_url)
    except requests.exceptions.HTTPError as exc:
        _log.exception("VLM non joignable — HTTP %s : %s", exc.response.status_code, models_url)
    except Exception:
        _log.exception("Erreur inattendue lors de la vérification VLM : %s", models_url)
    return False


def export_picture_images(
    pdf_path: Path,
    pictures: list[PictureTag],
    doc_name: str,
    output_dir: Path,
    norm: int = NORM,
    dpi: int = DPI_DEFAULT,
) -> None:
    """
    Docstring for export_picture_images
    Export des PNG pour chaque balise <picture>, nommés par coordonnées doctags.
    :param pdf_path: Description
    :type pdf_path: Path
    :param pictures: Description
    :type pictures: list[PictureTag]
    :param doc_name: Description
    :type doc_name: str
    :param output_dir: Description
    :type output_dir: Path
    :param norm: Description
    :type norm: int
    :param dpi: Description
    :type dpi: int
    """
    _log.info("Export des PNG dans : %s", output_dir)
# tester avec docling pour skip la normalisation de fitz pymyPDF
    with fitz.open(str(pdf_path)) as doc:
        for i, pic in enumerate(pictures, start=1):
            page = doc[pic["page"]]
            pix = page.get_pixmap(dpi=dpi, clip=_clip_rect(page, pic, norm))
            img_path = output_dir / (
                f"{doc_name}_page{pic['page'] + 1}_"
                f"x{pic['x0']}_y{pic['y0']}_x{pic['x1']}_y{pic['y1']}.png"
            )
            img_path.write_bytes(pix.tobytes("png"))
            _log.info("[%d/%d] PNG exporté : %s", i, len(pictures), img_path.name)

    _log.info("%d PNG exporté(s)", len(pictures))


def _vlm_worker(
    task_queue: queue.Queue,
    results: dict[int, VLMResult],
    results_lock: threading.Lock,
    vlm_cfg: VLMConfig,
) -> None:
    """
    Docstring for _vlm_worker
    Thread consommateur : appelle le VLM et stocke les résultats dans results

    :param task_queue: Description
    :type task_queue: queue.Queue
    :param results: Description
    :type results: dict[int, VLMResult]
    :param results_lock: Description
    :type results_lock: threading.Lock
    :param vlm_cfg: Description
    :type vlm_cfg: VLMConfig
    """
    while True:
        task: ImageTask | None = task_queue.get()
        if task is None:
            break

        _log.info(
            "[%d/%d] → VLM — Page %d loc=(%d,%d,%d,%d)",
            task.index, task.total, task.page + 1,
            task.x0, task.y0, task.x1, task.y1,
        )
        description = describe_image_b64(task.image_b64, task.prompt, vlm_cfg)

        with results_lock:
            results[task.index] = VLMResult(
                page=task.page,
                x0=task.x0, y0=task.y0,
                x1=task.x1, y1=task.y1,
                description=description,
                raw_tag=task.raw_tag,
            )

        if description:
            _log.info("[%d/%d] Description reçue (%d chars)", task.index, task.total, len(description))
        else:
            _log.warning("[%d/%d] Aucune description retournée par le VLM", task.index, task.total)

        task_queue.task_done()


def describe_all_pictures(
    pdf_path: Path,
    pictures: list[PictureTag],
    doc_elements: list[DocElement],
    vlm_cfg: VLMConfig,
    language: str = "french",
    n_before: int = N_BEFORE,
    n_after: int = N_AFTER,
    n_workers: int = 1,
    dpi: int = DPI_DEFAULT,
    norm: int = NORM,
    preextracted_images_dir: Path | None = None,
) -> dict[int, VLMResult]:
    """
    Docstring for describe_all_pictures
    Crop chaque image (ou charge depuis preextracted_images_dir), construit le prompt contextualisé, envoie au VLM.
    Retourne un dict indexé (1-based) : {index: VLMResult}.

    :param pdf_path: Description
    :type pdf_path: Path
    :param pictures: Description
    :type pictures: list[PictureTag]
    :param doc_elements: Description
    :type doc_elements: list[DocElement]
    :param vlm_cfg: Description
    :type vlm_cfg: VLMConfig
    :param language: Description
    :type language: str
    :param n_before: Description
    :type n_before: int
    :param n_after: Description
    :type n_after: int
    :param n_workers: Description
    :type n_workers: int
    :param preextracted_images_dir: Dossier contenant les PNGs pré-extraits par Docling (pic{i:03d}_page{p}.png).
    :type preextracted_images_dir: Path | None
    :return: Description
    :rtype: dict[int, VLMResult]
    """
    results: dict[int, VLMResult] = {}
    results_lock = threading.Lock()
    total = len(pictures)
    task_q: queue.Queue = queue.Queue()

    workers = [
        threading.Thread(target=_vlm_worker, args=(task_q, results, results_lock, vlm_cfg), daemon=True)
        for _ in range(n_workers)
    ]
    for w in workers:
        w.start()

    if preextracted_images_dir:
        _log.info("Mise en queue de %d image(s) — source : %s — %d worker(s)", total, preextracted_images_dir, n_workers)
    else:
        _log.info("Mise en queue de %d image(s) — source : fitz crop — %d worker(s)", total, n_workers)

    with fitz.open(str(pdf_path)) as pdf_doc:
        for i, pic in enumerate(pictures, start=1):
            ctx_before, ctx_after = build_context(pic, doc_elements, n_before, n_after)
            prompt = WIKI_PROMPT_TEMPLATE.format(
                context_before=ctx_before,
                context_after=ctx_after,
                language=language,
            )
            if preextracted_images_dir:
                image_b64 = load_preextracted_b64(preextracted_images_dir, i, pic["page"])
                if image_b64 is None:
                    _log.warning("[%d/%d] Image pré-extraite manquante — fallback fitz crop", i, total)
                    image_b64 = crop_to_b64(pdf_doc, pic, norm=norm, dpi=dpi)
            else:
                image_b64 = crop_to_b64(pdf_doc, pic, norm=norm, dpi=dpi)
            task_q.put(ImageTask(
                index=i, total=total,
                page=pic["page"],
                x0=pic["x0"], y0=pic["y0"],
                x1=pic["x1"], y1=pic["y1"],
                image_b64=image_b64,
                prompt=prompt,
                raw_tag=pic["raw_tag"],
            ))

    task_q.join()          # attendre que toutes les tâches soient traitées
    for _ in workers:      # puis envoyer les sentinels d'arrêt
        task_q.put(None)
    for w in workers:
        w.join()

    described = sum(1 for r in results.values() if r["description"])
    _log.info("%d/%d image(s) décrite(s) avec contexte", described, total)
    return results


def replace_picture_tags(content: str, results: dict[int, VLMResult]) -> str:
    """
    Docstring for replace_picture_tags
    Remplace les balises <picture> par les descriptions VLM dans le contenu doctags.

    :param content: Description
    :type content: str
    :param results: Description
    :type results: dict[int, VLMResult]
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

        pattern_in_item = re.compile(r"<list_item>\s*" + raw_tag + r"\s*</list_item>", re.DOTALL)
        if pattern_in_item.search(content):
            content = pattern_in_item.sub(f"<list_item>{desc_inline}</list_item>", content, count=1)
            _log.info("[%d] <picture> dans <list_item> → texte inline", idx)
        elif re.search(r"<list_item[^>]*>.*?</list_item>\s*" + raw_tag, content, re.DOTALL):
            content = content.replace(r["raw_tag"], f"<list_item>{desc_inline}</list_item>", 1)
            _log.info("[%d] <picture> entre list_items → <list_item> texte inline", idx)
        else:
            content = content.replace(r["raw_tag"], f"<text>\n{desc}\n</text>", 1)
            _log.info("[%d] <picture> standalone → <text>", idx)

        replaced += 1

    _log.info("%d/%d balise(s) <picture> remplacée(s)", replaced, len(results))
    return content


def export_descriptions_to_markdown(
    results: dict[int, VLMResult],
    doc_name: str,
    output_path: Path,
    vlm_model_name: str,
) -> None:
    """
    Docstring for export_descriptions_to_markdown
    Exporte les descriptions VLM dans un fichier Markdown de référence.

    :param results: Description
    :type results: dict[int, VLMResult]
    :param doc_name: Description
    :type doc_name: str
    :param output_path: Description
    :type output_path: Path
    :param vlm_model_name: Description
    :type vlm_model_name: str
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
                f"## OK - Image {i}/{total} — {page_str} | `{loc_str}`\n\n{r['description']}\n"
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
        f"> Modèle VLM : `{vlm_model_name}`\n\n---\n\n"
    )
    summary = (
        f"## Résumé\n\n"
        f"- Images détectées  : **{total}**\n"
        f"- Images décrites   : **{nb_described}**\n"
        f"- Images manquantes : **{nb_missing}**\n"
    )
    output_path.write_text(
        header + "\n\n---\n\n".join(sections) + "\n\n---\n\n" + summary,
        encoding="utf-8",
    )
    _log.info("Markdown exporté (%d/%d images décrites) : %s", nb_described, total, output_path)


# CLI
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Décrit les images d'un .doctags via VLM avec contexte textuel. "
            "Remplace les balises <picture> par les descriptions générées."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  uv run python description_image_context_modulaire.py \\\n"
            "      --doctags data/output_files/MonDoc/MonDoc_reordered_with_tables.doctags \\\n"
            "      --input   data/input_files/MonDoc.pdf \\\n"
            "      --image-description\n"
            "  uv run python description_image_context_modulaire.py --dotenv .env.test\n"
            "  uv run python description_image_context_modulaire.py --dotenv .env.test --no-image-description\n"
        ),
    )
    parser.add_argument(
        "--doctags", "-d",
        type=Path, default=None,
        help=(
            "Fichier .doctags source (produit par load_jsonline_doctags_modulaire.py). "
            "Si absent, résout data/output_files/<DOC_NAME>/<DOC_NAME>_reordered_with_tables.doctags."
        ),
    )
    parser.add_argument(
        "--input", "-i",
        type=Path, default=None,
        help="Fichier PDF source pour le crop des images. Si absent, résout data/input_files/<DOC_NAME>.pdf.",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path, default=None,
        help="Fichier .doctags enrichi en sortie. Défaut : <stem>_pictures.doctags dans le même dossier.",
    )
    parser.add_argument(
        "--markdown", "-m",
        type=Path, default=None,
        help="Fichier Markdown de sortie pour les descriptions. Défaut : <doc_name>_image_descriptions.md.",
    )
    parser.add_argument(
        "--images-dir",
        type=Path, default=None,
        help="Dossier de sortie pour les PNG exportés. Défaut : used_images/ dans le dossier du --doctags.",
    )
    parser.add_argument(
        "--doc-name",
        type=str, default=None,
        help="Nom du document (logs et Markdown). Si absent, déduit de DOC_NAME ou du nom du fichier --doctags.",
    )
    parser.add_argument(
        "--language",
        type=str, default="french",
        help="Langue de la réponse VLM. Défaut : french.",
    )
    parser.add_argument(
        "--image-description",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Active (--image-description) ou désactive (--no-image-description) la description VLM. "
            "Défaut : False (désactivé)."
        ),
    )
    parser.add_argument(
        "--workers", "-w",
        type=int, default=1, metavar="N",
        help="Nombre de threads VLM parallèles. Défaut : 1 (séquentiel, safe pour les API à rate-limit).",
    )
    parser.add_argument(
        "--timeout",
        type=int, default=120, metavar="SEC",
        help="Timeout en secondes pour chaque appel VLM. Défaut : 120.",
    )
    parser.add_argument(
        "--dpi",
        type=int, default=DPI_DEFAULT, metavar="N",
        help=f"Résolution DPI pour le crop des images PDF. Défaut : {DPI_DEFAULT}.",
    )
    parser.add_argument(
        "--n-before",
        type=int, default=N_BEFORE, metavar="N",
        help=f"Nombre d'éléments textuels avant l'image pour le contexte VLM. Défaut : {N_BEFORE}.",
    )
    parser.add_argument(
        "--n-after",
        type=int, default=N_AFTER, metavar="N",
        help=f"Nombre d'éléments textuels après l'image pour le contexte VLM. Défaut : {N_AFTER}.",
    )
    parser.add_argument(
        "--norm",
        type=int, default=NORM, metavar="N",
        help=f"Facteur de normalisation des coordonnées DocTags (système de coordonnées du .doctags). Défaut : {NORM}.",
    )
    parser.add_argument(
        "--dotenv",
        type=Path, default=None, metavar="FICHIER",
        help="Fichier .env à charger (VLM_URL, VLM_CA_PEM, VLM_MODEL_NAME, DOC_NAME). Toujours chargé pour la config VLM ; si absent, les variables sont lues depuis l'environnement.",
    )
    parser.add_argument(
        "--preextracted-images-dir",
        type=Path, default=None, metavar="DOSSIER",
        help=(
            "Dossier contenant les PNGs pré-extraits par pipeline_multietape_modular.py --extract-images "
            "(nommés pic{i:03d}_page{p}.png). Si fourni, remplace le crop fitz. "
            "Défaut : None (fitz utilisé)."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Niveau de log. Défaut : INFO. Passer DEBUG pour diagnostiquer un step Tekton.",
    )
    return parser.parse_args()


# Résolution des chemins
def _load_doc_name(args: argparse.Namespace) -> str:
    """
    Docstring for _load_doc_name
    Charge DOC_NAME depuis --dotenv ou l'environnement. Lève SystemExit si absent.

    :param args: Description
    :type args: argparse.Namespace
    :return: Description
    :rtype: str
    """
    if args.dotenv and not Path(args.dotenv).resolve().exists():
        raise SystemExit(f"Erreur : fichier .env introuvable — {Path(args.dotenv).resolve()}")
    doc_name = os.environ.get("DOC_NAME", "").strip()
    if not doc_name:
        raise SystemExit(
            "Erreur : fournir --doctags et --pdf, ou --dotenv <fichier> avec DOC_NAME, "
            "ou définir la variable DOC_NAME dans l'environnement."
        )
    return doc_name


def resolve_doctags(args: argparse.Namespace) -> Path:
    """
    Docstring for resolve_doctags

    :param args: Description
    :type args: argparse.Namespace
    :return: Description
    :rtype: Path
    """
    if args.doctags:
        return args.doctags.resolve()
    doc_name = _load_doc_name(args)
    return project_root() / "data" / "output_files" / doc_name / f"{doc_name}_reordered_with_tables.doctags"


def resolve_pdf(args: argparse.Namespace, doctags_path: Path) -> Path:
    """
    Docstring for resolve_pdf

    :param args: Description
    :type args: argparse.Namespace
    :param doctags_path: Description
    :type doctags_path: Path
    :return: Description
    :rtype: Path
    """
    if args.input:
        return args.input.resolve()
    env_name = os.environ.get("DOC_NAME", "").strip()
    doc_name = env_name or doctags_path.stem.split("_")[0]
    return project_root() / "data" / "input_files" / f"{doc_name}.pdf"


def _resolve_image_doc_name(args: argparse.Namespace, doctags_path: Path) -> str:
    """
    Docstring for _resolve_image_doc_name

    :param args: Description
    :type args: argparse.Namespace
    :param doctags_path: Description
    :type doctags_path: Path
    :return: Description
    :rtype: str
    """
    if args.doc_name:
        return args.doc_name
    env_name = os.environ.get("DOC_NAME", "").strip()
    if env_name:
        return env_name
    return doctags_path.stem.split("_")[0]


def resolve_output(args: argparse.Namespace, doctags_path: Path) -> Path:
    """
    Docstring for resolve_output
    
    :param args: Description
    :type args: argparse.Namespace
    :param doctags_path: Description
    :type doctags_path: Path
    :return: Description
    :rtype: Path
    """
    if args.output:
        return args.output.resolve()
    return doctags_path.parent / f"{doctags_path.stem}_pictures.doctags"


def resolve_markdown(args: argparse.Namespace, doctags_path: Path, doc_name: str) -> Path:
    """
    Docstring for resolve_markdown
    
    :param args: Description
    :type args: argparse.Namespace
    :param doctags_path: Description
    :type doctags_path: Path
    :param doc_name: Description
    :type doc_name: str
    :return: Description
    :rtype: Path
    """
    if args.markdown:
        return args.markdown.resolve()
    return doctags_path.parent / f"{doc_name}_image_descriptions.md"


def resolve_images_dir(args: argparse.Namespace, doctags_path: Path) -> Path:
    """
    Docstring for resolve_images_dir
    
    :param args: Description
    :type args: argparse.Namespace
    :param doctags_path: Description
    :type doctags_path: Path
    :return: Description
    :rtype: Path
    """
    if args.images_dir:
        return args.images_dir.resolve()
    return doctags_path.parent / "used_images"


# Point d'entrée
def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Config VLM — chargée depuis load_vlm_config (dotenv + CA certifi)
    # Le dotenv est chargé ICI, donc ENABLE_IMAGE_DESCRIPTION est lisible après.
    try:
        config = load_vlm_config(dotenv_path=args.dotenv)
    except RuntimeError as exc:
        # dotenv chargé mais VLM_URL absent — vérifier si la description est requise
        needs_vlm = args.image_description or os.environ.get("ENABLE_IMAGE_DESCRIPTION", "false").strip().lower() == "true"
        if needs_vlm:
            raise SystemExit(str(exc)) from exc
        config = {"VLM_URL": "", "CA_PATH": "", "VLM_MODEL_NAME": ""}

    # --image-description ou ENABLE_IMAGE_DESCRIPTION=true dans le .env (chargé ci-dessus)
    image_desc_enabled = args.image_description or os.environ.get("ENABLE_IMAGE_DESCRIPTION", "false").strip().lower() == "true"

    vlm_cfg = VLMConfig(
        url=config["VLM_URL"],
        ca_path=config["CA_PATH"],
        model_name=config["VLM_MODEL_NAME"],
        timeout=args.timeout,
    )

    # Résolution des chemins (DOC_NAME déjà chargé via load_vlm_config)
    doctags_path = resolve_doctags(args)
    if not doctags_path.exists():
        raise SystemExit(f"Erreur : fichier .doctags introuvable — {doctags_path}")

    pdf_path = resolve_pdf(args, doctags_path)
    if not pdf_path.exists():
        raise SystemExit(f"Erreur : fichier PDF introuvable — {pdf_path}")

    doc_name = _resolve_image_doc_name(args, doctags_path)
    output_path = resolve_output(args, doctags_path)
    markdown_path = resolve_markdown(args, doctags_path, doc_name)
    images_dir = resolve_images_dir(args, doctags_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)

    _log.info("Doctags source   : %s", doctags_path)
    _log.info("PDF source       : %s", pdf_path)
    _log.info("Sortie doctags   : %s", output_path)
    _log.info("Markdown         : %s", markdown_path)
    _log.info("Images PNG       : %s", images_dir)
    _log.info(
        "Descriptions VLM : %s pour '%s'",
        "ACTIVÉES" if image_desc_enabled else "DÉSACTIVÉES",
        doc_name,
    )

    # Étape 1 — Parsing (lecture unique du fichier)
    _log.info("ÉTAPE 1 — Parsing des balises <picture> + éléments du doctags")
    content = doctags_path.read_text(encoding="utf-8")
    pictures = parse_picture_tags(content)
    doc_elements = extract_document_elements(content)

    if not pictures:
        _log.warning("Aucune balise <picture> trouvée — doctags copié sans modification.")
        output_path.write_text(content, encoding="utf-8")
        markdown_path.write_text("", encoding="utf-8")
        sys.exit(0)

    # Étape 2 — Export PNG fitz (ignoré si des images pré-extraites sont disponibles)
    # Résolution de preextracted_dir :
    #   1. --preextracted-images-dir explicite
    #   2. used_images/ dans le dossier doctags si le dossier existe (généré par pipeline_multietape --extract-images)
    #   3. None → crop fitz
    if args.preextracted_images_dir:
        preextracted_dir = args.preextracted_images_dir.resolve()
    elif images_dir.exists() and any(images_dir.glob("pic*.png")):
        preextracted_dir = images_dir
        _log.info("Images pré-extraites détectées automatiquement : %s", preextracted_dir)
    else:
        preextracted_dir = None

    if preextracted_dir:
        _log.info("ÉTAPE 2 — Images pré-extraites : %s (crop fitz ignoré)", preextracted_dir)
    else:
        _log.info("ÉTAPE 2 — Export des images PNG (coordonnées doctags via fitz)")
        images_dir.mkdir(parents=True, exist_ok=True)
        export_picture_images(pdf_path, pictures, doc_name, images_dir, dpi=args.dpi, norm=args.norm)

    # Étape 3 — Description VLM (ou suppression si désactivée)
    _log.info("ÉTAPE 3 — Description des images avec contexte textuel")
    if not image_desc_enabled:
        _log.warning("Descriptions VLM désactivées pour '%s' — balises <picture> supprimées.", doc_name)
        output_path.write_text(remove_picture_tags(content), encoding="utf-8")
        markdown_path.write_text("", encoding="utf-8")
        sys.exit(0)

    if not vlm_cfg.model_name:
        raise SystemExit(
            "Erreur : VLM_MODEL_NAME non défini. "
            "Fournir --dotenv <fichier> ou définir VLM_MODEL_NAME dans l'environnement."
        )

    if not check_vlm_connection(vlm_cfg):
        _log.error("Arrêt — VLM non joignable. Vérifier VLM_URL et VLM_CA_PEM.")
        sys.exit(1)

    try:
        results = describe_all_pictures(
            pdf_path, pictures, doc_elements, vlm_cfg,
            language=args.language,
            n_workers=args.workers,
            n_before=args.n_before,
            n_after=args.n_after,
            dpi=args.dpi,
            norm=args.norm,
            preextracted_images_dir=preextracted_dir,
        )
    except Exception:
        _log.exception("Erreur lors de la description des images de '%s'", doc_name)
        sys.exit(1)

    # Étape 4 — Remplacement des balises <picture>
    _log.info("ÉTAPE 4 — Remplacement des balises <picture> dans le doctags")
    output_path.write_text(replace_picture_tags(content, results), encoding="utf-8")
    _log.info("Doctags enrichi sauvegardé : %s", output_path)

    # Étape 5 — Export Markdown
    _log.info("ÉTAPE 5 — Export des descriptions en Markdown")
    export_descriptions_to_markdown(results, doc_name, markdown_path, vlm_cfg.model_name)

    sys.exit(0)


if __name__ == "__main__":
    main()
    