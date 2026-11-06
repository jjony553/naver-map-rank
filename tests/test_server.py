"""브라우저 실행 없이 Flask API 계약과 중단/동시 요청을 확인합니다."""

import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app as server


READY = {"collectorReady": True, "browser": "chromium", "message": "준비 완료"}


def request_body(**changes):
    body = {"restaurant": {"name": "테스트 식당", "placeId": "123456789"},
            "keywords": ["성수 맛집"], "maxDepth": 100, "device": "desktop", "region": "서울 성동구"}
    body.update(changes)
    return body


def measurement(restaurant, keyword, **settings):
    return {"keyword": keyword, "placeId": restaurant["placeId"], "organicRank": 7,
            "adRank": 2, "observedAt": "2026-11-06T14:30:00+09:00", "status": "success",
            "maxDepth": settings["max_depth"], "region": settings["region"], "device": settings["device"],
            "source": "naver-map"}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / "index.html").write_text("<!doctype html><title>테스트 화면</title>", encoding="utf-8")
        (root / "core.mjs").write_text("export const test = 1;", encoding="utf-8")
        self.app = server.create_app({"TESTING": True, "DIST_ROOT": root, "PORT": 4173})
        self.client = self.app.test_client()
        self.health_patch = patch.object(server, "collector_health", return_value=READY)
        self.collect_patch = patch.object(server, "collect_keyword", side_effect=measurement)
        self.health = self.health_patch.start()
        self.collect = self.collect_patch.start()

    def tearDown(self):
        self.collect_patch.stop()
        self.health_patch.stop()
        self.temp.cleanup()

    def test_health_identifies_python_runtime_and_is_never_cached(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["service"], "place-rank-python")
        self.assertEqual(response.json["runtime"], "python")
        self.assertTrue(response.json["collectorReady"])
        self.assertFalse(response.json["scanActive"])
        self.assertEqual(response.json["supportedDevices"], ["desktop", "mobile"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_location_presets_are_public_representative_points_with_stable_ids(self):
        response = self.client.get("/api/locations")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["coordinateSystem"], "WGS84")
        presets = response.json["locations"]
        ids = [point["id"] for point in presets]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue({"seoul-cityhall", "seoul-seongsu", "seoul-gangnam", "seoul-hongdae",
                         "seoul-jamsil", "bundang-pangyo", "busan-haeundae", "daegu-dongseongno",
                         "daejeon-cityhall", "gwangju-cityhall", "jeju-cityhall"}.issubset(ids))
        for point in presets:
            self.assertEqual(server.validate_location(point)["latitude"], point["latitude"])
            self.assertTrue(point["anchor"])
            self.assertTrue(point["referenceUrl"].startswith("https://"))
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.collect.assert_not_called()

    def test_regions_cover_all_current_provinces_and_named_city_county_district_choices(self):
        response = self.client.get("/api/regions")
        self.assertEqual(response.status_code, 200)
        catalog = response.json
        self.assertEqual(catalog["asOf"], "2026-07-01")
        self.assertEqual(catalog["coordinateSystem"], "WGS84")
        self.assertTrue(catalog["source"]["url"].startswith("https://apihub.kma.go.kr/"))
        self.assertEqual(len(catalog["source"]["sha256"]), 64)
        expected = {"서울특별시", "전남광주통합특별시", "부산광역시", "대구광역시", "인천광역시",
                    "대전광역시", "울산광역시", "세종특별자치시", "경기도", "강원특별자치도",
                    "충청북도", "충청남도", "전북특별자치도", "경상북도", "경상남도", "제주특별자치도"}
        self.assertEqual({province["name"] for province in catalog["provinces"]}, expected)
        all_codes = []
        for province in catalog["provinces"]:
            self.assertIsInstance(province["code"], str)
            self.assertTrue(province["cities"])
            server.validate_location(province)
            for city in province["cities"]:
                self.assertIsInstance(city["code"], str)
                self.assertTrue(city["anchor"])
                server.validate_location(city)
                all_codes.append(city["code"])
        self.assertEqual(len(all_codes), len(set(all_codes)))
        self.assertEqual(len(all_codes), catalog["coverage"]["selectableCityCount"])
        self.assertEqual(catalog["coverage"]["sourceCityCount"], 256)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.collect.assert_not_called()

    def test_regions_include_city_aggregates_and_latest_incheon_hwaseong_gunwi_changes(self):
        provinces = {province["name"]: province for province in self.client.get("/api/regions").json["provinces"]}
        gyeonggi = {city["name"]: city for city in provinces["경기도"]["cities"]}
        for name in ["수원시", "성남시", "용인시", "화성시"]:
            self.assertTrue(gyeonggi[name]["isAggregate"])
            self.assertIn("근사 중심", gyeonggi[name]["anchor"])
        self.assertEqual(gyeonggi["수원시 장안구"]["parentCode"], gyeonggi["수원시"]["code"])
        self.assertTrue({"화성시 만세구", "화성시 효행구", "화성시 병점구", "화성시 동탄구"}.issubset(gyeonggi))
        incheon = {city["name"] for city in provinces["인천광역시"]["cities"]}
        self.assertTrue({"제물포구", "영종구", "서해구", "검단구"}.issubset(incheon))
        self.assertTrue({"중구", "동구", "서구"}.isdisjoint(incheon))
        self.assertIn("군위군", {city["name"] for city in provinces["대구광역시"]["cities"]})
        self.assertNotIn("군위군", {city["name"] for city in provinces["경상북도"]["cities"]})
        integrated = provinces["전남광주통합특별시"]
        self.assertTrue({"광주", "광주광역시", "전남", "전라남도"}.issubset(integrated["aliases"]))
        self.assertEqual(len(integrated["cities"]), 27)
        self.assertEqual(len(provinces["세종특별자치시"]["cities"]), 1)

    def test_static_html_head_and_javascript_module_mime(self):
        with self.client.get("/") as response:
            self.assertEqual(response.status_code, 200)
        with self.client.get("/core.mjs") as response:
            self.assertEqual(response.mimetype, "text/javascript")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        with self.client.head("/core.mjs") as response:
            self.assertEqual(response.data, b"")

    def test_static_file_cannot_escape_dist_root(self):
        for path in ["/../app.py", "/%2e%2e/app.py", "/..%5capp.py"]:
            with self.subTest(path=path):
                self.assertIn(self.client.get(path).status_code, (403, 404))

    def test_exact_cors_allowlist_and_preflight(self):
        for origin in ["http://127.0.0.1:4173", "http://localhost:4173", server.HOSTED_ORIGIN]:
            with self.subTest(origin=origin):
                response = self.client.options("/api/check", headers={"Origin": origin})
                self.assertEqual(response.status_code, 204)
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)
                self.assertEqual(response.headers["Access-Control-Allow-Private-Network"], "true")
        for origin in ["https://evil.example", "http://localhost:9000", "https://localhost:4173",
                       "http://127.0.0.1:4173.evil.example", "null"]:
            with self.subTest(origin=origin):
                response = self.client.post("/api/cancel", headers={"Origin": origin})
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_api_not_found_and_wrong_method_return_korean_json(self):
        self.assertEqual(self.client.get("/api/missing").status_code, 404)
        response = self.client.post("/api/health")
        self.assertEqual(response.status_code, 405)
        self.assertIn("error", response.json)

    def test_bad_json_and_oversized_body_are_rejected_before_collecting(self):
        self.assertEqual(self.client.post("/api/check", data="{", content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post("/api/check", data="{}", content_type="text/plain").status_code, 415)
        response = self.client.post("/api/check", data=" " * (server.MAX_BODY_BYTES + 1), content_type="application/json")
        self.assertEqual(response.status_code, 413)
        self.collect.assert_not_called()

    def test_invalid_conditions_are_rejected(self):
        invalid = [None, [], {}, request_body(restaurant=[]), request_body(restaurant={}),
                   request_body(keywords=[]), request_body(keywords=["a"] * 21),
                   request_body(keywords=[""]), request_body(keywords=[True]),
                   request_body(keywords=["가" * 101]), request_body(maxDepth=0),
                   request_body(maxDepth=101), request_body(maxDepth=True), request_body(maxDepth=1.2),
                   request_body(device="both"), request_body(device="tablet"), request_body(device=None),
                   request_body(device=["desktop", "mobile"]), request_body(region=[]), request_body(region="가" * 101)]
        for body in invalid:
            with self.subTest(body=body):
                response = self.client.post("/api/check", json=body) if body is not None else self.client.post(
                    "/api/check", data="null", content_type="application/json")
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.json)
        self.collect.assert_not_called()

    def test_success_preserves_independent_camelcase_ad_and_organic_ranks(self):
        for device in ("desktop", "mobile"):
            with self.subTest(device=device):
                self.collect.reset_mock()
                response = self.client.post("/api/check", json=request_body(
                    keywords=[" 성수 맛집 ", "성수 맛집", "서울숲 맛집"], device=device))
                self.assertEqual(response.status_code, 200)
                rows = response.json["results"]
                self.assertEqual([row["keyword"] for row in rows], ["성수 맛집", "서울숲 맛집"])
                self.assertEqual(rows[0]["organicRank"], 7)
                self.assertEqual(rows[0]["adRank"], 2)
                self.assertEqual(rows[0]["device"], device)
                self.assertNotIn("organic_rank", rows[0])
                self.assertFalse(response.json["cancelled"])
                self.assertEqual(self.collect.call_count, 2)
                self.assertEqual(self.collect.call_args.kwargs["max_depth"], 100)
                self.assertEqual(self.collect.call_args.kwargs["device"], device)
                self.assertEqual(self.collect.call_args.kwargs["region"], "서울 성동구")
                self.assertIsNone(self.collect.call_args.kwargs["location"])
                self.assertIsInstance(self.collect.call_args.kwargs["cancel_event"], threading.Event)

    def test_explicit_location_is_validated_and_forwarded_to_both_device_collectors(self):
        for device in ("desktop", "mobile"):
            with self.subTest(device=device):
                location = {"label": " 서울 성수 ", "latitude": 37.54444, "longitude": 127.05611}
                response = self.client.post("/api/check", json=request_body(device=device, location=location))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.collect.call_args.kwargs["location"],
                                 {"label": "서울 성수", "latitude": 37.54444, "longitude": 127.05611})
                self.assertEqual(self.collect.call_args.kwargs["region"], "서울 성동구")
        response = self.client.post("/api/check", json=request_body(
            location={"latitude": 0, "longitude": 0}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.collect.call_args.kwargs["location"],
                         {"label": "0.000000, 0.000000", "latitude": 0.0, "longitude": 0.0})
        self.assertEqual(self.client.post("/api/check", json=request_body(location=None)).status_code, 200)
        self.assertIsNone(self.collect.call_args.kwargs["location"])

    def test_invalid_or_partial_location_never_starts_collection(self):
        invalid = ["서울", [], {}, {"latitude": 37.5}, {"longitude": 127},
                   {"latitude": True, "longitude": 127}, {"latitude": 37.5, "longitude": False},
                   {"latitude": "37.5", "longitude": 127}, {"latitude": 37.5, "longitude": "127"},
                   {"latitude": 90.01, "longitude": 127}, {"latitude": -90.01, "longitude": 127},
                   {"latitude": 37.5, "longitude": 180.01}, {"latitude": float("nan"), "longitude": 127},
                   {"latitude": float("inf"), "longitude": 127}, {"latitude": 37.5, "longitude": float("-inf")},
                   {"latitude": 10 ** 400, "longitude": 127},
                   {"latitude": 37.5, "longitude": 127, "label": "가" * 101},
                   {"latitude": 37.5, "longitude": 127, "label": None}]
        for location in invalid:
            with self.subTest(location=location):
                response = self.client.post("/api/check", json=request_body(location=location))
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.json)
        self.collect.assert_not_called()

    def test_collector_unavailable_returns_503_and_releases_scan_slot(self):
        self.health.return_value = {"collectorReady": False, "message": "브라우저 설치가 필요합니다."}
        response = self.client.post("/api/check", json=request_body())
        self.assertEqual(response.status_code, 503)
        self.assertIn("브라우저", response.json["error"])
        self.health.return_value = READY
        self.assertEqual(self.client.post("/api/check", json=request_body()).status_code, 200)

    def test_collector_errors_are_error_observations_and_scan_slot_is_released(self):
        self.collect.side_effect = RuntimeError("네이버 접근 제한이 표시되었습니다.")
        for device in ("desktop", "mobile"):
            with self.subTest(device=device):
                location = {"label": "성수역", "latitude": 37.54444, "longitude": 127.05611}
                response = self.client.post("/api/check", json=request_body(device=device, location=location))
                row = response.json["results"][0]
                self.assertEqual(response.status_code, 200)
                self.assertEqual(row["status"], "error")
                self.assertEqual(row["device"], device)
                self.assertEqual(row["region"], "서울 성동구")
                self.assertEqual(row["requestedSearchLocation"], location)
                self.assertFalse(row["locationApplied"])
                self.assertIsNone(row["organicRank"])
                self.assertIsNone(row["adRank"])
                self.assertIn("접근 제한", row["error"])
                self.assertIn("observedAt", row)
                self.assertEqual(self.client.get("/api/health").json["scanActive"], False)
        self.collect.side_effect = measurement
        self.assertEqual(self.client.post("/api/check", json=request_body()).status_code, 200)

    def test_unexpected_health_error_also_releases_scan_slot(self):
        self.health.side_effect = RuntimeError("상태 조회 실패")
        with self.assertLogs(self.app.logger, level="ERROR"):
            response = self.client.post("/api/check", json=request_body())
        self.assertEqual(response.status_code, 500)
        self.assertFalse(self.app.extensions["scan_control"].active)

    def test_concurrent_scan_is_rejected_and_cancel_reaches_only_active_scan(self):
        started = threading.Event()
        responses = []

        def blocking_collector(_restaurant, _keyword, **settings):
            started.set()
            if not settings["cancel_event"].wait(3):
                raise RuntimeError("테스트 중단 요청 시간 초과")
            raise RuntimeError("조회가 취소되었습니다.")

        self.collect.side_effect = blocking_collector

        def run_scan():
            with self.app.test_client() as client:
                responses.append(client.post("/api/check", json=request_body(
                    keywords=["첫 키워드", "둘째 키워드"], device="mobile",
                    location={"label": "성수역", "latitude": 37.54444, "longitude": 127.05611})))

        thread = threading.Thread(target=run_scan)
        thread.start()
        try:
            self.assertTrue(started.wait(2), "수집기 요청이 시작되지 않았습니다.")
            self.assertTrue(self.client.get("/api/health").json["scanActive"])
            self.assertEqual(self.client.post("/api/check", json=request_body()).status_code, 409)
            response = self.client.post("/api/cancel")
            self.assertTrue(response.json["cancelled"])
        finally:
            self.app.extensions["scan_control"].cancel()
            thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertTrue(responses[0].json["cancelled"])
        self.assertEqual(len(responses[0].json["results"]), 1)
        self.assertEqual(responses[0].json["results"][0]["device"], "mobile")
        self.assertEqual(responses[0].json["results"][0]["requestedSearchLocation"],
                         {"label": "성수역", "latitude": 37.54444, "longitude": 127.05611})
        self.assertFalse(responses[0].json["results"][0]["locationApplied"])
        self.assertEqual(self.collect.call_args.kwargs["device"], "mobile")
        self.assertEqual(self.collect.call_count, 1)
        self.assertFalse(self.client.post("/api/cancel").json["cancelled"])
        self.collect.side_effect = measurement
        response = self.client.post("/api/check", json=request_body())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["cancelled"])


if __name__ == "__main__":
    unittest.main()
