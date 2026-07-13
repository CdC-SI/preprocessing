"""
Constantes de configuration pour le pipeline d'évaluation du Retrieval des documents de l'AFAC.

Ce module centralise tous les paramètres fixes utilisés par le pipeline :
chemins de répertoires, valeurs de k pour les métriques de classement,
et conventions de nommage des fichiers/dossiers.
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STAGE5 = PROJECT_ROOT / "data" / "output_files_preprocessing"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "pipeline_evaluation"

TOP_KS: list[int] = [1, 3, 5, 10, 20]
CANONICAL_K: int = 5 # Valeur de k canonique pour les rapports synthétiques (ex. : nDCG@5, MRR@5)

DOC_FOLDER_SUFFIX = ""
HYQ_FOLDER_PREFIX = "hyq_"
DOC_CSV_SUFFIX = "_final.csv"
