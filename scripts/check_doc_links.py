#!/usr/bin/env python3
"""Link checker dos docs (Story 21.4).

Por padrão valida apenas links relativos (determinístico, sem rede):
  - resolve `[texto](caminho)` contra o diretório do arquivo markdown;
  - aceita links absolutos a partir da raiz do repositório (`/docs/...`);
  - ignora âncoras (`#...`), `mailto:` e URLs externas.

Com `--external`, também faz HEAD/GET nos links http(s), ignorando
localhost/127.0.0.1 e exemplos (`example.com`, `xxxx`). Em CI usamos o modo
relativo para não depender de disponibilidade de sites externos.

Uso:
  python3 scripts/check_doc_links.py docs [--external]
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SKIP_PREFIXES = ("#", "mailto:", "tel:", "javascript:")
EXTERNAL_SKIP = ("localhost", "127.0.0.1", "example.com", "xxxx", "yyyy")


def _strip_suffix(target: str) -> str:
    return target.split("#", 1)[0].split("?", 1)[0]


def _external_ok(url: str) -> bool:
    if any(token in url for token in EXTERNAL_SKIP):
        return True
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "lata-velha-link-checker"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return 200 <= response.status < 400
    except urllib.error.HTTPError as error:
        return error.code < 400
    except Exception:  # noqa: BLE001 - link externo é fronteira de rede
        return False


def check_file(path: Path, repo_root: Path, external: bool) -> list[str]:
    problems: list[str] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for match in LINK_RE.finditer(text):
        target = match.group(1).strip()
        if not target or target.startswith(SKIP_PREFIXES):
            continue
        if target.startswith(("http://", "https://")):
            if external and not _external_ok(target):
                problems.append(f"{path}: link externo inacessível: {target}")
            continue
        relative = _strip_suffix(target)
        if not relative:
            continue
        candidate = (repo_root / relative.lstrip("/")) if relative.startswith("/") else (path.parent / relative)
        if not candidate.exists():
            problems.append(f"{path}: link relativo inexistente: {target}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Valida links dos documentos markdown")
    parser.add_argument("paths", nargs="+", help="Arquivos ou diretórios markdown")
    parser.add_argument("--external", action="store_true", help="Também valida links http(s)")
    args = parser.parse_args(argv)

    repo_root = Path.cwd()
    files: list[Path] = []
    for raw in args.paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(path.rglob("*.md")))
        elif path.is_file():
            files.append(path)

    problems: list[str] = []
    for path in files:
        problems.extend(check_file(path, repo_root, args.external))

    if problems:
        print(f"Link check: {len(problems)} problema(s) em {len(files)} arquivo(s)", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"Link check OK: {len(files)} arquivo(s), {0 if not args.external else 'com'} externos" + ("" if args.external else " (relativos)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
