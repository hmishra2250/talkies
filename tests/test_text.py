from talkies.text import normalize_text


def test_normalize_text_handles_spoken_layout_and_replacements() -> None:
    result = normalize_text(
        " hello   world new paragraph talkies new line done ",
        replacements={"talkies": "Talkies"},
    )
    assert result == "hello world\n\nTalkies\ndone"


def test_normalize_text_can_leave_spoken_commands_alone() -> None:
    result = normalize_text("a new line b", spoken_commands=False)
    assert result == "a new line b"
