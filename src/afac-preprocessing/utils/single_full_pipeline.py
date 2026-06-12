# Lancement successif de la pipeline pour un seul document
import subprocess
import sys
from pathlib import Path
import os
from dotenv import load_dotenv

STAGE_ROOT = Path("preprocessing/src/afac-preprocessing")
PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env.test")


def _run_step(cmd: list[str], env: dict) -> None:
    """Exécute une étape du pipeline. Quitte avec le code d'erreur si l'étape échoue."""
    label = Path(cmd[1]).name
    print(f"\n=== Lancement du script : {label}...")
    result = subprocess.run(cmd, stdout=sys.stdout, stderr=sys.stderr, text=True, env=env)
    if result.returncode != 0:
        print(f"=== Erreur lors du {label} (code {result.returncode})")
        sys.exit(result.returncode)


doc_name = os.environ.get("DOC_NAME", "")  # ex : "Adhésion traitement" – défini dans .env.test (sans .pdf)
if not doc_name:
    raise RuntimeError("DOC_NAME non défini. Ajoutez DOC_NAME=<nom_sans_extension> dans .env.test")
doc_title = f"{doc_name}.pdf"  # ex : "Adhésion traitement.pdf"
pdf = STAGE_ROOT / "data" / "input_files" / doc_title

env = os.environ.copy()  # copie de l'environnement courant
env["DOC_NAME"] = doc_name  # transmis aux scripts stage 1–4 via env (stem uniquement)

# Stage 1 – 4 : lisent DOC_NAME depuis l'environnement, pas de positional arg
stage1_4 = [
    STAGE_ROOT / "stage1_multi_steps_detection/pipeline_multietape.py",
    STAGE_ROOT / "stage1_multi_steps_detection/opencv_checker.py",
    STAGE_ROOT / "stage1_multi_steps_detection/control_doctags_balise_loc_y0_v2.py",
    STAGE_ROOT / "stage2_parser_ocr_vlm/export_table_docling.py",
    STAGE_ROOT / "stage2_parser_ocr_vlm/csv_to_json.py",
    STAGE_ROOT / "stage2_parser_ocr_vlm/load_jsonline_docling.py",
    STAGE_ROOT / "stage2_parser_ocr_vlm/description_image_context.py",
    STAGE_ROOT / "stage3_url_extraction_vlm_parse/get_url.py",
    STAGE_ROOT / "stage3_url_extraction_vlm_parse/url_tuning_vlm_v3.py",  # Changer selon la version utilisée : Base, v2, v3
    STAGE_ROOT / "stage4_doctags_to_markdown/convert_doctags_to_markdown.py",
    STAGE_ROOT / "stage4_doctags_to_markdown/markdown_control_vlm.py",
]

for script in stage1_4:
    _run_step([sys.executable, str(script)], env)

# Stage 5 : prennent des positional args CLI
# metadata_generation.py attend le chemin relatif du document dans folder_source
# (ex : "Sous-dossier/Adhésion traitement.pdf") – ajuster si le document est dans un sous-dossier
STAGE5 = STAGE_ROOT / "stage5_metadata"

stage5 = [
    # metadata_generation.py appelle run_enhancement et run_embedding en interne
    [sys.executable, str(STAGE5 / "metadata_generation.py"), doc_title],
    # hyq_embedding_doc.py est indépendant : génère les embeddings des questions hyq
    [sys.executable, str(STAGE5 / "hyq_embedding_doc.py"), doc_name, doc_title],
]

for cmd in stage5:
    _run_step(cmd, env)

print("\nToutes les étapes de la pipeline ont été exécutées avec succès.")
