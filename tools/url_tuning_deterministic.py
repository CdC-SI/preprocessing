"""
url_tuning_deterministic.py — Variante deterministe de l'etape url-tuning (08),
a tester en parallele de la methode VLM actuelle sans rien y toucher.

Contexte : url-tuning (src/afac_preprocessing/steps/url_tuning.py) rend chaque
page en image et demande a un VLM de retrouver visuellement ou placer chaque
lien dans le doctags. Or url-extraction (etape 07, sans VLM) a deja calcule,
pour chaque lien, sa page, son texte-ancre exact et son URI par matching
spatial PyMuPDF (page.get_links() + page.get_text("words")) — cf.
get_link_text() dans url_extraction.py. Cette information fiable est jetee par
url-tuning, qui la rederive via le VLM, ce qui a produit sur au moins un
document (TN - Fortune et revenu acquis sous forme de rente, cf.
docs/rapport_erreur_resolution.md) des balises cassees et des liens perdus.

DeterministicLinkInjector reinjecte les liens de hyperlinks_data_<doc>.jsonl
directement dans le doctags par recherche de texte-ancre (le texte est deja
connu, il suffit de le retrouver dans le doctags de Docling et de l'entourer
de la syntaxe Markdown), sans appel VLM ni rendu d'image.

Le script n'ecrit jamais dans un fichier existant : il produit
<doc>_url_det.doctags et <doc>_url_det.md a cote des sorties actuelles
(_url_vlm.*), pour comparaison directe. Rien n'est cable dans le pipeline —
outil de test autonome, mais les classes ci-dessous sont concues pour etre
reutilisees telles quelles (import direct) par d'autres scripts, ou pour
devenir plus tard l'implementation d'un vrai PipelineStep.

Usage :
    uv run python tools/url_tuning_deterministic.py --stage5 data/output_files_preprocessing \\
        --doc "afac/Taxation/TN/TN - Fortune et revenu acquis sous forme de rente"

    # Sur tout le corpus (tous les docs qui ont des liens) :
    uv run python tools/url_tuning_deterministic.py --stage5 data/output_files_preprocessing
"""
from __future__ import annotations

import argparse
import html
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from afac_preprocessing.steps.markdown_convert import convert_doctags_to_markdown
from afac_preprocessing.steps.url_tuning import (
    assemble_doctags,
    get_links_for_page,
    load_jsonl_links,
    split_doctags_by_page,
)
from afac_preprocessing.utils.pdf_utils import pdf_page_count

_log = logging.getLogger(__name__)

#: `]` et `(` sont exclus au meme titre que `)` : quand le texte-ancre est
#: lui-meme une URL visible, le markdown produit `[https://a](https://a)` et
#: un pattern qui accepte ces caracteres deborde a travers le `](` en
#: fabriquant une URL parasite qui n'existe nulle part.
URL_PATTERN = re.compile(r'(?:https?://|mailto:)[^\s")(\[\]<>]+')
_MD_LINK_DESTINATION = re.compile(r"\]\(([^)]*)\)")
_MD_ESCAPED_CHAR = re.compile(r"\\([_*\[\]()])")


def repair_escaped_link_destinations(markdown: str) -> str:
    """Repare les destinations de lien Markdown corrompues par l'export
    Docling, qui ne reconnait pas nos URLs injectees comme des liens mais
    seulement comme du texte a securiser, et leur applique donc deux
    traitements destructeurs :

    - l'echappement des caracteres speciaux Markdown (`_` surtout), qui
      transforme ``#art_17`` en ``#art\\_17`` ;
    - l'encodage des entites HTML, qui transforme le ``&`` separateur de
      parametres de requete en ``&amp;`` (70 occurrences sur le corpus, sur
      les URLs bpanda qui portent plusieurs parametres).

    Les deux cassent l'URL pour tout consommateur en aval. La reparation ne
    touche qu'a l'interieur de `](...)`, jamais au reste du document : un
    underscore echappe ou une entite HTML dans le corps du texte est laisse
    intact.
    """

    def _unescape(match: re.Match[str]) -> str:
        destination = _MD_ESCAPED_CHAR.sub(r"\1", match.group(1))
        return f"]({html.unescape(destination)})"

    return _MD_LINK_DESTINATION.sub(_unescape, markdown)


