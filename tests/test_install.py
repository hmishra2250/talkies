from talkies.install import _service_text


def test_service_starts_with_the_graphical_session() -> None:
    unit = _service_text()
    assert "WantedBy=graphical-session.target" in unit
    assert "PartOf=graphical-session.target" in unit
    assert "default.target" not in unit
    assert "Environment=DISPLAY" not in unit
