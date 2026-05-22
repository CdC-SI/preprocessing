import os
import base64
import re
import queue
import threading
from pathlib import Path
from dataclasses import dataclass
import fitz
import requests
from dotenv import load_dotenv
import certifi

# Chargement de l'environnement
dotenv_path = Path(__file__).resolve().parent.parent / ".env.test"
load_dotenv(dotenv_path=dotenv_path)

# Gestion du certificat CA
custom_ca = os.environ.get("VLM_CA_PEM")
ca_path = custom_ca if custom_ca and Path(custom_ca).exists() else certifi.where()

VLM_URL = os.environ.get("VLM_URL", "")
VLM_MODEL_NAME = os.environ.get("VLM_MODEL_NAME", "")
if not VLM_URL:
    raise RuntimeError(f"VLM_URL not set. Ensure {dotenv_path} exists and contains VLM_URL.")

print(f"VLM_URL: {VLM_URL}\nVLM_MODEL_NAME: {VLM_MODEL_NAME}")

DOC_NAME = "Lacunes d'assurance "
PDF_PATH = Path(f"preprocessing/matthias_guideline_preprocessing/data/input_files/{DOC_NAME}.pdf")
DOCTAGS_PATH = Path(f"preprocessing/matthias_guideline_preprocessing/data/output_files/stage1_test/{DOC_NAME}/{DOC_NAME}_reordered.doctags")
md_path = Path(f"preprocessing/matthias_guideline_preprocessing/data/output_files/stage2_test/{DOC_NAME}/{DOC_NAME}_image_descriptions.md")
md_path.parent.mkdir(parents=True, exist_ok=True)

NORM = 500
DPI  = 150 # Norme Qwen3.5
language = "french"

WIKI_PROMPT = f"""
You are a document analysis assistant.

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
Step 1: analyse the image.
Step 2: Determine whether the image adds any business value.
Step 3:
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
[IMAGE DESCRIPTION] Concise, business-oriented description.
Otherwise, do not include any additional relevant visual information.
Always respond in **{language}**.
"""

# Dataclass pour chaque image en mémoire
@dataclass
class ImageTask:
    index: int
    total: int
    page: int
    x0: int
    y0: int
    x1: int
    y1: int
    image_b64:  str   # image encodée en base64 directement en mémoire

# Extraction des balises <picture> depuis le doctags
def parse_picture_tags(doctags_path: Path) -> list[dict]:
    pictures = []
    content  = doctags_path.read_text(encoding="utf-8")
    page     = 0
    for line in content.splitlines():
        if "<page_break>" in line:
            page += 1
        m = re.search(r"<picture><loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)>", line)
        if m:
            pictures.append({
                "page": page,
                "x0": int(m.group(1)),
                "y0": int(m.group(2)),
                "x1": int(m.group(3)),
                "y1": int(m.group(4)),
            })
    return pictures

# Crop directement en mémoire → base64
def crop_image_to_b64(pdf_doc: fitz.Document, pic: dict, norm: int = NORM, dpi: int = DPI) -> str:
    page = pdf_doc[pic["page"]]
    pw, ph = page.rect.width, page.rect.height
    pix = page.get_pixmap(dpi=dpi, clip=fitz.Rect(
        pic["x0"] / norm * pw, pic["y0"] / norm * ph,
        pic["x1"] / norm * pw, pic["y1"] / norm * ph,
    ))
    return base64.b64encode(pix.tobytes("png")).decode("utf-8")

# Envoi direct au VLM
def describe_image_b64(image_b64: str) -> str:
    payload = {
        "model": VLM_MODEL_NAME,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": WIKI_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}}
                ]
            }
        ],
        "max_tokens": 512
    }
    try:
        response = requests.post(VLM_URL, json=payload, verify=ca_path, timeout=120)
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  Erreur API : {e}")
        return ""

# Worker consommateur de la queue
results: dict[int, dict] = {}
results_lock = threading.Lock()

def vlm_worker(task_queue: queue.Queue):
    while True:
        task: ImageTask = task_queue.get()
        if task is None:
            break
        print(f"  [{task.index}/{task.total}] Page {task.page+1} "
              f"loc=({task.x0},{task.y0},{task.x1},{task.y1}) → envoi au VLM...")
        description = describe_image_b64(task.image_b64)
        with results_lock:
            results[task.index] = {
                "page": task.page,
                "x0": task.x0, "y0": task.y0,
                "x1": task.x1, "y1": task.y1,
                "description": description,
            }
        status = f"{len(description)} chars" if description else " Aucune description"
        print(f" [{task.index}/{task.total}] → {status}")
        task_queue.task_done()

# Pipeline principal
def main():
    pictures = parse_picture_tags(DOCTAGS_PATH)
    total    = len(pictures)
    print(f"\n→ {total} image(s) détectée(s) dans le doctags\n")

    if not total:
        print("Aucune balise <picture> trouvée.")
        return

    pdf_doc    = fitz.open(str(PDF_PATH))
    task_queue = queue.Queue()

    # Démarre le worker (1 seul pour respecter les limites de l'API)
    worker_thread = threading.Thread(target=vlm_worker, args=(task_queue,), daemon=True)
    worker_thread.start()

    # ── Mise en queue des images directement en mémoire (pas de fichier PNG) ──
    print("=" * 60)
    print("Mise en queue des images (crop en mémoire → base64)")
    print("=" * 60)
    for i, pic in enumerate(pictures, start=1):
        image_b64 = crop_image_to_b64(pdf_doc, pic)
        task_queue.put(ImageTask(
            index=i, total=total,
            page=pic["page"],
            x0=pic["x0"], y0=pic["y0"],
            x1=pic["x1"], y1=pic["y1"],
            image_b64=image_b64,
        ))
        print(f"  → Image {i}/{total} mise en queue (page {pic['page']+1})")

    pdf_doc.close()

    # Attend que toutes les tâches soient traitées
    task_queue.join()
    task_queue.put(None)
    worker_thread.join()

    # Génération du markdown
    sections = []
    nb_described = 0
    nb_missing = 0

    for i in sorted(results.keys()):
        r = results[i]
        loc_str = f"loc({r['x0']}, {r['y0']}, {r['x1']}, {r['y1']})"
        page_str = f"Page {r['page']+1}"

        if r["description"]:
            nb_described += 1
            sections.append(
                f"## OK Image {i}/{total} — {page_str} | `{loc_str}`\n\n"
                f"{r['description']}\n"
            )
        else:
            nb_missing += 1
            sections.append(
                f"## Warning Image {i}/{total} — {page_str} | `{loc_str}`\n\n"
                f"> **Aucune description générée.**\n"
                f"> *Vérifier le matching des coordonnées ou la réponse du VLM.*\n"
            )

    header = (
        f"# Descriptions des images — *{DOC_NAME}*\n\n"
        f"> Généré automatiquement par le pipeline VLM  \n"
        f"> Document source : `{DOC_NAME}.pdf`  \n"
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
    md_path.write_text(md_content, encoding="utf-8")
    print(f"\nMarkdown sauvegardé : {md_path}")

if __name__ == "__main__":
    main()