@dataclass
class PageCanvas:
    """Le doctags d'une page en cours d'injection, avec la memoire des zones
    deja remplacees.

    Cette memoire est ce qui empeche un lien d'ecraser un lien deja pose, et
    elle est portee par la page plutot que par un appel de methode : une fois
    une URL injectee, elle fait partie du texte cherche, et une ancre courte
    ("art") peut sinon matcher a l'interieur d'une URL deja posee
    ("...fr#art_17") en produisant un lien imbrique illisible. Faire vivre
    l'etat au niveau de la page garantit la protection sur TOUTES les passes
    d'injection, y compris le repli inter-pages de
    DeterministicLinkInjector.inject_into_doctags().
    """

    text: str
    injected: list[tuple[int, int]] = field(default_factory=list)

    def is_free(self, start: int, end: int) -> bool:
        """True si le span (start, end) ne chevauche aucune zone deja injectee."""
        return not any(start < done_end and done_start < end for done_start, done_end in self.injected)

    def substitute(self, start: int, end: int, replacement: str) -> None:
        """Remplace le span par *replacement*, puis enregistre la zone occupee
        et decale les zones situees apres le point d'insertion."""
        self.text = self.text[:start] + replacement + self.text[end:]
        shift = len(replacement) - (end - start)
        self.injected = [
            (s, e) if e <= start else (s + shift, e + shift) for s, e in self.injected
        ]
        self.injected.append((start, start + len(replacement)))


