from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    """Return the text of agent/prompts/<name>.md."""
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
