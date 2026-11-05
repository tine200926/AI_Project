// Tigerair Taiwan's network, used to draw the destination picker. When the
// live route list (api-book.tigerairtw.com) answers, the page shows only the
// destinations it lists for the chosen origin, so a stale entry here does no
// harm; a route missing here can still be typed in as a code.

export const COUNTRIES = [
  { code: 'TW', name: '台灣' },
  { code: 'JP', name: '日本' },
  { code: 'KR', name: '韓國' },
  { code: 'HKMO', name: '港澳' },
  { code: 'TH', name: '泰國' },
  { code: 'VN', name: '越南' },
  { code: 'PH', name: '菲律賓' },
  { code: 'MY', name: '馬來西亞' },
  { code: 'OTHER', name: '其他' },
];

export const STATIONS = [
  ['TPE', '台北桃園', 'TW'],
  ['RMQ', '台中', 'TW'],
  ['KHH', '高雄', 'TW'],
  ['TNN', '台南', 'TW'],

  ['NRT', '東京成田', 'JP'],
  ['HND', '東京羽田', 'JP'],
  ['KIX', '大阪關西', 'JP'],
  ['NGO', '名古屋', 'JP'],
  ['FUK', '福岡', 'JP'],
  ['OKA', '沖繩', 'JP'],
  ['CTS', '札幌', 'JP'],
  ['HKD', '函館', 'JP'],
  ['SDJ', '仙台', 'JP'],
  ['AXT', '秋田', 'JP'],
  ['HNA', '岩手花卷', 'JP'],
  ['FKS', '福島', 'JP'],
  ['KIJ', '新潟', 'JP'],
  ['KMQ', '小松', 'JP'],
  ['OKJ', '岡山', 'JP'],
  ['YGJ', '米子', 'JP'],
  ['KCZ', '高知', 'JP'],
  ['HSG', '佐賀', 'JP'],
  ['OIT', '大分', 'JP'],
  ['KMJ', '熊本', 'JP'],
  ['KMI', '宮崎', 'JP'],
  ['ISG', '石垣', 'JP'],

  ['ICN', '首爾仁川', 'KR'],
  ['GMP', '首爾金浦', 'KR'],
  ['PUS', '釜山', 'KR'],
  ['CJU', '濟州', 'KR'],

  ['HKG', '香港', 'HKMO'],
  ['MFM', '澳門', 'HKMO'],

  ['BKK', '曼谷蘇凡納布', 'TH'],
  ['DMK', '曼谷廊曼', 'TH'],
  ['CNX', '清邁', 'TH'],
  ['HKT', '普吉', 'TH'],

  ['HAN', '河內', 'VN'],
  ['SGN', '胡志明市', 'VN'],
  ['DAD', '峴港', 'VN'],
  ['CXR', '芽莊', 'VN'],
  ['PQC', '富國島', 'VN'],

  ['MNL', '馬尼拉', 'PH'],
  ['CEB', '宿霧', 'PH'],

  ['KUL', '吉隆坡', 'MY'],
].map(([code, name, country]) => ({ code, name, country }));

const BY_CODE = new Map(STATIONS.map((s) => [s.code, s]));

export function stationInfo(code) {
  return BY_CODE.get(code) || null;
}

/** '大阪關西' for KIX, or the code itself when unknown. */
export function stationName(code, extraNames) {
  const s = BY_CODE.get(code);
  if (s) return s.name;
  const n = extraNames && extraNames.get(code);
  return n || code;
}