@dataclass
class DeterministicLinkInjector:
    """Logique pure d'injection de liens dans un doctags, sans I/O ni VLM.

    Reutilisable telle quelle par d'autres scripts, ou par une future
    implementation de PipelineStep : aucune dependance a un chemin de
    fichier, uniquement des chaines et des dicts de liens en entree/sortie.
    """

    case_insensitive: bool = True

    #: Nombre de paliers d'appariement, cf. _patterns_for().
    MATCH_TIERS: ClassVar[int] = 3

    #: Caracteres que PyMuPDF et Docling ne transcrivent pas a l'identique.
    #: Chaque chaine est une classe d'equivalence : n'importe lequel de ses
    #: caracteres peut representer n'importe quel autre. Constate sur le
    #: corpus : Docling normalise le tiret cadratin en trait d'union (les
    #: liens "bpanda" — le plus gros groupe d'echecs — ne tenaient qu'a ca)
    #: et l'apostrophe courbe en apostrophe droite.
    _EQUIVALENT_CHARS: ClassVar[tuple[str, ...]] = (
        "'’‘`´",
        "-–—‒―‐‑",
        '"“”«»',
    )

    #: Ponctuation susceptible d'etre perdue par Docling en bordure d'ancre
    #: (observe : l'ancre "(RAVS" face a un doctags qui ecrit "RAVS, art. 34d").
    _BORDER_PUNCTUATION: ClassVar[str] = "().,;:!?[]\"'’‘`´«»-–—"

    def _char_pattern(self, char: str) -> str:
        """Motif regex pour un caractere, elargi a sa classe d'equivalence."""
        for group in self._EQUIVALENT_CHARS:
            if char in group:
                return f"[{re.escape(group)}]"
        return re.escape(char)

    def _fuzzy_pattern(self, anchor_text: str, *, optional_borders: bool) -> re.Pattern[str] | None:
        """Construit un pattern tolerant aux differences de transcription
        entre PyMuPDF (texte source) et Docling (texte reconstruit), ou None
        si l'ancre ne contient aucun caractere significatif.

        Deux tolerances toujours actives, constatees sur le corpus :

        - **espacement** : les espaces de l'ancre sont ignores et un `\\s*`
          est insere entre chaque paire de caracteres significatifs, car
          Docling deplace parfois une espace au voisinage de la ponctuation
          ("(art." devient "( art.") ;
        - **classes d'equivalence** : cf. _EQUIVALENT_CHARS.

        *optional_borders* ajoute une troisieme tolerance, reservee au
        dernier palier de find_anchor_span() : la ponctuation de debut et de
        fin d'ancre devient facultative, car Docling perd parfois une
        parenthese ("(RAVS" face a un doctags qui ecrit "RAVS, art. 34d").
        Elle n'est jamais activee d'emblee : elle raccourcit l'ancre donc la
        rend moins discriminante, et une ancre courte comme "(art." finirait
        par matcher un simple "art" ailleurs dans la page.

        La ponctuation n'est rendue optionnelle que si l'ancre contient au
        moins un caractere non-ponctuation : sans ce garde-fou une ancre
        purement ponctuative donnerait un motif entierement optionnel, qui
        matcherait la chaine vide n'importe ou.
        """
        chars = [char for char in anchor_text if not char.isspace()]
        if not chars:
            return None

        tokens = [self._char_pattern(char) for char in chars]
        if optional_borders:
            for index in self._border_punctuation_indices(chars):
                tokens[index] += "?"

        flags = re.IGNORECASE if self.case_insensitive else 0
        return re.compile(r"\s*".join(tokens), flags)

    def _border_punctuation_indices(self, chars: list[str]) -> set[int]:
        """Indices de la ponctuation en debut et en fin d'ancre.

        Vide si l'ancre est entierement ponctuative — la rendre optionnelle
        produirait un motif qui matche la chaine vide n'importe ou.
        """
        if all(char in self._BORDER_PUNCTUATION for char in chars):
            return set()

        indices: set[int] = set()
        for index in range(len(chars)):
            if chars[index] not in self._BORDER_PUNCTUATION:
                break
            indices.add(index)
        for index in reversed(range(len(chars))):
            if chars[index] not in self._BORDER_PUNCTUATION:
                break
            indices.add(index)
        return indices

    @staticmethod
    def _longest_anchor_first(links: list[dict]) -> list[dict]:
        """Les ancres les plus longues d'abord : une ancre longue est plus
        discriminante, et la traiter en premier evite qu'une ancre courte
        incluse dedans ne prenne sa place."""
        return sorted(links, key=lambda link: -len(link.get("text") or ""))

    def _patterns_for(self, anchor_text: str) -> list[re.Pattern[str] | None]:
        """Les paliers d'appariement, du plus strict au plus permissif :

        0. correspondance litterale exacte ;
        1. tolerance espacement + classes d'equivalence de caracteres ;
        2. idem, plus la ponctuation de bordure rendue facultative.

        Une entree vaut None quand le palier ne s'applique pas a cette ancre.
        La longueur de la liste est stable (MATCH_TIERS) pour que l'indice de
        palier ait le meme sens d'une ancre a l'autre.
        """
        return [
            re.compile(re.escape(anchor_text)),
            self._fuzzy_pattern(anchor_text, optional_borders=False),
            self._fuzzy_pattern(anchor_text, optional_borders=True),
        ]

    def find_anchor_span(
        self, canvas: PageCanvas, anchor_text: str, *, tier: int | None = None
    ) -> tuple[int, int] | None:
        """Localise anchor_text dans la page, en ignorant toute occurrence qui
        chevauche une zone deja injectee — sans quoi deux liens PDF distincts
        partageant le meme texte-ancre (ex. la meme reference legale citee
        deux fois) reinjecteraient tous les deux dans la premiere occurrence.

        La recherche procede par paliers, du plus strict au plus permissif
        (cf. _patterns_for), et s'arrete au premier qui donne une occurrence
        libre.

        :param canvas: page en cours d'injection.
        :param anchor_text: texte-ancre a localiser (deja extrait par
            url-extraction).
        :param tier: n'essayer que ce palier. None (defaut) les essaie tous
            dans l'ordre — ce que veut un appel portant sur une seule page.
            inject_into_doctags() passe un palier explicite pour comparer
            toutes les pages a severite egale.
        :return: span (start, end) de la premiere occurrence libre, ou None.
        """
        if not anchor_text:
            return None

        patterns = self._patterns_for(anchor_text)
        selected = patterns if tier is None else patterns[tier : tier + 1]

        for pattern in selected:
            if pattern is None:
                continue
            for match in pattern.finditer(canvas.text):
                if canvas.is_free(*match.span()):
                    return match.span()

        return None

    def inject_link(self, canvas: PageCanvas, link: dict, *, tier: int | None = None) -> bool:
        """Injecte un lien dans la page si son texte-ancre y est trouvable.

        L'ancre est entouree de la syntaxe Markdown [texte](url), jamais d'une
        balise <a href> : <a href> n'est pas un tag doctags reconnu par le
        parseur Docling (verifie empiriquement — le lien disparait purement et
        simplement a l'export markdown), alors que la syntaxe Markdown inline
        traverse le parseur comme du texte normal et survit, au prix d'un
        echappement des underscores repare en aval par
        repair_escaped_link_destinations().

        Les balises englobantes (<list_item>, <text>, ...) ne sont jamais
        touchees, contrairement au VLM qui les reecrit parfois de travers.

        :param tier: palier d'appariement a utiliser, cf. find_anchor_span().
        :return: True si le lien a ete injecte, False s'il reste non-apparie.
        """
        uri = link.get("hyperlink")
        anchor_text = (link.get("text") or "").strip()
        if not uri or not anchor_text or anchor_text == "No text":
            return False

        span = self.find_anchor_span(canvas, anchor_text, tier=tier)
        if span is None:
            return False

        start, end = span
        canvas.substitute(start, end, f"[{canvas.text[start:end]}]({uri})")
        return True

    def inject_into_page(self, page_tags: str, page_links: list[dict]) -> tuple[str, list[dict]]:
        """Injecte une liste de liens dans le doctags d'une seule page.

        Enveloppe de commodite autour de PageCanvas, pour un appel isole ;
        inject_into_doctags() pilote les canvas directement afin que leur
        memoire d'injection survive d'une passe a l'autre.

        :param page_tags: contenu doctags d'une page.
        :param page_links: liens (dicts issus de hyperlinks_data_*.jsonl)
            appartenant a cette page.
        :return: (doctags modifie, liste des liens non retrouves)
        """
        canvas = PageCanvas(page_tags)
        unmatched = [
            link for link in self._longest_anchor_first(page_links) if not self.inject_link(canvas, link)
        ]
        return canvas.text, unmatched

    def inject_into_doctags(self, doctags: str, links: list[dict], n_pages: int) -> tuple[str, list[dict]]:
        """Decoupe le doctags par page (meme logique que url-tuning), injecte
        les liens de chaque page, puis reassemble.

        Repli inter-pages : un lien non retrouve sur la page indiquee par le
        PDF (link["page_number"]) est retente sur les autres pages avant
        d'etre abandonne. Necessaire car le decoupage par page de Docling
        n'est pas toujours fidele au decoupage reel du PDF sur ce corpus
        (contenu de deux pages fusionne dans un seul <page_footer>/<page_break>,
        cf. docs/rapport_erreur_resolution.md) — la carte "page PDF -> page
        doctags" n'est donc pas fiable a 100%, seul le texte-ancre l'est.

        Les deux passes partagent les memes PageCanvas : un lien de repli ne
        peut donc pas ecraser un lien pose lors de la premiere passe.

        Dans le repli, les paliers d'appariement priment sur l'ordre des
        pages : toutes les pages sont d'abord essayees en correspondance
        exacte, puis toutes en tolerant, etc. Parcourir page par page en
        essayant tous les paliers laisserait un match permissif sur une page
        precoce l'emporter sur la correspondance exacte disponible plus loin
        (constate : l'ancre "(art." captee dans "Dep[art]ement" de l'en-tete
        de page 1, alors que son "(art." exact figurait en page 3).

        :param doctags: doctags complet du document.
        :param links: tous les liens du document (hyperlinks_data_*.jsonl).
        :param n_pages: nombre de pages reel (PDF).
        :return: (doctags complet reassemble, liens non retrouves apres repli)
        """
        canvases = {
            page_num: PageCanvas(page_tags)
            for page_num, page_tags in split_doctags_by_page(doctags, n_pages).items()
        }

        unmatched: list[dict] = []
        for page_num, canvas in canvases.items():
            page_links = self._longest_anchor_first(get_links_for_page(links, page_num))
            unmatched.extend(link for link in page_links if not self.inject_link(canvas, link))

        final_unmatched = [
            link
            for link in self._longest_anchor_first(unmatched)
            # any() court-circuite : le lien est pose des la premiere
            # combinaison (palier, page) qui matche, et rien d'autre n'est
            # essaye — le palier etant la boucle externe, la severite prime.
            if not any(
                self.inject_link(canvas, link, tier=tier)
                for tier in range(self.MATCH_TIERS)
                for canvas in canvases.values()
            )
        ]

        assembled = assemble_doctags({num: canvas.text for num, canvas in canvases.items()})
        return assembled, final_unmatched


