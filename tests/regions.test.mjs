import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {searchRegions, getRegionSelection, matchRegionSelection} from '../dist/regions.mjs';

const catalog = {
  version: 1, asOf: '2026-07-01', source: 'test fixture', provinces: [
    {code: '11', name: '서울특별시', aliases: ['서울', '서울시'], latitude: 37.5664, longitude: 126.9778,
      cities: [
        {code: '11680', name: '강남구', latitude: 37.5172, longitude: 127.0473, anchor: '강남구 대표 지점'},
        {code: '11140', name: '중구', latitude: 37.5641, longitude: 126.9979, anchor: '중구 대표 지점'},
      ]},
    {code: '41', name: '경기도', aliases: ['경기'], latitude: 37.2893, longitude: 127.0535,
      cities: [
        {code: '41110', name: '수원시', latitude: 37.2636, longitude: 127.0286, anchor: '수원시 구 대표점 평균 근사 중심'},
        {code: '41111', name: '수원시 장안구', latitude: 37.3039, longitude: 127.0101, anchor: '장안구 대표 지점'},
        {code: '41610', name: '광주시', latitude: 37.4294, longitude: 127.255, anchor: '광주시 대표 지점'},
      ]},
    {code: '44', name: '충청남도', aliases: ['충남'], latitude: 36.6588, longitude: 126.6728,
      cities: [
        {code: '44130', name: '천안시', latitude: 36.8151, longitude: 127.1139, anchor: '천안시 대표 지점'},
        {code: '44131', name: '천안시 동남구', latitude: 36.7852, longitude: 127.1529, anchor: '동남구 대표 지점'},
      ]},
    {code: '46', name: '전남광주통합특별시', aliases: ['광주', '광주광역시', '전남', '전라남도', '광주전남'],
      latitude: 35.1595, longitude: 126.8526, cities: [
        {code: '46110', name: '목포시', latitude: 34.8118, longitude: 126.3922, anchor: '목포시 대표 지점'},
        {code: '46170', name: '나주시', latitude: 35.0159, longitude: 126.7108, anchor: '나주시 대표 지점'},
      ]},
    {code: '28', name: '인천광역시', aliases: ['인천'], latitude: 37.4563, longitude: 126.7052,
      cities: [{code: '28190', name: '중구', latitude: 37.4737, longitude: 126.6215, anchor: '테스트 중구 대표 지점'}]},
  ],
};

test('official and familiar province names find the same city with or without spaces and suffixes', () => {
  for (const query of ['경기도 수원', '경기 수원', '경기도수원시', '수원']) {
    const [first] = searchRegions(catalog, query);
    assert.equal(first.provinceCode, '41', query);
    assert.equal(first.cityCode, '41110', query);
    assert.equal(first.label, '경기도 수원시');
  }
  assert.equal(searchRegions(catalog, '서울강남구')[0].cityCode, '11680');
  assert.equal(searchRegions(catalog, '서울 강남')[0].cityCode, '11680');
  assert.equal(searchRegions(catalog, '충남천안')[0].cityCode, '44130');
  assert.equal(searchRegions(catalog, '충청남도 천안시')[0].cityCode, '44130');
});

test('exact city and district matches precede broader sequential matches and preserve compound district names', () => {
  assert.equal(searchRegions(catalog, '경기 수원')[0].cityCode, '41110');
  assert.equal(searchRegions(catalog, '경기 수원 장안')[0].cityCode, '41111');
  assert.equal(searchRegions(catalog, '경기수원장안구')[0].cityCode, '41111');
  assert.equal(searchRegions(catalog, '수원시 장안구')[0].label, '경기도 수원시 장안구');
  assert.equal(searchRegions(catalog, '천안 동남')[0].cityCode, '44131');
  assert.equal(searchRegions(catalog, '서울 중구')[0].cityCode, '11140');
  assert.equal(searchRegions(catalog, '중구').filter(row => row.cityCode !== null).length, 2);
});

test('integrated province canonical names stay intact while all supplied historical aliases remain searchable', () => {
  for (const query of ['전남광주통합특별시', '광주', '광주광역시', '전남', '전라남도', '광주전남']) {
    const province = searchRegions(catalog, query).find(row => row.provinceCode === '46' && row.cityCode === null);
    assert.equal(province?.label, '전남광주통합특별시', query);
  }
  assert.equal(searchRegions(catalog, '광주 목포')[0].cityCode, '46110');
  assert.equal(searchRegions(catalog, '전라남도 나주')[0].cityCode, '46170');
  assert.equal(searchRegions(catalog, '경기 광주시')[0].cityCode, '41610');
});

test('empty queries return no unsolicited locations and result limits are deterministic', () => {
  for (const query of ['', '  ', null, undefined, '...']) assert.deepEqual(searchRegions(catalog, query), []);
  assert.equal(searchRegions(catalog, '경기', 1).length, 1);
  assert.deepEqual(searchRegions(catalog, '경기', 0), []);
  assert.deepEqual(searchRegions(catalog, '경기'), searchRegions(catalog, '경기'));
  assert.throws(() => searchRegions(catalog, '경기', -1), /정수/);
  assert.throws(() => searchRegions(catalog, '경기', Infinity), /정수/);
  assert.throws(() => searchRegions(catalog, {}), /검색어/);
});

