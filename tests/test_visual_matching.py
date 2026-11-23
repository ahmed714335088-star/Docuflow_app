import unittest

from app.factual_profiles import is_giza_pyramid_topic
from app.visual_matching import (
    choose_commons_page,
    commons_license_is_reusable,
    rank_pixabay_hits,
    visual_relevance_score,
)
from app.visual_sources import _pixabay_search_query


class VisualMatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.anchor = "Giza pyramids Egypt"
        self.query = "Giza pyramids Egypt limestone casing stone block"

    def test_giza_demo_scope_excludes_other_pyramid_traditions(self) -> None:
        self.assertTrue(is_giza_pyramid_topic("أهرامات الجيزة"))
        self.assertTrue(is_giza_pyramid_topic("Egypt pyramids"))
        self.assertFalse(is_giza_pyramid_topic("Mayan pyramids"))
        self.assertFalse(is_giza_pyramid_topic("أهرامات الأزتك في المكسيك"))
        self.assertFalse(is_giza_pyramid_topic("pyramid architecture"))

    def test_irrelevant_pixabay_video_before_relevant_candidate_is_rejected(self) -> None:
        hits = [
            {"id": 11, "tags": "forest, waterfall, green, nature", "pageURL": "forest-waterfall"},
            {"id": 12, "tags": "Egypt, Giza, pyramid, limestone, casing, stone", "pageURL": "giza-pyramid-casing"},
        ]
        selected = rank_pixabay_hits(hits, self.query, self.anchor)
        self.assertEqual([item["id"] for item in selected], [12])

    def test_pixabay_scene_terms_must_match_in_addition_to_anchor(self) -> None:
        hits = [
            {"id": 21, "tags": "Egypt, Giza, pyramid, sunset, desert", "pageURL": "giza-sunset"},
        ]
        self.assertEqual(rank_pixabay_hits(hits, self.query, self.anchor), [])
        self.assertIsNone(visual_relevance_score("Egypt Giza pyramid sunset desert", self.anchor, self.query))

    def test_pixabay_api_query_is_two_words_and_keeps_scene_detail_for_tag_validation(self) -> None:
        query = "Bermuda Triangle cargo ship storm Atlantic night"
        self.assertEqual(_pixabay_search_query(query, "Bermuda Triangle"), "cargo ship")
        self.assertEqual(_pixabay_search_query("ocean waves", "ocean waves"), "ocean waves")

    def test_repeated_subject_term_in_scene_query_is_retained(self) -> None:
        query = "Giza pyramids Egypt Great Pyramid plateau overview"
        score = visual_relevance_score("Great Pyramid of Giza, Egypt", self.anchor, query)
        self.assertIsNotNone(score)

    def test_pixabay_used_still_ids_are_never_selected_twice(self) -> None:
        hits = [
            {"id": 31, "tags": "Egypt, Giza, pyramid, limestone, casing, stone"},
            {"id": 32, "tags": "Egypt, Giza, pyramid, limestone, casing, stone"},
        ]
        selected = rank_pixabay_hits(hits, self.query, self.anchor, {"31"})
        self.assertEqual([item["id"] for item in selected], [32])

    def test_commons_requires_scene_match_and_reusable_license(self) -> None:
        pages = [
            self._commons_page("File:Giza sunset.jpg", "Giza pyramid desert sunset", "CC BY-SA 4.0"),
            self._commons_page("File:Giza casing stone.jpg", "Giza pyramid limestone casing stone", "CC BY 2.0"),
            self._commons_page("File:Giza casing NC.jpg", "Giza pyramid limestone casing stone", "CC BY-NC 4.0"),
            self._commons_page("File:Giza casing unknown.jpg", "Giza pyramid limestone casing stone", ""),
        ]
        selected = choose_commons_page(pages, self.query, self.anchor, "landscape")
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected["title"], "File:Giza casing stone.jpg")
        self.assertEqual(selected["license"], "CC BY 2.0")

    def test_commons_requires_attribution_for_by_licenses_and_valid_links(self) -> None:
        missing_author = self._commons_page("File:Giza no author.jpg", "Giza pyramid limestone casing stone", "CC BY 4.0")
        missing_author["imageinfo"][0]["extmetadata"].pop("Artist", None)
        pages = [missing_author]
        self.assertIsNone(choose_commons_page(pages, self.query, self.anchor, "landscape"))

        missing_license_url = self._commons_page("File:Giza no license URL.jpg", "Giza pyramid limestone casing stone", "CC BY 4.0")
        missing_license_url["imageinfo"][0]["extmetadata"]["LicenseUrl"]["value"] = ""
        self.assertIsNone(choose_commons_page([missing_license_url], self.query, self.anchor, "landscape"))

        missing_page_url = self._commons_page("File:Giza no page URL.jpg", "Giza pyramid limestone casing stone", "CC BY 4.0")
        missing_page_url["imageinfo"][0]["descriptionurl"] = ""
        self.assertIsNone(choose_commons_page([missing_page_url], self.query, self.anchor, "landscape"))

        cc0_without_author = self._commons_page("File:Giza CC0.jpg", "Giza pyramid limestone casing stone", "CC0")
        cc0_without_author["imageinfo"][0]["extmetadata"].pop("Artist", None)
        self.assertIsNotNone(choose_commons_page([cc0_without_author], self.query, self.anchor, "landscape"))

    def test_commons_does_not_return_used_image_again(self) -> None:
        pages = [self._commons_page("File:Giza casing stone.jpg", "Giza pyramid limestone casing stone", "CC BY 4.0")]
        selected = choose_commons_page(pages, self.query, self.anchor, "landscape", {"File:Giza casing stone.jpg"})
        self.assertIsNone(selected)

    def test_reusable_license_allowlist(self) -> None:
        self.assertTrue(commons_license_is_reusable("CC BY-SA 3.0", "https://creativecommons.org/licenses/by-sa/3.0/"))
        self.assertTrue(commons_license_is_reusable("Public domain"))
        self.assertTrue(commons_license_is_reusable("CC0 1.0"))
        self.assertFalse(commons_license_is_reusable("CC BY-NC 4.0", "https://creativecommons.org/licenses/by-nc/4.0/"))
        self.assertFalse(commons_license_is_reusable("CC BY-ND 4.0", "https://creativecommons.org/licenses/by-nd/4.0/"))
        self.assertFalse(commons_license_is_reusable(""))

    @staticmethod
    def _commons_page(title: str, description: str, license_name: str) -> dict:
        return {
            "title": title,
            "categories": [{"title": "Category:Giza pyramid"}],
            "imageinfo": [{
                "url": "https://upload.wikimedia.org/example.jpg",
                "thumburl": "https://upload.wikimedia.org/thumb/example.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/" + title.replace(" ", "_"),
                "width": 1600,
                "height": 1000,
                "extmetadata": {
                    "ImageDescription": {"value": description},
                    "Artist": {"value": "<a>Test photographer</a>"},
                    "LicenseShortName": {"value": license_name},
                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0/" if license_name else ""},
                },
            }],
        }


if __name__ == "__main__":
    unittest.main()
