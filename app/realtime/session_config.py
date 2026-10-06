"""Realtime session configuration, built server-side for each call.

The kiosk never supplies instructions, tools or the model: everything here comes
from backend settings and the trusted kiosk context.
"""

from collections.abc import Sequence
from typing import Any

from app.config import Settings
from app.models.kiosk import Language
from app.realtime.tools import tool_definitions
from app.services.agent_instructions import instructions_for
from app.services.openai_stt_service import API_LANGUAGE, build_prompt


def turn_detection(settings: Settings) -> dict[str, Any]:
    """OpenAI's own turn detection; `interrupt_response` gives barge-in for free."""
    if settings.realtime_vad == "semantic_vad":
        return {
            "type": "semantic_vad",
            "eagerness": settings.realtime_vad_eagerness,
            "create_response": True,
            "interrupt_response": True,
        }
    return {
        "type": "server_vad",
        "threshold": settings.realtime_vad_threshold,
        "prefix_padding_ms": settings.realtime_vad_prefix_padding_ms,
        "silence_duration_ms": settings.realtime_vad_silence_ms,
        "create_response": True,
        "interrupt_response": True,
    }


def build_session_config(
    settings: Settings, language: Language, *, vocabulary: Sequence[str] = ()
) -> dict[str, Any]:
    audio_input: dict[str, Any] = {"turn_detection": turn_detection(settings)}
    if settings.realtime_noise_reduction:
        audio_input["noise_reduction"] = {"type": settings.realtime_noise_reduction}
    if settings.realtime_input_transcription_model:
        # Same Burmese handling as the chained STT: the API rejects "my", so Burmese
        # is signalled by a Burmese-script prompt carrying catalog brand names.
        transcription: dict[str, Any] = {
            "model": settings.realtime_input_transcription_model,
            "prompt": build_prompt(language, vocabulary),
        }
        if language in API_LANGUAGE:
            transcription["language"] = API_LANGUAGE[language]
        audio_input["transcription"] = transcription

    config: dict[str, Any] = {
        "type": "realtime",
        "model": settings.openai_realtime_model,
        "instructions": instructions_for(language, realtime=True),
        "output_modalities": ["audio"],
        "audio": {
            "input": audio_input,
            "output": {"voice": settings.openai_realtime_voice},
        },
        "max_output_tokens": settings.realtime_max_output_tokens,
    }
    tools = tool_definitions(sorted(settings.realtime_tool_names()))
    if tools:
        config["tools"] = tools
        config["tool_choice"] = "auto"
    return config
