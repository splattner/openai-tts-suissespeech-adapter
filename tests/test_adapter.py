import io
import json
import wave
from contextlib import asynccontextmanager

import httpx
import pytest

from adapter.main import Settings, create_app

AUTH = {"Authorization": "Bearer sv_test_example"}
SPEECH = {"input": "Grüezi mitenand!", "model": "tts-1-hd", "voice": "alloy"}


@asynccontextmanager
async def client_for(handler, settings=None):
    app = create_app(settings or Settings(), transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://adapter",
            headers=AUTH,
        ) as client:
            yield client


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("tts-1", "swift"),
        ("tts-1-hd", "studio"),
        ("gpt-4o-mini-tts", "studio"),
        ("studio", "studio"),
        ("swift", "swift"),
    ],
)
async def test_speech_mapping(model, expected):
    def upstream(request):
        assert str(request.url) == "https://api.suisse-speech.ch/v1/tts"
        assert request.headers["x-api-key"] == "sv_test_example"
        assert "authorization" not in request.headers
        assert json.loads(request.content) == {
            "text": SPEECH["input"],
            "model": expected,
            "voice": "anna",
            "language": "de-CH",
            "dialect": "standard",
            "speed": 1,
            "format": "mp3",
            "output": "audio",
        }
        return httpx.Response(
            200,
            content=b"audio-bytes",
            headers={
                "content-type": "audio/mpeg",
                "x-suisse-environment": "sandbox",
            },
        )

    async with client_for(upstream) as client:
        response = await client.post("/v1/audio/speech", json={**SPEECH, "model": model})
    assert response.status_code == 200
    assert response.content == b"audio-bytes"
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["x-suisse-environment"] == "sandbox"


async def test_extensions_and_voice_override():
    def upstream(request):
        payload = json.loads(request.content)
        assert payload["voice"] == "custom-voice"
        assert payload["language"] == "de-CH-bern"
        assert "dialect" not in payload
        assert payload["style"] == "fröhlich"
        assert payload["cache"] == "off"
        assert payload["speed"] == 1.2
        return httpx.Response(200, content=b"wav", headers={"content-type": "audio/wav"})

    async with client_for(upstream, Settings(voice_aliases={"alloy": "custom-voice"})) as client:
        response = await client.post(
            "/v1/audio/speech",
            json={
                **SPEECH,
                "language": "de-CH-bern",
                "instructions": "fröhlich",
                "cache": "off",
                "speed": 1.2,
                "response_format": "wav",
            },
        )
    assert response.status_code == 200


async def test_native_voice_and_explicit_dialect():
    def upstream(request):
        payload = json.loads(request.content)
        assert payload["voice"] == "native-voice"
        assert payload["dialect"] == "bern"
        assert payload["style"] == "explicit style"
        return httpx.Response(200, content=b"mp3", headers={"content-type": "audio/mpeg"})

    async with client_for(upstream) as client:
        response = await client.post(
            "/v1/audio/speech",
            json={
                **SPEECH,
                "voice": "native-voice",
                "dialect": "bern",
                "instructions": "ignored",
                "style": "explicit style",
            },
        )
    assert response.status_code == 200


@pytest.mark.parametrize(("output_format", "expected"), [("pcm", "pcm16"), ("opus", "opus")])
async def test_audio_formats(output_format, expected):
    def upstream(request):
        payload = json.loads(request.content)
        assert payload["format"] == expected
        if output_format == "pcm":
            assert payload["sample_rate_hz"] == 24000
        return httpx.Response(200, content=b"audio", headers={"content-type": "audio/ogg"})

    async with client_for(upstream) as client:
        response = await client.post(
            "/v1/audio/speech",
            json={
                **SPEECH,
                "response_format": output_format,
            },
        )
    assert response.status_code == 200


