"""Testes do link checker de docs (Story 21.4)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_doc_links as links  # noqa: E402


class LinkCheckerTests(unittest.TestCase):
    def test_link_relativo_existente(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "ok.md").write_text("[runbook](./runbook.md)\n", encoding="utf-8")
            (root / "docs" / "runbook.md").write_text("# runbook\n", encoding="utf-8")
            self.assertEqual(links.check_file(root / "docs" / "ok.md", root, False), [])

    def test_link_relativo_inexistente(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "bad.md").write_text("[x](./nao-existe.md)\n", encoding="utf-8")
            problems = links.check_file(root / "docs" / "bad.md", root, False)
            self.assertEqual(len(problems), 1)
            self.assertIn("nao-existe.md", problems[0])

    def test_ignora_ancora_e_mailto(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "ok.md").write_text("[a](#secao) [b](mailto:x@y.z)\n", encoding="utf-8")
            self.assertEqual(links.check_file(root / "docs" / "ok.md", root, False), [])


if __name__ == "__main__":
    unittest.main()
