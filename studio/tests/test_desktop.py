"""The desktop shell: start-up checks, the setup page, seeding of a packaged
install, and the server the window shows. No window is opened here; the
window is a view onto the same server these tests talk to."""
from __future__ import annotations

import urllib.request

import pytest
from PIL import Image

from studio import config
from studio.desktop import launcher


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    config.DATA_DIR.mkdir()
    return config.DATA_DIR


def test_icon_carries_every_size_windows_asks_for():
    ico = Image.open(launcher.ICON_ICO)
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= set(ico.info["sizes"])
    assert launcher.LOGO_SVG.read_text(encoding="utf-8").lstrip().startswith("<svg")


def test_server_serves_the_studio_and_answers_the_ping(data_dir):
    server = launcher.Server(launcher.pick_port(0))
    server.start()
    try:
        assert launcher.studio_answers(server.url)
        page = urllib.request.urlopen(server.url + "/").read().decode()
        assert "Journal Typesetting Studio" in page and "/brand/logo.svg" in page
        logo = urllib.request.urlopen(server.url + "/brand/logo.svg")
        assert logo.headers["Content-Type"].startswith("image/svg+xml")
        assert urllib.request.urlopen(server.url + "/favicon.ico").status == 200
    finally:
        server.stop()


def test_a_missing_pandoc_opens_the_setup_page_not_a_traceback(data_dir, monkeypatch):
    monkeypatch.delenv("PANDOC_PATH", raising=False)
    monkeypatch.setattr(launcher, "find_pandoc", lambda: None)
    problems = launcher.check_tools()
    assert [p["name"] for p in problems] == ["Pandoc"]
    app = launcher.Server(launcher.pick_port(0)).app
    page = app.test_client().get("/desktop/setup").get_data(as_text=True)
    assert "Pandoc" in page and "Check again" in page and "github.com/jgm/pandoc/releases" in page


def test_setup_page_moves_on_once_pandoc_is_found(data_dir, monkeypatch, tmp_path):
    fake = tmp_path / "pandoc"
    fake.write_text("")
    monkeypatch.setattr(launcher, "find_pandoc", lambda: str(fake))
    app = launcher.Server(launcher.pick_port(0)).app
    resp = app.test_client().get("/desktop/setup")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/")


def test_seeding_copies_defaults_and_never_overwrites_an_edit(tmp_path):
    src, dest = tmp_path / "bundled", tmp_path / "user"
    (src / "pub" / "journal").mkdir(parents=True)
    (src / "pub" / "journal" / "profile.yaml").write_text("title: bundled\n")
    (src / "pub" / "publisher.yaml").write_text("name: pub\n")
    (dest / "pub" / "journal").mkdir(parents=True)
    (dest / "pub" / "journal" / "profile.yaml").write_text("title: edited by the editor\n")
    launcher._seed(src, dest)
    assert (dest / "pub" / "journal" / "profile.yaml").read_text() == "title: edited by the editor\n"
    assert (dest / "pub" / "publisher.yaml").read_text() == "name: pub\n"


def test_pick_port_avoids_a_port_in_use():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        busy = s.getsockname()[1]
        assert launcher.pick_port(busy) != busy
