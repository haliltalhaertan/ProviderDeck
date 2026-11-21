/* ProviderDeck core — saf (DOM'suz) fiyat, filtre, gruplama, sıralama ve durum mantığı.
   Tarayıcıda window.PDCore, Node testlerinde module.exports. Bağımlılık yok. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.PDCore = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  const api = {};
  const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
  api.isNum = isNum;

  /* USD biçimi: null/undefined => 'Bilinmiyor' (asla $0). Küçük değerlerde anlamlı hane korunur. */
  const NF = new Map();
  function trNumber(abs, digits, minDigits) {
    const k = digits + ':' + minDigits;
    let f = NF.get(k);
    if (!f) { f = new Intl.NumberFormat('tr-TR', { minimumFractionDigits: minDigits, maximumFractionDigits: digits }); NF.set(k, f); }
    return f.format(abs);
  }
  api.trNumber = trNumber;
  api.formatUsd = function formatUsd(v) {
    if (!isNum(v)) return 'Bilinmiyor';
    const abs = Math.abs(v);
    let digits = 2;
    if (abs > 0 && abs < 1) digits = Math.min(8, Math.max(2, Math.ceil(-Math.log10(abs)) + 2));
    return (v < 0 ? '-' : '') + '$' + trNumber(abs, digits, 2);
  };

  /* İş yükü maliyeti. workload: {input, output, cachePercent}. Fiyatlar USD / 1M token. */
  api.estimateCost = function estimateCost(model, workload) {
    const r10 = (x) => Math.round(x * 1e10) / 1e10;
    if (model && model.billing === 'subscription')
      return { cost_usd: null, kind: 'subscription', warnings: ['Aboneliğe dahil; kota/pencere sınırlıdır, sınırsız API değildir.'], parts: null, tokens: null };
    const p = (model && model.pricing) || null;
    if (!p) return { cost_usd: null, kind: 'unknown', warnings: ['Fiyat bilgisi yok.'], parts: null, tokens: null };
    if (p.unit === 'credits' || (model && model.billing === 'credits'))
      return { cost_usd: null, kind: 'credits', warnings: ['Kredi birimi USD ile karşılaştırılamaz.'], parts: null, tokens: null };
    if (p.unit !== 'USD') return { cost_usd: null, kind: 'unknown', warnings: ['Fiyat birimi bilinmiyor.'], parts: null, tokens: null };
    if (!isNum(p.input_per_million_usd) || !isNum(p.output_per_million_usd))
      return { cost_usd: null, kind: 'unknown', warnings: ['Girdi veya çıktı fiyatı bilinmiyor.'], parts: null, tokens: null };
    const input = Math.max(0, Number(workload && workload.input) || 0);
    const output = Math.max(0, Number(workload && workload.output) || 0);
    const pct = Math.min(100, Math.max(0, Number(workload && workload.cachePercent) || 0));
    const writePct = Math.min(100 - pct, Math.max(0, Number(workload && workload.cacheWritePercent) || 0));
    const topupPct = Math.min(100, Math.max(0, Number(workload && workload.topupFeePercent) || 0));
    const warnings = [];
    let cacheRate = p.cache_read_per_million_usd;
    if (!isNum(cacheRate)) {
      cacheRate = p.input_per_million_usd;
      if (pct > 0 && input > 0) warnings.push('Önbellek okuma fiyatı bilinmiyor; önbellekli girdi tam girdi fiyatıyla hesaplandı.');
    }
    let writeRate = p.cache_write_per_million_usd;
    if (!isNum(writeRate)) {
      writeRate = p.input_per_million_usd;
      if (writePct > 0 && input > 0) warnings.push('Önbellek yazma fiyatı bilinmiyor; önbellek yazma tam girdi fiyatıyla hesaplandı.');
    }
    const cached = input * pct / 100;
    const cacheWrite = input * writePct / 100;
    const fresh = Math.max(0, input - cached - cacheWrite);
    const requestFee = isNum(p.request_usd) ? p.request_usd : 0;
    let cost = (fresh * p.input_per_million_usd + cached * cacheRate + cacheWrite * writeRate + output * p.output_per_million_usd) / 1e6 + requestFee;
    if (topupPct > 0) {
      cost *= 1 + topupPct / 100;
      warnings.push('Yükleme komisyonu (+%' + topupPct + ') dahil edildi.');
    }
    if (p.variable) warnings.push('Fiyat alt sağlayıcıya göre değişebilir; katalog tahmini.');
    const parts = {
      input: r10(fresh * p.input_per_million_usd / 1e6),
      cache: r10(cached * cacheRate / 1e6),
      output: r10(output * p.output_per_million_usd / 1e6),
      request: requestFee,
    };
    if (cacheWrite > 0) parts.cache_write = r10(cacheWrite * writeRate / 1e6);
    const tokens = { fresh, cached, output };
    if (cacheWrite > 0) tokens.cache_write = cacheWrite;
    return { cost_usd: r10(cost), kind: 'usd', warnings, parts, tokens };
  };

  /* Rota durumu: 'usable' (model uygun + hesap hazır), 'blocked', 'unknown'.
     Hesap erişimi (account 200) başarılı çıkarım demek değildir; bu ayrı verification alanındadır. */
  api.routeStatus = function routeStatus(model, provider) {
    const acc = provider && provider.access && provider.access.state;
    if ((model && model.availability === 'blocked') || acc === 'blocked' || acc === 'missing_key') return 'blocked';
    if (model && model.availability === 'eligible' && acc === 'ready') return 'usable';
    return 'unknown';
  };

  const fold = (s) => String(s == null ? '' : s).toLocaleLowerCase('tr-TR').replace(/ı/g, 'i');
  api.fold = fold;
  /* filters: {query, billing:Set|array|null, tools:bool, vision:bool, routes:'all'|'hide_blocked'|'usable', providerId} */
  api.filterModels = function filterModels(models, providersById, filters) {
    const f = filters || {};
    const terms = fold(f.query).split(/\s+/).filter(Boolean);
    const routes = f.routes || 'hide_blocked';
    const billing = f.billing ? new Set(f.billing) : null;
    return (models || []).filter((m) => {
      const prov = providersById && providersById[m.provider_id];
      if (terms.length) {
        const hay = fold([m.id, m.name, m.canonical_key, m.provider_id, prov && prov.name].join(' '));
        if (!terms.every((t) => hay.includes(t))) return false;
      }
      const st = api.routeStatus(m, prov);
      if (routes === 'hide_blocked' && st === 'blocked') return false;
      if (routes === 'usable' && st !== 'usable') return false;
      if (billing && billing.size && !billing.has(m.billing || 'unknown')) return false;
      if (f.tools && m.tools !== true) return false;
      if (f.vision && m.vision !== true) return false;
      if (f.providerId && m.provider_id !== f.providerId) return false;
      return true;
    });
  };

  /* Aynı model karşılaştırması yalnızca backend'in verdiği canonical_key ile birebir eşleşir.
     Anahtar yoksa sağlayıcı+tam id ile tekil grup: tahmini model eşitlemesi yapılmaz. */
  api.canonicalOf = (m) => (m && m.canonical_key) ? String(m.canonical_key) : '__id:' + (m && m.provider_id) + ':' + (m && m.id);
  api.groupByCanonical = function groupByCanonical(models) {
    const g = new Map();
    for (const m of models || []) {
      const k = api.canonicalOf(m);
      if (!g.has(k)) g.set(k, []);
      g.get(k).push(m);
    }
    return g;
  };

  /* Sıralama katmanları: 0 kullanılabilir+USD, 1 durumu bilinmeyen+USD, 2 abonelik (kullanılabilir/bilinmeyen),
     3 fiyatı bilinmeyen veya kredi, 4 engelli. Katman içinde USD artan, sonra ad. */
  const KIND_TIER = { usd: 0, subscription: 2, unknown: 3, credits: 3 };
  api.routeTier = function routeTier(status, estimate) {
    if (status === 'blocked') return 4;
    const k = KIND_TIER[estimate.kind];
    if (k === 0) return status === 'usable' ? 0 : 1;
    return k == null ? 3 : k;
  };
  api.rankRoutes = function rankRoutes(models, providersById, workload) {
    const rows = (models || []).map((m) => {
      const status = api.routeStatus(m, providersById && providersById[m.provider_id]);
      const estimate = api.estimateCost(m, workload);
      return { model: m, status, estimate, tier: api.routeTier(status, estimate) };
    });
    rows.sort((a, b) => {
      if (a.tier !== b.tier) return a.tier - b.tier;
      const ca = a.estimate.cost_usd, cb = b.estimate.cost_usd;
      if (isNum(ca) && isNum(cb) && ca !== cb) return ca - cb;
      if (a.status !== b.status) return a.status === 'usable' ? -1 : b.status === 'usable' ? 1 : 0;
      return String(a.model.name || a.model.id).localeCompare(String(b.model.name || b.model.id), 'tr') ||
        String(a.model.provider_id).localeCompare(String(b.model.provider_id), 'tr');
    });
    return rows;
  };
  /* Varsayılan "en ucuz": yalnızca kullanılabilir ve USD maliyeti bilinen rota. */
  api.cheapestUsable = function cheapestUsable(models, providersById, workload) {
    const r = api.rankRoutes(models, providersById, workload).find((x) => x.tier === 0);
    return r || null;
  };

  /* Aynı canonical_key altındaki rotaları karşılaştırır. deltaPct: en ucuz kullanılabilir USD rotasına göre %. */
  api.compareCanonical = function compareCanonical(models, providersById, key, workload) {
    const same = (models || []).filter((m) => api.canonicalOf(m) === key);
    const rows = api.rankRoutes(same, providersById, workload);
    const best = rows.find((r) => r.tier === 0) || null;
    const base = best ? best.estimate.cost_usd : null;
    for (const r of rows) {
      const c = r.estimate.cost_usd;
      r.deltaPct = (isNum(base) && base > 0 && isNum(c)) ? Math.round(((c - base) / base) * 1000) / 10 : null;
      r.isBest = r === best;
    }
    return { key, rows, best };
  };

  /* Sağlayıcı özetini her zaman yükten hesaplar; önbellekteki toplamlara güvenmez. */
  api.summarizeProviders = function summarizeProviders(providers) {
    const s = { total: 0, positiveUsd: 0, positiveUsdCount: 0, unknownBalance: 0, credits: [], byAccess: { ready: 0, blocked: 0, missing_key: 0, unknown: 0 }, stale: 0 };
    let cents = 0;
    for (const p of providers || []) {
      s.total++;
      if (isNum(p.balance_usd)) {
        if (p.balance_usd > 0) { cents += Math.round(p.balance_usd * 1e9); s.positiveUsdCount++; }
      } else s.unknownBalance++;
      if (p.credits && isNum(p.credits.value)) s.credits.push({ provider_id: p.id, value: p.credits.value, unit: p.credits.unit || 'kredi' });
      const st = p.access && p.access.state;
      s.byAccess[Object.prototype.hasOwnProperty.call(s.byAccess, st) ? st : 'unknown']++;
      if (p.stale) s.stale++;
    }
    s.positiveUsd = cents / 1e9;
    return s;
  };

  api.STALE_MS = 6 * 3600 * 1000;
  /* Ekran durumu. snapshot: son başarılı /api/state; fetchError: son isteğin hatası (string|null). */
  api.viewState = function viewState(snapshot, fetchError, nowMs) {
    if (!snapshot) return { phase: fetchError ? 'error' : 'loading', pollMs: fetchError ? 30000 : 2000, stale: false, ageMs: null, refreshError: null };
    const refresh = snapshot.refresh || {};
    const gen = snapshot.generated_at ? Date.parse(snapshot.generated_at) : NaN;
    const ageMs = Number.isFinite(gen) ? Math.max(0, nowMs - gen) : null;
    const anyStale = (snapshot.providers || []).some((p) => p && p.stale);
    const stale = anyStale || ageMs == null || ageMs > api.STALE_MS;
    const running = !!refresh.running;
    const phase = fetchError ? 'offline' : running ? 'refreshing' : 'ready';
    return { phase, pollMs: running ? 2000 : 30000, stale, ageMs, refreshError: refresh.error || null };
  };

  const shq = (s) => /^[A-Za-z0-9._:\/@+=-]+$/.test(s) ? s : "'" + String(s).replace(/'/g, "'\''") + "'";
  api.shellQuote = shq;
  api.aliasName = (m) => (String((m && (m.canonical_key || m.id)) || 'model').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'model').slice(0, 48);
  /* Panoya kopyalanacak metinler. Hiçbir yapılandırma otomatik yazılmaz. */
  api.copyCommands = function copyCommands(model, provider) {
    const id = String(model && model.id);
    const pid = String((model && model.provider_id) || (provider && provider.id) || '');
    if (pid === 'muse-code' || (provider && provider.kind === 'cli')) {
      return { id, kind: 'cli', command: (pid === 'muse-code' ? 'muse exec --model ' : pid + ' --model ') + shq(id) + ' "<görev>"', use: null };
    }
    const alias = api.aliasName(model);
    const target = pid + '/' + id;
    return { id, kind: 'hermes', command: 'hermes config set model.aliases.' + alias + ' ' + shq(target), use: '/model ' + alias };
  };

  api.relDuration = function relDuration(ms) {
    const min = Math.round(ms / 60000);
    if (min < 60) return Math.max(1, min) + ' dk';
    const h = Math.floor(min / 60), d = Math.floor(h / 24);
    if (d >= 1) return d + ' gün' + (h % 24 ? ' ' + (h % 24) + ' sa' : '');
    return h + ' sa' + (min % 60 ? ' ' + (min % 60) + ' dk' : '');
  };
  /* Kota penceresi: null kalan => bilinmiyor (tükenmiş değil). */
  api.describeWindow = function describeWindow(w, nowMs) {
    const pct = w && w.remaining_percent;
    const known = isNum(pct);
    const level = !known ? 'unknown' : pct <= 0 ? 'empty' : pct < 25 ? 'low' : 'ok';
    const t = w && w.reset_at ? Date.parse(w.reset_at) : NaN;
    let resetText = 'Sıfırlanma bilinmiyor';
    if (Number.isFinite(t)) resetText = t <= nowMs ? 'Sıfırlanmış olmalı' : api.relDuration(t - nowMs) + ' sonra';
    return { label: (w && w.label) || 'Pencere', scope: (w && w.scope) || 'account', percent: known ? Math.max(0, Math.min(100, pct)) : null,
      percentText: known ? '%' + api.trNumber(pct, 1, 0) : 'Bilinmiyor', level, resetText };
  };

  const SORT_KEYS = {
    name: (r) => String(r.model.name || r.model.id || ''),
    provider: (r) => String(r.model.provider_id || ''),
    context: (r) => r.model.context_length,
    input: (r) => r.model.pricing && r.model.pricing.input_per_million_usd,
    output: (r) => r.model.pricing && r.model.pricing.output_per_million_usd,
    cache: (r) => r.model.pricing && r.model.pricing.cache_read_per_million_usd,
    cost: (r) => r.estimate && r.estimate.cost_usd,
  };
  api.sortRows = function sortRows(rows, key, dir) {
    const out = (rows || []).slice();
    const get = SORT_KEYS[key];
    if (!get) return out; // 'rank': rankRoutes sırası korunur
    const sign = dir === 'desc' ? -1 : 1;
    return out.map((r, i) => [r, i]).sort((A, B) => {
      const a = get(A[0]), b = get(B[0]);
      const an = a == null || a === '', bn = b == null || b === '';
      if (an || bn) return an && bn ? A[1] - B[1] : an ? 1 : -1;
      const c = typeof a === 'string' ? a.localeCompare(b, 'tr') : a - b;
      return c ? sign * c : A[1] - B[1];
    }).map((x) => x[0]);
  };

  const arr = (v) => Array.isArray(v) ? v : [];
  /* /api/state yükünü savunmacı biçimde normalleştirir; değerleri uydurmaz, yalnız yapı garanti eder. */
  api.normalizeSnapshot = function normalizeSnapshot(raw) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('Geçersiz durum yükü');
    const providers = arr(raw.providers).filter((p) => p && typeof p === 'object' && p.id != null).map((p) => Object.assign({}, p, {
      windows: arr(p.windows).filter((w) => w && typeof w === 'object'),
      access: (p.access && typeof p.access === 'object') ? p.access : { state: 'unknown', reason: null, evidence: null },
    }));
    const providersById = {};
    for (const p of providers) providersById[p.id] = p;
    const r = raw.refresh && typeof raw.refresh === 'object' ? raw.refresh : {};
    return {
      schema_version: raw.schema_version == null ? null : raw.schema_version,
      generated_at: raw.generated_at || null,
      providers, providersById,
      models: arr(raw.models).filter((m) => m && typeof m === 'object' && m.id != null),
      notes: arr(raw.notes).filter((s) => typeof s === 'string'),
      errors: arr(raw.errors).filter((e) => e && typeof e === 'object'),
      refresh: { running: !!r.running, offline: !!r.offline, started_at: r.started_at || null, finished_at: r.finished_at || null, error: r.error || null },
    };
  };

  api.ACCESS_LABEL = { ready: 'Hazır', unknown: 'Durumu belirsiz', missing_key: 'Anahtar yok', blocked: 'Engelli' };
  api.groupProviders = function groupProviders(providers) {
    const order = ['ready', 'unknown', 'missing_key', 'blocked'];
    const buckets = {}; order.forEach((s) => { buckets[s] = []; });
    for (const p of providers || []) {
      const st = p && p.access && p.access.state;
      buckets[order.includes(st) ? st : 'unknown'].push(p);
    }
    return order.filter((s) => buckets[s].length).map((s) => ({
      state: s, label: api.ACCESS_LABEL[s],
      items: buckets[s].sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id), 'tr')),
    }));
  };

  api.formatContext = function formatContext(v) {
    if (!isNum(v)) return 'Bilinmiyor';
    if (v >= 1e6) return trNumber(v / 1e6, 2, 0) + 'M';
    if (v >= 1e3) return trNumber(v / 1e3, 0, 0) + 'K';
    return trNumber(v, 0, 0);
  };
  /* Birim fiyatı (1M token başına). Kredi asla $ ile gösterilmez; bilinmeyen birim fiyat gösterilmez. */
  api.formatPrice = function formatPrice(v, unit) {
    if (!isNum(v)) return 'Bilinmiyor';
    if (unit === 'credits') return trNumber(v, 6, 0) + ' kr';
    if (unit !== 'USD') return 'Bilinmiyor';
    return api.formatUsd(v);
  };
  api.formatTokens = (v) => isNum(v) ? trNumber(v, 0, 0) : 'Bilinmiyor';

  /* Karşılaştırma dizini: her canonical anahtar için rota sayısı ve kullanılabilir rota sayısı. */
  api.compareKeys = function compareKeys(models, providersById, opts) {
    const o = opts || {};
    const terms = fold(o.query).split(/\s+/).filter(Boolean);
    const out = [];
    for (const [key, list] of api.groupByCanonical(models)) {
      const name = list[0].name || list[0].id;
      if (terms.length) {
        const hay = fold([key, name].concat(list.map((m) => m.id)).join(' '));
        if (!terms.every((t) => hay.includes(t))) continue;
      }
      if (o.multiOnly && list.length < 2) continue;
      out.push({ key, name, routes: list.length,
        usable: list.filter((m) => api.routeStatus(m, providersById && providersById[m.provider_id]) === 'usable').length,
        providers: Array.from(new Set(list.map((m) => String(m.provider_id)))).sort() });
    }
    out.sort((a, b) => b.routes - a.routes || String(a.name).localeCompare(String(b.name), 'tr'));
    return out;
  };

  /* Tablo hücresi için birim fiyat görünümü. Abonelik rotası API dolar fiyatı taşımaz: 'Dahil'. */
  api.priceCell = function priceCell(model, field) {
    const p = (model && model.pricing) || {};
    if (model && model.billing === 'subscription') return { text: 'Dahil', kind: 'subscription' };
    const v = p[field];
    const text = api.formatPrice(v, p.unit);
    if (text === 'Bilinmiyor') return { text, kind: 'unknown' };
    if (p.unit === 'credits') return { text, kind: 'credits' };
    return { text, kind: v === 0 ? 'zero' : 'usd' };
  };

  return api;
});
