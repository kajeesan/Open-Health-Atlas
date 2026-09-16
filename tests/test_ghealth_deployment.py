"""Credential and disabled-deployment contracts for Google Health."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / "deploy/ghealth-auth"
SYNC = ROOT / "deploy/ghealth-sync"
INSTALLER = ROOT / "deploy/install-ghealth-sync.sh"
SERVICE = ROOT / "deploy/hermes-ghealth-sync.service"


def _load(path: Path, name: str):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_auth_and_sync_share_the_same_external_config_default():
    auth = _load(AUTH, "ghealth_auth_contract")
    sync = _load(SYNC, "ghealth_sync_contract")
    assert auth.CONFIG == sync.CONFIG == "/etc/hermes/ghealth.json"


def test_client_secret_uses_hidden_prompt_or_protected_file(tmp_path, monkeypatch):
    auth = _load(AUTH, "ghealth_auth_secret")
    monkeypatch.setattr(auth.getpass, "getpass", lambda _prompt: "fictional-secret")
    assert auth._load_client_secret(None) == "fictional-secret"
    secret = tmp_path / "client-secret"
    secret.write_text("fictional-file-secret\n")
    secret.chmod(0o600)
    assert auth._load_client_secret(str(secret)) == "fictional-file-secret"
    secret.chmod(0o644)
    with pytest.raises(SystemExit, match="mode 0600"):
        auth._load_client_secret(str(secret))
    assert "--client-secret\"" not in AUTH.read_text()


def test_installer_and_service_are_disabled_credential_bound_and_valid():
    installer = INSTALLER.read_text()
    service = SERVICE.read_text()
    commands = [line.strip() for line in installer.splitlines()]
    assert not any(line.startswith("systemctl enable") for line in commands)
    assert not any(line.startswith("systemctl start") for line in commands)
    assert "systemctl start" in installer  # printed operator instruction only
    assert "LoadCredential=ghealth.json:/etc/hermes/ghealth.json" in service
    assert "User=root" in service and "ProtectSystem=strict" in service
    checked = subprocess.run(
        ["sh", "-n", str(INSTALLER)], capture_output=True, text=True, timeout=30
    )
    assert checked.returncode == 0, checked.stderr
