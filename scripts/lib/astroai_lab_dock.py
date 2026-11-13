"""Compact AstroAI pill injected into contributed apps that are not Studio.

The pill is plain HTML with inline styles and ``rel="external"`` anchors so it
survives a ``script-src 'self'`` CSP and an in-app router. No extra process:
Agents and Compute are the existing hub (``canfar-lab`` keys, agent setup, Ray).
"""

from __future__ import annotations

# (element id, path under the session prefix, visible label, title)
Tool = tuple[str, str, str, str]

_PILL = (
    "position:fixed;z-index:2147483646;display:flex;gap:2px;align-items:center;"
    "padding:3px;background:rgba(11,16,38,.94);border:1px solid rgba(129,140,248,.4);"
    "border-radius:999px;box-shadow:0 8px 24px rgba(0,0,0,.35);"
    "font:600 12px/1.2 system-ui,-apple-system,sans-serif"
)
_LINK = (
    "display:inline-block;padding:5px 10px;border-radius:999px;color:#dfe4ff;"
    "text-decoration:none;white-space:nowrap"
)
_LINK_EMPH = (
    "display:inline-block;padding:5px 10px;border-radius:999px;color:#fff;"
    "text-decoration:none;white-space:nowrap;background:#3d8bfd"
)

OPENRESEARCH_TOOLS: tuple[Tool, ...] = (
    (
        "astroai-terminal-chip",
        "/astroai-terminal/",
        "Terminal",
        "Shell in this session",
    ),
    (
        "astroai-agents-chip",
        "/astroai-agents/",
        "AstroAI",
        "Model keys and coding agents",
    ),
    (
        "astroai-compute-chip",
        "/astroai-agents/#compute",
        "Compute",
        "Batch jobs and Ray clusters",
    ),
)

OPENSCIENCE_TOOLS: tuple[Tool, ...] = (
    (
        "astroai-agents-chip",
        "/astroai-agents/",
        "AstroAI",
        "Model keys and CANFAR compute",
    ),
    (
        "astroai-compute-chip",
        "/astroai-agents/#compute",
        "Compute",
        "Batch jobs and Ray clusters",
    ),
)


def lab_dock_html(
    prefix: str,
    tools: tuple[Tool, ...],
    *,
    place: str = "top",
    emphasize: str | None = None,
) -> str:
    """One pill. ``place`` is ``top`` (right) or ``bottom`` (right).

    ``emphasize`` is a tool id drawn as the primary call to action.
    """
    pos = "top:8px;right:8px" if place == "top" else "bottom:14px;right:14px"
    links: list[str] = []
    for chip, path, label, title in tools:
        href = f"{prefix}{path}"
        style = _LINK_EMPH if chip == emphasize else _LINK
        links.append(
            f'<a id="{chip}" href="{href}" rel="external" title="{title}" style="{style}">'
            f"{label}</a>"
        )
    return (
        f'<div id="astroai-lab-dock" data-astroai-dock style="{_PILL};{pos}">'
        + "".join(links)
        + "</div>"
    )
