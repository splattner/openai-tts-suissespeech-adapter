import asyncio
import json
import os
import secrets
import shutil
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

MODEL_ALIASES = {"tts-1": "swift", "tts-1-hd": "studio", "gpt-4o-mini-tts": "studio"}
OPENAI_VOICES = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "nova",
    "onyx",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
)
MEDIA_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "opus": "audio/ogg",
    "pcm": "audio/pcm",
    "aac": "audio/aac",
    "flac": "audio/flac",
}


@dataclass(frozen=True)
class Settings:
    base_url: str = "https://api.suisse-speech.ch/v1"
    upstream_api_key: str = ""
    adapter_api_key: str = ""
    default_voice: str = "anna"
    language: str = "de-CH"
    dialect: str = "standard"
    timeout: float = 120
    voice_aliases: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        aliases = json.loads(os.getenv("VOICE_ALIASES", "{}"))
        if not isinstance(aliases, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) or not value
            for key, value in aliases.items()
        ):
            raise ValueError("VOICE_ALIASES must be a JSON object of non-empty voice IDs")
        return cls(
            base_url=os.getenv("SUISSE_SPEECH_BASE_URL", cls.base_url).rstrip("/"),
            upstream_api_key=os.getenv("SUISSE_SPEECH_API_KEY", ""),
            adapter_api_key=os.getenv("ADAPTER_API_KEY", ""),
            default_voice=os.getenv("DEFAULT_VOICE", "anna"),
            language=os.getenv("DEFAULT_LANGUAGE", "de-CH"),
            dialect=os.getenv("DEFAULT_DIALECT", "standard"),
            timeout=float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "120")),
            voice_aliases=aliases,
        )


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    model: str = "tts-1-hd"
    input: str = Field(max_length=8000)
    voice: str | None = None
    response_format: Literal["mp3", "wav", "opus", "pcm", "aac", "flac"] = "mp3"
    speed: float = Field(default=1, ge=0.5, le=2)
    instructions: str | None = Field(default=None, max_length=600)
    stream_format: Literal["audio"] = "audio"
    language: str | None = None
    dialect: str | None = None
    style: str | None = Field(default=None, max_length=600)
    sample_rate_hz: int | None = Field(default=None, gt=0)
    cache: Literal["off", "use", "refresh"] | None = None
    cache_ttl_s: int | None = Field(default=None, ge=60, le=2592000)


