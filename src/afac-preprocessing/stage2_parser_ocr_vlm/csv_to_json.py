import pandas as pd
import jsonlines
from pathlib import Path
import os
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_vlm_config
config = load_vlm_config()

def deduplicate_columns(columns):
    # Docstring for deduplicate_columns
    # :param columns: Description
    # ici on ajoute un suffixe numérique aux colonnes dupliquées pour les rendre uniques (ex: "col", "col_2", "col_3", etc.)
    counts = {}
    new_cols = []
    for col in columns:
        if col not in counts:
            counts[col] = 1
            new_cols.append(col)
        else:
            counts[col] += 1
            new_cols.append(f"{col}_{counts[col]}")
    return new_cols

def safe_row_dict(row):
    # Convertit une ligne en dict JSON-safe (NaN → None, nombres → string)
    return {k: (None if pd.isna(v) else str(v)) for k, v in row.items()}

def has_numeric_headers(csv_path):
    # Vérifie si les noms de colonnes sont numériques (0, 1, 2...) -> vrai header potentiellement en row 1
    df_peek = pd.read_csv(csv_path, index_col = 0, nrows = 0)
    return all(str(col).strip().isdigit() for col in df_peek.columns)

def first_row_is_header(csv_path):
    # Vérifie si la première ligne de données contient des vrais noms de colonnes (texte non numérique)
    df_peek = pd.read_csv(csv_path, index_col = 0, nrows = 1)
    if df_peek.empty:
        return False
    first_row = df_peek.iloc[0]
    # La première ligne est un vrai header si toutes les valeurs sont des chaînes non numériques
    return all(
        isinstance(v, str) and not str(v).replace('.', '').replace('-', '').isdigit()
        for v in first_row
    )
DOC_NAME = os.environ.get("DOC_NAME", "")
tables_dir = Path(f"preprocessing/src/afc-preprocessing/data/output_files/stage2_test/{DOC_NAME}/tables")

csv_files = list(tables_dir.glob("*.csv"))

if not csv_files:
    print(f"Aucun fichier CSV trouvé dans : {tables_dir}")
else:
    print(f"{len(csv_files)} fichier(s) CSV trouvé(s) dans : {tables_dir}")
    for csv_path in sorted(csv_files):
        try:
            if has_numeric_headers(csv_path) and first_row_is_header(csv_path):
                # Cas : colonnes numériques (0,1,2...) ET première ligne = vrais headers texte
                df = pd.read_csv(csv_path, index_col = 0, header = 1)
            else:
                # Cas : headers déjà corrects en row 0, ou données purement numériques
                df = pd.read_csv(csv_path, index_col = 0, header = 0)

            df.columns = deduplicate_columns([str(col) for col in df.columns])

            if df.empty:
                print(f" {csv_path.name} -> skipped (empty table)")
                continue

            jsonl_path = csv_path.with_suffix(".jsonl")
            with jsonlines.open(jsonl_path, mode="w") as writer:
                for _, row in df.iterrows():
                    writer.write(safe_row_dict(row))
                    # writer._fp.write("\n") # Ajoute une ligne vide après chaque ligne JSONL

            print(f" {csv_path.name} → {jsonl_path.name}")
        except Exception as e:
            print(f" {csv_path.name} → ERROR: {e}")