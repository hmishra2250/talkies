from pathlib import Path

from talkies.config import load_config, save_hold_key, save_replacements


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
    assert config.transcription.beam_size == 1
    assert config.transcription.offline is True
    assert config.transcription.model_idle_timeout_seconds == 60
    assert config.behavior.paste is False
    assert config.behavior.restore_clipboard is True
    assert config.replacements == {"acme": "ACME"}


def test_save_replacements_preserves_other_settings(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[behavior]\npaste = false\n\n[replacements]\n"old" = "OLD"\n',
        encoding="utf-8",
    )
    save_replacements({"talkies": "Talkies", "C++": "C plus plus"}, path)
    config = load_config(path)
    assert config.behavior.paste is False
    assert config.replacements == {"C++": "C plus plus", "talkies": "Talkies"}


def test_save_hold_key_preserves_other_settings(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[behavior]\npaste = false\nhold_key = "Control_R"\n\n[replacements]\n"acme" = "ACME"\n',
        encoding="utf-8",
    )
    save_hold_key("Super_L+Shift_L+F23", path)
    config = load_config(path)
    assert config.behavior.hold_key == "Super_L+Shift_L+F23"
    assert config.behavior.paste is False
    assert config.replacements == {"acme": "ACME"}
    assert path.read_text(encoding="utf-8").count("hold_key =") == 1


def test_save_hold_key_adds_missing_behavior_section(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[audio]\nmicrophone = "default"\n', encoding="utf-8")
    save_hold_key("keycode:207", path)
    assert load_config(path).behavior.hold_key == "keycode:207"
