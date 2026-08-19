from pathlib import Path

from talkies.config import load_config


def test_partial_config_keeps_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[transcription]
model = "distil-large-v3"

[behavior]
paste = false

[replacements]
"acme" = "ACME"
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.transcription.model == "distil-large-v3"
    assert config.transcription.beam_size == 5
    assert config.transcription.offline is True
    assert config.transcription.model_idle_timeout_seconds == 60
    assert config.behavior.paste is False
    assert config.behavior.restore_clipboard is True
    assert config.replacements == {"acme": "ACME"}
