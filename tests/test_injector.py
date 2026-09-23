import subprocess

import pytest

from talkies import injector


def result(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def test_terminal_windows_use_terminal_paste_shortcut(monkeypatch) -> None:
    monkeypatch.setattr(injector, "_run", lambda _command: result("Gnome-terminal\n"))
    assert injector.active_window_paste_shortcut("10") == "ctrl+shift+v"


def test_regular_windows_use_standard_paste_shortcut(monkeypatch) -> None:
    monkeypatch.setattr(injector, "_run", lambda _command: result("Google-chrome\n"))
    assert injector.active_window_paste_shortcut("10") == "ctrl+v"


def test_window_app_name_uses_x11_class(monkeypatch) -> None:
    monkeypatch.setattr(injector.shutil, "which", lambda _name: "/bin/xprop")
    monkeypatch.setattr(
        injector, "_run", lambda _command: result('WM_CLASS(STRING) = "code", "Code"\n')
    )
    assert injector.window_app_name("10") == "Code"


def test_paste_failure_restores_clipboard(monkeypatch) -> None:
    writes = []

    def run(command, *, input_text=None):
        if command[-1] == "-o":
            return result("previous")
        if command[-1] == "getactivewindow":
            return result("10")
        if command[:2] == ["xdotool", "key"]:
            return result(returncode=1)
        if input_text is not None:
            writes.append(input_text)
        return result()

    monkeypatch.setattr(injector, "_run", run)
    monkeypatch.setattr(injector.shutil, "which", lambda _name: "/bin/tool")
    instance = injector.TextInjector(restore_delay_ms=0)
    with pytest.raises(injector.InjectionError):
        instance.inject("private words", target_window_id="10")
    assert writes == ["private words", "previous"]
