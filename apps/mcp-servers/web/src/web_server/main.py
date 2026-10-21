"""MCP tool server for web pages, served from a local corpus instead of the internet.

Live pages change between runs (N-07) and real requests would leave traces outside the test
(N-06), so every page comes from the session's fixture and nothing touches the network. Page text
is untrusted content. Every fetch is logged in the effect log, including ones for pages that do
not exist, because a fetched URL is itself a way to leak data (in its query string).
"""

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastmcp import FastMCP
from hindsight_server_kit import SessionStore, load_json_fixture, serve

PORT = 8013
BENIGN = Path(__file__).parent / "fixtures" / "benign.json"
SNIPPET_LENGTH = 160


def load_pages(path: Path | None) -> dict[str, Any]:
    """Load a session's page corpus: the benign fixture, or a fixture file / overlay on it."""
    return load_json_fixture(path, BENIGN)


server = FastMCP("Web Server")
store = SessionStore("web", load_pages)


# Tool names and docstrings below are published to clients as the MCP tool name and
# description, so renaming a function or editing its docstring changes the tool descriptor.
@server.tool()
def fetch_page(url: str) -> dict:
    """Fetch a web page by URL and return its title and text."""
    session = store.get()
    with session.lock:
        page = _find(session.data["pages"], url)
        status = 200 if page else 404
        session.record_effect(
            "fetch_page", url=url, status=status, page_id=page["page_id"] if page else None
        )
    if page is None:
        return {"url": url, "status": 404, "page_id": None, "title": "", "text": "Page not found."}
    return {"url": url, "status": 200, **{k: page[k] for k in ("page_id", "title", "text")}}


@server.tool()
def search(query: str) -> dict:
    """Search the web. Returns up to 10 results with their URL, title and a snippet."""
    words = _words(query)
    session = store.get()
    with session.lock:
        scored = []
        for order, page in enumerate(session.data["pages"]):
            text = _words(f"{page['title']} {page['text']}")
            score = sum(text.count(w) for w in words)
            if score:
                scored.append((-score, order, page))
    results = [
        {
            "page_id": page["page_id"],
            "url": page["url"],
            "title": page["title"],
            "snippet": page["text"][:SNIPPET_LENGTH],
        }
        for _, _, page in sorted(scored, key=lambda s: s[:2])[:10]
    ]
    return {"query": query, "results": results}


def _find(pages: list[dict[str, Any]], url: str) -> dict[str, Any] | None:
    # Match the URL as given, then without its query string, so that a known page fetched with
    # extra parameters still resolves while the full URL is what gets logged.
    exact, bare = _normalise(url, keep_query=True), _normalise(url, keep_query=False)
    for candidate in (exact, bare):
        for page in pages:
            if _normalise(page["url"], keep_query=True) == candidate:
                return page
    return None


def _normalise(url: str, keep_query: bool) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    query = parts.query if keep_query else ""
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def main() -> None:
    """Serve the tools over HTTP, with the kit's control endpoint on PORT + 100."""
    serve(server, store, port=PORT)
