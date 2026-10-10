from pathlib import Path


def load_persona(path: Path) -> str:
    """Load Luna's system prompt from disk. Edit the file to tune her voice."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Persona file is empty: {path}")
    return text