class APIError(Exception):
    def __init__(
        self,
        status: int,
        message: str,
        code: str,
        param: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.status = status
        self.message = message
        self.code = code
        self.param = param
        self.headers = headers


async def transcode(audio: bytes, output_format: str) -> bytes:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise APIError(
            400,
            "AAC/FLAC require ffmpeg; use mp3, wav, opus or pcm instead.",
            "unsupported_format",
            "response_format",
        )
    codec = "aac" if output_format == "aac" else "flac"
    container = "adts" if output_format == "aac" else "flac"
    process = await asyncio.create_subprocess_exec(
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-map",
        "0:a:0",
        "-c:a",
        codec,
        "-f",
        container,
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        converted, _ = await asyncio.wait_for(process.communicate(audio), timeout=60)
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise APIError(502, "Could not convert the upstream audio.", "audio_conversion_failed")
    return converted


def create_app(settings: Settings | None = None, transport=None) -> FastAPI:
    settings = settings or Settings.from_env()
    if settings.upstream_api_key and not settings.adapter_api_key:
        raise ValueError("ADAPTER_API_KEY is required when SUISSE_SPEECH_API_KEY is set")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(
            base_url=settings.base_url.rstrip("/") + "/",
            timeout=settings.timeout,
            transport=transport,
            follow_redirects=False,
        ) as client:
            app.state.client = client
            yield

    app = FastAPI(title="Suisse Speech OpenAI adapter", lifespan=lifespan)

    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, error: APIError):
        error_type = "invalid_request_error" if error.status < 500 else "server_error"
        return JSONResponse(
            status_code=error.status,
            headers=error.headers,
            content={
                "error": {
                    "message": error.message,
                    "type": error_type,
                    "param": error.param,
                    "code": error.code,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, error: RequestValidationError):
        issue = error.errors()[0]
        return await api_error_handler(
            request,
            APIError(
                400,
                issue["msg"],
                "invalid_request",
                str(issue["loc"][-1]),
            ),
        )

    async def upstream(request: Request, key: str, method: str, path: str, **kwargs):
        try:
            response = await request.app.state.client.request(
                method,
                path,
                headers={
                    "X-API-Key": key,
                    "Accept": "audio/*" if path == "tts" else "application/json",
                },
                **kwargs,
            )
        except httpx.TimeoutException:
            raise APIError(504, "Suisse Speech timed out.", "upstream_timeout") from None
        except httpx.RequestError:
            raise APIError(502, "Could not reach Suisse Speech.", "upstream_unavailable") from None
        if not 200 <= response.status_code < 300:
            status = response.status_code if response.status_code >= 400 else 502
            message = "Suisse Speech rejected the request."
            code = "upstream_error"
            try:
                error = response.json().get("error", {})
                if isinstance(error, dict):
                    message = str(error.get("message", message))
                    code = str(error.get("code", code))
            except (ValueError, AttributeError):
                pass
            for secret in (key, settings.adapter_api_key):
                if secret:
                    message = message.replace(secret, "[redacted]")
                    code = code.replace(secret, "[redacted]")
            if code == "tts.unknown_voice":
                code = "voice_not_found"
            headers = {
                name: response.headers[name]
                for name in ("retry-after", "x-request-id")
                if name in response.headers
            }
            raise APIError(status, message, code, headers=headers)
        return response

    async def authenticate(request: Request) -> str:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise APIError(
                401, "Provide an API key using Authorization: Bearer <key>.", "invalid_api_key"
            )
        token = token.strip()
        if settings.adapter_api_key and not secrets.compare_digest(
            token.encode(),
            settings.adapter_api_key.encode(),
        ):
            raise APIError(401, "Invalid adapter API key.", "invalid_api_key")
        return settings.upstream_api_key or token

    credential = Annotated[str, Depends(authenticate)]

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models(request: Request, key: credential):
        await upstream(request, key, "GET", "capabilities")
        return {
            "object": "list",
            "data": [
                {"id": model, "object": "model", "created": 0, "owned_by": "suisse-speech"}
                for model in (*MODEL_ALIASES, "studio", "swift")
            ],
        }

    @app.get("/v1/audio/voices")
    async def voices(request: Request, key: credential):
        response = await upstream(request, key, "GET", "tts/voices")
        try:
            catalogue = response.json()["voices"]
            if not isinstance(catalogue, list) or any(
                not isinstance(voice, dict) or not isinstance(voice.get("id"), str)
                for voice in catalogue
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise APIError(
                502,
                "Invalid Suisse Speech voice catalogue.",
                "upstream_error",
            ) from None
        return {"object": "list", "data": catalogue}

    @app.post("/v1/audio/speech")
    async def speech(body: SpeechRequest, request: Request, key: credential):
        if not body.input.strip():
            await upstream(request, key, "GET", "capabilities")
            raise APIError(400, "Input must not be empty.", "invalid_request", "input")
        model = MODEL_ALIASES.get(body.model, body.model)
        if model not in ("studio", "swift"):
            raise APIError(
                400,
                "Use studio, swift, tts-1, tts-1-hd or gpt-4o-mini-tts.",
                "model_not_found",
                "model",
            )
        voice = body.voice or settings.default_voice
        voice = settings.voice_aliases.get(
            voice,
            settings.default_voice if voice in OPENAI_VOICES else voice,
        )
        conversion = body.response_format in ("aac", "flac")
        if conversion and not shutil.which("ffmpeg"):
            raise APIError(
                400,
                "AAC/FLAC require ffmpeg; use mp3, wav, opus or pcm instead.",
                "unsupported_format",
                "response_format",
            )
        payload = {
            "text": body.input,
            "model": model,
            "voice": voice,
            "language": body.language or settings.language,
            "speed": body.speed,
            "output": "audio",
            "format": "wav"
            if conversion
            else ("pcm16" if body.response_format == "pcm" else body.response_format),
        }
        if body.dialect is not None:
            payload["dialect"] = body.dialect
        elif body.language is None:
            payload["dialect"] = settings.dialect
        if body.style is not None or body.instructions is not None:
            payload["style"] = body.style if body.style is not None else body.instructions
        for name in ("sample_rate_hz", "cache", "cache_ttl_s"):
            value = getattr(body, name)
            if value is not None:
                payload[name] = value
        if body.response_format == "pcm":
            if body.sample_rate_hz not in (None, 24000):
                raise APIError(
                    400, "OpenAI PCM requires 24000 Hz.", "invalid_request", "sample_rate_hz"
                )
            payload["sample_rate_hz"] = 24000
        response = await upstream(request, key, "POST", "tts", json=payload)
        if not response.content or not response.headers.get("content-type", "").lower().startswith(
            ("audio/", "application/octet-stream")
        ):
            raise APIError(502, "Suisse Speech did not return audio.", "upstream_error")
        try:
            audio = (
                await transcode(response.content, body.response_format)
                if conversion
                else response.content
            )
        except TimeoutError:
            raise APIError(504, "Audio conversion timed out.", "audio_conversion_timeout") from None
        headers = {
            name: value
            for name, value in response.headers.items()
            if name.startswith("x-suisse-") or name == "x-request-id"
        }
        return Response(audio, media_type=MEDIA_TYPES[body.response_format], headers=headers)

    return app


app = create_app()
