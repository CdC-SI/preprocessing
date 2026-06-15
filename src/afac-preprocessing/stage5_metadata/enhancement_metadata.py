
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
    python3 enhancement_metadata.py "Annulation et retaxation"
    python3 enhancement_metadata.py "Détachement" --stage4 ./data/output_files/stage4_test --stage5 ./data/output_files/stage5_test
"""
import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse
import httpx
from openai import OpenAI
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from prompts.metadata_prompts import (
    HYQ_PROMPT,
    INTENT_PROMPT_1,
    INTENT_PROMPT_2,
    INTENT_PROMPT_3,
    RESUME_PROMPT,
)
from utils.config import load_vlm_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STAGE4 = PROJECT_ROOT / "data" / "output_files" / "stage4_test"
DEFAULT_STAGE5 = PROJECT_ROOT / "data" / "output_files" / "stage5_test"

# Config & client

_config = load_vlm_config()
CA_PATH = _config["CA_PATH"]
VLM_MODEL_NAME = _config["VLM_MODEL_NAME"]

# Le client OpenAI attend l'url de base qui termine par /v1, pas besoin de mettre en plus /chat/completions
_parsed = urlparse(_config["VLM_URL"])
_base_url = urlunparse((_parsed.scheme, _parsed.netloc, "/v1", "", "", ""))

client = OpenAI(
    base_url=_base_url,
    api_key="no-key", # Si server interne, pas besoin de clé d'API
    http_client=httpx.Client(verify=CA_PATH),
)


# Pydantic models pour le response_format des appels VLM


class ResumeOutput(BaseModel):
    resume: str


class IntentOutput(BaseModel):
    intent: list[str]


class HyQOutput(BaseModel):
    hyq: list[str]


# Stage 4 content loader (mirrors get_stage4_content in metadata_generation.py)
def _read_stage4(stage4_dir: Path, doc_name: str) -> str:
    single = stage4_dir / f"{doc_name}_vlm_check.md"
    if single.exists():
        return single.read_text(encoding="utf-8")
    return ""


# Enrichissement avec le VLM 
def generate_resume(markdown_content: str) -> str:
    """
    Génère un résumé court du document markdown via structured output.

    :param markdown_content: Contenu markdown du document (stage 4)
    :type markdown_content: str
    :return: Résumé court du document
    :rtype: str
    """
    response = client.beta.chat.completions.parse(
        model=VLM_MODEL_NAME,
        messages=[
            {"role": "system", "content": RESUME_PROMPT},
            {"role": "user", "content": markdown_content},
        ],
        response_format=ResumeOutput,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    return response.choices[0].message.parsed.resume


def generate_intent(markdown_content: str) -> list[str]:
    """
    Génère une liste d'intents/objectifs du document depuis 3 perspectives expertes.
    Les 3 appels sont fusionnés et dédupliqués pour enrichir le résultat.

    :param markdown_content: Contenu markdown du document (stage 4)
    :type markdown_content: str
    :return: Liste d'intents uniques extraits du document
    :rtype: list[str]
    """
    intents: list[str] = []
    seen: set[str] = set()
    for system_prompt in [INTENT_PROMPT_1, INTENT_PROMPT_2, INTENT_PROMPT_3]:
        response = client.beta.chat.completions.parse(
            model=VLM_MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": markdown_content},
            ],
            response_format=IntentOutput,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        for item in response.choices[0].message.parsed.intent:
            normalized = item.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                intents.append(normalized)
    return intents


def generate_hyq(markdown_content: str) -> list[str]:
    """
    Génère une liste de questions hypothétiques auxquelles le document peut répondre.

    :param markdown_content: Contenu markdown du document (stage 4)
    :type markdown_content: str
    :return: Liste de questions hypothétiques
    :rtype: list[str]
    """
    response = client.beta.chat.completions.parse(
        model=VLM_MODEL_NAME,
        messages=[
            {"role": "system", "content": HYQ_PROMPT},
            {"role": "user", "content": markdown_content},
        ],
        response_format=HyQOutput,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    return response.choices[0].message.parsed.hyq


# Stage 5 writer
def write_stage5(
    stage5_dir: Path,
    doc_name: str,
    resume: str,
    intent: list[str],
    hyq: list[str],
) -> Path:
    """
    Écrit les 3 fichiers d'enrichissement dans stage5_dir/<doc_name>/.

    Fichiers produits :
        resume.md   - résumé en texte markdown
        intent.json - liste d'intents (array JSON)
        hyq.json    - liste de questions hypothétiques (array JSON)

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
    out_dir = stage5_dir / doc_name
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
def run_enhancement(doc_name: str, stage4_dir: Path, stage5_dir: Path) -> dict:
    """
    Lit le markdown stage4, appelle les 3 fonctions VLM et écrit les résultats dans stage5.

    :param doc_name: Nom du document (sans extension)
    :type doc_name: str
    :param stage4_dir: Dossier stage4
    :type stage4_dir: Path
    :param stage5_dir: Dossier stage5
    :type stage5_dir: Path
    :return: Dictionnaire avec les 3 champs enrichis
    :rtype: dict
    """
    markdown_content = _read_stage4(stage4_dir, doc_name)
    if not markdown_content:
        raise FileNotFoundError(
            f"Aucun fichier markdown trouvé pour '{doc_name}' dans {stage4_dir}"
        )

    print("Création du résumé : ")
    resume = generate_resume(markdown_content)

    print("Création des 'intents' :")
    intent = generate_intent(markdown_content)

    print("Création des questions hypothétiques (hyq) : ")
    hyq = generate_hyq(markdown_content)

    out_dir = write_stage5(stage5_dir, doc_name, resume, intent, hyq)
    print(f"OK stage5 écrit dans : {out_dir}")

    return {"resume": resume, "intent": intent, "hyq": hyq}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enrichit les métadonnées d'un document via VLM (resume, intent, hyq) et écrit les résultats dans stage5."
    )
    parser.add_argument(
        "doc_name",
        help='Nom du document sans extension. Ex: "Annulation et retaxation"',
    )
    parser.add_argument("--stage4", type=Path, default=DEFAULT_STAGE4)
    parser.add_argument("--stage5", type=Path, default=DEFAULT_STAGE5)
    args = parser.parse_args()

    print(f"\nEnrichissement de : {args.doc_name}")
    result = run_enhancement(args.doc_name, args.stage4, args.stage5)

    print("\n--- resume ---")
    print(result["resume"])
    print("\n--- intent ---")
    for item in result["intent"]:
        print(f"  - {item}")
    print("\n--- hyq ---")
    for q in result["hyq"]:
        print(f"  - {q}")


if __name__ == "__main__":
    main()
