import pandas as pd
import jsonlines
from pathlib import Path
from dotenv import load_dotenv
import os

# Chargement de .env.test
dotenv_path = Path(__file__).resolve().parent.parent / ".env.test"
print("Loading dotenv from:", dotenv_path.resolve(), "exists:", dotenv_path.exists())
load_dotenv(dotenv_path=dotenv_path)

DOC_NAME   = os.environ.get("DOC_NAME", "")
tables_dir = Path(f"preprocessing/matthias_guideline_preprocessing/data/output_files/stage2_test/{DOC_NAME}/tables")

# Parcours de tous les CSV du dossier tables
csv_files = list(tables_dir.glob("*.csv"))

if not csv_files:
    print(f"Aucun fichier CSV trouvé dans : {tables_dir}")
else:
    print(f"{len(csv_files)} fichier(s) CSV trouvé(s) dans : {tables_dir}")
    for csv_path in sorted(csv_files):
        df = pd.read_csv(csv_path)

        # Supprime la colonne d'index automatique si présente
        if "Unnamed: 0" in df.columns:
            df = df.drop(columns=["Unnamed: 0"])

        jsonl_path = csv_path.with_suffix(".jsonl")
        with jsonlines.open(jsonl_path, mode="w") as writer:
            for record in df.to_dict(orient="records"):
                writer.write(record)

        print(f" {csv_path.name} → {jsonl_path.name}")