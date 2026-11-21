import base64
import asyncio
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

import httpx

from app.models import ApiKeys, GenerateRequest
from app.media import make_voiceover
from app.pipeline import _scene_count_for_duration
from app.providers import (
    _discover_claude_sonnet_models,
    _extract_gemini_image,
    _generate_claude,
    _gemini_image_ids,
    _parse_json,
    _prompt,
    _strict_prompt,
    demo_script,
    generate_gemini_image,
    GeminiImageError,
)


class ProviderRoutingTests(unittest.TestCase):
    def test_scene_schedule_matches_all_selected_runtimes_and_keeps_voice_gap(self) -> None:
        expected_scene_counts = {1: 19, 3: 58, 5: 97, 10: 194}
        for minutes, expected_count in expected_scene_counts.items():
            requested_seconds = minutes * 60
            scene_count = _scene_count_for_duration(requested_seconds)
            interval = (requested_seconds - 0.22) / scene_count
            speech_budget = interval - 0.5
            self.assertEqual(scene_count, expected_count)
            self.assertAlmostEqual(scene_count * interval + 0.22, requested_seconds, places=8)
            self.assertGreaterEqual(speech_budget, 2.5)
            self.assertAlmostEqual(interval - speech_budget, 0.5, places=8)

    def test_giza_demo_has_enough_curated_scenes_for_one_minute(self) -> None:
        script = demo_script("Giza Egypt pyramids", "ar", 19)
        self.assertEqual(len(script["scenes"]), 19)
        self.assertEqual(script["model"], "curated-giza-demo-19-scenes")

    def test_voice_rate_defaults_to_minus_twelve_and_is_bounded(self) -> None:
        request = GenerateRequest(topics="موضوع وثائقي")
        self.assertEqual(request.voice_rate, -12)
        self.assertEqual(request.provider, "demo")
        self.assertEqual(GenerateRequest(topics="test", voice_rate=10).voice_rate, 10)
        with self.assertRaises(ValueError):
            GenerateRequest(topics="test", voice_rate=11)
        with self.assertRaises(ValueError):
            GenerateRequest(topics="test", voice_rate=-26)

    def test_claude_is_a_supported_text_provider(self) -> None:
        request = GenerateRequest(topics="test", provider="claude", keys=ApiKeys(claude="test-key"))
        self.assertEqual(request.provider, "claude")
        self.assertEqual(request.keys.claude, "test-key")

    def test_prompts_direct_visual_type_and_claude_friendly_json(self) -> None:
        for prompt in (_prompt("Bermuda", "en", "16:9", 2), _strict_prompt("Bermuda", "en", "16:9", 2)):
            self.assertIn("visual_type", prompt)
            self.assertIn("ai_image", prompt)
            self.assertIn("stock_video", prompt)

        exact_duration_prompt = _strict_prompt("Bermuda", "en", "16:9", 19, 60)
        self.assertIn("about 60 seconds long", exact_duration_prompt)
        self.assertIn("2.6 seconds", exact_duration_prompt)

    def test_parser_requires_known_visual_type_per_scene(self) -> None:
        payload = {
            "title": "Test",
            "visual_anchor": "Bermuda Triangle",
            "scenes": [{
                "narration": "Ships vanished without warning.",
                "translation_ar": "اختفت السفن بلا إنذار.",
                "visual_type": "stock_video",
                "visual_keywords": "Bermuda Triangle cargo ship storm night",
                "image_prompt": "Bermuda Triangle cargo ship caught in a real storm at night",
            }],
        }
        import json
        parsed = _parse_json(json.dumps(payload), "en", "16:9", 1)
        self.assertEqual(parsed["scenes"][0]["visual_type"], "stock_video")
        payload["scenes"][0]["visual_type"] = "generic_broll"
        with self.assertRaises(ValueError):
            _parse_json(json.dumps(payload), "en", "16:9", 1)

    def test_gemini_image_models_require_generate_content_and_image_name(self) -> None:
        payload = {"models": [
            {"name": "models/gemini-2.5-flash-image", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.1-flash-image-preview", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-1.5-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/imagen-3", "supportedGenerationMethods": ["predict"]},
        ]}
        self.assertEqual(
            _gemini_image_ids(payload),
            ["gemini-3.1-flash-image-preview", "gemini-2.5-flash-image"],
        )

    def test_inline_image_extracts_bytes_and_ignores_text(self) -> None:
        image_bytes = b"fake-image-bytes"
        encoded = base64.b64encode(image_bytes).decode("ascii")
        payload = {"candidates": [{"content": {"parts": [
            {"text": "an image was generated"},
            {"inlineData": {"mimeType": "image/png", "data": encoded}},
        ]}}]}
        self.assertEqual(_extract_gemini_image(payload), image_bytes)
        self.assertIsNone(_extract_gemini_image({"candidates": [{"content": {"parts": [{"text": "only text"}]}}]}))

    def test_claude_model_discovery_filters_and_prefers_active_sonnet(self) -> None:
        class Response:
            status_code = 200
            def json(self):
                return {"data": [
                    {"id": "claude-3-5-sonnet-latest"},
                    {"id": "claude-opus-4-6"},
                    {"id": "claude-sonnet-4-6"},
                ], "has_more": False}

        class Client:
            async def get(self, *args, **kwargs):
                return Response()

        models = asyncio.run(_discover_claude_sonnet_models(cast(httpx.AsyncClient, Client()), "fake-key"))
        self.assertEqual([item["id"] for item in models], ["claude-sonnet-4-6", "claude-3-5-sonnet-latest"])

    def test_edge_tts_receives_chosen_rate_and_minus_three_hz_pitch(self) -> None:
        class FakeCommunicate:
            arguments = None
            def __init__(self, **kwargs):
                type(self).arguments = kwargs
            async def save(self, path):
                Path(path).write_bytes(b"test")

        with patch("edge_tts.Communicate", FakeCommunicate):
            asyncio.run(make_voiceover("A concise narration.", "en-US-ChristopherNeural", Path("/tmp/docuflow-test.mp3"), rate=-12))
        arguments = cast(dict, FakeCommunicate.arguments)
        self.assertEqual(arguments["rate"], "-12%")
        self.assertEqual(arguments["pitch"], "-3Hz")
        Path("/tmp/docuflow-test.mp3").unlink(missing_ok=True)

    def test_claude_messages_uses_discovered_sonnet_and_parses_visual_director_schema(self) -> None:
        import json
        payload = {
            "title": "Bermuda evidence",
            "visual_anchor": "Bermuda Triangle",
            "scenes": [{
                "narration": "Ships crossed dangerous Atlantic waters.",
                "translation_ar": "عبرت السفن مياه الأطلسي الخطرة.",
                "visual_type": "stock_video",
                "visual_keywords": "Bermuda Triangle cargo ship Atlantic storm",
                "image_prompt": "Bermuda Triangle cargo ship crossing a stormy Atlantic sea",
            }],
        }
        class Response:
            status_code = 200
            def __init__(self, body): self.body = body
            def json(self): return self.body
        class Client:
            posted = None
            async def get(self, *args, **kwargs):
                return Response({"data": [{"id": "claude-sonnet-4-6"}], "has_more": False})
            async def post(self, *args, **kwargs):
                type(self).posted = kwargs["json"]
                return Response({"content": [{"type": "text", "text": json.dumps(payload)}]})
        result = asyncio.run(_generate_claude(cast(httpx.AsyncClient, Client()), "strict prompt", "test-key", "en", "16:9", 1))
        self.assertEqual(result["model"], "claude-sonnet-4-6")
        self.assertEqual(result["scenes"][0]["visual_type"], "stock_video")
        posted = cast(dict, Client.posted)
        self.assertEqual(posted["messages"][0]["content"], "strict prompt")

    def test_gemini_image_request_uses_image_modality_and_surfaces_quota(self) -> None:
        class Response:
            def __init__(self, status_code, body): self.status_code, self.body = status_code, body
            def json(self): return self.body
        class Client:
            arguments = None
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return None
            async def get(self, *_args, **_kwargs):
                return Response(200, {"models": [{"name": "models/gemini-2.5-flash-image", "supportedGenerationMethods": ["generateContent"]}]})
            async def post(self, *_args, **kwargs):
                type(self).arguments = kwargs["json"]
                return Response(429, {})
        with patch("app.providers.httpx.AsyncClient", return_value=Client()):
            with self.assertRaises(GeminiImageError) as captured:
                asyncio.run(generate_gemini_image("Bermuda Triangle storm", "test-key", "16:9", Path("/tmp/no-image.jpg")))
        self.assertEqual(captured.exception.status_code, 429)
        arguments = cast(dict, Client.arguments)
        self.assertEqual(arguments["generationConfig"]["responseModalities"], ["IMAGE"])


if __name__ == "__main__":
    unittest.main()
