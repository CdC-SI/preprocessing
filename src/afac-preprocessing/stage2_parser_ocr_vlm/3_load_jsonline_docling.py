import json
import re
from pathlib import Path
import os
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_vlm_config
config = load_vlm_config()

def load_jsonl_rows(jsonl_path: Path) -> list[dict]:
    # Charge toutes les lignes d'un fichier JSONL.
    rows = []
    with open(jsonl_path, encoding="utf-8") as f: # Vérifier le type d'encodage du fichier source UTF-8, Unicode ou autre
        for line in f:
            line = line.strip() # Nettoie les espaces et les retours à la ligne
            if line:
                rows.append(json.loads(line))
    return rows

def jsonl_rows_to_block(rows: list[dict]) -> str:
    # Convertit une liste de dicts en bloc texte JSONL.
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)

def replace_otsl_with_jsonl(
    doctags_path: Path,
    tables_dir: Path,
    output_path: Path,
) -> None:
    # Remplace chaque balise <otsl>...</otsl> dans le doctags par une balise <text>
    # contenant le contenu JSONL correspondant.
    # Les fichiers JSONL sont chargés depuis tables_dir, triés par nom, dans l'ordre
    # d'apparition des balises <otsl>.
    content = doctags_path.read_text(encoding="utf-8")

    # Récupère tous les fichiers JSONL du dossier tables, triés par nom
    jsonl_files = sorted(tables_dir.glob("*.jsonl"))
    if not jsonl_files:
        print(f" Aucun fichier JSONL trouvé dans : {tables_dir}")
         # Même sans table, on copie le fichier doctags d'entrée en sortie pour la suite de la pipeline
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        print(f" Fichier doctags copié sans modification : {output_path}")
        return

    print(f" {len(jsonl_files)} fichier(s) JSONL trouvé(s) dans : {tables_dir}")
    for f in jsonl_files:
        print(f"   • {f.name}")

    # Charge toutes les tables dans l'ordre
    all_tables = []
    for jsonl_path in jsonl_files:
        rows = load_jsonl_rows(jsonl_path)
        if rows:
            all_tables.append((jsonl_path.name, rows))
            print(f"  → {jsonl_path.name} : {len(rows)} ligne(s) chargée(s)")

    # Trouve toutes les balises <otsl>...</otsl> dans l'ordre 
    otsl_pattern = re.compile(r"<otsl>.*?</otsl>", re.DOTALL)
    matches = list(otsl_pattern.finditer(content))

    if not matches:
        print(" Aucune balise <otsl> trouvée dans le doctags.")
        return

    if len(matches) != len(all_tables):
        print(
            f" {len(matches)} balise(s) <otsl> trouvée(s) "
            f"mais {len(all_tables)} table(s) JSONL disponibles.\n"
            f" Remplacement dans l'ordre jusqu'à épuisement."
        )

    # Remplacement dans l'ordre
    result = content
    offset = 0

    for i, match in enumerate(matches): # Pour chaque balise <otsl> trouvée, on remplace par la table JSONL correspondante dans l'ordre
        if i >= len(all_tables):
            print(f" Pas de table JSONL pour la balise <otsl> n°{i+1}, ignorée.")
            break

        jsonl_name, rows = all_tables[i] # Récupère le nom et les lignes de la table JSONL correspondante
        jsonl_block = jsonl_rows_to_block(rows) # Convertit les lignes JSONL en bloc de texte à insérer
        new_tag = f"<text>\n{jsonl_block}\n</text>" # Nouveau contenu à insérer à la place de <otsl>...</otsl>

        start = match.start() + offset
        end = match.end() + offset

        result = result[:start] + new_tag + result[end:] # Remplace la balise <otsl>...</otsl> par le nouveau contenu JSONL
        offset += len(new_tag) - (match.end() - match.start()) # Met à jour l'offset pour les remplacements suivants

        print(
            f"  Table {i+1}/{len(matches)} remplacée "
            f"← {jsonl_name} ({len(rows)} ligne(s), {len(jsonl_block)} chars)"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result, encoding="utf-8")
    print(f"\n Doctags enrichi sauvegardé : {output_path}")

if __name__ == "__main__":
    # Root et sorties
    DOC_NAME = os.environ.get("DOC_NAME", "")
    PROJECT_ROOT = Path(__file__).resolve().parents[1]

    doctags_path = PROJECT_ROOT / "data" / "output_files" / "stage1_test" / DOC_NAME / f"{DOC_NAME}_reordered.doctags"
    tables_dir = PROJECT_ROOT / "data" / "output_files" / "stage2_test" / DOC_NAME / "tables"
    output_path = PROJECT_ROOT / "data" / "output_files" / "stage2_test" / DOC_NAME / f"{DOC_NAME}_reordered_with_tables.doctags"

    print("\n" + "=" * 60)
    print(f"Remplacement des tables dans : {doctags_path.name}")
    print("=" * 60)

    replace_otsl_with_jsonl(doctags_path, tables_dir, output_path)