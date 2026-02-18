from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests


LOGGER = logging.getLogger(__name__)


class WebSearch:
    """Hybrid search: local workspace first, optional online fallback."""

    def __init__(self, workspace_root: str | Path, allow_online: bool = True) -> None:
        self.workspace_root = Path(workspace_root)
        self.allow_online = allow_online

    def _search_local(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        try:
            cmd = ["rg", "--line-number", "--max-count", str(max_results), query, str(self.workspace_root)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=6, check=False)
            if result.returncode not in (0, 1):
                return []

            rows = []
            for line in result.stdout.splitlines():
                if ":" not in line:
                    continue
                parts = line.split(":", 2)
                if len(parts) < 3:
                    continue
                path, line_number, snippet = parts
                rows.append(
                    {
                        "source": "local",
                        "title": Path(path).name,
                        "path": path,
                        "line": line_number,
                        "snippet": snippet.strip(),
                    }
                )
                if len(rows) >= max_results:
                    break
            return rows
        except Exception:
            return []

    def _search_online(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        if not self.allow_online:
            return []

        url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            response = requests.get(url, timeout=8, headers={"User-Agent": "JarvisLocal/1.0"})
            response.raise_for_status()
            html = response.text
        except Exception:
            return []

        matches = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        results: list[dict[str, Any]] = []
        for href, title_html in matches[:max_results]:
            title = re.sub(r"<[^>]+>", "", title_html).strip()
            results.append({"source": "web", "title": title, "url": href})
        return results

    def search(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        local_results = self._search_local(query, max_results=max_results)
        if local_results:
            return local_results
        return self._search_online(query, max_results=max_results)

    def format_search_results(self, results: list[dict[str, Any]]) -> str:
        if not results:
            return "No encontre resultados."
        top = results[0]
        if top.get("source") == "local":
            return (
                f"Encontre resultados locales. El primero esta en {top.get('path')} "
                f"linea {top.get('line')}: {top.get('snippet')}"
            )
        return f"Busqueda completada. Primer resultado: {top.get('title')}."

