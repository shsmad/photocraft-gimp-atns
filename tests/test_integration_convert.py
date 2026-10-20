"""Integration-тест конвертера Action: нужен явный `PHOTOCRAFT_CLI`."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys

from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
ATN = REPO_ROOT / "actions" / "freqautosplit.atn"
CLI = os.environ.get("PHOTOCRAFT_CLI")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not CLI, reason="PHOTOCRAFT_CLI не задан"),
]

sys.path.insert(0, str(REPO_ROOT / "src"))


def _load(name: str) -> ModuleType:
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"poc_{name}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


convert = _load("convert_action")


def test_atn_roundtrip_is_byte_identical(tmp_path: Path) -> None:
    assert CLI
    cli = Path(CLI).expanduser()
    assert cli.is_file(), f"PHOTOCRAFT_CLI: файл не найден: {cli}"
    source = ATN.read_bytes()
    unpacked = tmp_path / "roundtrip.action.json"
    repacked = tmp_path / "roundtrip.atn"

    async def scenario() -> None:
        client = convert.MCPClient(cli, bridge=False)
        try:
            await client.start()
            await convert.unpack_action(client, source, unpacked)
            action = convert.pick_action(convert.load_actions(unpacked), None)
            await convert.pack_action(client, action, repacked)
        finally:
            await client.close()

    asyncio.run(scenario())
    assert repacked.read_bytes() == source
