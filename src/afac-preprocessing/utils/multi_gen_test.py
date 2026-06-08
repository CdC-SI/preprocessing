# launch_pipeline.py

import subprocess
import sys
from pathlib import Path
import os
import re

# Nombre d'exécutions souhaité
NB_GENERATIONS = 1  # nombre de génération

scripts = [
    "preprocessing/src/afac-preprocessing/stage3_url_extraction_vlm_parse/url_tuning_vlm_v3.py", # Changer selon les version utilisée Base, v2, v3, ...)
    "preprocessing/src/afac-preprocessing/stage4_doctags_to_markdown/convert_doctags_to_markdown.py", 
]


def update_gen_id(gen_id: int) -> None:
    """
    Docstring for update_gen_id
    - Met à jour la variable d'environnement GEN_ID dans le fichier .env.test avec la valeur fournie en argument. 
    - Si la variable GEN_ID existe déjà, elle est remplacée par la nouvelle valeur. 
    - Sinon, elle est ajoutée à la fin du fichier.

    :param gen_id: Description
    :type gen_id: int
    """
    if env_file.exists():
        content = env_file.read_text(encoding="utf-8")
    else:
        content = ""

    if re.search(r"^GEN_ID=.*$", content, flags=re.MULTILINE):
        content = re.sub(
            r"^GEN_ID=.*$",
            f'GEN_ID="{gen_id}"',
            content,
            flags=re.MULTILINE,
        )
    else:
        if content and not content.endswith("\n"):
            content += "\n"
        content += f'GEN_ID="{gen_id}"\n'

    env_file.write_text(content, encoding="utf-8")

pdf = Path("preprocessing/src/afac-preprocessing/data/input_files/Domicilié dans les DOM-TOM, UE.pdf")  # CHANGER SELON LES TESTS
env_file = Path(".env.test")

for gen_id in range(1, NB_GENERATIONS + 1):

    print(f"\nDébut génération {gen_id}/{NB_GENERATIONS}")

     # Mise à jour du .env
    update_gen_id(gen_id)

    # Préparation de l'environnement
    env = os.environ.copy()
    env["DOC_NAME"] = pdf.stem
    env["GEN_ID"] = str(gen_id)

    for script in scripts:

        print(f"\n=== Lancement du script : {script}")

        result = subprocess.run(
            [sys.executable, script],
            stdout=sys.stdout,
            stderr=sys.stderr,
            text=True,
            env=env,
        )

        if result.returncode != 0:
            print(f"Erreur lors de l'exécution de {script}")
            sys.exit(result.returncode)

    print(f"✓ Génération {gen_id} terminée")

print("\nToutes les générations ont été exécutées avec succès.")