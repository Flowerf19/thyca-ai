from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "install.sh").read_text(encoding="utf-8")


def test_install_sh_is_pipe_safe() -> None:
    assert "#!/bin/sh" in SCRIPT
    assert "git+https://github.com/Flowerf19/thyca-ai.git" in SCRIPT
    assert "uv tool install --python 3.14 --force" in SCRIPT
    assert "uv tool install ." not in SCRIPT
    assert "dirname" not in SCRIPT
    assert "thyca --version" in SCRIPT
