"""
reorder_doctags_v2_compare.py — Compare reorder-doctags v1 (texte .doctags) et
v2 (JSON natif Docling) sur un document deja traite, sans rien ecraser.

La logique de v2 vit dans `src/afac_preprocessing/steps/reorder_doctags_v2.py`
(`reorder_doctags_from_json`). Ce script se contente de la rejouer a cote de la
sortie actuelle (`_reordered.doctags`) et de verifier, contre le JSON qui fait
foi (`prov[].page_no`), qu'aucun element n'est desormais hors ordre de page.

Ecrit `<doc>_reordered_v2.doctags` a cote de la sortie existante. Rien d'autre
n'est touche.

Usage :
    uv run python tools/reorder_doctags_v2_compare.py \\
        --doc "afac/Taxation/TN/TN - Fortune et revenu acquis sous forme de rente"
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

from docling_core.types.doc.document import DoclingDocument

from afac_preprocessing.steps.reorder_doctags_v2 import reorder_doctags_from_json

_LOC_TAG_RE = re.compile(r"<loc_\d+>")


@dataclass
class OrderCheck:
    """Verifie qu'un texte donne apparait, dans le doctags produit, a une
    position coherente avec son page_no reel (issu du JSON)."""

    doc_ref: str
    total_items: int = 0
    checked: int = 0
    out_of_order: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.out_of_order

    def format_line(self) -> str:
        status = "OK" if self.ok else "REGRESSION"
        return (
            f"[{self.doc_ref}] {status} — {self.checked}/{self.total_items} "
            f"element(s) verifie(s) contre l'ordre de page attendu (JSON)"
        )


#: Une puce de liste ordonnee ("1. ", "2) ") fait partie du texte source
#: (`orig`) dans le JSON mais n'est pas repetee telle quelle dans l'export
#: doctags (la numerotation y est portee par la structure <ordered_list>, pas
#: par le texte). Sans ce nettoyage, la cle de recherche du premier item d'une
#: liste ne matche jamais — constate concretement sur "1. Supprimer la ligne
#: de taxation..." (TO - Reconsidération).
_ORDINAL_PREFIX_RE = re.compile(r"^\d+[.)]\s*")


def _search_key(text: str) -> str:
    """Cle de recherche stable : sans balises <loc_N>, sans puce numerotee.

    La puce doit etre retiree AVANT la troncature a 40 caracteres, pas apres :
    sinon "1. Supprimer la ligne..." (37 caracteres utiles une fois la puce
    otee) et "Supprimer la ligne..." (40 caracteres) produisent deux cles de
    longueurs differentes pour le meme texte de fond — chacune traquant sa
    propre position de reprise independamment, alors qu'elles designent la
    meme occurrence physique. Constate concretement sur "TO - Reconsidération"
    (item "1. Supprimer la ligne..." vs ses jumeaux non-numerotes).
    """
    return _ORDINAL_PREFIX_RE.sub("", _LOC_TAG_RE.sub("", text))[:40]


def check_page_order(doctags: str, json_path: Path, doc_ref: str) -> OrderCheck:
    """Verifie que, dans `doctags`, les elements identifiables (texte + page
    de reference) apparaissent dans un ordre non-decroissant de page_no.

    Le JSON fait foi pour le page_no (prov[].page_no) — cf. la note
    architecturale du 2026-08-24 dans la conversation avec l'utilisateur :
    le format .doctags ne porte pas cette info de facon fiable, le JSON si.

    Un meme texte peut legitimement apparaitre plusieurs fois dans le document
    (consigne repetee sur plusieurs pages, titre repris en sous-titre...).

    Un curseur GLOBAL et croissant a ete essaye puis abandonne : il masquait
    le vrai positif connu sur le document TN (l'item redevient introuvable
    APRES le curseur des qu'il est reellement deplace en arriere, et se fait
    silencieusement ignorer au lieu d'etre signale — verifie : avec un
    curseur global, le v1 de TN ressort "OK" alors que sa case a cocher et
    son paragraphe sont bel et bien inverses).

    Un curseur **par texte** (une position de reprise independante pour
    chaque cle de recherche) est le bon compromis : les items d'un meme texte
    repete sont assignes dans l'ordre a leurs occurrences successives, sans
    empecher un item isole de matcher une occurrence antérieure a celle d'un
    texte different rencontre juste avant — ce qui est exactement le cas
    qu'on veut detecter. Deux faux positifs ont ete constates et corriges
    avant d'arriver a cette version : collision entre deux items partageant
    le meme texte ("Soumis a l'AVS obligatoire", repete pages 1 et 2), et
    puce de liste ordonnee absente de l'export doctags qui faisait echouer
    la recherche du premier item d'une liste (cf. _search_key).
    """
    result = OrderCheck(doc_ref=doc_ref)
    doc = DoclingDocument.load_from_json(json_path)

    next_start: dict[str, int] = {}
    positions: list[tuple[int, int]] = []  # (index dans doctags, page_no)
    for item in doc.texts:
        text = (item.orig or "").strip()
        if len(text) < 15 or not item.prov:
            continue
        key = _search_key(text)
        idx = doctags.find(key, next_start.get(key, 0))
        if idx == -1:
            continue
        next_start[key] = idx + 1
        result.checked += 1
        positions.append((idx, item.prov[0].page_no))

    result.total_items = len(doc.texts)
    positions.sort()
    last_page = 0
    for idx, page_no in positions:
        if page_no < last_page:
            result.out_of_order.append(f"page {page_no} apparait apres page {last_page} (index {idx})")
        last_page = max(last_page, page_no)
    return result


def compare_doc(stage5_dir: Path, doc_ref: str) -> None:
    doc_dir = stage5_dir / doc_ref
    doc_name = Path(doc_ref).name

    json_path = doc_dir / f"{doc_name}.json"
    old_path = doc_dir / f"{doc_name}_reordered.doctags"
    new_path = doc_dir / f"{doc_name}_reordered_v2.doctags"

    if not json_path.exists():
        raise SystemExit(f"JSON introuvable : {json_path}")

    reorder_doctags_from_json(json_path, new_path)

    print(f"=== {doc_ref} ===\n")
    for label, path in (("v1 (actuel)", old_path), ("v2 (JSON)", new_path)):
        if not path.exists():
            print(f"{label:14s} — absent ({path.name})")
            continue
        content = path.read_text(encoding="utf-8")
        check = check_page_order(content, json_path, doc_ref)
        print(f"{label:14s} — {check.format_line()}")
        for issue in check.out_of_order:
            print(f"                  {issue}")

    print(f"\nEcrit : {new_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage5", type=Path, default=Path("data/output_files_preprocessing"))
    parser.add_argument("--doc", type=str, required=True, help="Document (chemin relatif a --stage5, sans extension).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    compare_doc(args.stage5.resolve(), args.doc)


if __name__ == "__main__":
    main()
