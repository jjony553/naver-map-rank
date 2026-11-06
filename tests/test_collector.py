"""Fast parsing tests, plus opt-in browser/public Naver integration checks."""

import os
import json
import math
import sys
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector import (
    _check_cancel, _context_options, _desktop_coordinate_evidence, _launch_browser,
    _parse_mobile_location_ack, _READ_PUBLIC_MOBILE_LOCATION_JS, _search_url,
    collector_health, collect_keyword, normalize_search_location,
    parse_place_id, rank_observation, read_cards,
)


class CollectorParsingTests(unittest.TestCase):
    def test_naver_urls_and_numeric_ids(self):
        for value in (
            "12345678", "https://map.naver.com/p/entry/place/12345678?c=15.0",
            "https://m.place.naver.com/restaurant/12345678/home",
            "https://map.naver.com/p/search/food/place/12345678",
            "https://map.naver.com/?placeId=12345678",
        ):
            with self.subTest(value=value):
                self.assertEqual(parse_place_id(value), "12345678")
        for value in ("https://example.com/place/12345678", "https://notnaver.com/place/12345678", "https://naver.com.example.org/place/12345678", "잘못된 링크", "https://naver.me/shortlink"):
            with self.subTest(value=value):
                self.assertIsNone(parse_place_id(value))

    def test_ads_and_organic_have_independent_deduplicated_ranks(self):
        fixture = [
            {"placeId": "10", "name": "광고 식당", "isAd": True},
            {"placeId": "20", "name": "일반 식당", "isAd": False},
            {"placeId": "30", "name": "대상 식당", "isAd": True},
            {"placeId": "20", "name": "일반 식당", "isAd": False},
            {"placeId": "30", "name": "대상 식당", "isAd": False},
        ]
        self.assertEqual(rank_observation(fixture, {"placeId": "30", "name": "대상 식당"}), {
            "organicRank": 2, "adRank": 2, "observedDepth": 2,
            "observedAdCount": 2, "matchMethod": "place-id",
        })

    def test_id_precedes_same_name(self):
        ranks = rank_observation([{"placeId": "99", "name": "동명 식당"}], {"placeId": "42", "name": "동명 식당"})
        self.assertIsNone(ranks["organicRank"])
        self.assertIsNone(ranks["adRank"])
        self.assertEqual(ranks["observedDepth"], 1)

    def test_rank_beyond_requested_scope_is_absent(self):
        fixture = [{"placeId": str(index), "name": f"식당 {index}"} for index in range(1, 13)]
        ranks = rank_observation(fixture, {"placeId": "12"}, max_depth=10)
        self.assertIsNone(ranks["organicRank"])
        self.assertIsNone(ranks["adRank"])
        self.assertEqual(ranks["observedDepth"], 10)

    def test_exact_normalized_names_keep_branches_separate(self):
        fixture = [{"name": "브런치 성수점"}, {"name": "브런치 서울숲점"}]
        self.assertEqual(rank_observation(fixture, {"name": "브런치 서울숲점"})["organicRank"], 2)
        self.assertIsNone(rank_observation(fixture, {"name": "브런치"})["organicRank"])

    def test_missing_playwright_returns_setup_instructions(self):
        with patch("collector._playwright_module", return_value=None):
            health = collector_health()
        self.assertFalse(health["collectorReady"])
        self.assertIn("python -m pip install", health["message"])

    def test_cancel_event_is_honored(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(RuntimeError, "취소"):
            _check_cancel(cancel, float("inf"))

    def test_device_is_never_silently_mislabeled(self):
        with self.assertRaisesRegex(ValueError, "desktop 또는 mobile"):
            collect_keyword({"name": "식당"}, "맛집", device="tablet")

    def test_mobile_uses_dedicated_map_url(self):
        self.assertTrue(_search_url("성수 파스타", "mobile").startswith("https://m.map.naver.com/search?query="))
        self.assertTrue(_search_url("성수 파스타", "desktop").startswith("https://map.naver.com/p/search/"))
        self.assertNotIn("성수 파스타", _search_url("성수 파스타", "mobile"))

    def test_mobile_context_changes_ua_touch_viewport_without_gps(self):
        class FakePlaywright:
            devices = {"Pixel 7": {"user_agent": "Mobile test browser", "is_mobile": True, "has_touch": True, "viewport": {"width": 412, "height": 839}}}
        options = _context_options(FakePlaywright(), "mobile")
        self.assertTrue(options["is_mobile"])
        self.assertTrue(options["has_touch"])
        self.assertIn("Mobile", options["user_agent"])
        self.assertEqual(options["viewport"]["width"], 412)
        self.assertNotIn("geolocation", options)
        self.assertNotIn("permissions", options)
        desktop = _context_options(FakePlaywright(), "desktop")
        self.assertEqual(desktop["viewport"], {"width": 1440, "height": 1000})
        self.assertNotIn("is_mobile", desktop)

    def test_search_location_rejects_invalid_and_non_finite_coordinates(self):
        chosen = {"label": " 강남역 ", "latitude": 37.4979, "longitude": 127.0276}
        self.assertEqual(normalize_search_location(chosen), {**chosen, "label": "강남역"})
        self.assertIsNone(normalize_search_location(None))
        for latitude, longitude in ((math.nan, 127), (math.inf, 127), (91, 127), (37, 181), (True, 127), ("37.5", 127)):
            with self.subTest(latitude=latitude, longitude=longitude):
                with self.assertRaises(ValueError):
                    normalize_search_location({"label": "선택위치", "latitude": latitude, "longitude": longitude})

    def test_custom_location_changes_browser_context_and_pc_public_center_url(self):
        class FakePlaywright:
            devices = {"Pixel 7": {"is_mobile": True, "has_touch": True, "viewport": {"width": 412, "height": 839}}}
        location = {"label": "강남역", "latitude": 37.4979, "longitude": 127.0276}
        for device in ("desktop", "mobile"):
            options = _context_options(FakePlaywright(), device, location)
            self.assertEqual(options["geolocation"]["latitude"], location["latitude"])
            self.assertNotIn("permissions", options)
        self.assertIn("?c=14140647.7487,4508736.7354,15,0,0,0,dh", _search_url("맛집", "desktop", location))
        self.assertNotIn("latitude", _search_url("맛집", "mobile", location))

    def test_pc_location_requires_real_naver_search_coordinate_evidence(self):
        location = {"label": "강남역", "latitude": 37.4979, "longitude": 127.0276}
        evidence = _desktop_coordinate_evidence("https://map.naver.com/p/api/search/query-analyze?query=food&coords=127.02760001%3B37.49789999", location)
        self.assertEqual(evidence["method"], "desktop-map-center")
        self.assertEqual(evidence["evidence"], "naver-query-analyze-coords")
        self.assertIsNotNone(_desktop_coordinate_evidence("https://pcmap.place.naver.com/restaurant/list?x=127.0276&y=37.4979", location))
        self.assertIsNone(_desktop_coordinate_evidence("https://example.com/p/api/search/query-analyze?coords=127.0276;37.4979", location))
        self.assertIsNone(_desktop_coordinate_evidence("https://pcmap.place.naver.com/restaurant/list?x=126&y=36&clientX=127.0276&clientY=37.4979", location))
        self.assertIsNone(_desktop_coordinate_evidence("https://map.naver.com/p/api/search/query-analyze?coords=NaN;37.4979", location))

    def test_mobile_location_requires_success_ack_with_matching_coordinates(self):
        location = {"label": "강남역", "latitude": 37.4979, "longitude": 127.0276}
        def ack(action, longitude):
            return "naver_callback(" + json.dumps({"result": {"actionResult": action, "locationList": [{"xpos": longitude, "ypos": 37.4979}]}}) + ");"
        self.assertTrue(_parse_mobile_location_ack(ack("SUCCESS", 127.0276), location))
        self.assertFalse(_parse_mobile_location_ack(ack("FAIL", 127.0276), location))
        self.assertFalse(_parse_mobile_location_ack(ack("SUCCESS", 126), location))
        self.assertFalse(_parse_mobile_location_ack("not JSONP", location))


class CollectorBrowserTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "Set RUN_BROWSER_TESTS=1 for the installed browser fixture")
    def test_current_naver_dom_ids_and_ad_labels(self):
        if not collector_health()["collectorReady"]:
            self.skipTest("Python Playwright or browser is not installed")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright)
            try:
                page = browser.new_page()
                page.set_content("""<div id="_pcmap_list_scroll_container"><ul>
                  <li><a href="#" data-nlog-params='{"place_id":"11","rank":1}'><span class="TYaxT">광고식당</span></a><span>광고</span></li>
                  <li><a href="#" data-nlog-params='{"place_id":"1241532707","rank":2}'><span class="TYaxT">성수다락</span></a></li>
                  </ul></div>""")
                cards = read_cards(page.main_frame)
                self.assertEqual(len(cards), 2)
                self.assertEqual(cards[0]["placeId"], "11")
                self.assertTrue(cards[0]["isAd"])
                self.assertEqual(cards[1]["placeId"], "1241532707")
                self.assertFalse(cards[1]["isAd"])
                self.assertEqual(rank_observation(cards, {"placeId": "1241532707"})["organicRank"], 1)
            finally:
                browser.close()

    @unittest.skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "Set RUN_BROWSER_TESTS=1 for the mobile DOM fixture")
    def test_mobile_map_links_names_ads_and_deduplication(self):
        if not collector_health()["collectorReady"]:
            self.skipTest("Python Playwright or browser is not installed")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright)
            try:
                context = browser.new_context(**_context_options(playwright, "mobile"))
                page = context.new_page()
                page.set_content("""<ul>
                  <li class="_aside_menu_item_123">메뉴</li>
                  <li class="_list_item_sis14_40"><a href="https://m.place.naver.com/place/11/home"><strong class="_item_name_sis14_275">광고식당</strong></a><span>광고</span></li>
                  <li class="_list_item_sis14_40"><a href="https://m.place.naver.com/place/1241532707/home"><img alt=""></a><a href="https://m.place.naver.com/place/1241532707/home"><strong class="_item_name_sis14_275">성수다락</strong></a></li>
                  </ul>""")
                cards = read_cards(page.main_frame, "mobile")
                self.assertEqual(len(cards), 2)
                self.assertEqual(cards[0]["placeId"], "11")
                self.assertTrue(cards[0]["isAd"])
                self.assertEqual(cards[1]["placeId"], "1241532707")
                self.assertFalse(cards[1]["isAd"])
                ranks = rank_observation(cards, {"placeId": "1241532707"})
                self.assertEqual(ranks["organicRank"], 1)
                self.assertIsNone(ranks["adRank"])
                browser_info = page.evaluate("({ua:navigator.userAgent,touch:navigator.maxTouchPoints,width:innerWidth})")
                self.assertIn("Mobile", browser_info["ua"])
                self.assertGreater(browser_info["touch"], 0)
                page.set_content('<script>window.__RQ_STREAMING_STATE__=[]; window.__RQ_STREAMING_STATE__.push(' + json.dumps({"queries": [{"queryKey": ["v1", "getMyLocationSuspense", "getMyLocation"], "state": {"data": {"item": {"hasMyLocation": True, "myLocation": {"latitude": 37.4979, "longitude": 127.0276}}}}}]}) + ');</script>')
                self.assertEqual(page.evaluate(_READ_PUBLIC_MOBILE_LOCATION_JS), {"latitude": 37.4979, "longitude": 127.0276})
            finally:
                browser.close()

    @unittest.skipUnless(os.environ.get("RUN_LIVE_NAVER_TEST") == "1", "Set RUN_LIVE_NAVER_TEST=1 for the public Naver integration check")
    def test_public_naver_place_id_observation(self):
        result = collect_keyword({"name": "성수다락", "placeId": "1241532707"}, "성수 파스타", max_depth=20, region="서울 성동구")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["placeId"], "1241532707")
        self.assertEqual(result["matchMethod"], "place-id")
        self.assertEqual(result["source"], "naver-map")
        self.assertEqual(result["device"], "desktop")
        self.assertEqual(result["clientMode"], "desktop-web")
        self.assertEqual(result["locationMode"], "naver-default")
        self.assertGreater(result["observedDepth"], 0)
        self.assertLessEqual(result["maxDepth"], 20)
        if result["organicRank"] is not None:
            self.assertLessEqual(result["organicRank"], result["maxDepth"])

    @unittest.skipUnless(os.environ.get("RUN_LIVE_NAVER_TEST") == "1", "Set RUN_LIVE_NAVER_TEST=1 for the public mobile Naver integration check")
    def test_public_mobile_naver_place_id_observation(self):
        result = collect_keyword({"name": "성수다락", "placeId": "1241532707"}, "성수 파스타", max_depth=20, region="서울 성동구", device="mobile")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["placeId"], "1241532707")
        self.assertEqual(result["matchMethod"], "place-id")
        self.assertEqual(result["device"], "mobile")
        self.assertEqual(result["clientMode"], "mobile-web-emulation")
        self.assertEqual(result["locationMode"], "naver-default")
        self.assertTrue(result["sourceUrl"].startswith("https://m.map.naver.com/"))
        self.assertGreater(result["observedDepth"], 0)
        self.assertLessEqual(result["maxDepth"], 20)
        if result["organicRank"] is not None:
            self.assertLessEqual(result["organicRank"], result["maxDepth"])

    @unittest.skipUnless(os.environ.get("RUN_LIVE_LOCATION_TEST") == "1", "Set RUN_LIVE_LOCATION_TEST=1 for the public Naver region checks")
    def test_public_naver_custom_search_region_both_devices(self):
        location = {"label": "강남역", "latitude": 37.4979, "longitude": 127.0276}
        for device in ("desktop", "mobile"):
            with self.subTest(device=device):
                result = collect_keyword({"name": "성수다락", "placeId": "1241532707"}, "맛집", max_depth=20, device=device, location=location)
                self.assertEqual(result["status"], "success")
                self.assertTrue(result["locationApplied"])
                self.assertEqual(result["searchLocation"], location)
                self.assertEqual(result["requestedSearchLocation"], location)
                self.assertEqual(result["locationMode"], "custom-geolocation")
                self.assertEqual(result["device"], device)
                self.assertEqual(result["locationVerification"]["method"], "desktop-map-center" if device == "desktop" else "mobile-my-location-ui")
                self.assertGreater(result["observedDepth"], 0)


if __name__ == "__main__":
    unittest.main()
