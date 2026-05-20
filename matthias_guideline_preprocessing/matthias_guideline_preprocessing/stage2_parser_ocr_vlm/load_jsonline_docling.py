import json
import re
from pathlib import Path


def load_jsonl_tables(jsonl_path: Path) -> list[list[dict]]:
    # Charge un fichier JSONL et regroupe les lignes par table.
    # Chaque table est séparée par une ligne vide dans le JSONL, ou si le fichier contient une seule table, retourne une seule liste.
    tables = []
    current_table = []

    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                # Ligne vide = séparateur entre tables
                if current_table:
                    tables.append(current_table)
                    current_table = []
            else:
                current_table.append(json.loads(line))

    if current_table:
        tables.append(current_table)

    print(f"→ {len(tables)} table(s) chargée(s) depuis {jsonl_path.name}")
    return tables


def jsonl_rows_to_block(rows: list[dict]) -> str:

    # Convertit une liste de dicts (lignes JSONL) en bloc texte JSONL, une ligne JSON par entrée.
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)


def replace_otsl_with_jsonl(
    doctags_path: Path,
    jsonl_path: Path,
    output_path: Path,
) -> None:
    # Remplace chaque balise <otsl>...</otsl> dans le doctags par une balise <table_data> contenant le contenu JSONL correspondant, dans l'ordre d'apparition des tables.
    content = doctags_path.read_text(encoding="utf-8")
    tables = load_jsonl_tables(jsonl_path)

    # Trouve toutes les balises <otsl>...</otsl> dans l'ordre
    otsl_pattern = re.compile(r"<otsl>.*?</otsl>", re.DOTALL)
    matches = list(otsl_pattern.finditer(content))

    if not matches:
        print("Aucune balise <otsl> trouvée dans le doctags.")
        return

    if len(matches) != len(tables):
        print(
            f"Attention : {len(matches)} balise(s) <otsl> trouvée(s) "
            f"mais {len(tables)} table(s) dans le JSONL.\n"
            f"Remplacement dans l'ordre jusqu'à épuisement des tables disponibles."
        )

    # Reconstruction du contenu en remplaçant chaque <otsl> par la table JSONL
    result = content
    offset = 0 # décalage dû aux remplacements précédents (longueur diff)

    for i, match in enumerate(matches):
        if i >= len(tables):
            print(f"Pas de table JSONL pour la balise <otsl> n°{i+1}, ignorée.")
            break

        jsonl_block = jsonl_rows_to_block(tables[i])
        new_tag = f"<text>\n{jsonl_block}\n</text>"

        start = match.start() + offset
        end = match.end() + offset

        result = result[:start] + new_tag + result[end:]
        offset += len(new_tag) - (match.end() - match.start())

        print(
            f"Table {i+1}/{len(matches)} remplacée "
            f"({len(tables[i])} ligne(s), {len(jsonl_block)} chars)"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result, encoding="utf-8")
    print(f"\nDoctags enrichi sauvegardé : {output_path}")


if __name__ == "__main__":
    DOC_NAME = "Confirmer l'adhésion"   # CHANGER SELON LES TESTS
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    doctags_path = PROJECT_ROOT / "data" / "output_files" / "stage2_test"/ DOC_NAME / f"{DOC_NAME}_with_pictures.doctags"
    jsonl_path = PROJECT_ROOT / "data" / "output_files" / "stage2_test"/ DOC_NAME / "tables" / f"{DOC_NAME}_table.jsonl"
    output_path = PROJECT_ROOT / "data" / "output_files" / "stage2_test"/ DOC_NAME / f"{DOC_NAME}_with_pictures_tables.doctags"

    print("\n" + "=" * 60)
    print(f"Remplacement des tables dans : {doctags_path.name}")
    print("=" * 60)

    replace_otsl_with_jsonl(doctags_path, jsonl_path, output_path)