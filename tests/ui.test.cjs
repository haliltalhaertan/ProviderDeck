'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const core = require('../providerdeck/web/core.js');

test('formatUsd: bilinmeyen null, 0 değerinden görsel olarak ayrı', () => {
  assert.equal(core.formatUsd(null), 'Bilinmiyor');
  assert.equal(core.formatUsd(undefined), 'Bilinmiyor');
  assert.equal(core.formatUsd(0), '$0,00');
  assert.equal(core.formatUsd(1.48), '$1,48');
  assert.equal(core.formatUsd(0.007), '$0,007');
  assert.equal(core.formatUsd(0.000123), '$0,000123');
  assert.equal(core.formatUsd(-0.5), '-$0,50');
});

test('estimateCost: USD/1M fiyatla girdi+çıktı, önbellek isabeti indirimli', () => {
  const m = { billing: 'api', pricing: { input_per_million_usd: 2, output_per_million_usd: 8, cache_read_per_million_usd: 0.5, request_usd: null, unit: 'USD', variable: false } };
  const r = core.estimateCost(m, { input: 1000000, output: 500000, cachePercent: 50 });
  // 500k önbellek *0.5 + 500k taze *2 + 500k çıktı *8 = 0.25 + 1 + 4
  assert.equal(r.cost_usd, 5.25);
  assert.equal(r.kind, 'usd');
  assert.equal(r.warnings.length, 0);
});

test('estimateCost: bilinmeyen fiyat null kalır, sıfır sayılmaz; kredi birimi USD değildir', () => {
  const noOut = { billing: 'api', pricing: { input_per_million_usd: 1, output_per_million_usd: null, cache_read_per_million_usd: null, request_usd: null, unit: 'USD' } };
  const r1 = core.estimateCost(noOut, { input: 1000, output: 1000, cachePercent: 0 });
  assert.equal(r1.cost_usd, null);
  assert.equal(r1.kind, 'unknown');
  const credits = { billing: 'credits', pricing: { input_per_million_usd: 3, output_per_million_usd: 3, cache_read_per_million_usd: null, request_usd: null, unit: 'credits' } };
  const r2 = core.estimateCost(credits, { input: 1000, output: 1000, cachePercent: 0 });
  assert.equal(r2.cost_usd, null);
  assert.equal(r2.kind, 'credits');
  const unknownUnit = { billing: 'unknown', pricing: { input_per_million_usd: 1, output_per_million_usd: 1, unit: 'unknown' } };
  assert.equal(core.estimateCost(unknownUnit, { input: 1, output: 1 }).cost_usd, null);
  assert.equal(core.estimateCost({ billing: 'api' }, { input: 1, output: 1 }).cost_usd, null);
});

test('estimateCost: önbellek fiyatı bilinmiyorsa bedava sayılmaz, girdi fiyatıyla muhafazakâr hesap + uyarı', () => {
  const m = { billing: 'api', pricing: { input_per_million_usd: 2, output_per_million_usd: 0, cache_read_per_million_usd: null, request_usd: 0.01, unit: 'USD' } };
  const r = core.estimateCost(m, { input: 1000000, output: 0, cachePercent: 80 });
  assert.equal(r.cost_usd, 2.01); // tüm girdi tam fiyat + istek ücreti
  assert.match(r.warnings.join(' '), /önbellek/i);
  const r0 = core.estimateCost(m, { input: 1000000, output: 0, cachePercent: 0 });
  assert.equal(r0.warnings.length, 0, '%0 isabette önbellek fiyatı uyarısı gereksiz');
});

