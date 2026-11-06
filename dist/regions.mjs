const CATALOG_ERROR = '지역 목록 형식이 올바르지 않습니다. regions.json 파일을 다시 확인해 주세요.';
const normalizeName = value => String(value ?? '').normalize('NFKC').toLowerCase()
  .replace(/[^0-9a-z\u3131-\u318e\uac00-\ud7a3]/g, '');
const unique = values => [...new Set(values.filter(Boolean))];
const coordinateKey = ({latitude, longitude}) => `${latitude.toFixed(5)}:${longitude.toFixed(5)}`;
const coordinatesValid = value => value && typeof value.latitude === 'number' &&
  typeof value.longitude === 'number' && Number.isFinite(value.latitude) &&
  Number.isFinite(value.longitude) && Math.abs(value.latitude) <= 90 && Math.abs(value.longitude) <= 180;
const withoutProvinceSuffix = value => value.replace(/(?:특별자치도|특별자치시|통합특별시|광역시|특별시|도)$/u, '');
const withoutCitySuffix = value => value.replace(/(?:시|군|구)$/u, '');

function aliasesOf(value) {
  if (value.aliases !== undefined && (!Array.isArray(value.aliases) ||
    value.aliases.some(alias => typeof alias !== 'string' || !alias.trim()))) throw new Error(CATALOG_ERROR);
  return [value.name, ...(value.aliases || [])];
}

function prepareCatalog(catalog) {
  if (!catalog || typeof catalog !== 'object' || !Array.isArray(catalog.provinces) || !catalog.provinces.length) {
    throw new Error(CATALOG_ERROR);
  }
  const provinces = [], regions = [], provinceCodes = new Set();
  for (const province of catalog.provinces) {
    if (!province || typeof province.code !== 'string' || !province.code.trim() || provinceCodes.has(province.code) ||
      typeof province.name !== 'string' || !province.name.trim() || !coordinatesValid(province) ||
      !Array.isArray(province.cities)) throw new Error(CATALOG_ERROR);
    provinceCodes.add(province.code);
    const provinceNames = unique(aliasesOf(province).flatMap(name => {
      const compact = normalizeName(name);
      return [compact, withoutProvinceSuffix(compact)];
    }));
    const provinceSelection = {provinceCode: province.code, cityCode: null, label: province.name,
      latitude: province.latitude, longitude: province.longitude,
      anchor: typeof province.anchor === 'string' && province.anchor.trim() ? province.anchor : `${province.name} 대표 지점 주변`};
    const preparedProvince = {selection: provinceSelection, provinceNames, names: provinceNames};
    provinces.push(preparedProvince);
    regions.push(preparedProvince);
    const cityCodes = new Set();
    for (const city of province.cities) {
      if (!city || typeof city.code !== 'string' || !city.code.trim() || cityCodes.has(city.code) ||
        typeof city.name !== 'string' || !city.name.trim() || !coordinatesValid(city)) throw new Error(CATALOG_ERROR);
      cityCodes.add(city.code);
      const cityNames = unique(aliasesOf(city).flatMap(name => {
        const parts = name.trim().split(/\s+/).map(normalizeName).filter(Boolean);
        const compact = parts.join('');
        const combinations = parts.reduce((prefixes, part) => prefixes.flatMap(prefix =>
          unique([part, withoutCitySuffix(part)]).map(variant => prefix + variant)), ['']);
        return [compact, withoutCitySuffix(compact), ...combinations,
          parts.at(-1), withoutCitySuffix(parts.at(-1) || '')];
      }));
      const names = unique([...cityNames, ...provinceNames.flatMap(provinceName => cityNames.map(cityName => provinceName + cityName))]);
      regions.push({selection: {provinceCode: province.code, cityCode: city.code,
        label: `${province.name} ${city.name}`, latitude: city.latitude, longitude: city.longitude,
        anchor: typeof city.anchor === 'string' && city.anchor.trim() ? city.anchor : `${city.name} 대표 지점 주변`},
      provinceNames, cityNames, names});
    }
  }
  return {provinces, regions};
}

