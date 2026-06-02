# Lancement successif de la pipeline 
import subprocess
import sys
from pathlib import Path
import os
from concurrent.futures import ProcessPoolExecutor, as_completed    

scripts = [
    "preprocessing/src/afac-preprocessing/stage1_multi_steps_detection/pipeline_multietape.py",
    "preprocessing/src/afac-preprocessing/stage1_multi_steps_detection/opencv_checker.py",
    "preprocessing/src/afac-preprocessing/stage1_multi_steps_detection/control_doctags_balise_loc_y0.py",
    "preprocessing/src/afac-preprocessing/stage2_parser_ocr_vlm/export_table_docling.py",
    "preprocessing/src/afac-preprocessing/stage2_parser_ocr_vlm/csv_to_json.py",
    "preprocessing/src/afac-preprocessing/stage2_parser_ocr_vlm/load_jsonline_docling.py",
    "preprocessing/src/afac-preprocessing/stage2_parser_ocr_vlm/description_image_context.py",
    "preprocessing/src/afac-preprocessing/stage3_url_extraction_vlm_parse/get_url.py",
    "preprocessing/src/afac-preprocessing/stage3_url_extraction_vlm_parse/url_tuning_vlm.py",
    "preprocessing/src/afac-preprocessing/stage4_doctags_to_markdown/convert_doctags_to_markdown.py",   
]

pdfs = sorted(Path("preprocessing/src/afac-preprocessing/data/input_files").glob("*.pdf"),
                key=lambda p: p.name# trier les fichiers PDF par ordre alphabétique de leur nom sans extension
                )  

def run_pipeline(pdf):
    print(f"=== Lancement du traitement du document : {pdf}...") 
    env = os.environ.copy()# copie de l'environnement courant
    env["DOC_NAME"] = pdf.stem  # définir la variable d'environnement DOC_NAME
    env["PYTHONUNBUFFERED"] = "1"  # désactiver le buffering pour que les sorties soient affichées en temps réel
    
    for script in scripts:
        print(f"=== Lancement du script : {script}...")
    
        result = subprocess.run(
            [sys.executable, script], 
            stdout=sys.stdout,
            stderr=sys.stderr, 
            text=True,
            env=env, # passer l'environnement modifié avec DOC_NAME au processus enfant
        )

        if result.returncode != 0:
            print(f"[{pdf.name}] ERREUR dans {script}")
            return f"FAILED: {pdf.name} at {script}"

    print(f"=== Fin traitement : {pdf.name} ===")
    return f"OK: {pdf.name}"

if __name__ == "__main__":
    with ProcessPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(run_pipeline, pdf) for pdf in pdfs]
        for future in as_completed(futures):
            try:
                result = future.result()
                print(f"\n>>> RESULT: {result}")
            except Exception as e:
                print(f"\n>>> EXCEPTION: {e}")

print("\nToutes les étapes de la pipeline ont été exécutées.")