test('estimateCost: abonelik "Aboneliğe dahil" olarak ayrı tür, sınırsız USD 0 değil', () => {
  const sub = { billing: 'subscription', pricing: { input_per_million_usd: null, output_per_million_usd: null, unit: 'unknown' } };
  const r = core.estimateCost(sub, { input: 10, output: 10 });
  assert.equal(r.kind, 'subscription');
  assert.equal(r.cost_usd, null);
  assert.match(r.warnings.join(' '), /kota/i);
  const free = { billing: 'free', pricing: { input_per_million_usd: 0, output_per_million_usd: 0, cache_read_per_million_usd: null, request_usd: null, unit: 'USD' } };
  const f = core.estimateCost(free, { input: 1000, output: 1000, cachePercent: 50 });
  assert.equal(f.cost_usd, 0);
  assert.equal(f.kind, 'usd');
});

const P = {
  openrouter: { id: 'openrouter', name: 'OpenRouter', kind: 'api', access: { state: 'ready' } },
  voidai: { id: 'voidai', name: 'VoidAI', kind: 'api', access: { state: 'blocked', reason: 'Discord doğrulaması' } },
  nokey: { id: 'nokey', name: 'NoKey', kind: 'api', access: { state: 'missing_key' } },
  unk: { id: 'unk', name: 'Unk', kind: 'api', access: { state: 'unknown' } },
};
test('routeStatus: engelli model/sağlayıcı ve anahtarsız rota blocked; doğrulanmamış ayrı', () => {
  assert.equal(core.routeStatus({ availability: 'eligible' }, P.openrouter), 'usable');
  assert.equal(core.routeStatus({ availability: 'blocked' }, P.openrouter), 'blocked');
  assert.equal(core.routeStatus({ availability: 'eligible' }, P.voidai), 'blocked');
  assert.equal(core.routeStatus({ availability: 'eligible' }, P.nokey), 'blocked');
  assert.equal(core.routeStatus({ availability: 'unknown' }, P.openrouter), 'unknown');
  assert.equal(core.routeStatus({ availability: 'eligible' }, P.unk), 'unknown');
  assert.equal(core.routeStatus({ availability: 'eligible' }, undefined), 'unknown');
});

const M = [
  { id: 'deepseek/deepseek-v4.1-flash', provider_id: 'openrouter', name: 'DeepSeek V4.1 Flash', canonical_key: 'deepseek-v4.1-flash', tools: true, vision: false, billing: 'api', availability: 'eligible', pricing: { input_per_million_usd: 0.3, output_per_million_usd: 1.2, cache_read_per_million_usd: 0.007, unit: 'USD' } },
  { id: 'deepseek-v4.1-flash', provider_id: 'voidai', name: 'DeepSeek V4.1 Flash', canonical_key: 'deepseek-v4.1-flash', tools: null, vision: null, billing: 'credits', availability: 'eligible', pricing: { input_per_million_usd: 0.1, output_per_million_usd: 0.1, unit: 'credits' } },
  { id: 'deepseek-v4.1-flash-uncensored', provider_id: 'unk', name: 'DeepSeek V4.1 Flash Uncensored', canonical_key: 'deepseek-v4.1-flash-uncensored', tools: false, vision: false, billing: 'api', availability: 'eligible', pricing: { input_per_million_usd: 0.2, output_per_million_usd: 0.9, unit: 'USD' } },
  { id: 'gpt-6.1-sol', provider_id: 'openrouter', name: 'GPT 6.1 Sol', canonical_key: 'gpt-6.1-sol', tools: true, vision: true, billing: 'subscription', availability: 'eligible', pricing: { input_per_million_usd: null, output_per_million_usd: null, unit: 'unknown' } },
  { id: 'liquid/lfm-3-1.7b:free', provider_id: 'nokey', name: 'LFM 3 1.7B (free)', canonical_key: 'lfm-3-1.7b', tools: false, vision: false, billing: 'free', availability: 'blocked', pricing: { input_per_million_usd: 0, output_per_million_usd: 0, unit: 'USD' } },
];
test('filterModels: arama id/ad/sağlayıcıda, Türkçe büyük-küçük harf duyarsız', () => {
  const ids = (r) => r.map((m) => m.id + '@' + m.provider_id);
  assert.deepEqual(ids(core.filterModels(M, P, { query: 'UNCENSORED', routes: 'all' })), ['deepseek-v4.1-flash-uncensored@unk']);
  assert.equal(core.filterModels(M, P, { query: 'voidai', routes: 'all' }).length, 1);
  assert.equal(core.filterModels(M, P, { query: '  ', routes: 'all' }).length, M.length);
  assert.equal(core.filterModels(M, P, { query: 'deepseek flash', routes: 'all' }).length, 3, 'çok kelimeli arama: tüm terimler eşleşmeli');
});

