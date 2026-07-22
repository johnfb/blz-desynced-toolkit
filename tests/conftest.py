"""Shared fixtures. Tests need the external game-data extract -- see CLAUDE.md's "What This
Is" for why it lives outside this repo. Defaults to the sibling-directory convention
documented there (../desynced-game-data relative to this repo); override with the
DESYNCED_GAME_DATA env var if your copy lives elsewhere."""

import os
from pathlib import Path

import pytest

from blz.desynced_toolkit import LupaEngine, open_asset_source

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GAME_DATA_DIR = REPO_ROOT.parent / "desynced-game-data"
GAME_DATA_DIR = os.environ.get("DESYNCED_GAME_DATA", str(DEFAULT_GAME_DATA_DIR))


@pytest.fixture(scope="session")
def engine():
    if not os.path.exists(GAME_DATA_DIR):
        pytest.skip(
            f"game data extract not found at {GAME_DATA_DIR} (set DESYNCED_GAME_DATA)"
        )
    src = open_asset_source(GAME_DATA_DIR)
    return LupaEngine(src)
