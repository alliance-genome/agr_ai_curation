"""Tests for config.models_loader."""

from pathlib import Path


def test_load_models_reads_yaml(tmp_path: Path):
    import src.lib.config.models_loader as models_loader_module

    models_loader_module.reset_cache()
    try:
        config_path = tmp_path / "models.yaml"
        config_path.write_text(
            """
models:
  - model_id: gpt-4o
    name: GPT-4o
    provider: openai
    default: true
    curator_visible: false
    reasoning_options: [low, medium, high]
    default_reasoning: medium
    reasoning_descriptions:
      medium: Balanced reasoning
    recommended_for:
      - General curation
    avoid_for:
      - Low-latency retrieval only
            """.strip(),
            encoding="utf-8",
        )

        loaded = models_loader_module.load_models(models_path=config_path, force_reload=True)

        assert "gpt-4o" in loaded
        assert loaded["gpt-4o"].default is True
        assert loaded["gpt-4o"].curator_visible is False
        assert loaded["gpt-4o"].reasoning_options == ["low", "medium", "high"]
        assert loaded["gpt-4o"].default_reasoning == "medium"
        assert loaded["gpt-4o"].reasoning_descriptions["medium"] == "Balanced reasoning"
        assert loaded["gpt-4o"].recommended_for == ["General curation"]
        assert loaded["gpt-4o"].avoid_for == ["Low-latency retrieval only"]
        assert models_loader_module.get_default_model().model_id == "gpt-4o"
    finally:
        # Avoid leaking temporary model registries into subsequent tests.
        models_loader_module.reset_cache()