test('filterModels: faturalama, araç, görsel ve rota filtreleri; varsayılan engellileri gizler', () => {
  assert.equal(core.filterModels(M, P, {}).some((m) => m.provider_id === 'voidai' || m.provider_id === 'nokey'), false, 'varsayılan: blocked gizli');
  assert.equal(core.filterModels(M, P, { routes: 'all' }).length, 5);
  assert.deepEqual(core.filterModels(M, P, { routes: 'usable' }).map((m) => m.id), ['deepseek/deepseek-v4.1-flash', 'gpt-6.1-sol']);
  assert.deepEqual(core.filterModels(M, P, { routes: 'all', billing: ['free', 'credits'] }).map((m) => m.provider_id), ['voidai', 'nokey']);
  assert.equal(core.filterModels(M, P, { routes: 'all', tools: true }).length, 2, 'tools null bilinmeyen => araç filtresinde elenir');
  assert.equal(core.filterModels(M, P, { routes: 'all', vision: true }).length, 1);
  assert.equal(core.filterModels(M, P, { routes: 'all', providerId: 'openrouter' }).length, 2);
});

test('groupByCanonical: yalnız birebir aynı canonical_key gruplanır; uncensored/flash varyantları ayrı', () => {
  const g = core.groupByCanonical(M);
  assert.equal(g.get('deepseek-v4.1-flash').length, 2);
  assert.equal(g.get('deepseek-v4.1-flash-uncensored').length, 1);
  assert.equal(g.size, 4);
  const noKey = core.groupByCanonical([{ id: 'X/Y', provider_id: 'a', canonical_key: null }, { id: 'x/y', provider_id: 'b', canonical_key: '' }]);
  assert.equal(noKey.size, 2, 'canonical_key yoksa tam id ile ayrı kalır, tahmini eşleme yok');
});

test('rankRoutes: kullanılabilir USD maliyeti artan; bilinmeyen/kredi/abonelik sonra; engelli en sonda ve asla kazanan değil', () => {
  const W = { input: 1000000, output: 100000, cachePercent: 0 };
  const ranked = core.rankRoutes(M, P, W);
  const order = ranked.map((r) => r.model.id + '@' + r.model.provider_id);
  // usable/unknown usd: uncensored@unk (0.29, unknown route) ve flash@openrouter (0.42, usable)
  assert.equal(order[0], 'deepseek/deepseek-v4.1-flash@openrouter', 'kullanılabilir rota bilinmeyen rotanın önünde');
  assert.equal(order[1], 'deepseek-v4.1-flash-uncensored@unk');
  assert.equal(order[2], 'gpt-6.1-sol@openrouter', 'abonelik USD sırasından sonra');
  assert.deepEqual(order.slice(3).sort(), ['deepseek-v4.1-flash@voidai', 'liquid/lfm-3-1.7b:free@nokey'].sort());
  assert.equal(ranked[0].status, 'usable');
  assert.equal(ranked[0].estimate.cost_usd, 0.42);
  const best = core.cheapestUsable(M, P, W);
  assert.equal(best.model.provider_id, 'openrouter');
  assert.equal(core.cheapestUsable(M.filter((m) => m.provider_id === 'nokey'), P, W), null, 'engelli bedava rota kazanmaz');
});

