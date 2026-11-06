import test from 'node:test';
import assert from 'node:assert/strict';
import {
  parseKeywords, parsePlaceId, stats, csvEncode, csvParse,
  parseImport, sampleState, historySeries, normalizeDeviceState,
  syncDeviceRows, activateDevice, allDeviceRows,
  normalizeSearchLocation, searchLocationKey, normalizeLocationState,
  stashCurrentLocation, changeSearchLocation, removeKeywords,
} from '../dist/core.mjs';

const at = '2026-11-06T14:30:00+09:00';
const observed = (organicRank, adRank = null, extra = {}) => ({
  keyword: '성수 파스타', organicRank, adRank, status: 'success',
  observedAt: at, maxDepth: 100, source: 'manual', ...extra,
});
const toImport = rows => JSON.stringify(rows);
const headers = ['keyword', 'organicRank', 'adRank', 'observedAt', 'placeId',
  'restaurant', 'device', 'region', 'maxDepth', 'source', 'status', 'error',
  'clientMode', 'locationMode', 'searchLatitude', 'searchLongitude',
  'searchLocationLabel', 'locationApplied', 'locationVerification'];
const exportRows = rows => csvEncode([headers, ...rows.map(row => headers.map(key => row[key]))]);

test('bulk input keeps keyword order, trims separators, and removes duplicate keywords', () => {
  assert.deepEqual(parseKeywords(' 성수 맛집\r\n성수 파스타,성수 맛집;서울숲 맛집\t\n'),
    ['성수 맛집', '성수 파스타', '서울숲 맛집']);
  assert.deepEqual(parseKeywords(' ,;\n\t '), []);
});

test('place identification accepts Naver detail formats and rejects deceptive hostnames', () => {
  assert.equal(parsePlaceId('123456789'), '123456789');
  assert.equal(parsePlaceId('https://map.naver.com/p/entry/place/123456789'), '123456789');
  assert.equal(parsePlaceId('https://m.place.naver.com/restaurant/123456789/home'), '123456789');
  assert.equal(parsePlaceId('https://map.naver.com/p/search/test?placeId=123456789'), '123456789');
  assert.equal(parsePlaceId('https://evilnaver.com/place/123456789'), '');
  assert.equal(parsePlaceId('https://naver.com.evil.example/place/123456789'), '');
  assert.equal(parsePlaceId('https://naver.me/short-url'), '');
});

test('averages exclude unranked, pending and error rows; TOP10 uses all successful observations', () => {
  const result = stats([
    observed(1, 2), observed(10), observed(11, 1), observed(null, 3),
    observed(null, null, {status: 'pending'}), observed(null, null, {status: 'error'}),
  ]);
  assert.equal(result.total, 6);
  assert.equal(result.observed, 4);
  assert.equal(result.organic, 3);
  assert.equal(result.average, 22 / 3);
  assert.equal(result.adAverage, 2);
  assert.equal(result.ads, 3);
  assert.equal(result.top10, 2);
  assert.equal(result.top10Rate, 50);
  assert.deepEqual(result.distribution, [1, 1, 1, 1, 2]);
  assert.equal(result.distribution.reduce((sum, n) => sum + n, 0), result.total);
});

test('empty and entirely unranked measurements never become rank zero', () => {
  const empty = stats([]);
  assert.equal(empty.average, null);
  assert.equal(empty.adAverage, null);
  assert.equal(empty.top10Rate, null);
  const unranked = stats([observed(null), observed(null)]);
  assert.equal(unranked.average, null);
  assert.equal(unranked.adAverage, null);
  assert.equal(unranked.top10Rate, 0);
  assert.deepEqual(unranked.distribution, [0, 0, 0, 2, 0]);
});

test('CSV handles Korean, commas, quotes, CRLF and multiline values without losing fields', () => {
  const rows = [['키워드', '메모'], ['성수, 파스타', '"분위기 좋은" 식당\n예약 가능'], ['서울숲 맛집', '']];
  const encoded = csvEncode(rows);
  assert.ok(encoded.startsWith('\uFEFF'));
  assert.deepEqual(csvParse(encoded), rows);
});

test('CSV export neutralizes spreadsheet formulas', () => {
  const values = ['=1+1', '+SUM(1,2)', '-2+3', '@SUM(1)', '\t=1'];
  assert.deepEqual(csvParse(csvEncode([values])), [values.map(value => "'" + value)]);
});

test('malformed CSV with an unclosed quote is rejected', () => {
  assert.throws(() => csvParse('keyword,organicRank\n"성수 맛집,3'), /따옴표/);
});

