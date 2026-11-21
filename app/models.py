from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ApiKeys(BaseModel):
    groq: str = ""
    gemini: str = ""
    claude: str = ""
    pixabay: str = ""
    pollinations: str = ""


class GenerateRequest(BaseModel):
    topics: str = Field(min_length=3, max_length=3000)
    aspect_ratio: Literal["16:9", "9:16"] = "16:9"
    duration_minutes: Literal[1, 3, 5, 10] = 1
    video_language: Literal["en", "ar"] = "en"
    voice: str = "en-US-ChristopherNeural"
    voice_rate: int = Field(default=-12, ge=-25, le=10)
    provider: Literal["demo", "groq", "gemini", "claude", "pollinations"] = "demo"
    allow_gemini_images: bool = False
    allow_pollinations: bool = False
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

    @model_validator(mode="before")
    @classmethod
    def default_voice_for_language(cls, values: object) -> object:
        if isinstance(values, dict) and not values.get("voice"):
            language = values.get("video_language", "en")
            values["voice"] = "ar-EG-ShakirNeural" if language == "ar" else "en-US-ChristopherNeural"
        return values

    @model_validator(mode="after")
    def validate_voice_language(self) -> "GenerateRequest":
        locale = self.voice.split("-", 1)[0].lower()
        if locale != self.video_language:
            language_name = "العربية" if self.video_language == "ar" else "الإنجليزية"
            raise ValueError(f"اختر صوتاً باللغة {language_name} ليتوافق مع لغة الفيديو.")
        return self
