"""Constants for the retrieval evaluation pipeline."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STAGE5 = PROJECT_ROOT / "data" / "output_files"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "evaluation_results"

TOP_KS: list[int] = [1, 3, 5, 10, 20]

DOC_FOLDER_SUFFIX = ""
HYQ_FOLDER_PREFIX = "hyq_"
DOC_CSV_SUFFIX = "_final.csv"