test('compareCanonical: aynı canonical rotalar sıralı, en ucuz yalnız kullanılabilir USD rotadan; fark yüzdesi', () => {
  const extra = { id: 'deepseek/deepseek-v4.1-flash', provider_id: 'unk', name: 'DeepSeek V4.1 Flash', canonical_key: 'deepseek-v4.1-flash', billing: 'api', availability: 'eligible', pricing: { input_per_million_usd: 0.1, output_per_million_usd: 0.4, unit: 'USD' } };
  const all = M.concat([extra]);
  const W = { input: 1000000, output: 0, cachePercent: 0 };
  const c = core.compareCanonical(all, P, 'deepseek-v4.1-flash', W);
  assert.equal(c.rows.length, 3);
  assert.equal(c.rows.every((r) => r.model.canonical_key === 'deepseek-v4.1-flash'), true);
  assert.equal(c.best.model.provider_id, 'openrouter', 'durumu bilinmeyen daha ucuz rota "en ucuz kullanılabilir" olmaz');
  assert.equal(c.rows[0].model.provider_id, 'openrouter');
  const unk = c.rows.find((r) => r.model.provider_id === 'unk');
  assert.equal(unk.deltaPct, -66.7, 'en iyiye göre fark (yuvarlanmış %)');
  assert.equal(c.rows.find((r) => r.model.provider_id === 'voidai').deltaPct, null, 'kredi farkı hesaplanmaz');
});

test('summarizeProviders: pozitif USD toplamı yalnız pozitif bilinen bakiyeler; kredi ayrı; sayımlar dinamik', () => {
  const provs = [
    { id: 'a', kind: 'api', balance_usd: 5.4587, credits: null, access: { state: 'ready' } },
    { id: 'b', kind: 'api', balance_usd: 0, credits: null, access: { state: 'blocked' } },
    { id: 'c', kind: 'api', balance_usd: -2, credits: null, access: { state: 'ready' } },
    { id: 'd', kind: 'api', balance_usd: null, credits: { value: 125000, unit: 'kredi' }, access: { state: 'blocked' } },
    { id: 'e', kind: 'subscription', balance_usd: null, credits: null, access: { state: 'ready' }, stale: true },
    { id: 'f', kind: 'cli', balance_usd: 1.5, credits: null, access: { state: 'unknown' } },
  ];
  const s = core.summarizeProviders(provs);
  assert.equal(s.total, 6);
  assert.equal(s.positiveUsd, 6.9587);
  assert.equal(s.positiveUsdCount, 2);
  assert.equal(s.unknownBalance, 2, 'null bakiye 0 sayılmaz, bilinmeyen sayılır');
  assert.deepEqual(s.credits, [{ provider_id: 'd', value: 125000, unit: 'kredi' }]);
  assert.deepEqual(s.byAccess, { ready: 3, blocked: 2, missing_key: 0, unknown: 1 });
  assert.equal(s.stale, 1);
  assert.equal(core.summarizeProviders([]).positiveUsd, 0);
});

test('viewState: yükleniyor/hata/yenileniyor/eski durumları ve yoklama aralığı', () => {
  const now = Date.parse('2026-11-13T12:00:00Z');
  assert.deepEqual(core.viewState(null, null, now), { phase: 'loading', pollMs: 2000, stale: false, ageMs: null, refreshError: null });
  const err = core.viewState(null, 'Ağ hatası', now);
  assert.equal(err.phase, 'error');
  assert.equal(err.pollMs, 30000);
  const fresh = { generated_at: '2026-11-13T11:50:00Z', providers: [], refresh: { running: false, error: null } };
  assert.deepEqual(core.viewState(fresh, null, now), { phase: 'ready', pollMs: 30000, stale: false, ageMs: 600000, refreshError: null });
  const running = Object.assign({}, fresh, { refresh: { running: true } });
  assert.equal(core.viewState(running, null, now).phase, 'refreshing');
  assert.equal(core.viewState(running, null, now).pollMs, 2000);
  const old = { generated_at: '2026-11-13T05:00:00Z', providers: [], refresh: { running: false, error: 'Zaman aşımı' } };
  const o = core.viewState(old, null, now);
  assert.equal(o.stale, true, '6 saatten eski veri eski sayılır');
  assert.equal(o.refreshError, 'Zaman aşımı');
  const offline = core.viewState(fresh, 'Sunucuya ulaşılamadı', now);
  assert.equal(offline.phase, 'offline', 'veri varken bağlantı hatası: son veri gösterilir');
  assert.equal(core.viewState({ generated_at: null, providers: [{ stale: true }] }, null, now).stale, true);
});