test('measurement CSV roundtrip preserves rank nulls, identity and environment', () => {
  const original = observed(null, 2, {placeId: '123456789', restaurant: '성수 테스트',
    device: 'mobile', region: '서울 성동구', maxDepth: 200});
  const [row] = parseImport(exportRows([original]), 'measurement.csv');
  assert.equal(row.organicRank, null);
  assert.equal(row.adRank, 2);
  assert.equal(row.placeId, '123456789');
  assert.equal(row.restaurant, '성수 테스트');
  assert.equal(row.device, 'mobile');
  assert.equal(row.region, '서울 성동구');
  assert.equal(row.maxDepth, 200);
  assert.equal(row.observedAt, at);
  assert.equal(row.source, 'import');
  assert.equal(row.originalSource, 'manual');
});

test('CSV accepts Korean headers and distinguishes unranked measurements', () => {
  const csv = csvEncode([
    ['키워드', '일반 순위', '광고 순위', '측정 시각'],
    ['성수 맛집', '미노출', '', at],
  ]);
  const [row] = parseImport(csv, 'measurement.csv');
  assert.equal(row.keyword, '성수 맛집');
  assert.equal(row.organicRank, null);
  assert.equal(row.adRank, null);
});

test('import rejects invalid rank values, missing rank columns, and ranks beyond checked depth', () => {
  for (const value of [0, -1, 1.5, '세 번째', 10001]) {
    assert.throws(() => parseImport(toImport([observed(value)]), 'measurement.json'), /순위/);
  }
  assert.throws(() => parseImport(toImport([{keyword: '성수 맛집', observedAt: at, organicRank: 1}]), 'measurement.json'), /열/);
  assert.throws(() => parseImport(toImport([observed(101)]), 'measurement.json'), /조회 범위/);
});

test('import requires timestamp for measured observations and limits bulk file size by row count', () => {
  assert.throws(() => parseImport(toImport([observed(2, null, {observedAt: ''})]), 'measurement.json'), /측정 시각/);
  assert.throws(() => parseImport(toImport([observed(2, null, {observedAt: 'yesterday'})]), 'measurement.json'), /측정 시각/);
  assert.throws(() => parseImport(toImport(Array.from({length: 2001}, () => observed(2))), 'measurement.json'), /2,000/);
});

test('pending rows exported before first measurement can be imported again', () => {
  const pending = {keyword: '새 키워드', organicRank: null, adRank: null,
    status: 'pending', maxDepth: 100, source: 'naver-map'};
  const [row] = parseImport(exportRows([pending]), 'measurement.csv');
  assert.equal(row.status, 'pending');
  assert.equal(row.organicRank, null);
  assert.equal(row.adRank, null);
});

test('imported errors cannot contribute stale numeric ranks to summary metrics', () => {
  const [row] = parseImport(toImport([observed(3, 1, {status: 'error', error: '조회 차단'})]), 'measurement.json');
  assert.equal(row.organicRank, null);
  assert.equal(row.adRank, null);
  assert.equal(row.error, '조회 차단');
  assert.equal(stats([row]).observed, 0);
});

test('sample exports cannot be promoted into actual measurement data by reimporting', () => {
  const demo = sampleState();
  assert.throws(() => parseImport(exportRows(demo.results), 'measurement.csv'), /샘플|demo|체험/i);
});

test('history aggregates latest observations per keyword in Korean calendar days', () => {
  const series = historySeries([
    observed(4, 2, {observedAt: '2026-11-06T00:00:00Z', history: [
      {observedAt: '2026-11-05T00:00:00Z', organicRank: 10, adRank: null},
      {observedAt: '2026-11-05T01:00:00Z', organicRank: 8, adRank: 3},
    ]}),
    observed(6, null, {keyword: '서울숲 맛집', observedAt: '2026-11-06T01:00:00Z', history: [
      {observedAt: '2026-11-04T16:00:00Z', organicRank: 12, adRank: null},
    ]}),
  ]);
  assert.deepEqual(series, [
    {date: '2026-11-05', organic: 10, ads: 3},
    {date: '2026-11-06', organic: 5, ads: 2},
  ]);
});

test('history chooses latest timestamp, independent of CSV or history row order', () => {
  const series = historySeries([observed(2, null, {history: [
    {observedAt: '2026-11-05T18:00:00+09:00', organicRank: 4, adRank: 1},
    {observedAt: '2026-11-05T09:00:00+09:00', organicRank: 20, adRank: 3},
  ]})]);
  assert.equal(series[0].organic, 4);
  assert.equal(series[0].ads, 1);
});

test('a failed current measurement preserves previously successful history in the trend', () => {
  const series = historySeries([observed(null, null, {status: 'error', history: [
    {observedAt: '2026-11-05T14:00:00+09:00', organicRank: 7, adRank: 2},
  ]})]);
  assert.deepEqual(series, [{date: '2026-11-05', organic: 7, ads: 2}]);
});

