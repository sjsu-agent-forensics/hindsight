"""MCP tool server for the web, served from a local corpus, with tools named after AgentDojo's Slack
suite.

Live pages change between runs and real requests would leave traces outside the test, so every
page comes from the session's fixture and nothing touches the network. Page text is untrusted
content. Both tools are logged: a posted page is published content, and a fetched URL is itself an
outbound request that can carry data in its path or query string, even when no page exists there.
"""

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from hindsight_server_kit import INTERNAL_DOMAIN, SessionStore, load_json_fixture, next_id, serve

PORT = 8013
BENIGN = Path(__file__).parent / "fixtures" / "benign.json"


def _load_pages(path: Path | None) -> dict[str, Any]:
    """Load a session's page corpus: the benign fixture, or a fixture file / overlay on it."""
    return load_json_fixture(path, BENIGN)


server = FastMCP("Web Server")
store = SessionStore("web", _load_pages)


# Tool names and docstrings below are published to clients as the MCP tool name and
# description, so renaming a function or editing its docstring changes the tool descriptor.
@server.tool()
def get_webpage(url: str) -> dict[str, Any]:
    """Fetch a web page by URL and return its title and text, or a 404 result if there is none."""
    _require_url(url)
    session = store.get()
    with session.lock:
        page = _find(session.data["pages"], url)
        session.record_effect(
            "get_webpage",
            url=url,
            status=200 if page else 404,
            page_id=page["page_id"] if page else None,
            external=_is_external(url),
        )
        if page is None:
            return {"url": url, "status": 404, "page_id": None, "title": "", "text": "Not found."}
        return {"url": url, "status": 200, **{k: page[k] for k in ("page_id", "title", "text")}}


@server.tool()
def post_webpage(url: str, content: str) -> dict[str, Any]:
    """Publish content at a URL, replacing any page already there."""
    _require_url(url)
    session = store.get()
    with session.lock:
        pages = session.data["pages"]
        page = _find(pages, url, keep_query_only=True)
        created = page is None
        if page is None:
            page = {"page_id": next_id(pages, "page_id", "w_"), "url": url, "title": ""}
            pages.append(page)
        page["text"] = content
        session.record_effect(
            "post_webpage",
            url=url,
            page_id=page["page_id"],
            created=created,
            content=content,
            external=_is_external(url),
        )
        return {"posted": True, "url": url, "page_id": page["page_id"], "created": created}


def _require_url(url: str) -> None:
    if not urlsplit(_with_scheme(url)).hostname:
        raise ToolError(f"{url!r} is not a URL, e.g. https://www.example.com/page.")


def _find(
    pages: list[dict[str, Any]], url: str, keep_query_only: bool = False
) -> dict[str, Any] | None:
    # Match the URL as given, then without its query string, so that a known page fetched with
    # extra parameters still resolves while the full URL is what gets logged.
    candidates = [_normalise(url, keep_query=True)]
    if not keep_query_only:
        candidates.append(_normalise(url, keep_query=False))
    for candidate in candidates:
        for page in pages:
            if _normalise(page["url"], keep_query=True) == candidate:
                return page
    return None


def _normalise(url: str, keep_query: bool) -> str:
    # Lowercase scheme and host, drop the fragment and a trailing slash; the path keeps its case.
    parts = urlsplit(_with_scheme(url))
    path = parts.path.rstrip("/") or "/"
    query = parts.query if keep_query else ""
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def _with_scheme(url: str) -> str:
    # Agents often drop the scheme ("www.example.com/page"); treat that as https.
    url = url.strip()
    return url if "://" in url else f"https://{url}"


def _is_external(url: str) -> bool:
    host = urlsplit(_with_scheme(url)).hostname or ""
    return not (host == INTERNAL_DOMAIN or host.endswith("." + INTERNAL_DOMAIN))


def main() -> None:
    """Serve the tools over HTTP, with the kit's control endpoint on PORT + 100."""
    serve(server, store, port=PORT)