test('copyCommands: tam id korunur; Hermes alias komutu; CLI rotası için muse exec, sahte alias yok', () => {
  const api = core.copyCommands({ id: 'deepseek/deepseek-v4.1-flash', provider_id: 'openrouter', canonical_key: 'deepseek-v4.1-flash' }, { id: 'openrouter', kind: 'api' });
  assert.equal(api.id, 'deepseek/deepseek-v4.1-flash');
  assert.equal(api.kind, 'hermes');
  assert.equal(api.command, 'hermes config set model.aliases.deepseek-v4-1-flash openrouter/deepseek/deepseek-v4.1-flash');
  assert.equal(api.use, '/model deepseek-v4-1-flash');
  const cli = core.copyCommands({ id: 'muse-spark-1.3', provider_id: 'muse-code', canonical_key: 'muse-spark-1.3' }, { id: 'muse-code', kind: 'cli' });
  assert.equal(cli.kind, 'cli');
  assert.equal(cli.command, 'muse exec --model muse-spark-1.3 "<görev>"');
  assert.equal(cli.use, null);
  const odd = core.copyCommands({ id: "a b;rm'x", provider_id: 'p' }, { id: 'p', kind: 'api' });
  assert.equal(odd.command, "hermes config set model.aliases.a-b-rm-x 'p/a b;rm'\''x'", 'kabuk için güvenli tırnak');
});

test('describeWindow: kalan yüzde null "Bilinmiyor", %0 "tükendi"; sıfırlanma göreli etiketi', () => {
  const now = Date.parse('2026-11-13T12:00:00Z');
  const a = core.describeWindow({ label: 'Haftalık', remaining_percent: 90, reset_at: '2026-11-15T15:00:00Z', scope: 'account' }, now);
  assert.equal(a.percentText, '%90');
  assert.equal(a.level, 'ok');
  assert.equal(a.resetText, '2 gün 3 sa sonra');
  const z = core.describeWindow({ label: 'Oturum', remaining_percent: 0, reset_at: '2026-11-13T12:45:00Z' }, now);
  assert.equal(z.percentText, '%0');
  assert.equal(z.level, 'empty');
  assert.equal(z.resetText, '45 dk sonra');
  const u = core.describeWindow({ label: 'Aylık', remaining_percent: null, reset_at: null }, now);
  assert.equal(u.percentText, 'Bilinmiyor');
  assert.equal(u.level, 'unknown');
  assert.equal(u.resetText, 'Sıfırlanma bilinmiyor');
  assert.equal(core.describeWindow({ remaining_percent: 15, reset_at: '2026-11-13T11:00:00Z' }, now).level, 'low');
  assert.equal(core.describeWindow({ remaining_percent: 15, reset_at: '2026-11-13T11:00:00Z' }, now).resetText, 'Sıfırlanmış olmalı');
});

