import json
import os
import socket
import sys
from pathlib import Path

OPTION_ENVIRONMENT = {
    "suisse_speech_api_key": ("SUISSE_SPEECH_API_KEY", ""),
    "adapter_api_key": ("ADAPTER_API_KEY", ""),
    "default_voice": ("DEFAULT_VOICE", "anna"),
    "default_language": ("DEFAULT_LANGUAGE", "de-CH"),
    "default_dialect": ("DEFAULT_DIALECT", "zurich"),
    "upstream_timeout_seconds": ("UPSTREAM_TIMEOUT_SECONDS", 120),
}


def environment_from_options(options: dict) -> dict[str, str]:
    environment = {
        variable: str(options.get(option, default) or default)
        for option, (variable, default) in OPTION_ENVIRONMENT.items()
    }
    if environment["SUISSE_SPEECH_API_KEY"] and not environment["ADAPTER_API_KEY"]:
        raise ValueError("Set adapter_api_key when storing suisse_speech_api_key in the add-on")
    if environment["ADAPTER_API_KEY"] and not environment["SUISSE_SPEECH_API_KEY"]:
        raise ValueError("Set suisse_speech_api_key as well, or leave both API key options empty")
    aliases = options.get("voice_aliases", [])
    environment["VOICE_ALIASES"] = json.dumps({item["alias"]: item["voice"] for item in aliases})
    return environment


def main():
    try:
        options = json.loads(Path("/data/options.json").read_text())
        os.environ.update(environment_from_options(options))
    except (OSError, ValueError, KeyError, TypeError):
        print(
            "Invalid add-on options. Check the configuration and set both API keys or neither.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    hostname = socket.gethostname().replace("_", "-")
    print(f"Internal OpenAI speech endpoint: http://{hostname}:8000/v1/audio/speech", flush=True)
    if os.getuid() == 0:
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    os.execv(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "adapter.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--no-access-log",
        ],
    )


if __name__ == "__main__":
    main()
