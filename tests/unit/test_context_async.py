"""Tests du ClientBundle et du PipelineContext — le cœur du piège P7 :
une seule boucle, un seul client par cible, pour tout le run."""

from pathlib import Path

import pytest

from afac_preprocessing.clients.bundle import ClientBundle
from afac_preprocessing.clients.fake import FakeEmbeddingClient, FakeVlmClient
from afac_preprocessing.context import PipelineContext
from afac_preprocessing.exceptions import EmbeddingUnavailable
from afac_preprocessing.settings import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        vlm_url="http://vlm.local/v1/chat/completions",  # type: ignore[arg-type]
        vlm_model_name="qwen-vl",
        project_root=tmp_path,
        data_root=tmp_path / "data",
    )


async def _double(x: int) -> int:
    return x * 2


def test_run_async_returns_value(settings: Settings) -> None:
    with ClientBundle(settings) as bundle:
        assert bundle.run_async(_double(21)) == 42


def test_run_async_reuses_the_same_loop(settings: Settings) -> None:
    with ClientBundle(settings) as bundle:
        bundle.run_async(_double(1))
        loop_first = bundle.loop
        bundle.run_async(_double(2))
        assert bundle.loop is loop_first


def test_vlm_client_is_built_once_and_reused(settings: Settings) -> None:
    with ClientBundle(settings) as bundle:
        assert bundle.vlm() is bundle.vlm()


def test_close_closes_the_loop(settings: Settings) -> None:
    bundle = ClientBundle(settings)
    bundle.run_async(_double(1))
    loop = bundle.loop
    bundle.close()
    assert loop.is_closed()


def test_embeddings_without_url_raises(settings: Settings) -> None:
    with ClientBundle(settings) as bundle:
        with pytest.raises(EmbeddingUnavailable):
            bundle.embeddings()


def test_context_for_pdf_builds_workspace_and_bundle(settings: Settings) -> None:
    ctx = PipelineContext.for_pdf(Path("/in/Doc.pdf"), settings)
    assert ctx.workspace.doc_name == "Doc"
    assert ctx.settings is settings
    assert ctx.dry_run is False
    ctx.clients.close()


def test_context_shares_an_injected_bundle(settings: Settings) -> None:
    # En batch : le MÊME bundle pour tous les documents du run.
    with ClientBundle(settings) as bundle:
        ctx_a = PipelineContext.for_pdf(Path("/in/A.pdf"), settings, clients=bundle)
        ctx_b = PipelineContext.for_pdf(Path("/in/B.pdf"), settings, clients=bundle)
        assert ctx_a.clients is ctx_b.clients
        ctx_a.run_async(_double(1))
        assert ctx_a.clients.loop is ctx_b.clients.loop


def test_fake_vlm_client_through_run_async(settings: Settings) -> None:
    fake = FakeVlmClient(vision_response="une description")
    with ClientBundle(settings) as bundle:
        result = bundle.run_async(fake.vision_completion("prompt", "b64=="))
        assert result == "une description"
        assert fake.calls[0][0] == "vision_completion"


def test_fake_embedding_client_returns_fixed_vector(settings: Settings) -> None:
    fake = FakeEmbeddingClient(embedding=[1.0, 2.0])
    with ClientBundle(settings) as bundle:
        assert bundle.run_async(fake.get_embedding("texte")) == [1.0, 2.0]
        assert fake.calls == ["texte"]
