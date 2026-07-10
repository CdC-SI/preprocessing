"""
Stage 5 - Script d'enrichissement des métadonnées avec VLM
Script 2 : enhancement_metadata.py
Dans ce script, nous allons ajouter les appels vlm pour enrichir les métadonnées avec :
- resume: str, demande un VLM de résumer le document markdown généré en stage 4
- intent: list[str], demande un VLM de générer une liste d'intent à partir du markdown généré en stage 4
- hyq: list[str], demande un VLM de générer une liste de questions hypothétiques à partir du markdown généré en stage 4

Output (stage5_test/<doc_name>/):
    resume.md   - résumé court en markdown
    intent.json - liste d'intents (list[str])
    hyq.json    - liste de questions hypothétiques (list[str])

Usage:
    uv run python enhancement_metadata.py --dotenv .env.test --doc-name "MonDoc"
    uv run python enhancement_metadata.py --doc-name "MonDoc" --stage4 ./data/output_files_preprocessing/stage4_test --stage5 ./data/output_files_preprocessing/stage5_test
"""
import argparse
import json
import logging
import sys
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel

from prompts.metadata_prompts import (
    HYQ_PROMPT,
    INTENT_PROMPT_1,
    INTENT_PROMPT_2,
    INTENT_PROMPT_3,
    RESUME_PROMPT,
)
from utils.paths import project_root, resolve_doc_name
from utils.vlm_client import build_sync_client, build_vlm_config, text_completion_structured

DEFAULT_OUTPUT_FILES = project_root() / "data" / "output_files_preprocessing"
DEFAULT_STAGE4 = DEFAULT_OUTPUT_FILES
DEFAULT_STAGE5 = DEFAULT_OUTPUT_FILES

_log = logging.getLogger(__name__)


# Pydantic models pour le response_format des appels VLM

class ResumeOutput(BaseModel):
    resume: str


class IntentOutput(BaseModel):
    intent: list[str]


class HyQOutput(BaseModel):
    hyq: list[str]


# Stage 4 content loader
def _read_stage4(stage4_dir: Path, doc_name: str) -> str:
    single = stage4_dir / doc_name / f"{doc_name}_final.md"
    if single.exists():
        return single.read_text(encoding="utf-8")
    return ""


# Enrichissement avec le VLM

def generate_resume(markdown_content: str, client: OpenAI, vlm_model_name: str) -> str:
    """
    Génère un résumé court du document markdown via structured output.

    :param markdown_content: Contenu markdown du document (stage 4)
    :type markdown_content: str
    :param client: Client OpenAI configuré
    :type client: OpenAI
    :param vlm_model_name: Nom du modèle VLM
    :type vlm_model_name: str
    :return: Résumé court du document
    :rtype: str
    """
    result = text_completion_structured(client, vlm_model_name, RESUME_PROMPT, markdown_content, ResumeOutput)
    return result.resume


def generate_intent(markdown_content: str, client: OpenAI, vlm_model_name: str) -> list[str]:
    """
    Génère une liste d'intents/objectifs du document depuis 3 perspectives expertes.
    Les 3 appels sont fusionnés et dédupliqués pour enrichir le résultat.

    :param markdown_content: Contenu markdown du document (stage 4)
    :type markdown_content: str
    :param client: Client OpenAI configuré
    :type client: OpenAI
    :param vlm_model_name: Nom du modèle VLM
    :type vlm_model_name: str
    :return: Liste d'intents uniques extraits du document
    :rtype: list[str]
    """
    intents: list[str] = []
    seen: set[str] = set()
    for system_prompt in [INTENT_PROMPT_1, INTENT_PROMPT_2, INTENT_PROMPT_3]:
        result = text_completion_structured(client, vlm_model_name, system_prompt, markdown_content, IntentOutput)
        for item in result.intent:
            normalized = item.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                intents.append(normalized)
    return intents


def generate_hyq(markdown_content: str, client: OpenAI, vlm_model_name: str) -> list[str]:
    """
    Génère une liste de questions hypothétiques auxquelles le document peut répondre.

    :param markdown_content: Contenu markdown du document (stage 4)
    :type markdown_content: str
    :param client: Client OpenAI configuré
    :type client: OpenAI
    :param vlm_model_name: Nom du modèle VLM
    :type vlm_model_name: str
    :return: Liste de questions hypothétiques
    :rtype: list[str]
    """
    result = text_completion_structured(client, vlm_model_name, HYQ_PROMPT, markdown_content, HyQOutput)
    return result.hyq


