from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ApiKeys(BaseModel):
    groq: str = ""
    gemini: str = ""
    pixabay: str = ""


class GenerateRequest(BaseModel):
    topics: str = Field(min_length=3, max_length=3000)
    aspect_ratio: Literal["16:9", "9:16"] = "16:9"
    voice: str = "en-US-ChristopherNeural"
    provider: Literal["demo", "groq", "gemini"] = "demo"
    keys: ApiKeys = Field(default_factory=ApiKeys)
    whisper_model: Literal["tiny", "base", "small"] = "tiny"
    background_music: bool = True

    @field_validator("topics")
    @classmethod
    def validate_topics(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("أدخل موضوعاً واحداً على الأقل.")
        topics = [line.strip(" •\t-") for line in value.splitlines() if line.strip(" •\t-")]
        if not topics:
            raise ValueError("اكتب كل موضوع في سطر مستقل.")
        if len(topics) > 8:
            raise ValueError("الحد الأقصى 8 موضوعات في الدفعة الواحدة.")
        return "\n".join(topics)
