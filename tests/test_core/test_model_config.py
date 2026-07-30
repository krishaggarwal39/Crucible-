"""
Model/provider configuration invariants.

These encode facts verified against the live APIs:
  - OpenRouter serves chat completions only; it has no embeddings endpoint.
  - gemini/text-embedding-004 is retired (404); gemini-embedding-001 works and
    returns 3072 dimensions natively but accepts a `dimensions` argument.
  - The NVIDIA Nemotron free models produce valid structured output under
    Instructor's TOOLS mode (which is what the pipeline uses).
  - openrouter/openai/gpt-oss-20b:free FAILS under TOOLS mode, so it must not be
    a fallback.
"""

from unittest.mock import AsyncMock

import pytest

from backend.core.config import Settings, get_settings

BASE = {"JWT_SECRET_KEY": "x", "CREDENTIAL_ENCRYPTION_KEY": None}


def make_settings(**overrides):
    """Build Settings with a valid Fernet key, ignoring the ambient .env values."""
    from cryptography.fernet import Fernet

    kwargs = {
        "JWT_SECRET_KEY": "test-secret",
        "CREDENTIAL_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "GEMINI_API_KEY": "",
        "OPENAI_API_KEY": "",
        "OPENROUTER_API_KEY": "",
    }
    kwargs.update(overrides)
    return Settings(**kwargs)


# ── OpenRouter cannot serve embeddings ────────────────────────────────────────

def test_openrouter_embedding_model_is_rejected():
    """
    OpenRouter is chat-only. Allowing an openrouter/... embedding model meant
    drift analysis failed once per trace with an opaque provider error.
    """
    with pytest.raises(Exception, match="OpenRouter"):
        make_settings(
            DEFAULT_EMBEDDING_MODEL="openrouter/nvidia/nemotron-3-super-120b-a12b:free"
        )


def test_gemini_embedding_model_is_accepted():
    s = make_settings(DEFAULT_EMBEDDING_MODEL="gemini/gemini-embedding-001")
    assert s.embedding_provider == "gemini"


# ── Graceful degradation without an embedding key ─────────────────────────────

def test_embeddings_disabled_without_provider_key():
    s = make_settings(DEFAULT_EMBEDDING_MODEL="gemini/gemini-embedding-001",
                      GEMINI_API_KEY="")
    assert s.embeddings_enabled is False
    assert "GEMINI_API_KEY" in s.embeddings_disabled_reason


def test_embeddings_disabled_with_placeholder_key():
    """A copied .env.example placeholder must not count as a real credential."""
    s = make_settings(DEFAULT_EMBEDDING_MODEL="gemini/gemini-embedding-001",
                      GEMINI_API_KEY="your_gemini_api_key_here")
    assert s.embeddings_enabled is False


def test_embeddings_enabled_with_real_key():
    s = make_settings(DEFAULT_EMBEDDING_MODEL="gemini/gemini-embedding-001",
                      GEMINI_API_KEY="AIzaSy-something-that-looks-real")
    assert s.embeddings_enabled is True
    assert s.embeddings_disabled_reason is None


def test_embeddings_can_be_switched_off_entirely():
    s = make_settings(DEFAULT_EMBEDDING_MODEL="")
    assert s.embeddings_enabled is False
    assert "DEFAULT_EMBEDDING_MODEL" in s.embeddings_disabled_reason


# ── Chat model / fallback invariants ──────────────────────────────────────────

def test_defaults_only_require_openrouter():
    """
    A single OPENROUTER_API_KEY must be enough for the judge, generator and
    evolution nodes.
    """
    s = get_settings()
    for model in (s.DEFAULT_JUDGE_MODEL, s.DEFAULT_GENERATOR_MODEL, *s.FALLBACK_MODELS):
        assert model.startswith("openrouter/"), f"{model} is not an OpenRouter model"


def test_gpt_oss_is_not_a_fallback():
    """
    openrouter/openai/gpt-oss-20b:free returns a provider error under Instructor's
    TOOLS mode, so listing it as a fallback converted a transient primary failure
    into a hard failure.
    """
    s = get_settings()
    assert not any("gpt-oss" in m for m in s.FALLBACK_MODELS)


def test_retired_embedding_model_is_not_the_default():
    """gemini/text-embedding-004 now returns 404 from Google."""
    assert get_settings().DEFAULT_EMBEDDING_MODEL != "gemini/text-embedding-004"


def test_embedding_dimensions_are_set():
    s = get_settings()
    assert s.EMBEDDING_DIMENSIONS > 0


# ── The analyzer must skip cleanly rather than erroring per trace ─────────────

@pytest.mark.asyncio
async def test_analyzer_skips_when_embeddings_disabled(mocker):
    from backend.agents import analyzer

    mocker.patch.object(
        type(analyzer.settings), "embeddings_enabled",
        property(lambda self: False),
    )
    mocker.patch.object(
        type(analyzer.settings), "embeddings_disabled_reason",
        property(lambda self: "GEMINI_API_KEY is not set."),
    )
    init = mocker.patch.object(analyzer.vector_db, "init_collection", new_callable=AsyncMock)
    download = mocker.patch.object(analyzer.s3_client, "download_trace", new_callable=AsyncMock)

    result = await analyzer.analyze_drift_node({
        "tenant_id": "t", "agent_id": "a", "run_id": "r",
        "traces": [{"trace_id": "tr1", "scenario_id": "s1", "storage_key": "k1"}],
    })

    profile = result["drift_profile"]
    assert profile["status"] == "embeddings_disabled"
    assert profile["score"] is None
    assert "GEMINI_API_KEY" in profile["reason"]
    assert result["total_cost_usd"] == 0.0
    # Crucially, no work was attempted.
    init.assert_not_called()
    download.assert_not_called()


# ── The requested dimension must reach the provider ───────────────────────────

@pytest.mark.asyncio
async def test_embed_requests_the_configured_dimensions(mocker):
    """
    gemini-embedding-001 returns 3072 by default. Without an explicit request the
    stored vectors would not match the Qdrant collection width.
    """
    from backend.connectors.llm import LLMClient

    fake = mocker.MagicMock()
    fake.data = [{"embedding": [0.1] * 768}]
    aembedding = mocker.patch("backend.connectors.llm.litellm.aembedding",
                              new_callable=AsyncMock, return_value=fake)
    mocker.patch("backend.connectors.llm.litellm.completion_cost", return_value=0.0)

    client = LLMClient()
    out = await client.embed("gemini/gemini-embedding-001", "hello", dimensions=768)

    assert len(out["vector"]) == 768
    assert aembedding.await_args.kwargs["dimensions"] == 768


@pytest.mark.asyncio
async def test_embed_retries_without_dimensions_if_unsupported(mocker):
    """A provider that rejects `dimensions` must still produce a vector."""
    from backend.connectors.llm import LLMClient

    fake = mocker.MagicMock()
    fake.data = [{"embedding": [0.2] * 1024}]

    calls = []

    async def side_effect(**kwargs):
        calls.append(kwargs)
        if "dimensions" in kwargs:
            raise ValueError("dimensions not supported by this model")
        return fake

    mocker.patch("backend.connectors.llm.litellm.aembedding", side_effect=side_effect)
    mocker.patch("backend.connectors.llm.litellm.completion_cost", return_value=0.0)

    out = await LLMClient().embed("some/legacy-embedder", "hello", dimensions=768)

    assert len(out["vector"]) == 1024
    assert len(calls) == 2
    assert "dimensions" in calls[0] and "dimensions" not in calls[1]
