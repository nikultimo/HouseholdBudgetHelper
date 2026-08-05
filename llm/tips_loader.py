from __future__ import annotations
import pathlib


def load_tips(path: str = "data/agent_tips.txt") -> list[str]:
    """Return non-empty, non-comment lines from the tips file. Returns [] if missing."""
    try:
        lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


def append_tip(tip: str, path: str = "data/agent_tips.txt") -> None:
    """Append a tip line to the file, creating it and its parent directory if needed."""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(tip.strip() + "\n")
