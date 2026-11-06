"""기상청 공개 XLSX로 오프라인 시·도 / 시·군·구 선택 목록을 생성합니다.

기본 실행은 보관된 원본을 읽습니다. 원본을 다시 받을 때만 --download를 사용하세요.
Python 표준 라이브러리만 사용하며, 화면 실행 시에는 이 스크립트를 호출하지 않습니다.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import io
import json
import math
from pathlib import Path
import re
import urllib.request
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
AS_OF = "2026-07-01"
SOURCE_PAGE = "https://apihub.kma.go.kr/apiList.do?seqApi=10"
SOURCE_URL = "https://apihub.kma.go.kr/getAttachFile.do?fileName=%EB%8F%99%EB%84%A4%EC%98%88%EB%B3%B4%EC%A7%80%EC%A0%90%EC%A2%8C%ED%91%9C%28%EC%9C%84%EA%B2%BD%EB%8F%84%29_260701.xlsx"
DEFAULT_SOURCE = ROOT / "data" / "sources" / "kma-region-coordinates-20260701.xlsx"
DEFAULT_OUTPUT = ROOT / "dist" / "regions.json"
XML_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

PROVINCE_ALIASES = {
    "서울특별시": ["서울", "서울시"],
    "전남광주통합특별시": ["광주", "광주광역시", "광주시", "전남", "전라남도", "전남광주", "광주전남"],
    "부산광역시": ["부산", "부산시"],
    "대구광역시": ["대구", "대구시"],
    "인천광역시": ["인천", "인천시"],
    "대전광역시": ["대전", "대전시"],
    "울산광역시": ["울산", "울산시"],
    "세종특별자치시": ["세종", "세종시"],
    "경기도": ["경기"],
    "강원특별자치도": ["강원", "강원도"],
    "충청북도": ["충북"],
    "충청남도": ["충남"],
    "전북특별자치도": ["전북", "전라북도"],
    "경상북도": ["경북"],
    "경상남도": ["경남"],
    "제주특별자치도": ["제주", "제주도"],
}


def read_rows(source: bytes) -> list[dict[str, str]]:
    with zipfile.ZipFile(io.BytesIO(source)) as book:
        strings = []
        if "xl/sharedStrings.xml" in book.namelist():
            shared = ET.fromstring(book.read("xl/sharedStrings.xml"))
            strings = ["".join(node.itertext()) for node in shared.findall("m:si", XML_NS)]
        sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
        rows = []
        for row in sheet.findall("m:sheetData/m:row", XML_NS)[1:]:
            values = {}
            for cell in row.findall("m:c", XML_NS):
                column = re.sub(r"\d", "", cell.attrib["r"])
                value = cell.find("m:v", XML_NS)
                text = value.text if value is not None else ""
                if cell.get("t") == "s":
                    text = strings[int(text)]
                elif cell.get("t") == "inlineStr":
                    text = "".join(cell.find("m:is", XML_NS).itertext())
                values[column] = text or ""
            rows.append(values)
        return rows


def coordinates(row: dict[str, str]) -> dict[str, float]:
    latitude, longitude = float(row["O"]), float(row["N"])
    if not (math.isfinite(latitude) and math.isfinite(longitude)
            and 32 <= latitude <= 39 and 124 <= longitude <= 133):
        raise ValueError(f"행정구역 {row.get('B')} {row.get('C')} {row.get('D')}의 좌표를 확인해 주세요.")
    return {"latitude": round(latitude, 8), "longitude": round(longitude, 8)}


def build_catalog(source: bytes) -> dict:
    rows = read_rows(source)
    # 이어도 같은 해상 예보지점은 행정 시·도 코드가 아니므로 제외합니다.
    provinces = [row for row in rows if row.get("C") and not row.get("D") and not row.get("E")
                 and re.fullmatch(r"\d{2}0{8}", row.get("B", ""))]
    if len(provinces) != 16:
        raise ValueError("2026-07-01 원본의 16개 현행 시·도 행을 확인할 수 없습니다.")
    output = []
    original_city_count = 0
    aggregate_count = 0
    for province in provinces:
        name = province["C"]
        cities = []
        city_groups = defaultdict(list)
        for row in rows:
            if row.get("C") != name or not row.get("D") or row.get("E"):
                continue
            original_city_count += 1
            raw_name = row["D"]
            district = re.fullmatch(r"(.+?시)(.+구)", raw_name)
            display_name = " ".join(district.groups()) if district else raw_name
            city = {"code": row["B"], "name": display_name, **coordinates(row),
                    "anchor": f"기상청 {display_name} 행정 대표점", "aliases": [raw_name]}
            if district:
                city["parentCode"] = row["B"][:4] + "000000"
                city["kind"] = "district"
                city_groups[district.group(1)].append(city)
            else:
                city["kind"] = "city" if raw_name.endswith("시") else "county" if raw_name.endswith("군") else "district"
            if name == "전남광주통합특별시":
                former = "광주" if raw_name in ("동구", "서구", "남구", "북구", "광산구") else "전남"
                city["aliases"] += [f"{former} {display_name}", f"{former}{raw_name}",
                                    f"{'광주광역시' if former == '광주' else '전라남도'} {display_name}"]
            if name == "인천광역시" and raw_name == "서해구":
                city["aliases"] += ["인천 서구", "서구"]
            cities.append(city)
        # 기상청 원본은 일반구가 있는 시의 시 단위 행을 생략합니다.
        # 선택 편의를 위해 구 대표점들의 평균으로 시 단위 근사 중심을 추가합니다.
        for city_name, districts in city_groups.items():
            aggregate_count += 1
            cities.append({
                "code": districts[0]["parentCode"], "name": city_name,
                "latitude": round(math.fsum(row["latitude"] for row in districts) / len(districts), 8),
                "longitude": round(math.fsum(row["longitude"] for row in districts) / len(districts), 8),
                "anchor": f"{city_name} 일반구 {len(districts)}곳의 기상청 대표점 평균 · 근사 중심",
                "aliases": [city_name.removesuffix("시"), f"{city_name} 전체"], "kind": "city", "isAggregate": True,
            })
        cities.sort(key=lambda city: city["code"])
        point = {"code": province["B"], "name": name, "aliases": PROVINCE_ALIASES.get(name, []),
                 **coordinates(province), "anchor": f"기상청 {name} 행정 대표점", "cities": cities}
        if name == "전남광주통합특별시":
            point["displayName"] = "전남광주통합특별시 (광주·전남)"
        output.append(point)
    if original_city_count != 256:
        raise ValueError(f"2026-07-01 원본의 시·군·구 256행과 일치하지 않습니다: {original_city_count}")
    return {
        "version": 1, "asOf": AS_OF, "coordinateSystem": "WGS84",
        "source": {"name": "기상청 동네예보 지점 좌표(위경도)_260701.xlsx", "url": SOURCE_PAGE,
                   "downloadUrl": SOURCE_URL, "sha256": hashlib.sha256(source).hexdigest(),
                   "description": "공개 원본의 1·2단계 행정구역명·행정구역코드·위경도 추출. 일반구가 있는 시는 구 대표점 평균으로 시 단위 선택을 추가."},
        "coverage": {"provinceCount": len(output), "sourceCityCount": original_city_count,
                     "addedCityAggregates": aggregate_count, "selectableCityCount": sum(len(p["cities"]) for p in output)},
        "notes": ["선택한 지역의 대표 지점을 검색 기준으로 사용합니다. 시·도나 시·군·구 영역 전체를 검색한다는 의미는 아닙니다.",
                  "광주·전남, 강원도·전라북도 등 익숙한 명칭도 지역 검색 별칭으로 제공합니다.",
                  "위치는 이용자 GPS를 추적하지 않으며, 구 대표점 평균으로 추가한 시는 근사 중심임을 anchor에 명시합니다."],
        "provinces": output,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--download", action="store_true", help="공식 기상청 원본을 다시 내려받습니다.")
    args = parser.parse_args()
    if args.download:
        with urllib.request.urlopen(SOURCE_URL, timeout=45) as response:
            source = response.read(20 * 1024 * 1024)
        if not source.startswith(b"PK"):
            raise ValueError("기상청 응답이 XLSX 파일이 아닙니다.")
        args.source.parent.mkdir(parents=True, exist_ok=True)
        args.source.write_bytes(source)
    if not args.source.is_file():
        parser.error("원본 파일이 없습니다. 최초 생성 시 --download를 추가하세요.")
    catalog = build_catalog(args.source.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "asOf": catalog["asOf"], **catalog["coverage"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