test('trend windows respect calendar duration rather than merely number of measurements', () => {
  const series = historySeries([observed(1, null, {history: [
    {observedAt: '2026-10-30T14:00:00+09:00', organicRank: 20, adRank: null},
    {observedAt: '2026-10-31T14:00:00+09:00', organicRank: 15, adRank: null},
    {observedAt: '2026-11-01T14:00:00+09:00', organicRank: null, adRank: null},
  ]})], 7);
  assert.deepEqual(series.map(point => point.date), ['2026-10-31', '2026-11-01', '2026-11-06']);
  assert.equal(series[1].organic, null);
});

test('sample history never includes observations after the sample snapshot', () => {
  const demo = sampleState();
  assert.ok(demo.results.every(row => row.source === 'demo'));
  assert.ok(demo.results.every(row => row.history.every(point =>
    Date.parse(point.observedAt) <= Date.parse(row.observedAt))));
});

test('legacy version-one data migrates measurements to their devices and selects the saved device', () => {
  const desktop = observed(3, 1, {device: 'desktop'});
  const mobile = observed(17, 2, {device: 'mobile'});
  const legacy = observed(7, null, {keyword: '서울숲 맛집'});
  const state = {version: 1, mode: 'actual', settings: {device: 'mobile', maxDepth: 100},
    results: [desktop, mobile, legacy]};

  normalizeDeviceState(state);

  assert.equal(state.version, 1);
  assert.equal(state.activeDevice, 'mobile');
  assert.equal(state.settings.device, 'mobile');
  assert.strictEqual(state.results, state.deviceResults.mobile);
  assert.equal(state.deviceResults.desktop.find(row => row.keyword === desktop.keyword).organicRank, 3);
  assert.equal(state.deviceResults.desktop.find(row => row.keyword === legacy.keyword).organicRank, 7);
  assert.equal(state.results.find(row => row.keyword === mobile.keyword).organicRank, 17);
  assert.equal(state.results.find(row => row.keyword === legacy.keyword).status, 'pending');
  assert.ok(state.deviceResults.desktop.every(row => row.device === 'desktop'));
  assert.ok(state.deviceResults.mobile.every(row => row.device === 'mobile'));
});

test('normalization survives storage roundtrips without duplicating or replacing device measurements', () => {
  const state = {version: 1, mode: 'actual', activeDevice: 'mobile',
    settings: {device: 'desktop', maxDepth: 100}, results: [], deviceResults: {
      desktop: [observed(2, 1, {device: 'desktop'})],
      mobile: [observed(22, 3, {device: 'mobile', history: [
        {observedAt: '2026-11-05T14:30:00+09:00', organicRank: 25, adRank: 4},
      ]})],
    }};
  const restored = JSON.parse(JSON.stringify(normalizeDeviceState(state)));

  normalizeDeviceState(restored);
  normalizeDeviceState(restored);

  assert.equal(restored.activeDevice, 'mobile');
  assert.equal(restored.settings.device, 'mobile');
  assert.strictEqual(restored.results, restored.deviceResults.mobile);
  assert.equal(restored.deviceResults.desktop.length, 1);
  assert.equal(restored.deviceResults.mobile.length, 1);
  assert.equal(restored.deviceResults.desktop[0].organicRank, 2);
  assert.equal(restored.results[0].organicRank, 22);
  assert.deepEqual(restored.results[0].history, state.deviceResults.mobile[0].history);
});

test('new keywords synchronize to the other device as fresh pending rows without copied observations', () => {
  const state = normalizeDeviceState({version: 1, mode: 'actual',
    settings: {device: 'desktop', maxDepth: 50}, results: []});
  state.results = [observed(4, 2, {device: 'desktop', previousRank: 8,
    error: 'old error', history: [
      {observedAt: '2026-11-05T14:30:00+09:00', organicRank: 8, adRank: 3},
    ]})];

  syncDeviceRows(state);

  const pending = state.deviceResults.mobile[0];
  assert.strictEqual(state.results, state.deviceResults.desktop);
  assert.equal(state.results[0].organicRank, 4);
  assert.equal(state.results[0].history.length, 1);
  assert.equal(pending.keyword, state.results[0].keyword);
  assert.equal(pending.device, 'mobile');
  assert.equal(pending.status, 'pending');
  assert.equal(pending.organicRank, null);
  assert.equal(pending.adRank, null);
  assert.equal(pending.maxDepth, 50);
  assert.equal(pending.clientMode, 'mobile-web-emulation');
  assert.equal(pending.locationMode, 'naver-default');
  assert.ok(pending.observedAt == null);
  assert.ok(pending.previousRank == null);
  assert.ok(pending.error == null);
  assert.ok(!pending.history || pending.history.length === 0);
});

