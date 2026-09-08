"""Run recap with API keys from the repository's Git-ignored .env file."""

import os
from pathlib import Path

from mawha_recap.cli import app

for line in (Path(__file__).resolve().parents[1] / ".env").read_text().splitlines():
    if line.strip() and not line.lstrip().startswith("#"):
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()
app()
