"""Application settings loaded from environment variables (and .env in local dev)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

TTSProviderName = Literal["openai", "mock"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "retail-kiosk-backend"
    app_env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    # OpenAI (secret stays server-side; never sent to Flutter)
    openai_api_key: SecretStr | None = None
    openai_stt_model: str = ""
    openai_agent_model: str = ""
    openai_tts_model: str = ""
    openai_timeout_seconds: float = Field(default=20.0, gt=0)
    openai_max_retries: int = Field(default=1, ge=0)
    # Keep idle HTTPS connections to OpenAI open (SDK default: 5 s). A new TLS
    # connection costs ~0.5-1 s, and kiosk customers arrive minutes apart.
    openai_keepalive_seconds: float = Field(default=120.0, gt=0)
    # Open a connection at startup so the first customer doesn't pay for it.
    openai_warmup: bool = True
    # >0: touch the API every N seconds while idle to keep the connection warm.
    openai_keepwarm_interval_seconds: float = Field(default=0.0, ge=0)

    # Speech-to-text
    stt_provider: Literal["openai", "mock"] = "openai"
    # A few seconds of speech normally transcribes in ~1 s.
    openai_stt_timeout_seconds: float = Field(default=6.0, gt=0)
    openai_stt_temperature: float = Field(default=0.0, ge=0, le=1)
    # Put catalog brand names in the STT prompt so they're spelled as in the catalog.
    stt_catalog_vocabulary: bool = True

    # Retail Store Assistant
    # Reasoning effort for reasoning models (e.g. "none", "minimal", "low"); empty = not sent.
    openai_agent_reasoning_effort: str = ""
    openai_agent_max_output_tokens: int = Field(default=500, gt=0)
    # Per model call. Normal calls take ~1-2 s; a stalled call is cut off and retried
    # once (openai_max_retries) while still inside agent_timeout_seconds.
    openai_agent_request_timeout_seconds: float = Field(default=6.0, gt=0)
    agent_timeout_seconds: float = Field(default=15.0, gt=0)
    agent_max_turns: int = Field(default=4, gt=0)
    # Render common answers (product location, hours, BMI, ...) from tool results in
    # Python instead of a second LLM call: ~1 s faster, wording fixed.
    agent_template_answers: bool = True
    # Agents SDK tracing uploads conversations to the OpenAI dashboard. Off by default.
    openai_agents_tracing: bool = False

    # Text-to-speech. Provider per language so Burmese can move to another vendor
    # (Azure, Google, ...) without touching English, the agent or Flutter.
    tts_provider: TTSProviderName = "openai"
    tts_provider_my_mm: TTSProviderName | None = None  # overrides tts_provider for my-MM
    tts_provider_en_us: TTSProviderName | None = None  # overrides tts_provider for en-US
    openai_tts_voice: str = "coral"
    openai_tts_format: Literal["mp3", "opus", "aac", "flac", "wav", "pcm"] = "mp3"
    openai_tts_speed: float = Field(default=1.0, ge=0.25, le=4.0)
    openai_tts_timeout_seconds: float = Field(default=15.0, gt=0)
    # Per attempt: if no audio has arrived by then the request is treated as stalled
    # and retried (tts_attempts). Normal first byte: ~1-2 s.
    tts_first_byte_timeout_seconds: float = Field(default=4.0, gt=0)
    tts_attempts: int = Field(default=2, ge=1, le=3)
    # Cache synthesized clips by exact text (template answers repeat verbatim).
    # ~50-150 KB per clip; 0 disables.
    tts_cache_entries: int = Field(default=300, ge=0)
    max_tts_chars: int = Field(default=1000, gt=0)

    # Conversation sessions (short-term, in memory, per kiosk session_id)
    session_ttl_seconds: int = Field(default=600, gt=0)
    session_max_turns: int = Field(default=6, gt=0)

    # Business data source: "mock" reads CSVs from mock_data_dir, "google_sheets" the sheet.
    business_data_source: Literal["mock", "google_sheets"] = "mock"
    mock_data_dir: str = "sample_data"

    # Google Sheets (POC business data)
    google_sheets_id: str = ""
    # Empty = Application Default Credentials (e.g. the Cloud Run service account).
    google_service_account_file: str = ""
    google_sheets_timeout_seconds: float = Field(default=10.0, gt=0)

    # Trusted kiosk context defaults
    default_organization_id: str = "ORG-001"
    # Development only (KIOSK_REGISTRY_REQUIRED=false): store for a kiosk that isn't in
    # the KIOSKS tab and sends no store_id. Empty = such kiosks are rejected.
    default_store_id: str = "STORE-001"

    # Cache
    cache_ttl_seconds: int = Field(default=300, ge=0)
    # After a failed load, wait this long before trying the source again.
    cache_retry_seconds: int = Field(default=15, ge=0)

    # Admin endpoints (/admin/*): open in development, otherwise require X-Admin-Key.
    admin_api_key: SecretStr | None = None

    # Kiosk auth (X-Kiosk-Key). Comma-separated keys; "KIOSK-001:<key>" binds a key to
    # one kiosk (a bound key can only act as that kiosk). Plain "<key>" = unbound.
    kiosk_auth_enabled: bool = False
    kiosk_api_keys: SecretStr | None = None
    # true: only kiosks listed in the KIOSKS tab may connect (their store comes from
    # the registry). false (dev): unregistered kiosks fall back to the client's store_id.
    kiosk_registry_required: bool = False

    # Voice mode the kiosk should use (served by GET /api/kiosk/config):
    # "realtime" = WebRTC to OpenAI Realtime; "chained" = STT -> agent -> TTS.
    voice_mode: Literal["chained", "realtime"] = "chained"

    # OpenAI Realtime (WebRTC audio flows kiosk <-> OpenAI; this backend creates the
    # call with the permanent key and runs tools over a server-side WebSocket).
    openai_realtime_model: str = ""
    openai_realtime_voice: str = "marin"
    realtime_vad: Literal["server_vad", "semantic_vad"] = "server_vad"
    realtime_vad_threshold: float = Field(default=0.6, ge=0, le=1)
    realtime_vad_silence_ms: int = Field(default=500, ge=100, le=5000)
    realtime_vad_prefix_padding_ms: int = Field(default=300, ge=0, le=2000)
    realtime_vad_eagerness: Literal["low", "medium", "high", "auto"] = "auto"
    # Kiosk microphones are usually a metre or more away: "far_field"; "" = off.
    realtime_noise_reduction: Literal["far_field", "near_field", ""] = "far_field"
    # Transcribe customer speech (for the on-screen bubble and optional logs). Costs extra.
    realtime_input_transcription_model: str = "gpt-4o-transcribe"
    realtime_max_output_tokens: int = Field(default=1024, gt=0)
    # Hang up when nobody has spoken / nothing happened for this long.
    realtime_idle_timeout_seconds: int = Field(default=60, ge=10)
    # Hard cap on one customer conversation.
    realtime_max_session_seconds: int = Field(default=600, ge=30)
    # Mascot greets first when the conversation starts.
    realtime_greeting: bool = True
    realtime_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    # Business tools offered to the Realtime model (allow-list, comma-separated).
    realtime_tools: str = "search_product,get_store_info,search_faq,calculate_bmi"
    # Cost guard: refuse new calls beyond this many at once.
    realtime_max_concurrent_calls: int = Field(default=20, gt=0)

    # Privacy / observability
    log_transcripts: bool = False

    # Request limits
    max_audio_bytes: int = Field(default=10 * 1024 * 1024, gt=0)

    @field_validator("openai_api_key", "kiosk_api_keys", "admin_api_key", mode="before")
    @classmethod
    def _clean_secret(cls, value: object) -> object:
        """Trim stray whitespace (e.g. a newline stored in Secret Manager) and treat an
        empty value (`ADMIN_API_KEY=`) as not set."""
        if isinstance(value, str):
            return value.strip() or None
        return value

    def kiosk_key_bindings(self) -> dict[str, str | None]:
        """key -> bound kiosk_id (None = unbound key). Entries: "KIOSK-001:<key>" or "<key>"."""
        if self.kiosk_api_keys is None:
            return {}
        bindings: dict[str, str | None] = {}
        for entry in self.kiosk_api_keys.get_secret_value().split(","):
            entry = entry.strip()
            if not entry:
                continue
            kiosk_id, sep, key = entry.partition(":")
            if sep and kiosk_id.strip() and key.strip():
                bindings[key.strip()] = kiosk_id.strip()
            else:
                bindings[entry] = None
        return bindings

    def realtime_tool_names(self) -> frozenset[str]:
        return frozenset(t.strip() for t in self.realtime_tools.split(",") if t.strip())

    def kiosk_key_set(self) -> frozenset[str]:
        return frozenset(self.kiosk_key_bindings())


@lru_cache
def get_settings() -> Settings:
    return Settings()
