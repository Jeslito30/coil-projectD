"""Every secret-like variable in committed env templates must be empty.

gitleaks skips deployment/contabo/.env.example (see .gitleaks.toml), so this test is the guard that
nobody fills the template with real values and commits it.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = [ROOT / "deployment" / "contabo" / ".env.example", ROOT / "backup" / "restic.env.example"]
SECRET_NAME = re.compile(r"(PASSWORD|TOKEN|SECRET|KEY)$")


class EnvTemplateTest(unittest.TestCase):
    def test_secret_variables_are_empty(self):
        for template in TEMPLATES:
            if not template.exists():
                continue
            for number, line in enumerate(template.read_text(encoding="utf-8").splitlines(), start=1):
                if line.lstrip().startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                if SECRET_NAME.search(name.strip()):
                    with self.subTest(file=template.name, line=number, variable=name.strip()):
                        self.assertEqual(value.strip(), "", "template must not contain a secret value")


if __name__ == "__main__":
    unittest.main()