test('device switching preserves independent ranks and histories and limits metrics to the active device', () => {
  const desktop = observed(2, 1, {device: 'desktop', history: [
    {observedAt: '2026-11-05T14:30:00+09:00', organicRank: 5, adRank: 2},
  ]});
  const mobile = observed(24, 4, {device: 'mobile', history: [
    {observedAt: '2026-11-05T14:30:00+09:00', organicRank: 30, adRank: 5},
  ]});
  const state = normalizeDeviceState({version: 1, mode: 'actual',
    settings: {device: 'desktop', maxDepth: 100}, results: [desktop, mobile]});

  assert.equal(stats(state.results).average, 2);
  assert.equal(stats(state.results).top10Rate, 100);
  assert.deepEqual(historySeries(state.results).map(point => point.organic), [5, 2]);

  activateDevice(state, 'mobile');

  assert.equal(state.activeDevice, 'mobile');
  assert.equal(state.settings.device, 'mobile');
  assert.strictEqual(state.results, state.deviceResults.mobile);
  assert.equal(stats(state.results).average, 24);
  assert.equal(stats(state.results).adAverage, 4);
  assert.equal(stats(state.results).top10Rate, 0);
  assert.deepEqual(historySeries(state.results).map(point => point.organic), [30, 24]);
  state.results[0].organicRank = 20;
  state.results.push(observed(8, null, {keyword: '모바일 전용 새 키워드', device: 'mobile'}));

  activateDevice(state, 'desktop');

  assert.strictEqual(state.results, state.deviceResults.desktop);
  assert.equal(state.results.find(row => row.keyword === desktop.keyword).organicRank, 2);
  assert.deepEqual(state.results.find(row => row.keyword === desktop.keyword).history, desktop.history);
  const pending = state.results.find(row => row.keyword === '모바일 전용 새 키워드');
  assert.equal(pending.status, 'pending');
  assert.equal(pending.organicRank, null);
  assert.equal(pending.clientMode, 'desktop-web');
  assert.equal(stats(state.results).average, 2);
  assert.equal(state.deviceResults.mobile.find(row => row.keyword === mobile.keyword).organicRank, 20);
  assert.deepEqual(state.deviceResults.mobile.find(row => row.keyword === mobile.keyword).history, mobile.history);
});

test('combined export includes both devices while retaining the selected device and synchronized keywords', () => {
  const state = normalizeDeviceState({version: 1, mode: 'actual',
    settings: {device: 'mobile', maxDepth: 100}, results: [
      observed(1, null, {keyword: 'PC에만 있던 키워드', device: 'desktop'}),
      observed(15, 3, {keyword: '모바일에만 있던 키워드', device: 'mobile'}),
    ]});

  const combined = allDeviceRows(state);

  assert.equal(combined.length, 4);
  assert.equal(new Set(combined.map(row => `${row.device}:${row.keyword}`)).size, 4);
  assert.deepEqual(state.deviceResults.desktop.map(row => row.keyword),
    state.deviceResults.mobile.map(row => row.keyword));
  assert.equal(state.activeDevice, 'mobile');
  assert.equal(state.settings.device, 'mobile');
  assert.strictEqual(state.results, state.deviceResults.mobile);
  assert.equal(stats(state.results).average, 15);
});

test('invalid device selections and a combined keyword set above 1000 are rejected', () => {
  const state = normalizeDeviceState({version: 1, mode: 'actual',
    settings: {device: 'desktop', maxDepth: 100}, results: []});
  assert.throws(() => activateDevice(state, 'tablet'), /PC|모바일/);
  assert.equal(state.activeDevice, 'desktop');
  state.results = Array.from({length: 1001}, (_, i) => observed(2, null,
    {keyword: `키워드 ${i}`, device: 'desktop'}));
  assert.throws(() => syncDeviceRows(state), /1,000/);
});

test('import accepts 1000 keywords for each device as 2000 distinct measurements', () => {
  const rows = Array.from({length: 1000}, (_, i) => [
    observed(3, 1, {keyword: `키워드 ${i}`, device: 'desktop'}),
    observed(15, 2, {keyword: `키워드 ${i}`, device: 'mobile'}),
  ]).flat();

  const imported = parseImport(toImport(rows), 'both-devices.json');

  assert.equal(imported.length, 2000);
  assert.equal(imported.filter(row => row.device === 'desktop').length, 1000);
  assert.equal(imported.filter(row => row.device === 'mobile').length, 1000);
  assert.equal(new Set(imported.map(row => row.keyword)).size, 1000);
  assert.equal(imported[0].organicRank, 3);
  assert.equal(imported[1].organicRank, 15);
});

test('import limits unique keywords independently of its two-device row allowance', () => {
  const rows = Array.from({length: 1001}, (_, i) => observed(2, null,
    {keyword: `서로 다른 키워드 ${i}`, device: i % 2 ? 'desktop' : 'mobile'}));
  assert.throws(() => parseImport(toImport(rows), 'measurement.json'), /1,000/);
});

