"""Place Rank Studio의 화면과 로컬 순위 조회 API를 제공하는 Flask 서버.

실행: python app.py
접속: http://127.0.0.1:4173
실제 브라우저 관측은 collector.py가 담당합니다.
"""

from datetime import datetime, timezone
import mimetypes
import os
from pathlib import Path
import threading

from flask import Flask, jsonify, request, send_file
from werkzeug.exceptions import BadRequest, HTTPException, RequestEntityTooLarge

from collector import collector_health, collect_keyword, parse_place_id
from locations import COORDINATE_SYSTEM, LOCATION_PRESETS, get_region_catalog, validate_location


PROJECT_ROOT = Path(__file__).resolve().parent
HOSTED_ORIGIN = "https://place-rank-studio-1106.klassicdev.chatgpt.site"
MAX_BODY_BYTES = 64 * 1024


class ScanControl:
    """동시에 한 요청만 관측하고, 중단 요청을 해당 관측에 전달합니다."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancel_event = None

    def start(self):
        with self._lock:
            if self._cancel_event is not None:
                return None
            self._cancel_event = threading.Event()
            return self._cancel_event

    def finish(self, cancel_event):
        with self._lock:
            if self._cancel_event is cancel_event:
                self._cancel_event = None

    def cancel(self):
        with self._lock:
            if self._cancel_event is None:
                return False
            self._cancel_event.set()
            return True

    @property
    def active(self):
        with self._lock:
            return self._cancel_event is not None


def validate_request(body):
    """화면에서 받은 값을 검증하고 수집기에 전달할 형태로 정리합니다."""
    if not isinstance(body, dict):
        raise ValueError("조회 조건을 JSON 객체로 입력해 주세요.")
    restaurant = body.get("restaurant")
    if not isinstance(restaurant, dict):
        raise ValueError("식당 정보를 입력해 주세요.")

    name = restaurant.get("name", "")
    url = restaurant.get("url", "")
    place_value = restaurant.get("placeId", "")
    if not isinstance(name, str) or not isinstance(url, str):
        raise ValueError("식당명과 네이버 지도 주소는 문자열로 입력해 주세요.")
    if not isinstance(place_value, (str, int)) or isinstance(place_value, bool):
        raise ValueError("네이버 플레이스 ID를 확인해 주세요.")
    if len(name) > 150 or len(url) > 2048 or len(str(place_value)) > 2048:
        raise ValueError("식당 정보가 너무 깁니다.")
    place_id = parse_place_id(place_value) or parse_place_id(url)
    if not name.strip() and not place_id:
        raise ValueError("식당명 또는 올바른 네이버 플레이스 ID가 필요합니다.")

    keywords = body.get("keywords")
    if not isinstance(keywords, list) or not 1 <= len(keywords) <= 20:
        raise ValueError("한 요청에 키워드 1~20개를 입력해 주세요.")
    if any(not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 100
           for keyword in keywords):
        raise ValueError("키워드는 1~100자의 문자열이어야 합니다.")

    max_depth = body.get("maxDepth", 100)
    if type(max_depth) is not int or not 1 <= max_depth <= 100:
        raise ValueError("조회 범위는 1~100위의 정수로 입력해 주세요.")
    device = body.get("device", "desktop")
    if not isinstance(device, str) or device not in ("desktop", "mobile"):
        raise ValueError("조회 환경은 desktop(PC) 또는 mobile(모바일) 중 하나로 지정해 주세요.")
    region = body.get("region", "")
    if not isinstance(region, str) or len(region) > 100:
        raise ValueError("측정 지역 기록은 100자 이하의 문자열로 입력해 주세요.")
    location = validate_location(body.get("location"))

    return {
        "restaurant": {"name": name.strip(), "url": url, "placeId": place_id or ""},
        "keywords": list(dict.fromkeys(keyword.strip() for keyword in keywords)),
        "max_depth": max_depth,
        "region": region.strip(),
        "device": device,
        "location": location,
    }


def error_observation(keyword, conditions, message):
    """조회 실패를 미노출(null 순위의 성공)과 구별해 기록합니다."""
    return {
        "keyword": keyword,
        "placeId": conditions["restaurant"]["placeId"],
        "organicRank": None,
        "adRank": None,
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "device": conditions["device"],
        "region": conditions["region"],
        "maxDepth": conditions["max_depth"],
        "source": "naver-map",
        "status": "error",
        "error": message,
        "requestedSearchLocation": conditions["location"],
        "locationApplied": False,
    }


def create_app(test_config=None):
    app = Flask(__name__, static_folder=None)
    app.json.ensure_ascii = False
    app.config.update(
        MAX_CONTENT_LENGTH=MAX_BODY_BYTES,
        PORT=int(os.environ.get("PORT", "4173")),
        DIST_ROOT=PROJECT_ROOT / "dist",
    )
    if test_config:
        app.config.update(test_config)

    port = app.config["PORT"]
    allowed_origins = {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        f"http://[::1]:{port}",
        HOSTED_ORIGIN,
    }
    scan_control = ScanControl()
    app.extensions["scan_control"] = scan_control

    @app.before_request
    def check_origin_and_size():
        origin = request.headers.get("Origin")
        if origin is not None and origin not in allowed_origins:
            return jsonify(error="허용되지 않은 사이트의 요청입니다. 로컬 화면에서 다시 시도해 주세요."), 403
        if request.content_length is not None and request.content_length > app.config["MAX_CONTENT_LENGTH"]:
            raise RequestEntityTooLarge()
        if request.method == "OPTIONS":
            return "", 204

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        origin = request.headers.get("Origin")
        if origin in allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers.add("Vary", "Origin")
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def body_too_large(_error):
        return jsonify(error="요청이 너무 큽니다. 한 요청은 64KB 이하로 보내 주세요."), 413

    @app.errorhandler(HTTPException)
    def http_error(error):
        messages = {
            400: "올바른 요청을 보내 주세요.",
            404: "요청한 파일 또는 API 경로를 찾을 수 없습니다.",
            405: "이 경로에서 지원하지 않는 요청 방식입니다.",
        }
        return jsonify(error=messages.get(error.code, "요청을 처리할 수 없습니다.")), error.code

    @app.get("/api/health")
    def health():
        try:
            details = collector_health()
        except Exception:
            app.logger.exception("수집기 상태 확인 실패")
            details = {"collectorReady": False, "message": "수집기 상태를 확인하지 못했어요. 프로그램을 다시 실행해 주세요."}
        return jsonify({**details, "ok": True, "service": "place-rank-python", "runtime": "python",
                        "scanActive": scan_control.active, "supportedDevices": ["desktop", "mobile"], "supportsSearchLocation": True})

    @app.get("/api/locations")
    def locations():
        return jsonify(coordinateSystem=COORDINATE_SYSTEM, locations=LOCATION_PRESETS,
                       message="시청·역·공공 명소 주변의 대표 근사 좌표입니다. 이용자의 실제 위치를 추적하지 않습니다.")

    @app.get("/api/regions")
    def regions():
        try:
            return jsonify(get_region_catalog())
        except (OSError, ValueError):
            return jsonify(error="전국 지역 목록 파일을 읽을 수 없습니다. 배포 폴더의 regions.json을 확인해 주세요."), 503

    @app.post("/api/check")
    def check_keywords():
        if not request.is_json:
            return jsonify(error="Content-Type을 application/json으로 설정해 주세요."), 415
        try:
            conditions = validate_request(request.get_json())
        except BadRequest:
            return jsonify(error="올바른 JSON 요청이 필요합니다."), 400
        except ValueError as error:
            return jsonify(error=str(error)), 400

        cancel_event = scan_control.start()
        if cancel_event is None:
            return jsonify(error="이미 조회 중입니다. 현재 조회를 마치거나 중단한 후 다시 시도해 주세요."), 409
        try:
            details = collector_health()
            if not details.get("collectorReady"):
                return jsonify(error=details.get("message") or "조회용 브라우저를 먼저 설치해 주세요.",
                               collectorReady=False), 503

            results = []
            for keyword in conditions["keywords"]:
                if cancel_event.is_set():
                    break
                try:
                    result = collect_keyword(
                        conditions["restaurant"], keyword,
                        max_depth=conditions["max_depth"], region=conditions["region"],
                        device=conditions["device"], cancel_event=cancel_event, location=conditions["location"],
                    )
                except Exception as error:
                    result = error_observation(keyword, conditions, str(error) or "조회 중 오류가 발생했습니다.")
                results.append(result)
            return jsonify(results=results, cancelled=cancel_event.is_set())
        except Exception:
            app.logger.exception("순위 조회 요청 처리 실패")
            return jsonify(error="수집기 실행 중 오류가 발생했어요. 프로그램을 다시 실행해 주세요."), 500
        finally:
            scan_control.finish(cancel_event)

    @app.post("/api/cancel")
    def cancel_scan():
        cancelled = scan_control.cancel()
        return jsonify(ok=True, cancelled=cancelled,
                       message="조회 중단을 요청했습니다." if cancelled else "현재 진행 중인 조회가 없습니다.")

    @app.route("/", defaults={"filename": "index.html"}, methods=["GET", "HEAD"])
    @app.route("/<path:filename>", methods=["GET", "HEAD"])
    def static_file(filename):
        if filename.startswith("api/"):
            return jsonify(error="API 경로를 찾을 수 없습니다."), 404
        try:
            root = Path(app.config["DIST_ROOT"]).resolve()
            file_path = (root / filename).resolve()
            if not file_path.is_relative_to(root):
                return jsonify(error="허용되지 않은 파일 경로입니다."), 403
            if not file_path.is_file():
                return jsonify(error="파일을 찾을 수 없습니다."), 404
            content_type = "text/javascript" if file_path.suffix in (".js", ".mjs") else mimetypes.guess_type(file_path)[0]
            response = send_file(file_path, mimetype=content_type or "application/octet-stream", conditional=True)
            response.headers["Cache-Control"] = "no-cache"
            return response
        except (OSError, ValueError):
            return jsonify(error="파일 경로를 확인해 주세요."), 400

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=app.config["PORT"], threaded=True, use_reloader=False)
