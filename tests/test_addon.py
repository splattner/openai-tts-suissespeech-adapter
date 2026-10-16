import importlib.util
import json
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("addon_run", ROOT / "ha-addon/run.py")
addon_run = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(addon_run)


def test_default_addon_configuration():
    environment = addon_run.environment_from_options({})
    assert environment["DEFAULT_DIALECT"] == "zurich"
    assert environment["SUISSE_SPEECH_API_KEY"] == ""
    assert environment["ADAPTER_API_KEY"] == ""
    assert environment["VOICE_ALIASES"] == "{}"


def test_custom_addon_configuration():
    environment = addon_run.environment_from_options(
        {
            "suisse_speech_api_key": "sv_private",
            "adapter_api_key": "client-secret",
            "default_dialect": "bern",
            "upstream_timeout_seconds": 180,
            "voice_aliases": [{"alias": "alloy", "voice": "anna"}],
        }
    )
    assert environment["SUISSE_SPEECH_API_KEY"] == "sv_private"
    assert environment["ADAPTER_API_KEY"] == "client-secret"
    assert environment["DEFAULT_DIALECT"] == "bern"
    assert environment["UPSTREAM_TIMEOUT_SECONDS"] == "180"
    assert json.loads(environment["VOICE_ALIASES"]) == {"alloy": "anna"}


@pytest.mark.parametrize("option", ["suisse_speech_api_key", "adapter_api_key"])
def test_incomplete_credential_options(option):
    with pytest.raises(ValueError):
        addon_run.environment_from_options({option: "secret"})


def test_versions_are_synchronized():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    config = yaml.safe_load((ROOT / "ha-addon/config.yaml").read_text())
    manifest = json.loads((ROOT / ".release-please-manifest.json").read_text())
    version = project["project"]["version"]
    assert config["version"] == version
    assert manifest["."] == version
    assert (
        next(
            package for package in lock["package"] if package["name"] == project["project"]["name"]
        )["version"]
        == version
    )


def test_addon_defaults_match_launcher_and_schema():
    config = yaml.safe_load((ROOT / "ha-addon/config.yaml").read_text())
    environment = addon_run.environment_from_options(config["options"])
    assert environment == addon_run.environment_from_options({})
    assert set(config["options"]) == set(config["schema"])
    assert config["arch"] == ["aarch64", "amd64"]
    assert "host_network" not in config
    assert "homeassistant_api" not in config
    assert config["ports"] == {"8000/tcp": None}
    assert "ingress" not in config


def test_startup_logs_internal_endpoint(monkeypatch, capsys):
    monkeypatch.setattr(addon_run.Path, "read_text", lambda path: "{}")
    monkeypatch.setattr(
        addon_run.socket, "gethostname", lambda: "b81c4299_suisse_speech_openai_tts"
    )
    monkeypatch.setattr(addon_run.os, "getuid", lambda: 10001)
    monkeypatch.setattr(addon_run.os, "environ", {})
    commands = []
    monkeypatch.setattr(addon_run.os, "execv", lambda executable, args: commands.append(args))
    addon_run.main()
    assert (
        "http://b81c4299-suisse-speech-openai-tts:8000/v1/audio/speech" in capsys.readouterr().out
    )
    assert commands[0][1:4] == ["-m", "uvicorn", "adapter.main:app"]