test('import validates device names and keeps absent or empty legacy device values on desktop', () => {
  for (const device of ['tablet', 'Mobile', 'phone', 'mobile ']) {
    assert.throws(() => parseImport(toImport([observed(2, null, {device})]),
      'measurement.json'), /device|desktop|mobile/);
  }
  const imported = parseImport(toImport([
    observed(2), observed(3, null, {device: ''}), observed(4, null, {device: 'mobile'}),
  ]), 'measurement.json');
  assert.deepEqual(imported.map(row => row.device), ['desktop', 'desktop', 'mobile']);
});

test('CSV and JSON imports preserve client and location modes without claiming real mobile GPS', () => {
  const original = [
    observed(2, 1, {device: 'desktop', clientMode: 'desktop-web', locationMode: 'naver-default'}),
    observed(12, 3, {device: 'mobile', clientMode: 'mobile-web-emulation', locationMode: 'naver-default'}),
  ];
  for (const [text, filename] of [
    [exportRows(original), 'environment.csv'], [toImport(original), 'environment.json'],
  ]) {
    const imported = parseImport(text, filename);
    assert.deepEqual(imported.map(row => [row.device, row.clientMode, row.locationMode]), [
      ['desktop', 'desktop-web', 'naver-default'],
      ['mobile', 'mobile-web-emulation', 'naver-default'],
    ]);
  }
  const [legacy] = parseImport(toImport([observed(2)]), 'legacy.json');
  assert.equal(legacy.clientMode, 'imported-unspecified');
  assert.equal(legacy.locationMode, 'unspecified');
});

const locationA = {label: '성수역', latitude: 37.5445, longitude: 127.0557};
const locationB = {label: '강남역', latitude: 37.4979, longitude: 127.0276};
const locationState = (rows = [], extra = {}) => normalizeLocationState(normalizeDeviceState({
  version: 1, mode: 'actual', settings: {device: 'desktop', maxDepth: 100, region: '기록용 지역 메모'},
  results: rows, ...extra,
}));

test('search location validation requires a complete finite numeric coordinate pair', () => {
  assert.equal(normalizeSearchLocation(null), null);
  assert.equal(normalizeSearchLocation(undefined), null);
  assert.deepEqual(normalizeSearchLocation({...locationA, label: '  성수역  '}), locationA);
  assert.deepEqual(normalizeSearchLocation({latitude: 0, longitude: 0}),
    {latitude: 0, longitude: 0, label: '0.00000, 0.00000'});
  for (const invalid of [
    '', [], '성수역', {latitude: 37.5}, {longitude: 127},
    {latitude: '', longitude: 127}, {latitude: '37.5', longitude: 127},
    {latitude: NaN, longitude: 127}, {latitude: 37.5, longitude: Infinity},
    {latitude: 90.1, longitude: 127}, {latitude: -90.1, longitude: 127},
    {latitude: 37.5, longitude: 180.1}, {latitude: 37.5, longitude: -180.1},
    {...locationA, label: '가'.repeat(101)},
  ]) assert.throws(() => normalizeSearchLocation(invalid), /좌표|위도|경도|숫자|100자/);
});

test('location identity uses rounded coordinates and keeps labels independent of measurement identity', () => {
  assert.equal(searchLocationKey(null), 'default');
  assert.equal(searchLocationKey(locationA), searchLocationKey({...locationA, label: '서울숲 근처'}));
  assert.equal(searchLocationKey(locationA), searchLocationKey({
    label: '같은 측정 지점', latitude: 37.5445001, longitude: 127.0557001,
  }));
  assert.notEqual(searchLocationKey(locationA), searchLocationKey(locationB));
  assert.notEqual(searchLocationKey(locationA), searchLocationKey({
    ...locationA, latitude: locationA.latitude + 0.0001,
  }));
});

test('legacy region notes remain default-scope observations without being promoted to applied coordinates', () => {
  const state = locationState([
    observed(3, 1, {device: 'desktop', region: '서울 성동구'}),
    observed(13, 2, {device: 'mobile', region: '서울 성동구'}),
  ], {settings: {device: 'mobile', maxDepth: 100, region: '서울 성동구', searchLocation: locationA}});

  assert.equal(state.locationKey, 'default');
  assert.equal(state.settings.searchLocation, null);
  assert.equal(state.settings.region, '서울 성동구');
  assert.equal(state.activeDevice, 'mobile');
  assert.equal(state.results[0].organicRank, 13);
  assert.equal(state.deviceResults.desktop[0].organicRank, 3);
  assert.deepEqual(state.keywordRegistry, ['성수 파스타']);
  stashCurrentLocation(state);
  assert.equal(state.locationProfiles.default.searchLocation, null);
  assert.equal(state.locationProfiles.default.deviceResults.desktop[0].organicRank, 3);
});