/** Search official names and catalog aliases; exact matches precede prefix and sequential matches. */
export function searchRegions(catalog, query, limit = 30) {
  const {regions} = prepareCatalog(catalog);
  if (!Number.isInteger(limit) || limit < 0) throw new Error('검색 결과 개수는 0 이상의 정수여야 합니다.');
  if (query != null && typeof query !== 'string') throw new Error('지역 검색어는 글자로 입력해 주세요.');
  const text = String(query ?? '').trim();
  const normalized = normalizeName(text);
  if (!normalized || !limit || text.length > 200) return [];
  const terms = text.split(/\s+/).map(normalizeName).filter(Boolean);
  const score = names => {
    if (names.includes(normalized)) return 0;
    if (names.some(name => name.startsWith(normalized))) return 1;
    if (terms.length > 1 && names.some(name => {
      let position = 0;
      return terms.every(term => {
        const found = name.indexOf(term, position);
        if (found < 0) return false;
        position = found + term.length;
        return true;
      });
    })) return 2;
    return names.some(name => name.includes(normalized)) ? 3 : Infinity;
  };
  return regions.map((region, order) => ({region, score: score(region.names), order}))
    .filter(item => Number.isFinite(item.score))
    .sort((a, b) => a.score - b.score || a.order - b.order)
    .slice(0, limit).map(item => ({...item.region.selection}));
}

/** Return a representative point for a province or city without changing the input catalog. */
export function getRegionSelection(catalog, provinceCode, cityCode = '') {
  const {regions} = prepareCatalog(catalog);
  if (typeof provinceCode !== 'string' || (cityCode != null && typeof cityCode !== 'string')) {
    throw new Error('시·도와 시·군·구 선택값을 확인해 주세요.');
  }
  const selected = regions.find(region => region.selection.provinceCode === provinceCode &&
    region.selection.cityCode === (cityCode || null));
  if (!selected) throw new Error('선택한 시·도 또는 시·군·구를 지역 목록에서 찾지 못했습니다.');
  return {...selected.selection};
}

function distanceKm(left, right) {
  const radians = degrees => degrees * Math.PI / 180;
  const latitudeDelta = radians(right.latitude - left.latitude);
  const longitudeDelta = radians(right.longitude - left.longitude);
  const value = Math.sin(latitudeDelta / 2) ** 2 + Math.cos(radians(left.latitude)) *
    Math.cos(radians(right.latitude)) * Math.sin(longitudeDelta / 2) ** 2;
  return 6371 * 2 * Math.atan2(Math.sqrt(value), Math.sqrt(Math.max(0, 1 - value)));
}

/** UI-only suggestion. Never replace an existing stored measurement coordinate with this result. */
export function matchRegionSelection(catalog, existingSearchLocation) {
  const {regions, provinces} = prepareCatalog(catalog);
  if (!coordinatesValid(existingSearchLocation)) return null;
  const name = normalizeName(existingSearchLocation.label);
  const exactCoordinates = regions.filter(region => coordinateKey(region.selection) === coordinateKey(existingSearchLocation));
  if (exactCoordinates.length) {
    const named = exactCoordinates.find(region => region.names.includes(name));
    return {...(named || exactCoordinates[0]).selection, matchType: 'coordinates'};
  }
  const exactNames = name ? regions.filter(region => region.names.includes(name)) : [];
  if (exactNames.length === 1) return {...exactNames[0].selection, matchType: 'name'};
  if (exactNames.length > 1) return null;
  const provinceMatches = name ? provinces.filter(province => province.provinceNames.some(alias => name.startsWith(alias))) : [];
  const provinceCode = provinceMatches.length === 1 ? provinceMatches[0].selection.provinceCode : null;
  const nearby = regions.filter(region => region.selection.cityCode !== null &&
    (!provinceCode || region.selection.provinceCode === provinceCode))
    .map(region => ({region, distance: distanceKm(existingSearchLocation, region.selection)}))
    .sort((a, b) => a.distance - b.distance);
  // Representative points are not administrative boundaries; avoid guessing a district from kilometres away.
  if (nearby[0]?.distance <= 0.25 && (!nearby[1] || nearby[1].distance - nearby[0].distance >= 0.1)) {
    return {...nearby[0].region.selection, matchType: 'nearby'};
  }
  return provinceMatches.length === 1 ? {...provinceMatches[0].selection, matchType: 'name'} : null;
}
