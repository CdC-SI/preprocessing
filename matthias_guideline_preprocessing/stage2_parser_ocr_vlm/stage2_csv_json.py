import pandas as pd
import jsonlines
from pathlib import Path

# Root
DOC_NAME = "Confirmer l'adhésion" # CHANGER SELON LES TESTS
csv_path = Path(f"preprocessing/matthias_guideline_preprocessing/data/output_files/stage2_test/{DOC_NAME}/tables/{DOC_NAME}-table-1.csv") # CHANGER CHEMIN POUR CHAQUE TEST

# Lecture du CSV en gardant toutes les colonnes (y compris l'ID)
df = pd.read_csv(csv_path)

# Export chaque ligne comme un objet JSON (une ligne = un objet)
jsonl_table_path = csv_path.with_name(f"{DOC_NAME}_table.jsonl")
with jsonlines.open(jsonl_table_path, mode='w') as writer:
    for record in df.to_dict(orient='records'):
        writer.write(record)
print(f"Table exportée en JSONL (une ligne = un objet) : {jsonl_table_path}")