test('sortRows: sütuna göre sıralama; null değerler yön ne olursa olsun en sonda', () => {
  const rows = [
    { model: { name: 'b', provider_id: 'x', context_length: null, pricing: { input_per_million_usd: 2 } }, estimate: { cost_usd: null }, tier: 3 },
    { model: { name: 'a', provider_id: 'y', context_length: 1000, pricing: { input_per_million_usd: null } }, estimate: { cost_usd: 1 }, tier: 0 },
    { model: { name: 'c', provider_id: 'z', context_length: 5000, pricing: { input_per_million_usd: 1 } }, estimate: { cost_usd: 3 }, tier: 0 },
  ];
  const names = (r) => r.map((x) => x.model.name).join('');
  assert.equal(names(core.sortRows(rows, 'name', 'asc')), 'abc');
  assert.equal(names(core.sortRows(rows, 'name', 'desc')), 'cba');
  assert.equal(names(core.sortRows(rows, 'context', 'desc')), 'cab');
  assert.equal(names(core.sortRows(rows, 'context', 'asc')), 'acb');
  assert.equal(names(core.sortRows(rows, 'input', 'asc')), 'cba');
  assert.equal(names(core.sortRows(rows, 'cost', 'desc')), 'cab', 'tahmini maliyet: null sonda');
  assert.equal(names(core.sortRows(rows, 'rank', 'asc')), 'bac', 'rank: verilen sırayı korur');
  assert.equal(rows[0].model.name, 'b', 'girdi dizisi değiştirilmez');
});

test('normalizeSnapshot: eksik/yanlış tipte alanlar güvenli boş değerlere iner; providersById', () => {
  const n = core.normalizeSnapshot({ providers: 'x', models: [{ id: 'm', provider_id: 'p' }, null], errors: null, notes: ['n', 3] });
  assert.deepEqual(n.providers, []);
  assert.equal(n.models.length, 1);
  assert.deepEqual(n.errors, []);
  assert.deepEqual(n.notes, ['n']);
  assert.deepEqual(n.refresh, { running: false, started_at: null, finished_at: null, error: null });
  const n2 = core.normalizeSnapshot({ providers: [{ id: 'a', name: 'A' }, { name: 'idsiz' }], models: [] });
  assert.deepEqual(Object.keys(n2.providersById), ['a']);
  assert.equal(n2.providers[0].windows.length, 0);
  assert.equal(n2.providers[0].access.state, 'unknown');
  assert.throws(() => core.normalizeSnapshot('bad'), /geçersiz/i);
});

test('groupProviders: hazır → belirsiz → anahtar yok → engelli; boş grup yok; ad sırası Türkçe', () => {
  const g = core.groupProviders([
    { id: 'z', name: 'Zeta', access: { state: 'blocked' } },
    { id: 'c', name: 'Çağrı', access: { state: 'ready' } },
    { id: 'b', name: 'Beta', access: { state: 'ready' } },
    { id: 'u', name: 'Unk', access: null },
    { id: 'w', name: 'Weird', access: { state: 'garip' } },
  ]);
  assert.deepEqual(g.map((x) => x.state), ['ready', 'unknown', 'blocked']);
  assert.deepEqual(g[0].items.map((p) => p.id), ['b', 'c']);
  assert.deepEqual(g[1].items.map((p) => p.id), ['u', 'w']);
  assert.equal(g[0].label, 'Hazır');
});

test('formatContext/formatPrice: bağlam kısaltması, null bilinmiyor; kredi fiyatı USD işaretsiz', () => {
  assert.equal(core.formatContext(null), 'Bilinmiyor');
  assert.equal(core.formatContext(1048576), '1,05M');
  assert.equal(core.formatContext(128000), '128K');
  assert.equal(core.formatContext(512), '512');
  assert.equal(core.formatPrice(0.3, 'USD'), '$0,30');
  assert.equal(core.formatPrice(null, 'USD'), 'Bilinmiyor');
  assert.equal(core.formatPrice(2, 'credits'), '2 kr');
  assert.equal(core.formatPrice(2, 'unknown'), 'Bilinmiyor');
  assert.equal(core.formatTokens(1234567), '1.234.567');
});