test('markup-like input remains an inert search string and never becomes a location selection', () => {
  const original = JSON.stringify(catalog);
  for (const query of ['<script>alert(1)</script>', '<img src=x onerror=alert(1)>', '__proto__', 'constructor', '서울<img src=x>']) {
    assert.deepEqual(searchRegions(catalog, query), []);
  }
  assert.equal(JSON.stringify(catalog), original);
});

test('selection returns province names without synthetic suffixes and keeps representative anchors', () => {
  const province = getRegionSelection(catalog, '41');
  assert.equal(province.cityCode, null);
  assert.equal(province.label, '경기도');
  assert.equal(province.latitude, 37.2893);
  const city = getRegionSelection(catalog, '41', '41110');
  assert.equal(city.label, '경기도 수원시');
  assert.equal(city.anchor, '수원시 구 대표점 평균 근사 중심');
  city.latitude = 0;
  assert.equal(getRegionSelection(catalog, '41', '41110').latitude, 37.2636);
  assert.throws(() => getRegionSelection(catalog, 'missing'), /찾지 못/);
  assert.throws(() => getRegionSelection(catalog, '11', '41110'), /찾지 못/);
  assert.throws(() => getRegionSelection(catalog, 41), /선택값/);
});

test('matching uses exact rounded coordinates before labels and never changes saved coordinates', () => {
  const existing = {label: '이전에 쓰던 별도 지점명', latitude: 37.2636001, longitude: 127.0286001};
  const before = structuredClone(existing);
  const matched = matchRegionSelection(catalog, existing);
  assert.equal(matched.cityCode, '41110');
  assert.equal(matched.matchType, 'coordinates');
  assert.deepEqual(existing, before);
  assert.notEqual(matched.latitude, existing.latitude);
});

test('matching familiar saved labels suggests their administrative region while retaining original measurement points', () => {
  const existing = {label: '서울 강남', latitude: 37.497928, longitude: 127.027583};
  const matched = matchRegionSelection(catalog, existing);
  assert.equal(matched.provinceCode, '11');
  assert.equal(matched.cityCode, '11680');
  assert.equal(matched.matchType, 'name');
  assert.equal(existing.latitude, 37.497928);
  assert.equal(existing.longitude, 127.027583);
  assert.equal(matchRegionSelection(catalog, {...existing, label: '광주광역시'}).provinceCode, '46');
});

test('nearby matching is conservative and unknown or ambiguous locations do not force a city', () => {
  const nearby = matchRegionSelection(catalog, {label: '저장한 개인 지점', latitude: 37.5173, longitude: 127.0474});
  assert.equal(nearby.cityCode, '11680');
  assert.equal(nearby.matchType, 'nearby');
  assert.equal(matchRegionSelection(catalog, {label: '중구', latitude: 36, longitude: 128}), null);
  assert.equal(matchRegionSelection(catalog, {label: '알 수 없는 지역', latitude: 0, longitude: 0}), null);
  const broad = matchRegionSelection(catalog, {label: '서울 시청', latitude: 37.5665, longitude: 126.978});
  assert.equal(broad.provinceCode, '11');
  assert.equal(broad.cityCode, null);
  assert.equal(matchRegionSelection(catalog, null), null);
  assert.equal(matchRegionSelection(catalog, {latitude: '37.5', longitude: 127}), null);
});

test('broken catalog data reports a clear error instead of silently substituting another point', () => {
  const invalidCatalogs = [null, {}, {provinces: []}, {provinces: [{code: '11', name: '서울', cities: []}]},
    {...catalog, provinces: [{...catalog.provinces[0], latitude: NaN}]},
    {...catalog, provinces: [{...catalog.provinces[0], cities: [{code: '1', name: '강남구', latitude: 37.5, longitude: false}]}]},
    {...catalog, provinces: [{...catalog.provinces[0], aliases: '서울'}]},
  ];
  for (const invalid of invalidCatalogs) {
    assert.throws(() => searchRegions(invalid, '서울'), /지역 목록|regions.json/);
    assert.throws(() => getRegionSelection(invalid, '11'), /지역 목록|regions.json/);
    assert.throws(() => matchRegionSelection(invalid, null), /지역 목록|regions.json/);
  }
});

test('the bundled national catalog supports provincial aliases, aggregate cities and current district names', () => {
  const national = JSON.parse(readFileSync(new URL('../dist/regions.json', import.meta.url), 'utf8'));
  const snapshot = JSON.stringify(national);
  for (const province of national.provinces) {
    assert.equal(getRegionSelection(national, province.code).label, province.name);
    for (const alias of [province.name, ...(province.aliases || [])]) {
      assert.ok(searchRegions(national, alias).some(row => row.provinceCode === province.code && row.cityCode === null), alias);
    }
  }
  for (const [query, expected] of [
    ['경기도 수원', '경기도 수원시'], ['경기 수원', '경기도 수원시'],
    ['서울강남구', '서울특별시 강남구'], ['충남천안', '충청남도 천안시'],
    ['경기수원장안구', '경기도 수원시 장안구'], ['인천검단구', '인천광역시 검단구'],
    ['인천제물포구', '인천광역시 제물포구'],
  ]) assert.equal(searchRegions(national, query)[0]?.label, expected, query);
  const integrated = national.provinces.find(province => province.aliases?.includes('전라남도'));
  assert.ok(integrated);
  for (const query of ['광주광역시', '전라남도', '전남', '광주전남']) {
    assert.ok(searchRegions(national, query).some(row => row.provinceCode === integrated.code && row.cityCode === null), query);
  }
  assert.equal(JSON.stringify(national), snapshot);
});