@dataclass
class DocLinkReport:
    """Resultat de comparaison pour un document : liens attendus vs liens
    effectivement retrouves dans le markdown final."""

    doc_ref: str
    error: str | None = None
    pages: int = 0
    links_total: int = 0
    links_unmatched: int = 0
    unmatched_texts: list[str] = field(default_factory=list)
    expected_unique_urls: int = 0
    found_unique_urls: int = 0
    missing_urls: list[str] = field(default_factory=list)
    output_doctags: Path | None = None
    output_markdown: Path | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.missing_urls

    def format_line(self) -> str:
        if self.error is not None:
            return f"[{self.doc_ref}] {self.error}"
        status = "OK" if self.ok else "INCOMPLET"
        return (
            f"[{self.doc_ref}] {status} — {self.links_total} lien(s), "
            f"{self.links_unmatched} non-apparie(s), "
            f"{self.found_unique_urls}/{self.expected_unique_urls} URL(s) unique(s) "
            f"dans le markdown final"
        )


class DeterministicUrlTuningTester:
    """Orchestre l'injection deterministe sur un ou plusieurs documents deja
    traites par le pipeline, et compare le resultat aux liens attendus.

    N'ecrit que des fichiers nouveaux (suffixe _url_det), jamais les sorties
    existantes de url-tuning.
    """

    SOURCE_DOCTAGS_SUFFIXES = ("_reordered_with_tables_pictures.doctags", "_reordered_with_tables.doctags")

    def __init__(
        self, stage5_dir: Path, input_root: Path, injector: DeterministicLinkInjector | None = None
    ) -> None:
        self.stage5_dir = stage5_dir
        self.input_root = input_root
        self.injector = injector or DeterministicLinkInjector()

    def discover_docs(self) -> list[str]:
        """Chemins relatifs (a stage5_dir) de tous les documents deja traites
        qui possedent des liens extraits."""
        return sorted(
            str(p.relative_to(self.stage5_dir))
            for p in self.stage5_dir.rglob("*")
            if p.is_dir()
            and (p / f"{p.name}.doctags").exists()
            and any(p.glob("hyperlinks_data_*.jsonl"))
        )

    def _source_doctags(self, doc_dir: Path, doc_name: str) -> Path | None:
        """Meme ordre de preference que UrlTuningStep._source_doctags."""
        for suffix in self.SOURCE_DOCTAGS_SUFFIXES:
            candidate = doc_dir / f"{doc_name}{suffix}"
            if candidate.exists():
                return candidate
        return None

    def process_doc(self, doc_ref: str) -> DocLinkReport:
        """Traite un document, en isolant tout echec inattendu dans le rapport.

        Un document au doctags malforme ne doit pas faire tomber le lot :
        l'exception devient l'erreur du rapport, et les autres documents
        continuent d'etre traites.
        """
        try:
            return self._process_doc(doc_ref)
        except Exception as exc:  # noqa: BLE001 — outil de lot : aucun echec ne doit interrompre le run
            _log.exception("Echec inattendu sur %s", doc_ref)
            return DocLinkReport(doc_ref=doc_ref, error=f"echec inattendu — {type(exc).__name__}: {exc}")

    def _process_doc(self, doc_ref: str) -> DocLinkReport:
        """Injection deterministe + conversion Markdown de controle.
        Ecrit <doc>_url_det.doctags et _url_det.md."""
        report = DocLinkReport(doc_ref=doc_ref)
        doc_dir = self.stage5_dir / doc_ref
        doc_name = Path(doc_ref).name

        doctags_path = self._source_doctags(doc_dir, doc_name)
        jsonl_path = doc_dir / f"hyperlinks_data_{doc_name}.jsonl"
        pdf_path = self.input_root / f"{doc_ref}.pdf"

        if doctags_path is None:
            report.error = "pas de doctags source (_reordered_with_tables[_pictures].doctags absent)"
            return report
        if not jsonl_path.exists():
            report.error = "pas de hyperlinks_data_*.jsonl"
            return report

        links = load_jsonl_links(jsonl_path)
        if not links:
            report.error = "aucun lien pour ce document, ignore"
            return report

        n_pages = pdf_page_count(pdf_path) or len({link.get("page_number") for link in links})
        doctags = doctags_path.read_text(encoding="utf-8")
        new_doctags, unmatched = self.injector.inject_into_doctags(doctags, links, n_pages)

        out_doctags = doc_dir / f"{doc_name}_url_det.doctags"
        out_doctags.write_text(new_doctags, encoding="utf-8")

        markdown = convert_doctags_to_markdown(out_doctags, expected_pages=n_pages)
        markdown = repair_escaped_link_destinations(markdown)
        out_markdown = doc_dir / f"{doc_name}_url_det.md"
        out_markdown.write_text(markdown, encoding="utf-8")

        expected_urls = {link["hyperlink"] for link in links if link.get("hyperlink")}
        found_urls = set(URL_PATTERN.findall(markdown))

        report.pages = n_pages
        report.links_total = len(links)
        report.links_unmatched = len(unmatched)
        report.unmatched_texts = [link.get("text", "") for link in unmatched]
        report.expected_unique_urls = len(expected_urls)
        report.found_unique_urls = len(found_urls)
        report.missing_urls = sorted(expected_urls - found_urls)
        report.output_doctags = out_doctags
        report.output_markdown = out_markdown
        return report

    def run(self, doc_refs: list[str] | None = None) -> list[DocLinkReport]:
        refs = doc_refs if doc_refs is not None else self.discover_docs()
        return [self.process_doc(doc_ref) for doc_ref in refs]

    @staticmethod
    def print_summary(reports: list[DocLinkReport]) -> None:
        for report in reports:
            print(report.format_line())
            for text in report.unmatched_texts:
                print(f"    ancre introuvable : {text!r}")
            for url in report.missing_urls:
                print(f"    URL manquante : {url}")

        processed = [report for report in reports if report.error is None]
        n_ok = sum(report.ok for report in processed)
        print(f"\n{len(processed)} document(s) traite(s), {n_ok} avec toutes les URLs retrouvees.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Variante deterministe (sans VLM) de url-tuning : reinjecte les liens "
            "deja extraits par url-extraction via recherche de texte-ancre. Ecrit "
            "des fichiers _url_det.* separes, ne touche a rien d'existant."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--stage5", type=Path, default=Path("data/output_files_preprocessing"))
    parser.add_argument("--input-root", type=Path, default=Path("data/input_files"))
    parser.add_argument(
        "--doc",
        type=str,
        default=None,
        help="Un seul document (chemin relatif a --stage5, sans extension).",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=args.log_level, format="%(levelname)s %(message)s")

    tester = DeterministicUrlTuningTester(
        stage5_dir=args.stage5.resolve(),
        input_root=args.input_root.resolve(),
    )
    doc_refs = [args.doc] if args.doc else tester.discover_docs()
    if not doc_refs:
        raise SystemExit(f"Aucun document avec des liens trouve sous {tester.stage5_dir}")

    tester.print_summary(tester.run(doc_refs))


if __name__ == "__main__":
    main()