test('estimateCost parts: taze girdi/önbellek/çıktı/istek kırılımı toplamla tutarlı', () => {
  const m = { billing: 'api', pricing: { input_per_million_usd: 2, output_per_million_usd: 8, cache_read_per_million_usd: 0.5, request_usd: 0.001, unit: 'USD' } };
  const r = core.estimateCost(m, { input: 1000000, output: 500000, cachePercent: 50 });
  assert.deepEqual(r.parts, { input: 1, cache: 0.25, output: 4, request: 0.001 });
  assert.equal(r.cost_usd, 5.251);
  assert.equal(r.tokens.cached, 500000);
  assert.equal(r.tokens.fresh, 500000);
  assert.equal(core.estimateCost({ billing: 'credits', pricing: { unit: 'credits' } }, {}).parts, null);
});

test('compareKeys: canonical anahtar dizini, çok rotalılar önce, kullanılabilir rota sayısı', () => {
  const extra = { id: 'deepseek/deepseek-v4.1-flash', provider_id: 'unk', name: 'DeepSeek V4.1 Flash', canonical_key: 'deepseek-v4.1-flash', availability: 'eligible' };
  const keys = core.compareKeys(M.concat([extra]), P);
  assert.equal(keys[0].key, 'deepseek-v4.1-flash');
  assert.equal(keys[0].routes, 3);
  assert.equal(keys[0].usable, 1);
  assert.deepEqual(keys[0].providers, ['openrouter', 'unk', 'voidai']);
  assert.equal(keys.length, 4);
  assert.equal(core.compareKeys(M, P, { multiOnly: true }).length, 1);
  assert.equal(core.compareKeys(M, P, { query: 'gpt' })[0].key, 'gpt-6.1-sol');
});

test('priceCell: abonelik rotasında 1M fiyatı "dahil" (dolar 0 değil); kredi/bilinmeyen ayrı', () => {
  const sub = { billing: 'subscription', pricing: { input_per_million_usd: 0, output_per_million_usd: 0, unit: 'USD' } };
  assert.deepEqual(core.priceCell(sub, 'input_per_million_usd'), { text: 'Dahil', kind: 'subscription' });
  const api = { billing: 'api', pricing: { input_per_million_usd: 0.3, cache_read_per_million_usd: null, unit: 'USD' } };
  assert.deepEqual(core.priceCell(api, 'input_per_million_usd'), { text: '$0,30', kind: 'usd' });
  assert.deepEqual(core.priceCell(api, 'cache_read_per_million_usd'), { text: 'Bilinmiyor', kind: 'unknown' });
  const free = { billing: 'free', pricing: { output_per_million_usd: 0, unit: 'USD' } };
  assert.deepEqual(core.priceCell(free, 'output_per_million_usd'), { text: '$0,00', kind: 'zero' });
  const cr = { billing: 'credits', pricing: { input_per_million_usd: 2, unit: 'credits' } };
  assert.deepEqual(core.priceCell(cr, 'input_per_million_usd'), { text: '2 kr', kind: 'credits' });
  assert.deepEqual(core.priceCell({}, 'input_per_million_usd'), { text: 'Bilinmiyor', kind: 'unknown' });
});

test('estimateCost: cacheWritePercent ve topupFeePercent desteklenir', () => {
  const m = {
    billing: 'api',
    pricing: { input_per_million_usd: 3, output_per_million_usd: 15, cache_read_per_million_usd: 0.3, cache_write_per_million_usd: 3.75, request_usd: 0, unit: 'USD' },
  };
  const r = core.estimateCost(m, { input: 1000000, output: 0, cachePercent: 50, cacheWritePercent: 25, topupFeePercent: 5 });
  assert.equal(r.kind, 'usd');
  assert.ok(Math.abs(r.cost_usd - (0.25 * 3 + 0.5 * 0.3 + 0.25 * 3.75) * 1.05) < 1e-6);
  assert.equal(r.parts.cache_write, 0.9375);
});
