"""DocumentWorkspace — l'unique propriétaire des conventions de chemins.

Remplace les 44 fonctions ``resolve_*`` et les f-strings de nommage dispersés.
Les noms produits sont **contractuels** : ils ont été relevés sur la sortie
réelle du pipeline (``find data/output_files_preprocessing/Mineur``) et sur
les f-strings du code — le disque fait autorité, pas ce docstring.

Les noms de documents contiennent accents, espaces et apostrophes
typographiques (``Cas de sortie - Prolongation d'adhésion``) : aucun nettoyage
n'est appliqué au-delà du ``strip()`` déjà pratiqué par l'orchestrateur.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .settings import Settings


@dataclass(frozen=True)
class DocumentWorkspace:
    """Chemins d'entrée/sortie d'un document traité par le pipeline."""

    doc_name: str
    source_pdf: Path
    root: Path
    # Dossier du document RELATIF à input_files/ (ex. Path("afac/Adhésion")).
    # Alimenté dès le lot 2, consommé par root au lot F1 seulement :
    # for_document() le calcule, root l'ignore (décision du plan, § 2.2).
    relative_dir: Path = field(default_factory=lambda: Path())

    # --- doctags ---
    @property
    def doctags(self) -> Path:
        return self.root / f"{self.doc_name}.doctags"

    @property
    def reordered_doctags(self) -> Path:
        return self.root / f"{self.doc_name}_reordered.doctags"

    @property
    def reordered_with_tables_doctags(self) -> Path:
        return self.root / f"{self.doc_name}_reordered_with_tables.doctags"

    @property
    def reordered_with_tables_pictures_doctags(self) -> Path:
        return self.root / f"{self.doc_name}_reordered_with_tables_pictures.doctags"

    @property
    def url_vlm_doctags(self) -> Path:
        return self.root / f"{self.doc_name}_url_vlm.doctags"

    # --- markdown ---
    @property
    def markdown(self) -> Path:
        return self.root / f"{self.doc_name}.md"

    @property
    def url_vlm_markdown(self) -> Path:
        return self.root / f"{self.doc_name}_url_vlm.md"

    @property
    def vlm_check_markdown(self) -> Path:
        return self.root / f"{self.doc_name}_vlm_check.md"

    @property
    def image_descriptions(self) -> Path:
        return self.root / f"{self.doc_name}_image_descriptions.md"

    @property
    def final_markdown(self) -> Path:
        return self.root / f"{self.doc_name}_final.md"

    @property
    def final_embed_markdown(self) -> Path:
        # Produit par markdown_tables_to_jsonl.py (outil HORS pipeline,
        # décision n°14) ; metadata_generation le LIT s'il existe.
        return self.root / f"{self.doc_name}_final_embed.md"

    # --- données à la racine du doc ---
    @property
    def docling_json(self) -> Path:
        # ⚠ lu par load_input_json (metadata_generation)
        return self.root / f"{self.doc_name}.json"

    @property
    def text_dump(self) -> Path:
        return self.root / f"{self.doc_name}.txt"

    @property
    def hyperlinks_jsonl(self) -> Path:
        return self.root / f"hyperlinks_data_{self.doc_name}.jsonl"

    # --- sous-dossiers ---
    @property
    def used_images_dir(self) -> Path:
        return self.root / "used_images"

    @property
    def tables_dir(self) -> Path:
        return self.root / "tables"

    @property
    def metadata_dir(self) -> Path:
        return self.root / "metadata"

    @property
    def opencv_validation_dir(self) -> Path:
        # QA visuelle (étape opencv-check, désactivée par défaut) — relevé
        # dans opencv_checker.resolve_output, absent du gel de référence
        # précisément parce que l'étape y était sautée.
        return self.root / "opencv_validation"

    # --- contenu de metadata/ (relevé sur la sortie réelle) ---
    @property
    def final_csv(self) -> Path:
        # ⚠ dans metadata/, PAS à la racine du doc
        return self.metadata_dir / f"{self.doc_name}_final.csv"

    @property
    def resume_markdown(self) -> Path:
        return self.metadata_dir / "resume.md"

    @property
    def intent_json(self) -> Path:
        return self.metadata_dir / "intent.json"

    @property
    def hyq_json(self) -> Path:
        return self.metadata_dir / "hyq.json"

    @property
    def embedding_json(self) -> Path:
        return self.metadata_dir / "embedding.json"

    @property
    def hyq_dir(self) -> Path:
        return self.metadata_dir / f"hyq_{self.doc_name}"

    def ensure_dirs(self) -> None:
        """Crée root/ et ses sous-dossiers standards s'ils manquent."""
        for directory in (self.root, self.used_images_dir, self.tables_dir, self.metadata_dir):
            directory.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_document(cls, pdf: Path, settings: Settings) -> DocumentWorkspace:
        """Construit le workspace d'un PDF selon les conventions actuelles.

        ``relative_dir`` porte le sous-dossier du PDF dans input_files/
        (``Path(".")`` si hors input_files/) ; ``root`` reste PLAT au lot 2 —
        le layout miroir n'arrive qu'au lot F1.
        """
        pdf = Path(pdf)
        doc_name = pdf.stem.strip()
        try:
            relative_dir = pdf.resolve().relative_to(
                settings.input_files_root.resolve()
            ).parent
        except ValueError:
            relative_dir = Path()
        root = settings.output_files_root / doc_name
        return cls(
            doc_name=doc_name,
            source_pdf=pdf,
            root=root,
            relative_dir=relative_dir,
        )
