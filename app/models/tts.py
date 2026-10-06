from pydantic import BaseModel, Field, field_validator

from app.models.kiosk import Language


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000, examples=["ဆိုင်က ည ၉ နာရီမှာ ပိတ်ပါတယ်။"])
    language: Language = Field(examples=["my-MM"])

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must not be blank")
        return value
