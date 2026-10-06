"""OpenAI speech-to-text provider.

Burmese notes (verified 2026-10-06 with gpt-4o-transcribe):
- The API rejects language="my", so Burmese is signalled through the prompt.
- Without a Burmese prompt the model guesses other languages ("Sai beir a cein ...").
- A prompt written in Burmese script beats an English one; temperature 0 makes
  results consistent between runs.
"""

from collections.abc import Sequence

import openai
from openai import AsyncOpenAI, omit

from app.models.kiosk import Language
from app.services.stt_service import STTError, STTTimeoutError, TranscriptionResult
from app.utils.logging import get_logger
from app.utils.timing import Timer

logger = get_logger(__name__)

# ISO-639-1 codes the API accepts. Burmese ("my") is not one of them.
API_LANGUAGE: dict[Language, str] = {"en-US": "en"}

BURMESE_PROMPT = (
    "ဆိုင်ထဲက kiosk ကို ဖောက်သည်တွေ မြန်မာလို မေးကြပါတယ်။ "
    "ကုန်ပစ္စည်းနဲ့ brand နာမည်တွေကို အင်္ဂလိပ်လို ရောပြောတတ်ပါတယ်။ "
    "Words: aisle, rack, shelf, parking, restroom, BMI, cm, kg."
)
ENGLISH_PROMPT = (
    "A customer at a retail store kiosk asking about products, store hours, policies or BMI. "
    "Words: aisle, rack, shelf, parking, restroom, BMI, cm, kg."
)
MAX_VOCABULARY_CHARS = 600  # keep the prompt short; it is resent with every request


def build_prompt(language: Language | None, vocabulary: Sequence[str]) -> str:
    prompt = ENGLISH_PROMPT if language == "en-US" else BURMESE_PROMPT
    terms: list[str] = []
    used = 0
    for term in vocabulary:
        used += len(term) + 2
        if used > MAX_VOCABULARY_CHARS:
            break
        terms.append(term)
    return f"{prompt} Brands: {', '.join(terms)}." if terms else prompt


class OpenAISpeechToText:
    name = "openai"

    def __init__(
        self, client: AsyncOpenAI, *, model: str, request_timeout: float, temperature: float = 0.0
    ) -> None:
        self._client = client.with_options(timeout=request_timeout)
        self._model = model
        self._temperature = temperature

    @property
    def model(self) -> str:
        return self._model

    async def transcribe(
        self,
        audio: bytes,
        filename: str,
        language: Language | None = None,
        *,
        content_type: str | None = None,
        vocabulary: Sequence[str] = (),
    ) -> TranscriptionResult:
        api_language = API_LANGUAGE.get(language) if language else None
        timer = Timer()
        try:
            with timer:
                result = await self._client.audio.transcriptions.create(
                    model=self._model,
                    file=(filename, audio, content_type),
                    prompt=build_prompt(language, vocabulary),
                    temperature=self._temperature,
                    response_format="json",
                    language=api_language or omit,
                )
        except openai.APITimeoutError as exc:
            logger.warning(
                "stt_provider_timeout", extra={"provider": self.name, "stt_ms": timer.ms}
            )
            raise STTTimeoutError() from exc
        except openai.BadRequestError as exc:
            # Typically undecodable/corrupt audio.
            logger.warning(
                "stt_provider_rejected",
                extra={"provider": self.name, "detail": str(exc)[:300], "stt_ms": timer.ms},
            )
            raise STTError("The audio could not be processed.") from exc
        except openai.APIError as exc:
            logger.error(
                "stt_provider_error",
                extra={"provider": self.name, "error": type(exc).__name__, "stt_ms": timer.ms},
            )
            raise STTError() from exc

        return TranscriptionResult(
            text=result.text.strip(),
            language=language,
            duration_ms=timer.ms,
            provider=self.name,
            model=self._model,
        )
