"""위치 선택에 사용하는 공개 대표 좌표와 요청 검증.

좌표는 WGS84 위도/경도이며, 각 공공 명소·역 주변을 가리키는 근사 지점입니다.
행정구역 전체의 중심이나 이용자의 실제 GPS 위치를 뜻하지 않습니다.
실행 중 외부 지오코딩 서비스나 위치 추적 API를 호출하지 않습니다.
referenceUrl에는 대표 지점을 확인한 공개 자료를 기록합니다.
"""

from functools import lru_cache
import json
import math
from pathlib import Path


COORDINATE_SYSTEM = "WGS84"
REGIONS_PATH = Path(__file__).resolve().parent / "dist" / "regions.json"
LOCATION_PRESETS = [
    {"id": "seoul-cityhall", "label": "서울 시청", "latitude": 37.566406, "longitude": 126.977822,
     "anchor": "서울시청 본관 주변", "referenceUrl": "https://en.wikipedia.org/wiki/Seoul_City_Hall"},
    {"id": "seoul-seongsu", "label": "서울 성수", "latitude": 37.54444, "longitude": 127.05611,
     "anchor": "성수역 주변", "referenceUrl": "https://en.wikipedia.org/wiki/Seongsu_station"},
    {"id": "seoul-gangnam", "label": "서울 강남", "latitude": 37.497928, "longitude": 127.027583,
     "anchor": "강남역 주변", "referenceUrl": "https://en.wikipedia.org/wiki/Gangnam_station"},
    {"id": "seoul-hongdae", "label": "서울 홍대", "latitude": 37.55667, "longitude": 126.92361,
     "anchor": "홍대입구역 주변", "referenceUrl": "https://en.wikipedia.org/wiki/Hongik_University_station"},
    {"id": "seoul-jamsil", "label": "서울 잠실", "latitude": 37.51333, "longitude": 127.10028,
     "anchor": "잠실역 주변", "referenceUrl": "https://en.wikipedia.org/wiki/Jamsil_station"},
    {"id": "seoul-myeongdong", "label": "서울 명동", "latitude": 37.56094, "longitude": 126.98666,
     "anchor": "명동역 주변", "referenceUrl": "https://en.wikipedia.org/wiki/Myeong-dong_station"},
    {"id": "seoul-itaewon", "label": "서울 이태원", "latitude": 37.5345, "longitude": 126.9944,
     "anchor": "이태원역 주변", "referenceUrl": "https://en.wikipedia.org/wiki/Itaewon_station"},
    {"id": "seoul-yeouido", "label": "서울 여의도", "latitude": 37.52167, "longitude": 126.92417,
     "anchor": "여의도역 주변", "referenceUrl": "https://en.wikipedia.org/wiki/Yeouido_station"},
    {"id": "seoul-gwanghwamun", "label": "서울 광화문", "latitude": 37.57278, "longitude": 126.97667,
     "anchor": "광화문광장 주변", "referenceUrl": "https://en.wikipedia.org/wiki/Gwanghwamun_Square"},
    {"id": "seoul-jongno3ga", "label": "서울 종로3가", "latitude": 37.57, "longitude": 126.99167,
     "anchor": "종로3가역 주변", "referenceUrl": "https://en.wikipedia.org/wiki/Jongno_3-ga_station"},
    {"id": "bundang-pangyo", "label": "성남 판교", "latitude": 37.394762, "longitude": 127.111137,
     "anchor": "판교역 주변", "referenceUrl": "https://en.wikipedia.org/wiki/Pangyo_station"},
    {"id": "busan-haeundae", "label": "부산 해운대", "latitude": 35.1637, "longitude": 129.1589,
     "anchor": "해운대역 주변", "referenceUrl": "https://en.wikipedia.org/wiki/Haeundae_station"},
    {"id": "daegu-dongseongno", "label": "대구 동성로", "latitude": 35.86472, "longitude": 128.59333,
     "anchor": "동성로 남쪽 반월당역 주변", "referenceUrl": "https://en.wikipedia.org/wiki/Banwoldang_station"},
    {"id": "daejeon-cityhall", "label": "대전 시청", "latitude": 36.35051, "longitude": 127.38496,
     "anchor": "대전시청 주변", "referenceUrl": "https://mapcarta.com/W60862444"},
    {"id": "gwangju-cityhall", "label": "광주광역시 시청", "latitude": 35.159611, "longitude": 126.852353,
     "anchor": "광주광역시청 주변", "referenceUrl": "https://www.wikidata.org/wiki/Q12585108"},
    {"id": "jeju-cityhall", "label": "제주 시청", "latitude": 33.499575, "longitude": 126.53105,
     "anchor": "제주시청 주변", "referenceUrl": "https://commons.wikimedia.org/wiki/File:Jeju_City_Hall.JPG"},
]


@lru_cache(maxsize=1)
def get_region_catalog():
    """같이 배포된 전국 지역 목록을 읽습니다. 외부 서버나 API 키는 필요 없습니다."""
    return json.loads(REGIONS_PATH.read_text(encoding="utf-8"))


def validate_location(value):
    """좌표를 명시적으로 선택한 경우만 정리해서 반환합니다. 기본 위치는 None."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("검색 위치는 위도와 경도를 포함한 객체로 입력해 주세요.")
    latitude = value.get("latitude")
    longitude = value.get("longitude")
    for coordinate, limit, name in ((latitude, 90, "위도"), (longitude, 180, "경도")):
        if (type(coordinate) not in (int, float) or not -limit <= coordinate <= limit
                or not math.isfinite(coordinate)):
            raise ValueError(f"검색 위치의 {name}는 {-limit}~{limit} 범위의 유한한 숫자여야 합니다.")
    label = value.get("label", "")
    if not isinstance(label, str) or len(label) > 100:
        raise ValueError("검색 위치 이름은 100자 이하의 문자열로 입력해 주세요.")
    return {"label": label.strip() or f"{latitude:.6f}, {longitude:.6f}",
            "latitude": float(latitude), "longitude": float(longitude)}