test('a new location starts both devices pending with requested coordinates and no copied rank or history', () => {
  const state = locationState([observed(2, 1, {device: 'desktop', previousRank: 6, history: [
    {observedAt: '2026-11-05T14:30:00+09:00', organicRank: 6, adRank: 2},
  ]})]);

  changeSearchLocation(state, locationA);

  assert.equal(state.locationKey, searchLocationKey(locationA));
  assert.deepEqual(state.settings.searchLocation, locationA);
  assert.equal(state.settings.region, '기록용 지역 메모');
  assert.strictEqual(state.results, state.deviceResults.desktop);
  assert.equal(state.locationProfiles.default.deviceResults.desktop[0].organicRank, 2);
  for (const device of ['desktop', 'mobile']) {
    const [row] = state.deviceResults[device];
    assert.equal(row.device, device);
    assert.equal(row.keyword, '성수 파스타');
    assert.equal(row.status, 'pending');
    assert.equal(row.organicRank, null);
    assert.equal(row.adRank, null);
    assert.ok(row.observedAt == null);
    assert.ok(row.previousRank == null);
    assert.ok(!row.history || row.history.length === 0);
    assert.equal(row.locationMode, 'custom-geolocation');
    assert.equal(row.locationApplied, false);
    assert.deepEqual(row.requestedSearchLocation, locationA);
  }
  assert.equal(stats(state.results).average, null);
  assert.deepEqual(historySeries(state.results), []);
});

test('region changes preserve independent desktop and mobile ranks and histories across repeated switches', () => {
  const makeRow = (device, rank, previous) => observed(rank, device === 'desktop' ? 1 : 2, {
    device, history: [{observedAt: '2026-11-05T14:30:00+09:00', organicRank: previous, adRank: null}],
  });
  const state = locationState([makeRow('desktop', 1, 2), makeRow('mobile', 11, 12)]);
  changeSearchLocation(state, locationA);
  state.deviceResults.desktop = [makeRow('desktop', 3, 4)];
  state.deviceResults.mobile = [makeRow('mobile', 13, 14)];
  state.results = state.deviceResults[state.activeDevice];
  changeSearchLocation(state, locationB);
  state.deviceResults.desktop = [makeRow('desktop', 5, 6)];
  state.deviceResults.mobile = [makeRow('mobile', 15, 16)];
  state.results = state.deviceResults[state.activeDevice];

  for (const [location, desktop, mobile] of [
    [locationA, 3, 13], [locationB, 5, 15], [null, 1, 11], [locationA, 3, 13],
  ]) {
    changeSearchLocation(state, location);
    activateDevice(state, 'desktop');
    assert.equal(stats(state.results).average, desktop);
    assert.deepEqual(historySeries(state.results).map(point => point.organic), [desktop + 1, desktop]);
    activateDevice(state, 'mobile');
    assert.equal(stats(state.results).average, mobile);
    assert.deepEqual(historySeries(state.results).map(point => point.organic), [mobile + 1, mobile]);
    assert.strictEqual(state.results, state.deviceResults.mobile);
  }
});

test('location snapshots clone nested histories and restoring a profile does not mutate its archived copy', () => {
  const state = locationState([observed(2, 1, {device: 'desktop', history: [
    {observedAt: '2026-11-05T14:30:00+09:00', organicRank: 8, adRank: null},
  ]})]);
  stashCurrentLocation(state);
  state.results[0].organicRank = 3;
  state.results[0].history[0].organicRank = 9;
  assert.equal(state.locationProfiles.default.deviceResults.desktop[0].organicRank, 2);
  assert.equal(state.locationProfiles.default.deviceResults.desktop[0].history[0].organicRank, 8);

  changeSearchLocation(state, locationA);
  changeSearchLocation(state, null);
  state.results[0].history[0].organicRank = 10;
  assert.equal(state.locationProfiles.default.deviceResults.desktop[0].history[0].organicRank, 9);
});

test('renaming the same rounded coordinate location keeps existing measurements and the selected device', () => {
  const state = locationState();
  state.keywordRegistry = ['성수 파스타'];
  changeSearchLocation(state, locationA);
  activateDevice(state, 'mobile');
  Object.assign(state.results[0], observed(7, 2, {device: 'mobile', history: [
    {observedAt: '2026-11-05T14:30:00+09:00', organicRank: 9, adRank: 3},
  ]}));
  const renamed = {label: '이름을 바꾼 같은 지점', latitude: 37.5445001, longitude: 127.0557001};

  changeSearchLocation(state, renamed);

  assert.equal(state.locationKey, searchLocationKey(locationA));
  assert.equal(state.activeDevice, 'mobile');
  assert.equal(state.settings.device, 'mobile');
  assert.deepEqual(state.settings.searchLocation, renamed);
  assert.equal(state.results[0].organicRank, 7);
  assert.equal(state.results[0].adRank, 2);
  assert.deepEqual(historySeries(state.results).map(point => point.organic), [9, 7]);
});

