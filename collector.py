"""Read public Naver Map result cards and keep advertising/organic ranks separate.

No account cookies, private APIs, generated rankings, or CAPTCHA bypass are used.
The small pure helpers work even when the optional Playwright package is absent.
"""

from __future__ import annotations

from datetime import datetime, timezone
import importlib
import json
import math
import os
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import parse_qs, quote, urlparse


def parse_place_id(value=""):
    """Accept a numeric ID or a full Naver place/map URL, never unrelated URLs."""
    text = str(value or "").strip()
    if re.fullmatch(r"[0-9]+", text):
        return text
    try:
        url = urlparse(text)
        host = (url.hostname or "").lower()
        if url.scheme not in {"http", "https"} or not any(
            host == domain or host.endswith("." + domain)
            for domain in ("naver.com", "naver.me")
        ):
            return None
        match = re.search(r"/(?:restaurant|cafe|place|entry/place)/([0-9]+)(?:/|$)", url.path)
        if match:
            return match.group(1)
        candidate = parse_qs(url.query).get("placeId", [""])[0]
        return candidate if re.fullmatch(r"[0-9]+", candidate) else None
    except (ValueError, TypeError):
        return None


def normalize_place_name(value=""):
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return "".join(
        char for char in text
        if not char.isspace() and unicodedata.category(char)[0] not in {"P", "S"}
    )


def rank_observation(entries, restaurant, max_depth=100):
    """Deduplicate cards in each lane; ads never increase the organic position."""
    target_id = parse_place_id(restaurant.get("placeId")) or parse_place_id(restaurant.get("url"))
    target_name = normalize_place_name(restaurant.get("name"))
    organic_seen, ad_seen = set(), set()
    organic_rank = ad_rank = None
    organic_count = ad_count = 0
    for entry in entries:
        place_id = parse_place_id(entry.get("placeId")) or parse_place_id(entry.get("href"))
        name = normalize_place_name(entry.get("name"))
        if not place_id and not name:
            continue
        key = place_id or "name:" + name
        is_ad = entry.get("isAd") is True
        seen = ad_seen if is_ad else organic_seen
        if key in seen:
            continue
        seen.add(key)
        if is_ad:
            ad_count += 1
        else:
            if organic_count >= max_depth:
                continue
            organic_count += 1
        matches = place_id == target_id if target_id else bool(target_name and name == target_name)
        if matches and is_ad and ad_rank is None:
            ad_rank = ad_count
        if matches and not is_ad and organic_rank is None:
            organic_rank = organic_count
    return {
        "organicRank": organic_rank, "adRank": ad_rank,
        "observedDepth": organic_count, "observedAdCount": ad_count,
        "matchMethod": "place-id" if target_id else "exact-name",
    }


def _playwright_module():
    try:
        return importlib.import_module("playwright.sync_api")
    except ImportError:
        return None


