export const STORAGE_KEY = 'place-rank-studio-v1';
export const SAMPLE_DATE = '2026-11-06T14:30:00+09:00';
export const DEVICES = ['desktop', 'mobile'];
const validDevice = device => DEVICES.includes(device);
export function normalizeSearchLocation(value) {
  if (value == null) return null;
  if (typeof value !== 'object' || Array.isArray(value)) throw new Error('검색 지역 좌표를 확인해 주세요.');
  const {latitude, longitude} = value;
  if (typeof latitude !== 'number' || typeof longitude !== 'number' || !Number.isFinite(latitude) || !Number.isFinite(longitude) || latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) throw new Error('위도는 -90~90, 경도는 -180~180의 숫자여야 합니다.');
  const label = String(value.label || `${latitude.toFixed(5)}, ${longitude.toFixed(5)}`).trim();
  if (label.length > 100) throw new Error('검색 지역 이름은 100자 이내로 입력해 주세요.');
  return {label, latitude, longitude};
}
export function searchLocationKey(value) {
  const location = normalizeSearchLocation(value);
  return location ? `location:${location.latitude.toFixed(5)}:${location.longitude.toFixed(5)}` : 'default';
}
export function normalizeLocationState(state) {
  // A pre-existing region string was a note, never evidence of applied coordinates.
  const hasLocationProfiles = state.locationProfiles && typeof state.locationProfiles === 'object' && !Array.isArray(state.locationProfiles);
  if (!hasLocationProfiles) {
    state.locationProfiles = {};
    state.settings.searchLocation = null;
    state.locationKey = 'default';
  } else {
    state.settings.searchLocation = normalizeSearchLocation(state.settings.searchLocation);
    state.locationKey = searchLocationKey(state.settings.searchLocation);
  }
  state.keywordRegistry = [...new Set([...(state.keywordRegistry || []), ...DEVICES.flatMap(device => state.deviceResults[device].map(row => row.keyword))])];
  return syncDeviceRows(state);
}
export function stashCurrentLocation(state) {
  syncDeviceRows(state);
  const location = normalizeSearchLocation(state.settings.searchLocation);
  state.locationProfiles ||= {};
  state.locationKey = searchLocationKey(location);
  state.locationProfiles[state.locationKey] = {
    searchLocation: location,
    deviceResults: structuredClone(Object.fromEntries(DEVICES.map(device => [device, state.deviceResults[device].filter(row => row.status !== 'pending' || row.observedAt || row.history?.length)]))),
  };
  return state;
}
export function changeSearchLocation(state, value) {
  const location = normalizeSearchLocation(value), nextKey = searchLocationKey(location);
  if (nextKey === state.locationKey) {
    state.settings.searchLocation = location;
    return state;
  }
  stashCurrentLocation(state);
  const profile = state.locationProfiles[nextKey];
  state.settings.searchLocation = location;
  state.locationKey = nextKey;
  state.deviceResults = profile ? structuredClone(profile.deviceResults) : {desktop: [], mobile: []};
  state.results = state.deviceResults[state.activeDevice];
  return syncDeviceRows(state);
}
export function removeKeywords(state, keywords) {
  const remove = new Set(keywords);
  state.keywordRegistry = (state.keywordRegistry || []).filter(keyword => !remove.has(keyword));
  for (const device of DEVICES) state.deviceResults[device] = state.deviceResults[device].filter(row => !remove.has(row.keyword));
  for (const profile of Object.values(state.locationProfiles || {})) for (const device of DEVICES) profile.deviceResults[device] = profile.deviceResults[device].filter(row => !remove.has(row.keyword));
  state.results = state.deviceResults[state.activeDevice];
  return syncDeviceRows(state);
}
export function normalizeDeviceState(state) {
  const active = validDevice(state.activeDevice) ? state.activeDevice : validDevice(state.settings?.device) ? state.settings.device : 'desktop';
  if (!state.deviceResults || !DEVICES.every(device => Array.isArray(state.deviceResults[device]))) {
    state.deviceResults = {desktop: [], mobile: []};
    for (const row of state.results || []) {
      const device = row.device === 'mobile' ? 'mobile' : 'desktop';
      state.deviceResults[device].push({...row, device});
    }
  }
  state.activeDevice = active;
  state.settings = {...state.settings, device: active};
  state.results = state.deviceResults[active];
  return syncDeviceRows(state);
}
export function syncDeviceRows(state) {
  state.deviceResults[state.activeDevice] = state.results;
  const keywords = [...new Set([...(state.keywordRegistry || []), ...DEVICES.flatMap(device => state.deviceResults[device].map(row => row.keyword))])];
  if (keywords.length > 1000) throw new Error('총 1,000개 키워드까지 관리할 수 있습니다.');
  state.keywordRegistry = keywords;
  for (const device of DEVICES) {
    const byKeyword = new Map(state.deviceResults[device].map(row => [row.keyword, row]));
    state.deviceResults[device] = keywords.map(keyword => {
      const row = byKeyword.get(keyword);
      return row ? {...row, device} : {keyword, device, organicRank: null, adRank: null, status: 'pending',
        maxDepth: state.settings.maxDepth, source: state.mode === 'demo' ? 'demo' : 'naver-map',
        clientMode: device === 'mobile' ? 'mobile-web-emulation' : 'desktop-web', locationMode: state.settings.searchLocation ? 'custom-geolocation' : 'naver-default', requestedSearchLocation: state.settings.searchLocation ? structuredClone(state.settings.searchLocation) : null, locationApplied: false};
    });
  }
  state.results = state.deviceResults[state.activeDevice];
  return state;
}
export function activateDevice(state, device) {
  if (!validDevice(device)) throw new Error('PC 또는 모바일 웹을 선택해 주세요.');
  syncDeviceRows(state);
  state.activeDevice = device;
  state.settings.device = device;
  state.results = state.deviceResults[device];
  return state;
}
export function allDeviceRows(state) {
  syncDeviceRows(state);
  return DEVICES.flatMap(device => state.deviceResults[device]);
}
export function parseKeywords(text) {
  return [...new Set(String(text).split(/[\n,;\t]+/).map(s => s.trim()).filter(Boolean))];
}
export function parsePlaceId(input) {
  const value = String(input || '').trim();
  if (/^\d{5,}$/.test(value)) return value;
  try {
    const url = new URL(value);
    if (!/(^|\.)naver\.com$/.test(url.hostname)) return '';
    return url.pathname.match(/\/(?:place|restaurant|cafe)\/(\d+)/)?.[1] || url.searchParams.get('placeId')?.match(/^\d+$/)?.[0] || '';
  } catch { return ''; }
}
export function stats(results) {
  const observed = results.filter(r => r.status === 'success');
  const organic = observed.filter(r => Number.isFinite(r.organicRank) && r.organicRank > 0);
  const ads = observed.filter(r => Number.isFinite(r.adRank) && r.adRank > 0);
  const top10 = organic.filter(r => r.organicRank <= 10).length;
  return {total: results.length, observed: observed.length, organic: organic.length, ads: ads.length,
    average: organic.length ? organic.reduce((s,r) => s+r.organicRank,0)/organic.length : null,
    adAverage: ads.length ? ads.reduce((s,r) => s+r.adRank,0)/ads.length : null,
    top10, top10Rate: observed.length ? top10/observed.length*100 : null,
    distribution: [organic.filter(r=>r.organicRank<=3).length, organic.filter(r=>r.organicRank>3&&r.organicRank<=10).length,
      organic.filter(r=>r.organicRank>10).length, observed.filter(r=>r.organicRank===null).length,
      results.filter(r=>r.status!=='success').length]};
}
export function csvEncode(rows) {
  return '\uFEFF' + rows.map(row => row.map(v => {
    let s = v == null ? '' : String(v);
    if (typeof v === 'string' && /^[=+\-@\t\r]/.test(s)) s = "'" + s;
    return /[",\n\r]/.test(s) ? '"'+s.replaceAll('"','""')+'"' : s;
  }).join(',')).join('\r\n');
}
export function csvParse(text) {
  const rows=[]; let row=[], value='', quoted=false;
  text=String(text).replace(/^\uFEFF/,'');
  for(let i=0;i<text.length;i++) {
    const c=text[i];
    if(c==='"') { if(quoted && text[i+1]==='"'){value+='"';i++;} else if(!quoted && value.length) throw new Error('CSV 따옴표 형식을 확인해 주세요.'); else quoted=!quoted; }
    else if(c===','&&!quoted){row.push(value);value='';}
    else if((c==='\n'||c==='\r')&&!quoted){if(c==='\r'&&text[i+1]==='\n')i++;row.push(value);if(row.some(Boolean))rows.push(row);row=[];value='';}
    else value+=c;
  }
  if(quoted)throw new Error('CSV의 닫히지 않은 따옴표를 확인해 주세요.');
  row.push(value);if(row.some(Boolean))rows.push(row);return rows;
}
const aliases={keyword:['keyword','키워드'],organicRank:['organicRank','일반순위','일반 순위'],adRank:['adRank','광고순위','광고 순위'],observedAt:['observedAt','측정시각','측정 시각'],placeId:['placeId','플레이스ID'],restaurant:['restaurant','매장명'],region:['region','지역'],maxDepth:['maxDepth','조회범위'],device:['device','기기'],source:['source','데이터출처'],originalSource:['originalSource','원본출처'],status:['status','상태'],error:['error','오류'],clientMode:['clientMode','조회방식'],locationMode:['locationMode','위치기준'],searchLatitude:['searchLatitude','검색위도'],searchLongitude:['searchLongitude','검색경도'],searchLocationLabel:['searchLocationLabel','검색지역'],locationApplied:['locationApplied','지역적용'],locationVerification:['locationVerification','적용확인']};
export function parseImport(text, filename) {
  let input;
  if(filename.endsWith('.json')){let json=JSON.parse(text); input=Array.isArray(json)?json:json.results;}
  else {const rows=csvParse(text);if(rows.length<2)throw new Error('헤더와 데이터가 필요합니다.');const headers=rows.shift().map(h=>h.trim());input=rows.map(row=>Object.fromEntries(headers.map((h,i)=>[h,row[i]??''])));}
  if(!Array.isArray(input)||!input.length)throw new Error('가져올 데이터가 없습니다.');
  if(input.length>2000)throw new Error('한 번에 최대 2,000행(기기별 1,000개)까지 가져올 수 있습니다.');
  const get=(r,key)=>{for(const a of aliases[key])if(Object.hasOwn(r,a))return r[a];};
  const rank=(v,line)=>{if(v==null||String(v).trim()===''||v==='미노출')return null;const n=Number(v);if(!Number.isInteger(n)||n<1||n>10000)throw new Error(`${line}행의 순위는 양의 정수 또는 빈칸이어야 합니다.`);return n;};
  const records = input.map((r,i)=>{const keyword=String(get(r,'keyword')??'').trim();if(!keyword||keyword.length>100)throw new Error(`${i+2}행의 키워드를 확인해 주세요.`);
    if(get(r,'organicRank')===undefined||get(r,'adRank')===undefined)throw new Error('organicRank, adRank 열이 모두 필요합니다.');
    const status=get(r,'status')||'success';if(!['success','error','pending'].includes(status))throw new Error(`${i+2}행의 status는 success, error, pending 중 하나여야 합니다.`);
    if((get(r,'source')==='demo'||get(r,'originalSource')==='demo'))throw new Error('샘플 데이터는 실제 측정 데이터로 가져올 수 없습니다. 샘플 체험 모드를 이용해 주세요.');
    const observedAt=String(get(r,'observedAt')??'');if(status!=='pending'&&(!observedAt||!Number.isFinite(Date.parse(observedAt))))throw new Error(`${i+2}행의 observedAt(측정 시각)이 필요합니다.`);
    if(observedAt&&!Number.isFinite(Date.parse(observedAt)))throw new Error(`${i+2}행의 측정 시각을 확인해 주세요.`);
    const maxDepth=Number(get(r,'maxDepth')??100);if(!Number.isInteger(maxDepth)||maxDepth<0||maxDepth>1000)throw new Error(`${i+2}행의 조회 범위를 확인해 주세요.`);
    const organicRank=rank(get(r,'organicRank'),i+2),adRank=rank(get(r,'adRank'),i+2);
    if(organicRank>maxDepth)throw new Error(`${i+2}행의 일반 순위가 조회 범위보다 큽니다.`);
    const device = String(get(r,'device') || 'desktop');
    if(!validDevice(device))throw new Error(`${i+2}행의 device는 desktop 또는 mobile이어야 합니다.`);
    const rawLocation=r.searchLocation||r.requestedSearchLocation;
    if(rawLocation!=null&&(typeof rawLocation!=='object'||Array.isArray(rawLocation)))throw new Error(`${i+2}행의 검색 지역 형식을 확인해 주세요.`);
    const latValue=get(r,'searchLatitude')??rawLocation?.latitude,lonValue=get(r,'searchLongitude')??rawLocation?.longitude;
    const hasLat=latValue!==undefined&&latValue!==null&&String(latValue).trim()!=='',hasLon=lonValue!==undefined&&lonValue!==null&&String(lonValue).trim()!=='';
    if((latValue!=null&&!['number','string'].includes(typeof latValue))||(lonValue!=null&&!['number','string'].includes(typeof lonValue)))throw new Error(`${i+2}행의 검색 좌표는 숫자여야 합니다.`);
    if(hasLat!==hasLon||typeof latValue==='boolean'||typeof lonValue==='boolean')throw new Error(`${i+2}행의 검색 위도와 경도를 함께 입력해 주세요.`);
    const searchLocation=hasLat?normalizeSearchLocation({latitude:Number(latValue),longitude:Number(lonValue),label:get(r,'searchLocationLabel')||rawLocation?.label}):null;
    const appliedValue=get(r,'locationApplied');let locationApplied=null;
    if(appliedValue!==undefined&&appliedValue!==null&&appliedValue!==''){if(appliedValue===true||appliedValue==='true')locationApplied=true;else if(appliedValue===false||appliedValue==='false')locationApplied=false;else throw new Error(`${i+2}행의 locationApplied는 true 또는 false여야 합니다.`);}
    if(get(r,'locationMode')==='custom-geolocation'&&!searchLocation)throw new Error(`${i+2}행의 지정 지역 좌표가 필요합니다.`);
    if(searchLocation&&status==='success'&&locationApplied!==true)throw new Error(`${i+2}행의 지정 지역 성공 기록은 locationApplied:true가 필요합니다.`);
    return {searchLocation:locationApplied===true?searchLocation:null,requestedSearchLocation:searchLocation,locationApplied,locationVerification:String(get(r,'locationVerification')||''),keyword,organicRank:status==='success'?organicRank:null,adRank:status==='success'?adRank:null,observedAt,placeId:String(get(r,'placeId')||''),restaurant:String(get(r,'restaurant')||''),region:String(get(r,'region')||'미지정'),maxDepth,device,source:'import',originalSource:String(get(r,'originalSource')||get(r,'source')||''),clientMode:String(get(r,'clientMode')||'imported-unspecified'),locationMode:String(get(r,'locationMode')||(searchLocation?'custom-geolocation':'unspecified')),status,error:status==='error'?String(get(r,'error')||'가져온 오류 결과'):undefined};});
  if(new Set(records.map(row=>row.keyword)).size>1000)throw new Error('서로 다른 키워드는 최대 1,000개까지 가져올 수 있습니다.');
  return records;
}
export function sampleState() {
  const keywords=['성수 맛집','성수 파스타','성수 데이트','서울숲 맛집','성수동 레스토랑','서울숲 파스타','성수 양식','성수 소개팅','성수 뇨끼','성수 스테이크','성수 점심','서울숲 데이트','성수 와인','성수동 파스타 맛집','성수 분위기 맛집','서울숲 레스토랑','성수 기념일','성수 이탈리안','뚝섬역 맛집','성수 저녁','성수 단체모임','성수 주차 맛집','성수 브런치','성수 가족외식'];
  const ranks=[3,2,5,4,7,1,8,6,3,9,5,6,4,7,8,9,10,10,12,17,22,28,null,null];
  const previous=[5,3,8,4,10,3,10,9,6,12,8,6,5,10,12,11,14,13,16,21,26,32,null,null];
  return {version:1,mode:'demo',restaurant:{name:'성수온실',placeId:'',url:'',address:'서울 성동구 · 이탈리안 레스토랑'},settings:{endpoint:'',region:'서울 성동구',maxDepth:100,device:'desktop'},results:keywords.map((keyword,i)=>({keyword,organicRank:ranks[i],adRank:i%3===0?i%4+1:null,previousRank:previous[i],placeId:'',observedAt:SAMPLE_DATE,device:'desktop',region:'서울 성동구',maxDepth:100,source:'demo',status:'success',history:Array.from({length:7},(_,d)=>({observedAt:new Date(Date.parse(SAMPLE_DATE)-(6-d)*86400000).toISOString(),organicRank:ranks[i]===null?null:ranks[i]+[6,5,7,4,3,2,0][d],adRank:i%3===0?Math.min(5,i%4+1+[1,1,0,1,0,0,0][d]):null}))})),reports:[]};
}
export function historySeries(results,days=7) {
  const buckets=new Map();
  for(const r of results){
    const history=[...(r.history||[]),...(r.status==='success'?[{observedAt:r.observedAt,organicRank:r.organicRank,adRank:r.adRank}]:[])].filter(p=>p.observedAt&&Number.isFinite(Date.parse(p.observedAt))).sort((a,b)=>Date.parse(a.observedAt)-Date.parse(b.observedAt));
    const unique=new Map();for(const point of history){const date=new Date(point.observedAt).toLocaleDateString('sv-SE',{timeZone:'Asia/Seoul'});unique.set(date,point);}
    for(const [date,p] of unique){if(!buckets.has(date))buckets.set(date,{date,organic:[],ads:[]});const b=buckets.get(date);if(Number.isFinite(p.organicRank))b.organic.push(p.organicRank);if(Number.isFinite(p.adRank))b.ads.push(p.adRank);}
  }
  const sorted=[...buckets.values()].sort((a,b)=>a.date.localeCompare(b.date));
  const last=sorted.at(-1)?.date;if(!last)return[];const cutoff=Date.parse(last)-(days-1)*86400000;
  return sorted.filter(b=>Date.parse(b.date)>=cutoff).map(b=>({date:b.date,organic:b.organic.length?b.organic.reduce((a,c)=>a+c,0)/b.organic.length:null,ads:b.ads.length?b.ads.reduce((a,c)=>a+c,0)/b.ads.length:null}));
}