test('keywords added in another location are shared globally while their measurements stay in that location', () => {
  const state = locationState([observed(2, 1, {device: 'desktop'})]);
  changeSearchLocation(state, locationA);
  state.results.push(observed(4, 2, {keyword: '서울숲 맛집', device: 'desktop'}));
  syncDeviceRows(state);
  changeSearchLocation(state, locationB);

  assert.deepEqual(state.keywordRegistry, ['성수 파스타', '서울숲 맛집']);
  assert.ok(state.results.every(row => row.status === 'pending'));
  assert.equal(state.results.length, 2);
  changeSearchLocation(state, null);
  assert.equal(state.results.find(row => row.keyword === '성수 파스타').organicRank, 2);
  assert.equal(state.results.find(row => row.keyword === '서울숲 맛집').status, 'pending');
  changeSearchLocation(state, locationA);
  assert.equal(state.results.find(row => row.keyword === '서울숲 맛집').organicRank, 4);
});

test('keyword removal clears both devices and every saved region so deleted keywords cannot reappear', () => {
  const state = locationState([observed(2, 1, {device: 'desktop'}), observed(12, 2, {device: 'mobile'})]);
  changeSearchLocation(state, locationA);
  Object.assign(state.results[0], observed(3, 1, {device: 'desktop'}));
  state.results.push(observed(8, 2, {keyword: '유지할 키워드', device: 'desktop'}));
  changeSearchLocation(state, locationB);
  Object.assign(state.results.find(row => row.keyword === '성수 파스타'), observed(4, 2, {device: 'desktop'}));

  removeKeywords(state, ['성수 파스타']);

  assert.deepEqual(state.keywordRegistry, ['유지할 키워드']);
  for (const profile of Object.values(state.locationProfiles)) {
    for (const device of ['desktop', 'mobile']) {
      assert.ok(profile.deviceResults[device].every(row => row.keyword !== '성수 파스타'));
    }
  }
  for (const location of [locationA, null, locationB, locationA]) {
    changeSearchLocation(state, location);
    assert.deepEqual(state.deviceResults.desktop.map(row => row.keyword), ['유지할 키워드']);
    assert.deepEqual(state.deviceResults.mobile.map(row => row.keyword), ['유지할 키워드']);
  }
  assert.equal(state.results[0].organicRank, 8);
});

test('location archives omit empty pending placeholders while preserving prior observation histories', () => {
  const state = locationState();
  state.results = [
    {keyword: '아직 측정하지 않음', device: 'desktop', status: 'pending', organicRank: null, adRank: null},
    {keyword: '이력이 있는 대기행', device: 'desktop', status: 'pending', organicRank: null, adRank: null,
      history: [{observedAt: at, organicRank: 6, adRank: 1}]},
  ];

  stashCurrentLocation(state);

  assert.deepEqual(state.keywordRegistry, ['아직 측정하지 않음', '이력이 있는 대기행']);
  assert.deepEqual(state.locationProfiles.default.deviceResults.desktop.map(row => row.keyword), ['이력이 있는 대기행']);
  assert.deepEqual(state.locationProfiles.default.deviceResults.mobile, []);
  assert.equal(state.results.length, 2);
  changeSearchLocation(state, locationA);
  stashCurrentLocation(state);
  assert.deepEqual(state.locationProfiles[searchLocationKey(locationA)].deviceResults, {desktop: [], mobile: []});
  changeSearchLocation(state, null);
  assert.equal(state.results.length, 2);
  assert.equal(state.results.find(row => row.keyword === '아직 측정하지 않음').status, 'pending');
  assert.deepEqual(historySeries(state.results), [{date: '2026-11-06', organic: 6, ads: 1}]);
});

test('location profiles and the active region survive browser storage serialization', () => {
  const state = locationState([observed(2, 1, {device: 'desktop'})]);
  changeSearchLocation(state, locationA);
  activateDevice(state, 'mobile');
  Object.assign(state.results[0], observed(8, 2, {device: 'mobile'}));
  stashCurrentLocation(state);
  const restored = normalizeLocationState(normalizeDeviceState(JSON.parse(JSON.stringify(state))));

  assert.equal(restored.locationKey, searchLocationKey(locationA));
  assert.deepEqual(restored.settings.searchLocation, locationA);
  assert.equal(restored.activeDevice, 'mobile');
  assert.strictEqual(restored.results, restored.deviceResults.mobile);
  assert.equal(restored.results[0].organicRank, 8);
  changeSearchLocation(restored, null);
  activateDevice(restored, 'desktop');
  assert.equal(restored.results[0].organicRank, 2);
  changeSearchLocation(restored, locationA);
  activateDevice(restored, 'mobile');
  assert.equal(restored.results[0].organicRank, 8);
});