def _external_browsers():
    """Prefer an already installed Edge or Chrome so Windows setup stays small."""
    candidates = []
    if os.name == "nt":
        program = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        program_x86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
        candidates = [
            ("msedge", program_x86 / "Microsoft/Edge/Application/msedge.exe"),
            ("msedge", program / "Microsoft/Edge/Application/msedge.exe"),
            ("chrome", program / "Google/Chrome/Application/chrome.exe"),
            ("chrome", program_x86 / "Google/Chrome/Application/chrome.exe"),
            ("chrome", local / "Google/Chrome/Application/chrome.exe"),
        ]
    elif os.uname().sysname == "Darwin":
        candidates = [
            ("msedge", Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")),
            ("chrome", Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")),
        ]
    else:
        candidates = [
            ("msedge", Path("/usr/bin/microsoft-edge")),
            ("chrome", Path("/usr/bin/google-chrome")),
            ("chrome", Path("/usr/bin/google-chrome-stable")),
        ]
    return [(name, path) for name, path in candidates if path.is_file()]


def collector_health():
    """Return setup information without starting a browser."""
    module = _playwright_module()
    if module is None:
        return {
            "collectorReady": False, "browser": None,
            "message": "수집기 설치가 필요합니다. 프로젝트 폴더에서 python -m pip install -r requirements.txt를 실행하세요.",
        }
    installed = _external_browsers()
    if installed:
        return {
            "collectorReady": True, "browser": installed[0][0],
            "message": "실제 네이버 지도 화면을 읽는 Python 수집기가 준비되었습니다.",
        }
    try:
        with module.sync_playwright() as playwright:
            ready = Path(playwright.chromium.executable_path).is_file()
    except Exception:
        ready = False
    return {
        "collectorReady": ready, "browser": "chromium" if ready else None,
        "message": "실제 네이버 지도 화면을 읽는 Python 수집기가 준비되었습니다." if ready else
            "브라우저 설치가 필요합니다. python -m playwright install chromium을 실행하세요.",
    }


def _launch_browser(playwright):
    options = {"headless": True, "timeout": 20000}
    errors = []
    for _name, executable in _external_browsers():
        try:
            return playwright.chromium.launch(executable_path=str(executable), **options)
        except Exception as error:
            errors.append(str(error))
    if Path(playwright.chromium.executable_path).is_file():
        return playwright.chromium.launch(**options)
    if errors:
        raise RuntimeError("설치된 브라우저를 실행할 수 없습니다. 브라우저 접근 권한을 확인하거나 python -m playwright install chromium을 실행하세요.")
    raise RuntimeError("브라우저가 없습니다. python -m playwright install chromium을 실행하세요.")


_READ_CARDS_JS = r"""() => {
  const rows = [...document.querySelectorAll('#_pcmap_list_scroll_container li, ul > li.UEzoS, ul > li.VLTHu')];
  const cards = rows.length ? rows : [...document.querySelectorAll('a[href*="/restaurant/"], a[href*="/cafe/"], a[href*="/entry/place/"]')].map(a => a.closest('li')).filter(Boolean);
  return [...new Set(cards)].map(row => {
    const anchors = [...row.querySelectorAll('a[href]')];
    const anchor = anchors.find(a => /\/(restaurant|cafe|place|entry\/place)\/\d+/.test(a.getAttribute('href') || ''));
    let id = row.getAttribute('data-id') || row.getAttribute('data-place-id');
    if (!id) {
      const tracked = row.querySelector('[data-nlog-params*="place_id"]');
      try { id = JSON.parse(tracked?.getAttribute('data-nlog-params') || '{}').place_id || null; } catch {}
    }
    const name = row.querySelector('.TYaxT, .YwYLL, .place_bluelink, [data-testid="place-name"]')?.textContent?.trim()
      || anchor?.querySelector('span')?.textContent?.trim() || '';
    const adText = [...row.querySelectorAll('span, em, button, a, i')].some(el => ['광고', 'AD'].includes(el.textContent?.trim()));
    const adLabel = Boolean(row.querySelector('[aria-label="광고"], [title="광고"], img[alt="광고"], svg[aria-label="광고"]'));
    return { name, href: anchor?.href || '', placeId: id, isAd: adText || adLabel };
  }).filter(card => card.name || card.href || card.placeId);
}"""


_READ_MOBILE_CARDS_JS = r"""() => {
  const linkedRows = [...document.querySelectorAll('a[href*="place.naver.com/place/"], a[href*="place.naver.com/restaurant/"], a[href*="place.naver.com/cafe/"]')].map(a => a.closest('li')).filter(Boolean);
  const rows = [...new Set([...document.querySelectorAll('li[class*="_list_item_"]'), ...linkedRows])];
  return rows.map(row => {
    const anchors = [...row.querySelectorAll('a[href]')];
    const anchor = anchors.find(a => /\/(place|restaurant|cafe)\/\d+/.test(a.getAttribute('href') || ''));
    let id = row.getAttribute('data-id') || row.getAttribute('data-place-id') || anchor?.href.match(/\/(?:place|restaurant|cafe)\/(\d+)/)?.[1];
    if (!id) {
      const tracked = row.querySelector('[data-nlog-params*="place_id"]');
      try { id = JSON.parse(tracked?.getAttribute('data-nlog-params') || '{}').place_id || null; } catch {}
    }
    const name = row.querySelector('[class*="_item_name_"], .TYaxT, .YwYLL, [data-testid="place-name"]')?.textContent?.trim() || '';
    const adText = [...row.querySelectorAll('span, em, button, a, i')].some(el => ['광고', 'AD'].includes(el.textContent?.trim()));
    const adLabel = Boolean(row.querySelector('[aria-label="광고"], [title="광고"], img[alt="광고"], svg[aria-label="광고"]'));
    return { name, href: anchor?.href || '', placeId: id || null, isAd: adText || adLabel };
  }).filter(card => card.name || card.placeId);
}"""


def read_cards(frame, device="desktop"):
    return frame.evaluate(_READ_MOBILE_CARDS_JS if device == "mobile" else _READ_CARDS_JS)


def normalize_search_location(location):
    if location is None:
        return None
    if not isinstance(location, dict):
        raise ValueError("검색 지역은 지역명과 위도·경도 좌표로 입력하세요.")
    label = location.get("label", "")
    if not isinstance(label, str) or not label.strip() or len(label) > 100:
        raise ValueError("검색 지역 이름은 1~100자로 입력하세요.")
    coordinates = {}
    for name, limit in (("latitude", 90), ("longitude", 180)):
        value = location.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not -limit <= value <= limit:
            raise ValueError("검색 지역의 위도·경도 좌표가 올바르지 않습니다.")
        coordinates[name] = float(value)
    return {"label": label.strip(), **coordinates}


def _context_options(playwright, device, location=None):
    options = {"locale": "ko-KR", "timezone_id": "Asia/Seoul"}
    if device == "mobile":
        # Playwright's standard device descriptor changes UA, touch support and
        # viewport together. This is mobile web emulation, not the Naver app.
        options.update(playwright.devices["Pixel 7"])
    else:
        options["viewport"] = {"width": 1440, "height": 1000}
    if location is not None:
        options["geolocation"] = {"latitude": location["latitude"], "longitude": location["longitude"], "accuracy": 100}
    # Permissions are granted separately to the selected Naver origin only.
    return options


def _search_url(keyword, device, location=None):
    encoded = quote(keyword.strip(), safe="")
    if device == "mobile":
        return "https://m.map.naver.com/search?query=" + encoded
    url = "https://map.naver.com/p/search/" + encoded
    if location is not None:
        latitude = location["latitude"]
        if abs(latitude) > 85.05112878:
            raise ValueError("PC 지도 기준점은 위도 -85.0511~85.0511 범위에서 설정할 수 있습니다.")
        x = 6378137 * math.radians(location["longitude"])
        y = 6378137 * math.log(math.tan(math.pi / 4 + math.radians(latitude) / 2))
        # This is the public map-centre URL used by Naver's normal web UI.
        url += f"?c={x:.4f},{y:.4f},15,0,0,0,dh"
    return url


def _coordinate_matches(longitude, latitude, location):
    try:
        lon, lat = float(longitude), float(latitude)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(lon) and math.isfinite(lat) and abs(lon - location["longitude"]) <= 0.0005 and abs(lat - location["latitude"]) <= 0.0005


def _desktop_coordinate_evidence(url, location):
    """Inspect requests made by Naver's UI; never issue an internal API call."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    method = None
    pair = None
    if parsed.hostname == "map.naver.com" and parsed.path == "/p/api/search/query-analyze":
        pair = query.get("coords", [""])[0].split(";")
        method = "naver-query-analyze-coords"
    elif parsed.hostname in {"pcmap.place.naver.com", "m.place.naver.com"} and parsed.path.endswith("/list"):
        pair = [query.get("x", [None])[0], query.get("y", [None])[0]]
        method = "naver-result-frame-xy"
    if not pair or len(pair) != 2 or not _coordinate_matches(pair[0], pair[1], location):
        return None
    return {"method": "desktop-map-center", "evidence": method,
            "longitude": float(pair[0]), "latitude": float(pair[1]),
            "description": "네이버 검색 요청의 지도 기준좌표를 확인했습니다. 접속 IP 기준 위치와 광고 개인화는 별도로 적용될 수 있습니다."}


_READ_PUBLIC_MOBILE_LOCATION_JS = r"""() => {
  // Parse public server-rendered HTML data, without evaluating page scripts.
  for (const script of document.scripts) {
    if (script.src || !script.textContent.includes('__RQ_STREAMING_STATE__.push(')) continue;
    const match = script.textContent.match(/__RQ_STREAMING_STATE__\.push\((\{.*\})\);/s);
    if (!match) continue;
    try {
      for (const query of JSON.parse(match[1]).queries || []) {
        if (!(query.queryKey || []).includes('getMyLocation')) continue;
        const item = query.state?.data?.item;
        if (item?.hasMyLocation && item.myLocation) return item.myLocation;
      }
    } catch {}
  }
  return null;
}"""


def _parse_mobile_location_ack(text, location):
    match = re.fullmatch(r"\s*[\w$.]+\((\{.*\})\);?\s*", text, flags=re.S)
    if not match:
        return False
    try:
        result = json.loads(match.group(1)).get("result", {})
        return result.get("actionResult") == "SUCCESS" and any(
            _coordinate_matches(item.get("xpos"), item.get("ypos"), location)
            for item in result.get("locationList", []) if isinstance(item, dict)
        )
    except (ValueError, TypeError, AttributeError):
        return False


def _apply_mobile_location(page, location, cancel_event, deadline):
    """Use Naver's normal My Location button, then verify its reloaded page."""
    _check_cancel(cancel_event, deadline)
    _ensure_not_blocked(page, page.main_frame)
    def is_setting_response(response):
        url = urlparse(response.url)
        query = parse_qs(url.query)
        return url.hostname == "mloc.map.naver.com" and url.path == "/service/location/setLocationInfoAjax.nhn" and _coordinate_matches(query.get("lng", [None])[0], query.get("lat", [None])[0], location)
    try:
        with page.expect_response(is_setting_response, timeout=15000) as setting:
            page.get_by_role("button", name="내위치", exact=True).click(timeout=12000)
        response = setting.value
        if response.status != 200:
            raise RuntimeError("네이버가 선택한 검색 지역의 위치설정을 확인하지 않았습니다.")
        # Naver immediately navigates after its JSONP callback. Chromium can
        # discard that previous document's response body before Playwright reads
        # it. The reloaded page's own coordinates remain mandatory evidence.
        try:
            ack_text = response.text()
        except Exception:
            ack_text = None
        if ack_text is not None and not _parse_mobile_location_ack(ack_text, location):
            raise RuntimeError("네이버가 선택한 검색 지역의 위치설정을 확인하지 않았습니다.")
        check = "expected => { const point = (" + _READ_PUBLIC_MOBILE_LOCATION_JS + ")(); return point && Math.abs(point.latitude-expected.latitude)<=0.0005 && Math.abs(point.longitude-expected.longitude)<=0.0005; }"
        page.wait_for_function(check, arg=location, timeout=18000)
        actual = page.evaluate(_READ_PUBLIC_MOBILE_LOCATION_JS)
        if not actual or not _coordinate_matches(actual.get("longitude"), actual.get("latitude"), location):
            raise RuntimeError("네이버 재검색 페이지의 지역 좌표를 확인하지 못했습니다.")
        _check_cancel(cancel_event, deadline)
        return {"method": "mobile-my-location-ui", "evidence": "naver-location-request-and-reloaded-page-coordinates",
                "latitude": actual["latitude"], "longitude": actual["longitude"],
                "settingResponse": "action-success" if ack_text is not None else "http-200",
                "description": "네이버 내위치 요청의 선택좌표와 재검색 페이지의 위치좌표를 함께 확인했습니다. 브라우저에만 적용되며 실제 기기 위치를 바꾸지 않습니다."}
    except Exception as error:
        _ensure_not_blocked(page, page.main_frame)
        if isinstance(error, RuntimeError):
            raise
        raise RuntimeError("선택한 검색 지역이 네이버 모바일 검색에 반영됐는지 확인하지 못해 조회를 중단했습니다.") from error


def _ensure_not_blocked(page, frame):
    try:
        body = frame.locator("body").inner_text(timeout=5000)
    except Exception:
        body = ""
    if re.search(r"보안\s*확인|자동입력\s*방지|비정상적인\s*접근|접근이\s*제한|서비스\s*이용이\s*제한|captcha|access denied", body, re.I):
        raise RuntimeError("네이버에서 접근 제한 또는 보안 확인을 표시했습니다. 우회하지 않고 조회를 중단했습니다.")
    if page.is_closed():
        raise RuntimeError("네이버 조회 브라우저가 종료되었습니다.")


def _check_cancel(cancel_event, deadline):
    if (cancel_event is not None and cancel_event.is_set()) or time.monotonic() >= deadline:
        raise RuntimeError("조회 시간이 초과되었거나 취소되었습니다.")


def _observed_at():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def collect_keyword(restaurant, keyword, max_depth=100, region="", device="desktop", cancel_event=None, location=None):
    """Observe one keyword on the public PC/mobile web map; failures raise RuntimeError.

    Each caller owns its synchronous Playwright instance. A Flask worker thread
    may call this directly; cancel_event is a threading.Event polled between
    browser operations. Browser operations also have their own finite timeouts.
    """
    if not isinstance(restaurant, dict):
        raise ValueError("식당 정보를 입력하세요.")
    if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 100:
        raise ValueError("키워드는 1~100자로 입력하세요.")
    if type(max_depth) is not int or not 1 <= max_depth <= 100:
        raise ValueError("조회 깊이는 1~100위 범위여야 합니다.")
    if device not in {"desktop", "mobile"}:
        raise ValueError("기기는 desktop 또는 mobile 중에서 선택하세요.")
    location = normalize_search_location(location)
    target_id = parse_place_id(restaurant.get("placeId")) or parse_place_id(restaurant.get("url"))
    if not target_id and not normalize_place_name(restaurant.get("name")):
        raise ValueError("식당명 또는 네이버 플레이스 ID를 입력하세요.")
    module = _playwright_module()
    if module is None:
        raise RuntimeError("수집기가 설치되지 않았습니다. python -m pip install -r requirements.txt를 실행해 주세요.")

    deadline = time.monotonic() + 85
    _check_cancel(cancel_event, deadline)
    with module.sync_playwright() as playwright:
        browser = None
        try:
            browser = _launch_browser(playwright)
            _check_cancel(cancel_event, deadline)
            context = browser.new_context(**_context_options(playwright, device, location))
            if location is not None:
                context.grant_permissions(["geolocation"], origin="https://m.map.naver.com" if device == "mobile" else "https://map.naver.com")
            page = context.new_page()
            page.set_default_timeout(12000)
            coordinate_evidence = []
            if location is not None and device == "desktop":
                def observe_request(request):
                    evidence = _desktop_coordinate_evidence(request.url, location)
                    if evidence is not None:
                        coordinate_evidence.append(evidence)
                page.on("request", observe_request)
            search_url = _search_url(keyword, device, location)
            page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
            _check_cancel(cancel_event, deadline)
            _ensure_not_blocked(page, page.main_frame)
            location_verification = None
            if location is not None and device == "mobile":
                location_verification = _apply_mobile_location(page, location, cancel_event, deadline)
            if device == "mobile":
                frame = page.main_frame
                try:
                    frame.locator('li[class*="_list_item_"], li a[href*="place.naver.com/place/"]').first.wait_for(state="attached", timeout=18000)
                except module.TimeoutError:
                    _ensure_not_blocked(page, frame)
            else:
                try:
                    iframe = page.wait_for_selector("#searchIframe", timeout=18000)
                except module.TimeoutError:
                    iframe = None
                if iframe is None:
                    _ensure_not_blocked(page, page.main_frame)
                    raise RuntimeError("네이버 검색 결과 프레임을 읽을 수 없습니다. 접근 제한 또는 화면 구조 변경일 수 있습니다.")
                frame = iframe.content_frame()
                if frame is None:
                    raise RuntimeError("네이버 검색 결과가 준비되지 않았습니다.")
            try:
                frame.locator("li").first.wait_for(state="attached", timeout=12000)
            except module.TimeoutError:
                pass
            _check_cancel(cancel_event, deadline)
            _ensure_not_blocked(page, frame)
            if location is not None and device == "desktop":
                iframe_source = page.locator("#searchIframe").get_attribute("src") or ""
                frame_evidence = _desktop_coordinate_evidence(iframe_source, location)
                if frame_evidence is not None:
                    coordinate_evidence.append(frame_evidence)
                if not coordinate_evidence:
                    raise RuntimeError("선택한 검색 지역이 네이버 PC 검색 기준좌표에 반영됐는지 확인하지 못해 조회를 중단했습니다.")
                location_verification = dict(coordinate_evidence[-1])
                frame_query = parse_qs(urlparse(iframe_source).query)
                try:
                    result_longitude = float(frame_query.get("x", [""])[0])
                    result_latitude = float(frame_query.get("y", [""])[0])
                    if math.isfinite(result_longitude) and math.isfinite(result_latitude):
                        location_verification["resultCenter"] = {"latitude": result_latitude, "longitude": result_longitude}
                        location_verification["resultCenterMatches"] = _coordinate_matches(result_longitude, result_latitude, location)
                except (ValueError, TypeError, OverflowError):
                    pass
                location_verification["clientLocationSource"] = "naver-default"

            entries = []
            previous_fingerprint = ""
            unchanged = 0
            page_number = 1
            scan_deadline = min(deadline, time.monotonic() + 55)
            for _iteration in range(40):
                _check_cancel(cancel_event, deadline)
                if time.monotonic() >= scan_deadline:
                    break
                _ensure_not_blocked(page, frame)
                cards = read_cards(frame, device)
                entries.extend(cards)
                ranks = rank_observation(entries, restaurant, max_depth)
                if ranks["observedDepth"] >= max_depth:
                    break
                fingerprint = "|".join(f"{card.get('placeId') or card.get('href')}:{card.get('isAd')}" for card in cards)
                unchanged = unchanged + 1 if fingerprint == previous_fingerprint else 0
                previous_fingerprint = fingerprint
                if unchanged >= 2:
                    next_page = frame.get_by_role("button", name=re.compile(r"^다음\s*페이지$")).first
                    if page_number >= 5 or not next_page.count():
                        break
                    if next_page.get_attribute("aria-disabled") == "true":
                        break
                    next_page.click()
                    page_number += 1
                    unchanged = 0
                else:
                    frame.evaluate("""() => {
                      const container = document.querySelector('#_pcmap_list_scroll_container');
                      if (container) container.scrollTop = container.scrollHeight;
                      else window.scrollTo(0, document.body.scrollHeight);
                    }""")
                page.wait_for_timeout(900)

            _check_cancel(cancel_event, deadline)
            base = {
                "keyword": keyword.strip(), "placeId": target_id,
                "observedAt": _observed_at(), "device": device, "region": str(region),
                "clientMode": "mobile-web-emulation" if device == "mobile" else "desktop-web",
                "locationMode": "custom-geolocation" if location is not None else "naver-default",
                "locationApplied": location is not None and location_verification is not None,
                "searchLocation": location, "requestedSearchLocation": location,
                "locationVerification": location_verification, "sourceUrl": page.url,
                "requestedDepth": max_depth, "source": "naver-map", "status": "success",
            }
            if not entries:
                text = frame.locator("body").inner_text()
                if re.search(r"검색\s*결과가\s*없|검색결과가\s*없|찾을\s*수\s*없습니다", text):
                    return {**base, **rank_observation([], restaurant, max_depth), "maxDepth": 0, "note": "검색 결과 없음"}
                raise RuntimeError("순위를 계산할 수 있는 식당 카드를 찾지 못했습니다. 네이버 화면 구조가 변경되었을 수 있습니다.")
            if target_id and not any(parse_place_id(entry.get("placeId")) or parse_place_id(entry.get("href")) for entry in entries):
                raise RuntimeError("식당 카드는 발견했지만 플레이스 ID를 읽지 못했습니다. 네이버 화면 구조가 변경되었을 수 있습니다.")
            ranks = rank_observation(entries, restaurant, max_depth)
            depth = ranks["observedDepth"]
            surface = "모바일 웹 에뮬레이션" if device == "mobile" else "PC 웹"
            if location is None:
                position_note = "GPS 좌표와 위치 권한을 설정하지 않았으며 입력한 지역 메모는 기록용입니다."
            else:
                position_note = f"선택 지역 '{location['label']}'의 검색 기준좌표 반영을 확인했습니다. PC는 지도 중심점, 모바일은 브라우저 내위치 기준이며 실제 기기 위치를 바꾸지 않습니다. 키워드의 지역 해석·접속 IP·광고 개인화에 따라 결과가 달라질 수 있습니다."
            return {
                **base, **ranks, "maxDepth": depth,
                "note": f"조회 당시 {surface} 화면에서 일반 결과 {depth}개를 확인했습니다. 미노출은 이 범위에만 해당합니다. {position_note} 실제 네이버 앱의 순위와 다를 수 있습니다.",
            }
        except Exception as error:
            _check_cancel(cancel_event, deadline)
            message = str(error)
            if re.search(r"ERR_NETWORK_ACCESS_DENIED|ERR_INTERNET_DISCONNECTED|ERR_NAME_NOT_RESOLVED|ERR_CONNECTION", message, re.I):
                raise RuntimeError("네이버 지도에 연결할 수 없습니다. 인터넷 연결과 방화벽의 브라우저 접근 권한을 확인하세요.") from error
            if isinstance(error, module.TimeoutError) or re.search(r"Timeout.*exceeded|timed out", message, re.I):
                raise RuntimeError("네이버 지도 응답 시간이 초과되었습니다. 잠시 후 다시 조회해 주세요.") from error
            if isinstance(error, (RuntimeError, ValueError)):
                raise
            raise RuntimeError("네이버 지도 조회에 실패했습니다. 브라우저 상태와 인터넷 연결을 확인해 주세요.") from error
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
