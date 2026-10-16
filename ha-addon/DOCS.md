# Suisse Speech OpenAI TTS

This add-on connects the **OpenAI TTS** custom integration by `sfortis` to
Suisse Speech. It needs a Suisse Speech account/key; it does not use OpenAI.

## Installation and first start

1. Install this add-on from the Suisse Speech OpenAI TTS repository.
2. Set your preferred `default_voice`, `default_language`, and `default_dialect`
   in the Configuration tab. Defaults: `anna`, `de-CH`, `zurich`.
3. For the simplest setup, leave **both API key options empty**. You will put
   your Suisse Speech API key in the OpenAI TTS integration instead.
4. Start the add-on and enable **Start on boot** and **Watchdog** if desired.
5. Install `sfortis/openai_tts` through HACS and add a **Custom** provider.
6. Open the add-on's Log tab and copy the **Internal OpenAI speech endpoint**
   into the integration's API endpoint field: `http://ADDON_HOSTNAME:8000/v1/audio/speech`.
7. Enter your Suisse Speech key in the integration's API key field. Select
   model `studio` or `swift`, voice `anna` (or a live catalogue voice), format `mp3`.

Home Assistant reaches the add-on through the internal Supervisor network.
Port 8000 stays **inside the container** and does not conflict with a host service.
No host port is published by default. Use the internal hostname, not `localhost`
or your Home Assistant host's LAN IP.

With the repository URL from the installation instructions, the usual endpoint is:

```text
http://b81c4299-suisse-speech-openai-tts:8000/v1/audio/speech
```

The hostname depends on the repository ID; the Log tab is authoritative.
For an existing 0.1.0 installation with a port conflict, clear the host port
mapping in the add-on's **Network** tab, save, and restart. Updating the add-on
may retain your previous port setting, so check it even after updating.

**Why not ingress?** Ingress proxies an authenticated Home Assistant browser
session. The OpenAI TTS integration makes server-to-server HTTP requests with
an API key; it does not carry an ingress/browser session. An ingress/sidebar URL
is therefore not a suitable speech API endpoint. This service does not need ingress.

If other devices on your LAN need the API, optionally map `8000/tcp` to a free
host port in the Network tab, e.g. `18000`, and use
`http://YOUR_HOME_ASSISTANT_IP:18000/v1/audio/speech` on those devices. Internal
Home Assistant requests still use the add-on hostname and container port 8000.

## Configuration

| Option | Default | Meaning |
| --- | --- | --- |
| `suisse_speech_api_key` | empty | Optional server-held Suisse Speech key |
| `adapter_api_key` | empty | Required client secret when a provider key is stored |
| `default_voice` | `anna` | Native Suisse Speech voice ID |
| `default_language` | `de-CH` | Provider language code |
| `default_dialect` | `zurich` | Dialect within the default language |
| `upstream_timeout_seconds` | `120` | HTTP operation timeout, 1–600 seconds |
| `voice_aliases` | `[]` | Optional list of `{alias, voice}` mappings |

To store the Suisse Speech key only in the add-on, configure **both** keys:

```yaml
suisse_speech_api_key: sv_your_provider_key
adapter_api_key: replace-with-a-long-random-secret
default_voice: anna
default_language: de-CH
default_dialect: zurich
upstream_timeout_seconds: 120
voice_aliases:
  - alias: alloy
    voice: anna
```

Then put `adapter_api_key`, not the Suisse Speech key, in the integration.
Setting only one of these options refuses startup. Both empty enables
pass-through: the client's Bearer key is forwarded as Suisse's `X-API-Key`.
The add-on reads its configuration at startup; restart it after changes.

## Dialects, models, and formats

`studio` and `swift` work directly. `tts-1-hd` and `gpt-4o-mini-tts` are aliases
for `studio`; `tts-1` maps to `swift`. Standard OpenAI voice names such as
`alloy` default to `default_voice`; native Suisse voice IDs are passed through.

To select a dialect per TTS agent, use the Custom provider's Extra JSON payload:

```json
{"language":"de-CH","dialect":"bern","style":"freundlich und herzlich"}
```

MP3, WAV, Opus and 24 kHz PCM are native. AAC and FLAC are converted with the
included ffmpeg. Speed is limited to Suisse's 0.5–2.0 range. Audio is buffered,
not realtime streamed; SSE is unsupported. Input is limited to 8,000 characters.

## Troubleshooting and security

- **Port already in use:** clear the host port in the Network tab; no mapping is
  needed for Home Assistant's internal connection.
- **Connection refused:** check that the add-on is running and copy the internal
  endpoint from its Log tab. The endpoint must end in `/v1/audio/speech`.
- **401/403:** check which credential mode you selected and the key in the integration.
- **429:** check your Suisse Speech allowance/concurrency. Requests are not retried automatically.
- **Synthetic audio:** `sv_test_…` keys produce sandbox audio; use a live key for real voices.
- **Health:** `http://ADDON_HOSTNAME:8000/healthz` checks the local server internally,
  not provider availability or credentials.

The add-on needs no Home Assistant API, host network, devices, or privileged
mode. It reads `/data/options.json`, drops to a non-root user, and does not save
text/audio. Speech text is sent to Suisse Speech. API keys are masked in the
configuration UI, but remain secrets in your Home Assistant backups.
Only expose the port on a trusted network; use HTTPS and rate limits if you
publish it externally. HTTP is unencrypted, and synthesis can incur charges.