test('CSV custom-location measurements preserve coordinates, applied state and verification metadata', () => {
  const original = observed(5, 2, {device: 'mobile', clientMode: 'mobile-web-emulation',
    locationMode: 'custom-geolocation', searchLatitude: locationA.latitude,
    searchLongitude: locationA.longitude, searchLocationLabel: locationA.label,
    locationApplied: true, locationVerification: 'browser-geolocation'});

  const [row] = parseImport(exportRows([original]), 'custom-location.csv');

  assert.equal(row.organicRank, 5);
  assert.equal(row.adRank, 2);
  assert.equal(row.device, 'mobile');
  assert.equal(row.locationMode, 'custom-geolocation');
  assert.deepEqual(row.searchLocation, locationA);
  assert.deepEqual(row.requestedSearchLocation, locationA);
  assert.equal(row.locationApplied, true);
  assert.equal(row.locationVerification, 'browser-geolocation');
});

test('JSON import accepts nested applied or requested search locations without discarding their labels', () => {
  for (const field of ['searchLocation', 'requestedSearchLocation']) {
    const [row] = parseImport(toImport([observed(3, null, {
      [field]: locationB, locationApplied: true, locationMode: 'custom-geolocation',
    })]), 'custom-location.json');
    assert.deepEqual(row.searchLocation, locationB);
    assert.deepEqual(row.requestedSearchLocation, locationB);
    assert.equal(row.locationApplied, true);
  }
});

test('requested locations on failed or pending imports do not become applied successful measurements', () => {
  for (const status of ['pending', 'error']) {
    for (const locationApplied of [false, 'false']) {
      const [row] = parseImport(toImport([observed(7, 2, {status,
        requestedSearchLocation: locationA, locationApplied, locationMode: 'custom-geolocation',
      })]), 'unapplied-location.json');
      assert.deepEqual(row.requestedSearchLocation, locationA);
      assert.equal(row.searchLocation, null);
      assert.equal(row.locationApplied, false);
      assert.equal(row.organicRank, null);
      assert.equal(row.adRank, null);
      assert.equal(stats([row]).observed, 0);
    }
  }
  const [legacy] = parseImport(toImport([observed(2)]), 'legacy-location.json');
  assert.equal(legacy.searchLocation, null);
  assert.equal(legacy.requestedSearchLocation, null);
  assert.equal(legacy.locationApplied, null);
});

test('custom-location successes require verified applied coordinates and strict applied booleans', () => {
  assert.throws(() => parseImport(toImport([observed(3, null,
    {locationMode: 'custom-geolocation', locationApplied: true})]), 'missing-location.json'), /좌표/);
  for (const locationApplied of [undefined, null, '', false, 'false']) {
    assert.throws(() => parseImport(toImport([observed(3, null,
      {searchLocation: locationA, locationMode: 'custom-geolocation', locationApplied})]),
    'unverified-location.json'), /locationApplied|true/);
  }
  for (const locationApplied of [0, 1, 'TRUE', 'False', 'yes', '0']) {
    assert.throws(() => parseImport(toImport([observed(null, null,
      {status: 'pending', requestedSearchLocation: locationA, locationApplied})]),
    'invalid-applied.json'), /locationApplied|true|false/);
  }
});

test('coordinate imports reject incomplete, nonfinite, out-of-range and nonnumeric JSON values', () => {
  for (const coordinates of [
    {searchLatitude: 37.5}, {searchLongitude: 127},
    {searchLatitude: '', searchLongitude: 127},
    {searchLatitude: 37.5, searchLongitude: ''},
    {searchLatitude: 'NaN', searchLongitude: 127},
    {searchLatitude: 37.5, searchLongitude: 'Infinity'},
    {searchLatitude: 91, searchLongitude: 127},
    {searchLatitude: 37.5, searchLongitude: -181},
    {searchLatitude: true, searchLongitude: 127},
    {searchLatitude: 37.5, searchLongitude: false},
    {searchLatitude: [], searchLongitude: 127},
    {searchLatitude: 37.5, searchLongitude: [127]},
  ]) {
    assert.throws(() => parseImport(toImport([observed(2, null,
      {...coordinates, locationApplied: true})]), 'invalid-coordinates.json'), /위도|경도|좌표|숫자/);
  }
});

test('negative numeric GPS coordinates roundtrip through CSV while formula-like strings remain neutralized', () => {
  assert.deepEqual(csvParse(csvEncode([[-33.8688, -70.6693, '-1+2']])),
    [['-33.8688', '-70.6693', "'-1+2"]]);
  const southWest = {label: '남서반구 측정지점', latitude: -33.8688, longitude: -70.6693};
  const original = observed(4, 1, {device: 'desktop', locationMode: 'custom-geolocation',
    searchLatitude: southWest.latitude, searchLongitude: southWest.longitude,
    searchLocationLabel: southWest.label, locationApplied: true});

  const [row] = parseImport(exportRows([original]), 'negative-coordinates.csv');

  assert.deepEqual(row.searchLocation, southWest);
  assert.deepEqual(row.requestedSearchLocation, southWest);
  assert.equal(row.locationApplied, true);
});
