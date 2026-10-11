"""The in-app user guide: Markdown files shipped in the package, rendered once at startup.

The guide is trusted content from the package, but raw HTML in it is still escaped, so the
page needs no exceptions to the content security policy.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from markdown_it import MarkdownIt

GUIDE_DIR = Path(__file__).parent / "guide"


@dataclass(frozen=True)
class Section:
    """One rendered guide file: its HTML and a table of contents of its `##` headings."""

    html: str
    contents: list[tuple[str, str]]  # (anchor id, heading text)


def slug(text: str) -> str:
    """GitHub-style heading anchor, so links work both here and on GitHub."""
    text = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return text.replace(" ", "-")


def render(markdown: str, taken: set[str] | None = None) -> Section:
    """Render Markdown with ids on h2/h3 headings. `taken` keeps ids unique across files."""
    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    tokens = md.parse(markdown)
    taken = set() if taken is None else taken
    contents = []
    for i, token in enumerate(tokens):
        for child in token.children or []:
            if child.type == "image":
                child.attrSet("loading", "lazy")  # screenshots load as you scroll
        if token.type != "heading_open" or token.tag not in ("h2", "h3"):
            continue
        text = tokens[i + 1].content
        anchor = base = slug(text) or "section"
        n = 1
        while anchor in taken:
            anchor = f"{base}-{n}"
            n += 1
        taken.add(anchor)
        token.attrSet("id", anchor)
        if token.tag == "h2":
            contents.append((anchor, text))
    return Section(md.renderer.render(tokens, md.options, {}), contents)


def load(directory: Path = GUIDE_DIR) -> dict[str, Section]:
    """The member guide ("using") and the admin guide ("admin"), with unique anchors."""
    taken: set[str] = set()
    return {
        name: render((directory / f"{name}.md").read_text(encoding="utf-8"), taken)
        for name in ("using", "admin")
    }