@pytest.mark.parametrize("status", [200, 401, 403, 429])
async def test_home_assistant_empty_text_probe(status):
    def upstream(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/capabilities"
        return httpx.Response(status, json={"error": {"code": "probe", "message": "probe"}})

    async with client_for(upstream) as client:
        response = await client.post("/v1/audio/speech", json={**SPEECH, "input": ""})
    assert response.status_code == (400 if status == 200 else status)


@pytest.mark.parametrize("path", ["/v1/models", "/v1/audio/voices", "/v1/audio/speech"])
async def test_auth_required(path):
    def upstream(request):
        pytest.fail("Unauthenticated requests must not reach Suisse Speech")

    async with client_for(upstream) as client:
        client.headers.pop("authorization")
        response = (
            await client.post(path, json=SPEECH)
            if path.endswith("speech")
            else await client.get(path)
        )
    assert response.status_code == 401


async def test_server_key_and_local_authentication():
    def upstream(request):
        assert request.headers["x-api-key"] == "sv_server_key"
        return httpx.Response(200, json={})

    settings = Settings(upstream_api_key="sv_server_key", adapter_api_key="local-secret")
    async with client_for(upstream, settings) as client:
        rejected = await client.get("/v1/models")
        accepted = await client.get("/v1/models", headers={"Authorization": "Bearer local-secret"})
    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert {model["id"] for model in accepted.json()["data"]} >= {"studio", "swift", "tts-1"}


def test_server_key_requires_adapter_key():
    with pytest.raises(ValueError, match="ADAPTER_API_KEY"):
        create_app(Settings(upstream_api_key="sv_private"))


async def test_live_voice_catalogue():
    def upstream(request):
        assert request.url.path == "/v1/tts/voices"
        return httpx.Response(200, json={"voices": [{"id": "anna", "name": "Anna"}]})

    async with client_for(upstream) as client:
        response = await client.get("/v1/audio/voices?limit=1000")
    assert response.json() == {"object": "list", "data": [{"id": "anna", "name": "Anna"}]}


@pytest.mark.parametrize("payload", [{}, {"voices": None}, {"voices": ["anna"]}])
async def test_invalid_catalogue(payload):
    async with client_for(lambda request: httpx.Response(200, json=payload)) as client:
        response = await client.get("/v1/audio/voices")
    assert response.status_code == 502


@pytest.mark.parametrize(
    "change",
    [
        {"speed": 0.25},
        {"speed": 4},
        {"input": "a" * 8001},
        {"response_format": "invalid"},
        {"model": "unknown"},
        {"instructions": "a" * 601},
        {"stream_format": "sse"},
        {"response_format": "pcm", "sample_rate_hz": 48000},
        {"unknown_parameter": True},
    ],
)
async def test_invalid_requests_do_not_synthesize(change):
    def upstream(request):
        pytest.fail("Invalid requests must not synthesize audio")

    async with client_for(upstream) as client:
        response = await client.post("/v1/audio/speech", json={**SPEECH, **change})
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"


@pytest.mark.parametrize("status", [400, 401, 403, 429, 503])
async def test_upstream_errors_and_redaction(status):
    def upstream(request):
        return httpx.Response(
            status,
            json={
                "error": {
                    "code": "tts.capacity",
                    "message": "Rejected sv_test_example",
                }
            },
            headers={"Retry-After": "5"},
        )

    async with client_for(upstream) as client:
        response = await client.post("/v1/audio/speech", json=SPEECH)
    assert response.status_code == status
    assert response.headers["retry-after"] == "5"
    assert response.json()["error"]["code"] == "tts.capacity"
    assert "sv_test_example" not in response.text


async def test_unknown_voice_error():
    async with client_for(
        lambda request: httpx.Response(
            400,
            json={
                "error": {
                    "code": "tts.unknown_voice",
                    "message": "Unknown voice",
                }
            },
        )
    ) as client:
        response = await client.post("/v1/audio/speech", json=SPEECH)
    assert response.json()["error"]["code"] == "voice_not_found"


@pytest.mark.parametrize(
    ("exception", "status"),
    [
        (httpx.ReadTimeout("timeout"), 504),
        (httpx.ConnectError("connection"), 502),
    ],
)
async def test_network_failures(exception, status):
    def upstream(request):
        raise exception

    async with client_for(upstream) as client:
        response = await client.post("/v1/audio/speech", json=SPEECH)
    assert response.status_code == status


async def test_non_audio_response_is_not_served_as_audio():
    async with client_for(lambda request: httpx.Response(200, json={"unexpected": True})) as client:
        response = await client.post("/v1/audio/speech", json=SPEECH)
    assert response.status_code == 502


async def test_missing_ffmpeg_fails_before_synthesis(monkeypatch):
    monkeypatch.setattr("adapter.main.shutil.which", lambda name: None)
    async with client_for(lambda request: pytest.fail("Must not synthesize")) as client:
        response = await client.post("/v1/audio/speech", json={**SPEECH, "response_format": "flac"})
    assert response.status_code == 400


@pytest.mark.parametrize("output_format", ["aac", "flac"])
async def test_real_ffmpeg_conversion(output_format):
    import shutil

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not installed")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x00" * 2400)
    async with client_for(
        lambda request: httpx.Response(
            200,
            content=buffer.getvalue(),
            headers={"content-type": "audio/wav"},
        )
    ) as client:
        response = await client.post(
            "/v1/audio/speech",
            json={
                **SPEECH,
                "response_format": output_format,
            },
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == f"audio/{output_format}"
    assert response.content.startswith(b"fLaC" if output_format == "flac" else b"\xff")


async def test_health_does_not_call_provider():
    async with client_for(lambda request: pytest.fail("Health must be local")) as client:
        response = await client.get("/healthz")
    assert response.json() == {"status": "ok"}


def test_environment_settings(monkeypatch):
    monkeypatch.setenv("DEFAULT_DIALECT", "bern")
    monkeypatch.setenv("VOICE_ALIASES", '{"alloy":"anna"}')
    assert Settings.from_env().dialect == "bern"
    assert Settings.from_env().voice_aliases == {"alloy": "anna"}
    monkeypatch.setenv("VOICE_ALIASES", '["anna"]')
    with pytest.raises(ValueError, match="VOICE_ALIASES"):
        Settings.from_env()