# Stage 5 writer
def write_stage5(
    stage5_dir: Path,
    doc_name: str,
    resume: str,
    intent: list[str],
    hyq: list[str],
) -> Path:
    """
    Écrit les 3 fichiers d'enrichissement dans stage5_dir/<doc_name>/metadata/.

    Fichiers produits :
        metadata/resume.md   - résumé en texte markdown
        metadata/intent.json - liste d'intents (array JSON)
        metadata/hyq.json    - liste de questions hypothétiques (array JSON)

    :param stage5_dir: Dossier racine stage5
    :type stage5_dir: Path
    :param doc_name: Nom du document (sans extension)
    :type doc_name: str
    :param resume: Résumé court
    :type resume: str
    :param intent: Liste d'intents
    :type intent: list[str]
    :param hyq: Liste de questions hypothétiques
    :type hyq: list[str]
    :return: Chemin du dossier créé
    :rtype: Path
    """
    out_dir = stage5_dir / doc_name / "metadata"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "resume.md").write_text(resume, encoding="utf-8")
    (out_dir / "intent.json").write_text(
        json.dumps(intent, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "hyq.json").write_text(
        json.dumps(hyq, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out_dir


# Orchestration
def run_enhancement(
    doc_name: str,
    stage4_dir: Path,
    stage5_dir: Path,
    dotenv_path: Path | None = None,
) -> dict:
    """
    Lit le markdown stage4, appelle les 3 fonctions VLM et écrit les résultats dans stage5.
    La config VLM est chargée ici (pas au niveau module) pour respecter le --dotenv tardif.

    :param doc_name: Nom du document (sans extension)
    :type doc_name: str
    :param stage4_dir: Dossier stage4
    :type stage4_dir: Path
    :param stage5_dir: Dossier stage5
    :type stage5_dir: Path
    :param dotenv_path: Fichier .env à charger pour la config VLM
    :type dotenv_path: Path | None
    :return: Dictionnaire avec les 3 champs enrichis
    :rtype: dict
    """
    vlm_cfg = build_vlm_config(dotenv_path=dotenv_path)
    vlm_model_name = vlm_cfg.vlm_model_name
    client = build_sync_client(vlm_cfg)

    markdown_content = _read_stage4(stage4_dir, doc_name)
    if not markdown_content:
        raise FileNotFoundError(
            f"Aucun fichier markdown trouvé pour '{doc_name}' dans {stage4_dir}"
        )

    _log.info("Création du résumé")
    resume = generate_resume(markdown_content, client, vlm_model_name)

    _log.info("Création des 'intents'")
    intent = generate_intent(markdown_content, client, vlm_model_name)

    _log.info("Création des questions hypothétiques (hyq)")
    hyq = generate_hyq(markdown_content, client, vlm_model_name)

    out_dir = write_stage5(stage5_dir, doc_name, resume, intent, hyq)
    _log.info("stage5 écrit dans : %s", out_dir)

    return {"resume": resume, "intent": intent, "hyq": hyq}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrichit les métadonnées d'un document via VLM (resume, intent, hyq) et écrit les résultats dans stage5."
    )
    parser.add_argument(
        "--doc-name",
        type=str,
        default=None,
        help="Nom du document sans extension. Si absent, résout DOC_NAME depuis --dotenv ou l'environnement.",
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FICHIER",
        help="Fichier .env à charger (VLM_URL, VLM_CA_PEM, VLM_MODEL_NAME, DOC_NAME).",
    )
    parser.add_argument("--stage4", type=Path, default=DEFAULT_STAGE4)
    parser.add_argument("--stage5", type=Path, default=DEFAULT_STAGE5)
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Niveau de journalisation. Défaut : INFO.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    doc_name = resolve_doc_name(args, primary_flag="--doc-name")
    dotenv_path = args.dotenv

    _log.info("Enrichissement de : %s", doc_name)
    result = run_enhancement(doc_name, args.stage4, args.stage5, dotenv_path=dotenv_path)

    _log.info("resume : %s", result["resume"])
    _log.info("intent : %s", result["intent"])
    _log.info("hyq    : %s", result["hyq"])
    sys.exit(0)


if __name__ == "__main__":
    main()
