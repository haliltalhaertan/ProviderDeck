/* ProviderDeck arayüzü — yalnız DOM düğümleri/textContent; sağlayıcı metinleri HTML olarak yorumlanmaz.
   Veri: GET /api/state (aynı köken). Yenileme: POST /api/refresh + X-ProviderDeck-Request: 1, gövdesiz. */
(function () {
  'use strict';
  const C = window.PDCore;
  const $ = (id) => document.getElementById(id);
  const LS = 'providerdeck.ui.v1';
  const PAGE = 200;

  const BILLING = { api: 'API', subscription: 'Abonelik', free: 'Ücretsiz', credits: 'Kredi', unknown: 'Bilinmiyor' };
  const KIND = { api: 'API cüzdanı', subscription: 'Abonelik', free: 'Ücretsiz katman', cli: 'CLI rotası' };
  const EVIDENCE = { account: 'Hesap uç noktası', inference: 'Gerçek yanıt üretimi', catalog: 'Model kataloğu', local: 'Yerel yapılandırma', audit: 'Denetim kaydı' };
  const VERIF = { ok: ['Yanıt üretimi doğrulandı', 'ok'], failed: ['Yanıt üretimi başarısız', 'bad'], untested: ['Yanıt üretimi test edilmedi', 'muted'] };
  const ROUTE = { usable: ['Kullanılabilir', 'ok'], blocked: ['Engelli', 'bad'], unknown: ['Erişim doğrulanmadı', 'muted'] };
  const PRESETS = { chat: { input: 3000, output: 600, cachePercent: 0 }, agent: { input: 60000, output: 2000, cachePercent: 70 }, long: { input: 400000, output: 4000, cachePercent: 50 } };

  /* ---------- durum ---------- */
  const saved = (() => { try { return JSON.parse(localStorage.getItem(LS)) || {}; } catch (_) { return {}; } })();
  const S = {
    snap: null, fetchError: null, groups: new Map(), counts: {},
    tab: 'monitor', theme: saved.theme || 'system',
    f: Object.assign({ query: '', billing: [], routes: 'hide_blocked', tools: false, vision: false, providerId: null }, saved.f || {}),
    w: Object.assign({}, PRESETS.agent, saved.w || {}),
    sort: saved.sort || { key: 'rank', dir: 'asc' },
    limit: PAGE, sel: null, cmpKey: null, cq: '', cmpMulti: true,
    pollTimer: null, refreshPending: false, nousBusy: false,
  };
  function persist() {
    try { localStorage.setItem(LS, JSON.stringify({ theme: S.theme, f: Object.assign({}, S.f, { providerId: null }), w: S.w, sort: S.sort })); } catch (_) { /* yok say */ }
  }

  /* ---------- DOM yardımcıları ---------- */
  function h(tag, props, ...kids) {
    const e = document.createElement(tag);
    if (props) for (const k of Object.keys(props)) {
      const v = props[k];
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else if (k === 'data') for (const d of Object.keys(v)) e.dataset[d] = v[d];
      else e.setAttribute(k, v === true ? '' : String(v));
    }
    for (const c of kids.flat(Infinity)) if (c != null && c !== false) e.append(c instanceof Node ? c : String(c));
    return e;
  }
  const tag = (text, tone, title) => h('span', { class: 'tag ' + (tone || ''), title: title || null }, text);
  const unk = (t) => h('span', { class: 'unknown' }, t || 'Bilinmiyor');
  const fmtDate = (iso) => { const t = Date.parse(iso); return Number.isFinite(t) ? new Intl.DateTimeFormat('tr-TR', { dateStyle: 'short', timeStyle: 'short' }).format(t) : 'Bilinmiyor'; };
  const ago = (ms) => ms == null ? 'zamanı bilinmiyor' : ms < 60000 ? 'az önce' : C.relDuration(ms) + ' önce';
  const safeUrl = (u) => { try { const x = new URL(u); return x.protocol === 'https:' ? x.href : null; } catch (_) { return null; } };
  const provOf = (m) => S.snap && S.snap.providersById[m.provider_id];
  const provName = (id) => { const p = S.snap && S.snap.providersById[id]; return (p && p.name) || String(id); };

  function usdNode(v) {
    if (!C.isNum(v)) return unk();
    return h('span', { class: v === 0 ? 'zero' : null }, C.formatUsd(v));
  }
  function priceNode(m, field) {
    const c = C.priceCell(m, field);
    if (c.kind === 'unknown') return h('span', { class: 'unknown', title: 'Fiyat bilinmiyor (0 değildir)' }, '—');
    if (c.kind === 'subscription') return h('span', { class: 'zero', title: 'Aboneliğe dahil; API dolar fiyatı değildir' }, c.text);
    return h('span', { class: c.kind === 'zero' ? 'zero' : null, title: c.kind === 'credits' ? 'Kredi birimi; USD değildir' : null }, c.text);
  }

  let toastT = null;
  function toast(msg) {
    const t = $('toast'); t.textContent = msg; t.hidden = false;
    clearTimeout(toastT); toastT = setTimeout(() => { t.hidden = true; }, 2600);
  }
  async function copy(text, label) {
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
      else {
        const ta = h('textarea', { 'aria-hidden': 'true', style: 'position:fixed;opacity:0;left:-9999px' }); ta.value = text;
        document.body.append(ta); ta.select(); const ok = document.execCommand('copy'); ta.remove();
        if (!ok) throw new Error('copy');
      }
      toast((label || 'Metin') + ' panoya kopyalandı');
    } catch (_) { toast('Kopyalanamadı; metni elle seçin'); }
  }

  /* ---------- tema ---------- */
  const mqLight = window.matchMedia('(prefers-color-scheme: light)');
  function applyTheme() {
    const resolved = S.theme === 'system' ? (mqLight.matches ? 'light' : 'dark') : S.theme;
    document.documentElement.dataset.theme = resolved;
    $('theme-label').textContent = { system: 'Sistem', dark: 'Koyu', light: 'Açık' }[S.theme];
  }
  mqLight.addEventListener('change', applyTheme);

  /* ---------- veri ---------- */
  async function load() {
    clearTimeout(S.pollTimer);
    try {
      const r = await fetch('/api/state', { cache: 'no-store', headers: { Accept: 'application/json' } });
      if (!r.ok) throw new Error('Sunucu ' + r.status + ' döndürdü');
      const raw = await r.json();
      const snap = C.normalizeSnapshot(raw);
      snap.nousFreeTest = raw.nous_free_test || null;
      const wasRunning = S.snap && S.snap.refresh.running;
      S.snap = snap; S.fetchError = null;
      S.groups = C.groupByCanonical(snap.models);
      S.counts = {};
      for (const m of snap.models) S.counts[m.provider_id] = (S.counts[m.provider_id] || 0) + 1;
      if (S.f.providerId && !snap.providersById[S.f.providerId]) S.f.providerId = null;
      if (wasRunning && !snap.refresh.running) toast(snap.refresh.error ? 'Yenileme hatayla bitti' : 'Yenileme tamamlandı');
      renderAll();
    } catch (e) {
      S.fetchError = (e && e.message && !/fetch/i.test(e.message)) ? e.message : 'Sunucuya ulaşılamadı';
      renderStatus();
      if (!S.snap) renderTable();
    }
    schedule();
  }
  function schedule() {
    const vs = C.viewState(S.snap, S.fetchError, Date.now());
    const test = S.snap && S.snap.nousFreeTest;
    const watchingNous = S.sel && S.sel.type === 'provider' && S.sel.id === 'nous' && test && (test.running || test.wait_seconds > 0);
    const ms = S.refreshPending ? 2000 : watchingNous ? Math.min(5000, vs.pollMs) : vs.pollMs;
    S.pollTimer = setTimeout(() => { if (!document.hidden || vs.phase === 'refreshing') load(); else schedule(); }, ms);
  }
  async function refresh() {
    if (S.snap && S.snap.refresh.running) return;
    const b = $('btn-refresh'); b.disabled = true; S.refreshPending = true;
    try {
      const r = await fetch('/api/refresh', { method: 'POST', cache: 'no-store', headers: { 'X-ProviderDeck-Request': '1' } });
      if (!r.ok && r.status !== 202 && r.status !== 409) throw new Error('Yenileme isteği reddedildi (' + r.status + ')');
      toast('Salt okunur hesap/katalog yenilemesi başladı');
    } catch (e) {
      toast(e && e.message && !/fetch/i.test(e.message) ? e.message : 'Yenileme başlatılamadı');
    } finally {
      setTimeout(() => { S.refreshPending = false; }, 4000);
      load();
    }
  }

  async function testNousFree() {
    if (S.nousBusy) return;
    S.nousBusy = true;
    renderDetail();
    try {
      const response = await fetch('/api/nous-free-test', { method: 'POST', cache: 'no-store', headers: { 'X-ProviderDeck-Request': '1' } });
      const result = await response.json();
      toast(result.message || 'Ücretsiz model testinin sonucu alınamadı.');
    } catch (_) {
      toast('Ücretsiz model testinin sonucu alınamadı.');
    } finally {
      S.nousBusy = false;
      await load();
    }
  }

  /* ---------- durum çubuğu ---------- */
  function renderStatus() {
    const vs = C.viewState(S.snap, S.fetchError, Date.now());
    const sync = $('sync'); sync.dataset.phase = vs.phase; sync.dataset.stale = vs.stale ? '1' : '0';
    const gen = S.snap && S.snap.generated_at;
    let text;
    if (vs.phase === 'loading') text = 'Yükleniyor…';
    else if (vs.phase === 'error') text = 'Bağlantı yok';
    else if (vs.phase === 'refreshing') {
      const st = S.snap.refresh.started_at ? Date.parse(S.snap.refresh.started_at) : NaN;
      const sec = Number.isFinite(st) ? Math.max(0, Math.round((Date.now() - st) / 1000)) : null;
      text = 'Yenileniyor…' + (sec == null ? '' : sec < 60 ? ' ' + sec + ' sn' : ' ' + C.relDuration(sec * 1000));
    }
    else text = (vs.stale ? 'Eski veri · ' : 'Güncel · ') + ago(vs.ageMs);
    if (vs.phase === 'offline') text = 'Çevrimdışı · son veri ' + ago(vs.ageMs);
    $('sync-text').textContent = text;
    sync.title = gen ? 'Anlık görüntü: ' + fmtDate(gen) : 'Anlık görüntü zamanı bilinmiyor';
    const b = $('btn-refresh');
    const running = vs.phase === 'refreshing';
    b.disabled = running || vs.phase === 'loading' || vs.phase === 'error';
    b.textContent = running ? 'Yenileniyor…' : 'Yenile';
    b.setAttribute('aria-busy', running ? 'true' : 'false');

    const ban = $('banner'); ban.replaceChildren();
    let tone = null;
    if (vs.phase === 'error') {
      tone = 'bad';
      ban.append(h('strong', null, 'Durum alınamadı.'), ' ' + S.fetchError + '. ProviderDeck yerel sunucusunun çalıştığından emin olun.',
        h('button', { class: 'btn btn-sm', type: 'button', onclick: load }, 'Tekrar dene'));
    } else if (vs.phase === 'offline') {
      tone = 'warn';
      ban.append(h('strong', null, 'Sunucuya ulaşılamıyor.'), ' Son alınan veri gösteriliyor (' + fmtDate(gen) + ').');
    } else if (vs.refreshError) {
      tone = 'warn';
      ban.append(h('strong', null, 'Son yenileme başarısız:'), ' ' + vs.refreshError);
    } else if (vs.stale && S.snap) {
      tone = 'info';
      const n = S.snap.providers.filter((p) => p.stale).length;
      ban.append(h('strong', null, 'Veri güncel olmayabilir.'), ' ' + (n ? n + ' sağlayıcı eski veya önbellekten. ' : '') + 'Yenile, bakiye ve kataloğu salt okunur olarak tekrar çeker; model çağrısı yapmaz.');
    }
    ban.hidden = !tone; if (tone) ban.dataset.tone = tone;
  }

  /* ---------- hesap paneli ---------- */
  function fundsNode(p) {
    if (C.isNum(p.balance_usd)) return usdNode(p.balance_usd);
    if (p.credits && C.isNum(p.credits.value)) return h('span', { title: 'USD değildir' }, C.trNumber(p.credits.value, 2, 0) + ' ' + (p.credits.unit || 'kredi'));
    if (p.kind === 'subscription' || p.kind === 'cli') return h('span', { class: 'zero', title: 'Dolar cüzdanı değil; kota pencereleri geçerli' }, 'Kota');
    return unk();
  }
  function windowNode(w) {
    const d = C.describeWindow(w, Date.now());
    const bar = h('span', { class: 'win-bar', role: 'img', 'aria-label': d.label + ' kalan ' + d.percentText });
    if (d.percent != null) bar.append(h('i', { style: 'width:' + d.percent + '%' }));
    return h('div', { class: 'win', data: { level: d.level } },
      h('span', { class: 'win-label', title: d.label + (d.scope === 'model' ? ' · model' : '') }, d.label + (d.scope === 'model' ? ' · model' : '')), bar,
      h('span', { class: 'win-val' + (d.level === 'unknown' ? ' unknown' : '') }, d.percentText),
      h('span', { class: 'win-reset' }, d.resetText));
  }
  function accessTag(p) {
    const st = (p.access && p.access.state) || 'unknown';
    const tone = { ready: 'ok', blocked: 'bad', missing_key: 'warn', unknown: 'muted' }[st] || 'muted';
    return tag(C.ACCESS_LABEL[st] || 'Durumu belirsiz', tone + ' dot', p.access && p.access.reason);
  }
  function renderAccounts() {
    const snap = S.snap, led = $('ledger'), list = $('provider-list');
    led.replaceChildren(); list.replaceChildren();
    if (!snap) {
      list.append(h('p', { class: 'hint' }, S.fetchError ? 'Hesap verisi yüklenemedi.' : 'Hesaplar yükleniyor…'));
      return;
    }
    const s = C.summarizeProviders(snap.providers);
    const row = (dt, dd, cls) => h('div', { class: cls || null, style: 'display:contents' }, h('dt', null, dt), h('dd', null, dd));
    led.append(
      row('Pozitif USD bakiye', C.formatUsd(s.positiveUsd), 'ledger-main'),
      row('Sağlayıcı', String(s.total)),
      row('Hazır / engelli', s.byAccess.ready + ' / ' + (s.byAccess.blocked + s.byAccess.missing_key)),
      row('Model rotası', C.trNumber(snap.models.length, 0, 0)));
    for (const c of s.credits) led.append(row(provName(c.provider_id), C.trNumber(c.value, 2, 0) + ' ' + c.unit));
    led.append(h('dd', { class: 'ledger-note' }, 'Toplam yalnız ' + s.positiveUsdCount + ' pozitif USD cüzdanını içerir; negatif, bilinmeyen (' + s.unknownBalance + ') ve kredi birimleri hariç. Abonelik kotası dolar değildir.'));

    $('provider-all').setAttribute('aria-pressed', S.f.providerId ? 'false' : 'true');
    for (const g of C.groupProviders(snap.providers)) {
      list.append(h('div', { class: 'group-label' }, h('span', null, g.label), h('span', null, String(g.items.length))));
      for (const p of g.items) {
        const n = S.counts[p.id] || 0;
        const btn = h('button', { class: 'prov', type: 'button', 'aria-pressed': S.f.providerId === p.id ? 'true' : 'false',
          onclick: () => selectProvider(p.id) },
          h('span', { class: 'prov-top' }, h('span', { class: 'prov-name' }, p.name || p.id), h('span', { class: 'prov-funds' }, fundsNode(p))),
          h('span', { class: 'prov-sub' }, accessTag(p), h('span', null, KIND[p.kind] || p.kind || ''), h('span', null, '· ' + n + ' model'),
            p.stale ? tag('eski', 'warn', 'Önbellekten / güncel değil') : null));
        if (p.windows.length) btn.append(h('span', { class: 'prov-windows' }, p.windows.map(windowNode)));
        list.append(btn);
      }
    }
  }
  function selectProvider(id) {
    S.f.providerId = S.f.providerId === id ? null : id;
    S.limit = PAGE;
    if (S.f.providerId) openDetail({ type: 'provider', id });
    closeAccountsDrawer();
    if (S.tab !== 'monitor') setTab('monitor');
    renderAccounts(); renderTable();
  }

  /* ---------- izleme tablosu ---------- */
  function currentRows() {
    const snap = S.snap;
    const models = C.filterModels(snap.models, snap.providersById, S.f);
    let rows = C.rankRoutes(models, snap.providersById, S.w);
    if (S.sort.key !== 'rank') rows = C.sortRows(rows, S.sort.key, S.sort.dir);
    return rows;
  }
  function costNode(r) {
    const e = r.estimate, m = r.model;
    if (e.kind === 'usd') {
      return h('span', null, h('span', { class: 'cost' + (e.cost_usd === 0 ? ' zero' : '') }, C.formatUsd(e.cost_usd)),
        e.warnings.length ? h('span', { class: 'cost-sub', title: e.warnings.join(' ') }, 'uyarılı tahmin') : (m.billing === 'free' ? h('span', { class: 'cost-sub' }, 'ücretsiz rota') : null));
    }
    if (e.kind === 'subscription') return h('span', null, h('span', { class: 'cost' }, 'Aboneliğe dahil'), h('span', { class: 'cost-sub' }, 'kota sınırlı'));
    if (e.kind === 'credits') return h('span', { title: 'Kredi birimi USD ile karşılaştırılamaz' }, h('span', { class: 'cost' }, 'Kredi'), h('span', { class: 'cost-sub' }, 'USD değil'));
    return unk();
  }
  function flagTags(m) {
    const out = [];
    if (m.billing && m.billing !== 'api') out.push(tag(BILLING[m.billing] || m.billing, m.billing === 'free' || m.billing === 'subscription' ? 'info' : 'muted'));
    if (m.tools === true) out.push(tag('Araç', 'muted'));
    if (m.vision === true) out.push(tag('Görsel', 'muted'));
    if (m.pricing && m.pricing.variable) out.push(tag('Değişken fiyat', 'warn', 'Alt sağlayıcıya göre değişebilir'));
    return out;
  }
  function renderTable() {
    const body = $('grid-body'), empty = $('grid-empty'), more = $('grid-more');
    body.replaceChildren(); empty.hidden = true; more.hidden = true;
    for (const th of document.querySelectorAll('#grid thead th')) {
      const b = th.querySelector('.sort');
      if (!b) continue;
      const on = b.dataset.sort === S.sort.key;
      if (on) th.setAttribute('aria-sort', S.sort.key === 'rank' ? 'ascending' : S.sort.dir === 'desc' ? 'descending' : 'ascending');
      else th.removeAttribute('aria-sort');
    }
    if (!S.snap) {
      if (S.fetchError) {
        empty.replaceChildren(h('strong', null, 'Model verisi yüklenemedi'), S.fetchError + '. Bağlantı her 30 saniyede yeniden denenir.');
        empty.hidden = false;
      } else {
        for (let i = 0; i < 8; i++) body.append(h('tr', { class: 'skel', 'aria-hidden': 'true' }, Array.from({ length: 8 }, (_, j) => h('td', null, h('i', { style: 'width:' + [70, 50, 40, 30, 35, 35, 35, 45][j] + '%' })))));
        $('result-count').textContent = 'Anlık görüntü yükleniyor…';
        $('result-best').textContent = '';
      }
      return;
    }
    const rows = currentRows();
    const total = S.snap.models.length;
    $('result-count').replaceChildren(h('strong', null, C.trNumber(rows.length, 0, 0)), ' / ' + C.trNumber(total, 0, 0) + ' rota',
      S.f.providerId ? ' · ' + provName(S.f.providerId) : '', S.f.routes === 'hide_blocked' ? ' · engelliler gizli' : '');
    const best = rows.find((r) => r.tier === 0 && r.estimate.cost_usd != null);
    const cheapest = rows.filter((r) => r.tier === 0).reduce((a, r) => (!a || r.estimate.cost_usd < a.estimate.cost_usd ? r : a), null);
    $('result-best').replaceChildren();
    if (cheapest) $('result-best').append('En düşük bilinen maliyet (kullanılabilir): ', h('strong', null, (cheapest.model.name || cheapest.model.id) + ' · ' + provName(cheapest.model.provider_id) + ' · ' + C.formatUsd(cheapest.estimate.cost_usd)), ' — farklı modeller kalite olarak eşdeğer değildir.');
    else if (best === undefined && rows.length) $('result-best').textContent = 'Bu filtrede maliyeti bilinen kullanılabilir USD rotası yok.';

    if (!rows.length) {
      empty.replaceChildren(h('strong', null, total ? 'Filtreyle eşleşen rota yok' : 'Anlık görüntüde model yok'),
        total ? 'Aramayı veya filtreleri gevşetin; engelli rotalar için “Tümü” seçeneğini deneyin.' : 'Yenile ile hesap kataloglarını salt okunur olarak çekin.');
      if (total) empty.append(h('div', { style: 'margin-top:10px' }, h('button', { class: 'btn btn-sm', type: 'button', onclick: resetFilters }, 'Filtreleri sıfırla')));
      empty.hidden = false;
      return;
    }
    const frag = document.createDocumentFragment();
    const shown = rows.slice(0, S.limit);
    let focusSet = false;
    for (const r of shown) {
      const m = r.model, p = provOf(m);
      const key = m.provider_id + '\u0000' + m.id;
      const sel = S.sel && S.sel.type === 'model' && S.sel.key === key;
      const rt = ROUTE[r.status];
      const tr = h('tr', { tabindex: sel || (!S.sel && !focusSet) ? '0' : '-1', 'aria-selected': sel ? 'true' : 'false', class: r.status === 'blocked' ? 'is-blocked' : null, data: { key } },
        h('td', { class: 'c-name' }, h('span', { class: 'm-name', title: m.name || m.id }, m.name || m.id), h('span', { class: 'm-id', title: m.id }, m.id),
          h('span', { class: 'm-flags' }, flagTags(m))),
        h('td', { class: 'c-prov' }, h('span', { class: 'p-name' }, (p && p.name) || m.provider_id), h('span', { class: 'p-kind' }, KIND[p && p.kind] || '')),
        h('td', { class: 'c-route' }, h('span', { class: 'tags' }, tag(rt[0], rt[1] + ' dot', r.status === 'blocked' ? ((p && p.access && p.access.reason) || 'Rota engelli') : null),
          m.verification && m.verification.state === 'ok' ? tag('Yanıt OK', 'ok') : m.verification && m.verification.state === 'failed' ? tag('Yanıt hatası', 'bad') : null)),
        h('td', { class: 'c-ctx num' }, C.isNum(m.context_length) ? C.formatContext(m.context_length) : unk('—')),
        h('td', { class: 'c-price num' }, priceNode(m, 'input_per_million_usd')),
        h('td', { class: 'c-price num' }, priceNode(m, 'cache_read_per_million_usd')),
        h('td', { class: 'c-price num' }, priceNode(m, 'output_per_million_usd')),
        h('td', { class: 'c-cost num' }, r === cheapest ? h('span', { class: 'best-mark' }, 'en düşük bilinen') : null, costNode(r)));
      if (sel || (!S.sel && !focusSet)) focusSet = true;
      frag.append(tr);
    }
    body.append(frag);
    if (rows.length > S.limit) {
      more.hidden = false;
      $('btn-more').textContent = 'Sonraki ' + Math.min(PAGE, rows.length - S.limit) + ' rotayı göster (' + C.trNumber(rows.length - S.limit, 0, 0) + ' kaldı)';
    }
  }
  function rowModel(key) {
    const i = key.indexOf('\u0000');
    const pid = key.slice(0, i), id = key.slice(i + 1);
    return S.snap.models.find((m) => String(m.provider_id) === pid && String(m.id) === id) || null;
  }
  function resetFilters() {
    S.f = { query: '', billing: [], routes: 'hide_blocked', tools: false, vision: false, providerId: null };
    syncControls(); S.limit = PAGE; persist(); renderAccounts(); renderTable();
  }

  /* ---------- ayrıntı paneli ---------- */
  function openDetail(sel) { S.sel = sel; renderDetail(); }
  function closeDetail() {
    const prev = S.sel; S.sel = null; renderDetail();
    if (prev && prev.type === 'model') { const tr = document.querySelector('#grid-body tr[data-key="' + CSS.escape(prev.key) + '"]'); if (tr) tr.focus(); }
    renderTable();
  }
  function kv(pairs) {
    return h('dl', { class: 'kv' }, pairs.filter(Boolean).map(([k, v, cls]) => h('div', { style: 'display:contents', class: cls || null }, h('dt', null, k), h('dd', null, v))));
  }
  function cmdRow(label, text, copyLabel) {
    return h('div', null, h('div', { class: 'cmd-label' }, label),
      h('div', { class: 'cmd' }, h('code', null, text), h('button', { class: 'btn btn-sm', type: 'button', onclick: () => copy(text, copyLabel) }, 'Kopyala')));
  }
  function modelDetail(m) {
    const p = provOf(m) || {};
    const status = C.routeStatus(m, p);
    const e = C.estimateCost(m, S.w);
    const pr = m.pricing || {};
    const out = [];
    out.push(h('div', null, h('h3', { class: 'd-title' }, m.name || m.id),
      h('div', { class: 'd-id' }, h('code', null, m.id)),
      h('div', { class: 'm-flags', style: 'margin-top:6px' }, tag(ROUTE[status][0], ROUTE[status][1] + ' dot'), tag(BILLING[m.billing] || 'Bilinmiyor', 'muted'),
        tag((VERIF[m.verification && m.verification.state] || VERIF.untested)[0], (VERIF[m.verification && m.verification.state] || VERIF.untested)[1]))));

    const cc = C.copyCommands(m, p);
    const cmds = h('div', { class: 'cmdbox' }, cmdRow('Tam API model kimliği', cc.id, 'Model kimliği'));
    if (cc.kind === 'cli') cmds.append(cmdRow('CLI komutu (Hermes sağlayıcı takma adı değildir)', cc.command, 'CLI komutu'));
    else { cmds.append(cmdRow('Hermes takma adı tanımla', cc.command, 'Takma ad komutu')); cmds.append(cmdRow('Oturumda kullan', cc.use, 'Model komutu')); }
    cmds.append(h('p', { class: 'hint' }, 'Yalnız panoya kopyalar; yapılandırma veya varsayılan model değiştirilmez.'));
    if (status === 'blocked') cmds.append(h('p', { class: 'note bad' }, 'Bu rota şu an engelli: ' + ((p.access && p.access.reason) || 'erişim yok') + '.'));
    out.push(h('section', { class: 'd-sec' }, h('h3', null, 'Kopyala'), cmds));

    const unit = pr.unit;
    out.push(h('section', { class: 'd-sec' }, h('h3', null, 'Fiyat / 1M token' + (unit === 'credits' ? ' (kredi)' : '')), kv([
      ['Girdi', priceNode(m, 'input_per_million_usd')],
      ['Önbellek okuma', priceNode(m, 'cache_read_per_million_usd')],
      ['Çıktı', priceNode(m, 'output_per_million_usd')],
      C.isNum(pr.request_usd) && pr.request_usd > 0 ? ['İstek başı', usdNode(pr.request_usd)] : null,
      ['Bağlam', C.isNum(m.context_length) ? C.formatTokens(m.context_length) + ' token' : unk()],
      ['Araç / görsel', (m.tools === true ? 'var' : m.tools === false ? 'yok' : '?') + ' / ' + (m.vision === true ? 'var' : m.vision === false ? 'yok' : '?')],
    ])));

    const est = [];
    if (e.kind === 'usd') {
      est.push(kv([
        ['Taze girdi · ' + C.formatTokens(e.tokens.fresh), usdNode(e.parts.input)],
        ['Önbellekli · ' + C.formatTokens(e.tokens.cached), usdNode(e.parts.cache)],
        ['Çıktı · ' + C.formatTokens(e.tokens.output), usdNode(e.parts.output)],
        e.parts.request ? ['İstek ücreti', usdNode(e.parts.request)] : null,
        ['Tahmini istek', h('strong', null, C.formatUsd(e.cost_usd)), 'total'],
      ]));
    } else if (e.kind === 'subscription') est.push(h('p', { class: 'note' }, 'Aboneliğe dahil: marjinal dolar maliyeti yok, ancak kota pencereleri ve rota sınırları geçerli. Sınırsız API değildir.'));
    else if (e.kind === 'credits') est.push(h('p', { class: 'note warn' }, 'Fiyat kredi birimiyle; USD ile karşılaştırılamaz ve en ucuz USD sıralamasına girmez.'));
    else est.push(h('p', { class: 'note' }, 'Maliyet hesaplanamıyor: ' + e.warnings.join(' ')));
    for (const w of e.warnings) if (e.kind === 'usd') est.push(h('p', { class: 'note warn' }, w));
    out.push(h('section', { class: 'd-sec' }, h('h3', null, 'İş yükü tahmini'), est));

    const sibs = (S.groups.get(C.canonicalOf(m)) || []);
    if (sibs.length > 1) {
      const ranked = C.compareCanonical(S.snap.models, S.snap.providersById, C.canonicalOf(m), S.w);
      out.push(h('section', { class: 'd-sec' }, h('h3', null, 'Aynı model, diğer rotalar'),
        h('div', { class: 'siblings' }, ranked.rows.map((r) => h('div', { class: 'sib' },
          h('span', null, provName(r.model.provider_id) + (r.model === m ? ' (bu)' : ''), ' ', r.status !== 'usable' ? tag(ROUTE[r.status][0], ROUTE[r.status][1]) : null),
          h('span', { class: 'num' }, costNode(r))))),
        h('button', { class: 'btn btn-sm', type: 'button', style: 'margin-top:8px', onclick: () => openCompare(C.canonicalOf(m)) }, 'Karşılaştırmada aç')));
    }

    const notes = [];
    if (m.warning) notes.push(h('p', { class: 'note warn' }, m.warning));
    if (m.verification && m.verification.reason) notes.push(h('p', { class: 'note' }, m.verification.reason + (m.verification.at ? ' (' + fmtDate(m.verification.at) + ')' : '')));
    if (p.kind === 'cli') notes.push(h('p', { class: 'note' }, 'CLI aboneliği rotası: yerel Meta API modelleri bu nedenle ücretsiz sayılmaz.'));
    out.push(h('section', { class: 'd-sec' }, h('h3', null, 'Kaynak ve uyarılar'), h('div', { class: 'notes' }, notes,
      kv([['Sağlayıcı', h('button', { class: 'btn btn-sm btn-ghost', type: 'button', onclick: () => openDetail({ type: 'provider', id: m.provider_id }) }, p.name || m.provider_id)],
        ['Güncelleme', m.updated_at ? fmtDate(m.updated_at) : unk()],
        ['Kaynak', sourceNode(m.source)]]))));
    return out;
  }
  function sourceNode(src) {
    if (!src) return unk();
    const u = safeUrl(src);
    return u ? h('a', { href: u, target: '_blank', rel: 'noopener noreferrer' }, new URL(u).host + new URL(u).pathname) : h('span', null, src === 'audit' ? 'Denetim kaydı' : src);
  }
  function nousTestSection(p) {
    const test = S.snap.nousFreeTest;
    if (!test) return null;
    const noKey = p.access.state === 'missing_key';
    const busy = S.nousBusy || test.running;
    const waiting = test.wait_seconds > 0;
    const result = test.result;
    return h('section', { class: 'd-sec', 'aria-labelledby': 'nous-free-test-title' },
      h('h3', { id: 'nous-free-test-title' }, 'Ücretsiz model testi'),
      h('p', { class: 'note' }, h('code', null, test.model), ' · Tek kısa istek; ücretli modele geçilmez. Bakiye ve kota doğrulanmaz.'),
      h('button', { id: 'btn-nous-free-test', type: 'button', class: 'btn btn-sm', disabled: !test.enabled || noKey || busy || waiting,
        onclick: testNousFree }, busy ? 'Test sürüyor…' : 'Ücretsiz modeli test et'),
      h('p', { class: 'hint' }, !test.enabled ? 'Çevrimdışı modda kapalı.' : noKey ? 'NOUS_API_KEY ekledikten sonra Yenile’ye bas.' : waiting ? 'Tekrar test için kısa bir süre bekle.' : ''),
      h('p', { id: 'nous-free-result', role: 'status', class: result ? 'note' + (result.code === 'ok' ? '' : ' warn') : 'hint' },
        result ? result.message + ' · ' + fmtDate(result.at) : 'Henüz test yapılmadı.'));
  }
  function providerDetail(p) {
    const out = [];
    const errs = S.snap.errors.filter((x) => x.provider_id === p.id);
    out.push(h('div', null, h('h3', { class: 'd-title' }, p.name || p.id), h('div', { class: 'd-id' }, h('code', null, p.id)),
      h('div', { class: 'm-flags', style: 'margin-top:6px' }, accessTag(p), tag(KIND[p.kind] || p.kind || 'Tür bilinmiyor', 'muted'), p.stale ? tag('Eski veri', 'warn') : null)));
    if (p.id === 'nous') out.push(nousTestSection(p));
    out.push(h('section', { class: 'd-sec' }, h('h3', null, 'Bakiye'), kv([
      ['USD cüzdanı', C.isNum(p.balance_usd) ? usdNode(p.balance_usd) : unk(p.kind === 'subscription' || p.kind === 'cli' ? 'Yok / bilinmiyor' : 'Bilinmiyor')],
      p.credits ? ['Kredi (USD değil)', C.isNum(p.credits.value) ? C.trNumber(p.credits.value, 2, 0) + ' ' + (p.credits.unit || 'kredi') : unk()] : null,
      ['Okunma', p.fetched_at ? fmtDate(p.fetched_at) : unk()],
    ])));
    out.push(h('section', { class: 'd-sec' }, h('h3', null, 'Kota pencereleri'),
      p.windows.length ? h('div', { class: 'prov-windows' }, p.windows.map(windowNode)) : h('p', { class: 'hint' }, p.kind === 'subscription' || p.kind === 'cli' ? 'Sayısal kota sunucu tarafından bildirilmedi (tükenmiş anlamına gelmez).' : 'Kota penceresi yok.')));
    const a = p.access || {};
    out.push(h('section', { class: 'd-sec' }, h('h3', null, 'Erişim kanıtı'), h('div', { class: 'notes' },
      a.reason ? h('p', { class: 'note' + (a.state === 'blocked' || a.state === 'missing_key' ? ' bad' : '') }, a.reason) : null,
      kv([['Kanıt türü', EVIDENCE[a.evidence] || unk()],
        ['Son yanıt üretimi', p.last_inference ? (p.last_inference.status === 'ok' ? 'Başarılı' : p.last_inference.status === 'failed' ? 'Başarısız' : 'Bilinmiyor') + ' · ' + fmtDate(p.last_inference.at) : unk('Kayıt yok')],
        p.last_inference ? ['Model', h('code', null, String(p.last_inference.model || '?'))] : null,
        ['Katalogdaki model', String(S.counts[p.id] || 0) + (C.isNum(p.models_count) && p.models_count !== (S.counts[p.id] || 0) ? ' (sağlayıcı bildirimi ' + p.models_count + ')' : '')]]),
      a.evidence !== 'inference' ? h('p', { class: 'hint' }, 'Hesap veya katalog yanıtı, model yanıt üretiminin çalıştığını kanıtlamaz.') : null)));
    if (p.note || errs.length) out.push(h('section', { class: 'd-sec' }, h('h3', null, 'Notlar ve sorunlar'), h('div', { class: 'notes' },
      p.note ? h('p', { class: 'note' }, p.note) : null, errs.map((x) => h('p', { class: 'note bad' }, x.message || 'Bilinmeyen hata')))));
    const dash = safeUrl(p.dashboard_url);
    out.push(h('div', { style: 'display:flex;gap:8px;flex-wrap:wrap' },
      h('button', { class: 'btn btn-sm', type: 'button', onclick: () => { if (S.f.providerId !== p.id) selectProvider(p.id); } }, S.f.providerId === p.id ? 'Modeller filtrelendi' : 'Modellerini göster'),
      dash ? h('a', { class: 'btn btn-sm btn-ghost', href: dash, target: '_blank', rel: 'noopener noreferrer' }, 'Resmî panel / belgeler ↗') : null));
    return out;
  }
  function renderDetail() {
    const d = $('detail'), body = $('detail-body'), shell = document.querySelector('.shell');
    let content = null, title = 'Ayrıntı';
    if (S.sel && S.snap) {
      if (S.sel.type === 'model') { const m = rowModel(S.sel.key); if (m) { content = modelDetail(m); title = 'Model rotası'; } }
      else { const p = S.snap.providersById[S.sel.id]; if (p) { content = providerDetail(p); title = 'Sağlayıcı hesabı'; } }
    }
    if (!content) { S.sel = null; d.hidden = true; shell.classList.remove('has-detail'); updateScrim(); return; }
    $('detail-title').textContent = title;
    body.replaceChildren(...content.filter(Boolean));
    d.hidden = false; shell.classList.add('has-detail'); updateScrim();
  }

  /* ---------- karşılaştırma ---------- */
  function openCompare(key) { S.cmpKey = key; setTab('compare'); }
  function renderCompare() {
    const list = $('cmp-list'), pane = $('cmp-pane');
    list.replaceChildren(); pane.replaceChildren();
    if (!S.snap) { pane.append(h('p', { class: 'hint' }, S.fetchError ? 'Veri yüklenemedi.' : 'Yükleniyor…')); return; }
    const keys = C.compareKeys(S.snap.models, S.snap.providersById, { query: S.cq, multiOnly: S.cmpMulti });
    $('cmp-index-count').textContent = C.trNumber(keys.length, 0, 0) + ' model anahtarı' + (S.cmpMulti ? ' · birden çok rotalı' : '');
    if (!S.cmpKey && keys.length) S.cmpKey = keys[0].key;
    const shown = keys.slice(0, 300);
    for (const k of shown) {
      list.append(h('li', { role: 'option', tabindex: k.key === S.cmpKey ? '0' : '-1', 'aria-selected': k.key === S.cmpKey ? 'true' : 'false', data: { key: k.key } },
        h('span', { class: 'k-name' }, k.name), h('span', { class: 'k-count', title: 'kullanılabilir / toplam rota' }, k.usable + '/' + k.routes),
        h('span', { class: 'k-key' }, k.key.startsWith('__id:') ? 'eşsiz kimlik' : k.key)));
    }
    if (!list.querySelector('[tabindex="0"]') && list.firstChild) list.firstChild.tabIndex = 0;
    if (keys.length > shown.length) list.append(h('li', { class: 'hint', 'aria-disabled': 'true' }, 'İlk 300 gösteriliyor; aramayı daraltın.'));
    if (!keys.length) list.append(h('li', { class: 'hint', 'aria-disabled': 'true' }, 'Eşleşen model yok.'));

    if (!S.cmpKey || !S.groups.has(S.cmpKey)) { pane.append(h('div', { class: 'empty' }, h('strong', null, 'Bir model seçin'), 'Yalnızca aynı canonical anahtara sahip rotalar yan yana gösterilir.')); return; }
    const c = C.compareCanonical(S.snap.models, S.snap.providersById, S.cmpKey, S.w);
    const first = c.rows[0].model;
    const named = c.rows.find((r) => r.model.name && r.model.name !== r.model.id) || c.rows[0];
    pane.append(h('div', { class: 'cmp-head' }, h('h2', null, named.model.name || first.id),
      h('p', null, 'Anahtar ', h('code', null, S.cmpKey.startsWith('__id:') ? first.id : S.cmpKey), ' · ' + c.rows.length + ' rota · iş yükü ' + C.formatTokens(S.w.input) + ' girdi / ' + C.formatTokens(S.w.output) + ' çıktı / %' + S.w.cachePercent + ' önbellek'),
      h('p', null, 'Yalnız birebir aynı model anahtarı eşleşir; uncensored, flash, pro ve tarihli varyantlar ayrı modeldir. En ucuz yalnız kullanılabilir ve USD fiyatı bilinen rotadan seçilir.')));
    const tb = h('tbody');
    for (const r of c.rows) {
      const m = r.model, pr = m.pricing || {}, rt = ROUTE[r.status];
      const delta = r.isBest ? h('span', { class: 'delta-down' }, 'en ucuz') : r.deltaPct == null ? unk('—') : h('span', { class: r.deltaPct > 0 ? 'delta-up' : 'delta-down' }, (r.deltaPct > 0 ? '+' : '') + C.trNumber(r.deltaPct, 1, 0) + '%');
      const cc = C.copyCommands(m, provOf(m) || {});
      tb.append(h('tr', { class: (r.isBest ? 'is-best ' : '') + (r.status === 'blocked' ? 'is-blocked' : '') },
        h('td', { 'data-label': 'Sağlayıcı' }, h('button', { class: 'btn btn-sm btn-ghost', type: 'button', onclick: () => { openDetail({ type: 'model', key: m.provider_id + '\u0000' + m.id }); } }, provName(m.provider_id))),
        h('td', { 'data-label': 'Rota' }, h('span', { class: 'tags' }, tag(rt[0], rt[1] + ' dot'), ' ', tag(BILLING[m.billing] || 'Bilinmiyor', 'muted'))),
        h('td', { 'data-label': 'Girdi /1M', class: 'num' }, priceNode(m, 'input_per_million_usd')),
        h('td', { 'data-label': 'Önbellek /1M', class: 'num' }, priceNode(m, 'cache_read_per_million_usd')),
        h('td', { 'data-label': 'Çıktı /1M', class: 'num' }, priceNode(m, 'output_per_million_usd')),
        h('td', { 'data-label': 'Tahmini istek', class: 'num' }, costNode(r)),
        h('td', { 'data-label': 'Fark', class: 'num' }, delta),
        h('td', { 'data-label': 'Kopyala', class: 'cmp-copy' }, h('button', { class: 'btn btn-sm', type: 'button', title: m.id, onclick: () => copy(m.id, 'Model kimliği') }, 'Kimlik'),
          ' ', h('button', { class: 'btn btn-sm', type: 'button', title: cc.command, onclick: () => copy(cc.command, cc.kind === 'cli' ? 'CLI komutu' : 'Takma ad komutu') }, cc.kind === 'cli' ? 'CLI' : 'Takma ad'))));
    }
    pane.append(h('div', { style: 'overflow:auto' }, h('table', { class: 'cmp-table' },
      h('thead', null, h('tr', null, ['Sağlayıcı', 'Rota', 'Girdi /1M', 'Önbellek /1M', 'Çıktı /1M', 'Tahmini istek', 'Fark', 'Kopyala'].map((t, i) => h('th', { scope: 'col', class: i >= 2 && i <= 6 ? 'num' : null }, t)))), tb)));
    if (!c.best) pane.append(h('p', { class: 'note warn' }, 'Bu model için kullanılabilir ve USD fiyatı bilinen rota yok; sıralama yalnız bilgi amaçlıdır.'));
    const base = S.cmpKey;
    const near = base.startsWith('__id:') ? [] : Array.from(S.groups.keys()).filter((k) => k !== base && !k.startsWith('__id:') && (k.startsWith(base + '-') || k.startsWith(base + ':') || base.startsWith(k + '-'))).slice(0, 8);
    if (near.length) pane.append(h('div', null, h('p', { class: 'hint' }, 'Benzer adlı ama farklı modeller (eşdeğer sayılmaz):'),
      h('div', { class: 'seg-row', style: 'margin-top:6px' }, near.map((k) => h('button', { class: 'chip', type: 'button', onclick: () => { S.cmpKey = k; renderCompare(); } }, k)))));
  }

  /* ---------- sekmeler, çekmeceler ---------- */
  function setTab(t) {
    S.tab = t;
    for (const [id, name] of [['tab-monitor', 'monitor'], ['tab-compare', 'compare']]) {
      const b = $(id), on = name === t;
      b.setAttribute('aria-selected', on ? 'true' : 'false'); b.tabIndex = on ? 0 : -1;
      $('view-' + name).hidden = !on;
    }
    history.replaceState(null, '', t === 'compare' ? '#karsilastir' : '#');
    if (t === 'compare') renderCompare();
  }
  const narrow = window.matchMedia('(max-width: 900px)');
  const midw = window.matchMedia('(max-width: 1640px)');
  function closeAccountsDrawer() { $('accounts').classList.remove('open'); $('btn-accounts').setAttribute('aria-expanded', 'false'); updateScrim(); }
  function updateScrim() {
    const acc = narrow.matches && $('accounts').classList.contains('open');
    const det = midw.matches && !$('detail').hidden;
    $('scrim').hidden = !(acc || det);
  }

  /* ---------- kontroller ---------- */
  function syncControls() {
    $('q').value = S.f.query;
    $('routes').value = S.f.routes;
    $('f-tools').checked = !!S.f.tools; $('f-vision').checked = !!S.f.vision;
    for (const b of document.querySelectorAll('[data-billing]')) b.setAttribute('aria-pressed', S.f.billing.includes(b.dataset.billing) ? 'true' : 'false');
    $('w-in').value = S.w.input; $('w-out').value = S.w.output; $('w-cache').value = S.w.cachePercent;
    $('w-cache-out').textContent = '%' + S.w.cachePercent;
    for (const b of document.querySelectorAll('[data-preset]')) {
      const p = PRESETS[b.dataset.preset];
      b.setAttribute('aria-pressed', p.input === S.w.input && p.output === S.w.output && p.cachePercent === S.w.cachePercent ? 'true' : 'false');
    }
  }
  function onFilters() { S.limit = PAGE; persist(); renderTable(); }
  function onWorkload() {
    const num = (v, max) => Math.max(0, Math.min(max, Math.round(Number(v) || 0)));
    S.w = { input: num($('w-in').value, 1e8), output: num($('w-out').value, 1e8), cachePercent: num($('w-cache').value, 100) };
    $('w-cache-out').textContent = '%' + S.w.cachePercent;
    for (const b of document.querySelectorAll('[data-preset]')) {
      const p = PRESETS[b.dataset.preset];
      b.setAttribute('aria-pressed', p.input === S.w.input && p.output === S.w.output && p.cachePercent === S.w.cachePercent ? 'true' : 'false');
    }
    persist(); renderTable(); if (S.sel) renderDetail(); if (S.tab === 'compare') renderCompare();
  }
  let qT = null;
  function bind() {
    $('q').addEventListener('input', (e) => { clearTimeout(qT); qT = setTimeout(() => { S.f.query = e.target.value; onFilters(); }, 120); });
    $('filters').addEventListener('submit', (e) => e.preventDefault());
    $('routes').addEventListener('change', (e) => { S.f.routes = e.target.value; onFilters(); });
    $('f-tools').addEventListener('change', (e) => { S.f.tools = e.target.checked; onFilters(); });
    $('f-vision').addEventListener('change', (e) => { S.f.vision = e.target.checked; onFilters(); });
    for (const b of document.querySelectorAll('[data-billing]')) b.addEventListener('click', () => {
      const v = b.dataset.billing; const set = new Set(S.f.billing);
      set.has(v) ? set.delete(v) : set.add(v); S.f.billing = Array.from(set);
      b.setAttribute('aria-pressed', set.has(v) ? 'true' : 'false'); onFilters();
    });
    for (const id of ['w-in', 'w-out', 'w-cache']) $(id).addEventListener('input', onWorkload);
    for (const b of document.querySelectorAll('[data-preset]')) b.addEventListener('click', () => { S.w = Object.assign({}, PRESETS[b.dataset.preset]); syncControls(); onWorkload(); });

    for (const b of document.querySelectorAll('#grid .sort')) b.addEventListener('click', () => {
      const k = b.dataset.sort;
      if (k === 'rank') S.sort = { key: 'rank', dir: 'asc' };
      else if (S.sort.key === k) S.sort = { key: k, dir: S.sort.dir === 'asc' ? 'desc' : 'asc' };
      else S.sort = { key: k, dir: k === 'context' ? 'desc' : 'asc' };
      persist(); renderTable();
    });
    $('btn-more').addEventListener('click', () => { S.limit += PAGE; renderTable(); });

    const body = $('grid-body');
    const activate = (tr) => { if (!tr || !tr.dataset.key) return; openDetail({ type: 'model', key: tr.dataset.key }); markRow(tr); };
    body.addEventListener('click', (e) => activate(e.target.closest('tr')));
    body.addEventListener('keydown', (e) => {
      const tr = e.target.closest('tr'); if (!tr) return;
      let next = null;
      if (e.key === 'ArrowDown') next = tr.nextElementSibling;
      else if (e.key === 'ArrowUp') next = tr.previousElementSibling;
      else if (e.key === 'Home') next = body.firstElementChild;
      else if (e.key === 'End') next = body.lastElementChild;
      else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(tr); return; }
      else if (e.key === 'c' && !e.ctrlKey && !e.metaKey) { const m = rowModel(tr.dataset.key); if (m) copy(m.id, 'Model kimliği'); return; }
      if (next) { e.preventDefault(); tr.tabIndex = -1; next.tabIndex = 0; next.focus(); }
    });
    function markRow(tr) {
      for (const r of body.querySelectorAll('tr[aria-selected="true"]')) { r.setAttribute('aria-selected', 'false'); r.tabIndex = -1; }
      tr.setAttribute('aria-selected', 'true'); tr.tabIndex = 0;
    }

    const cl = $('cmp-list');
    const pick = (li) => { if (!li || !li.dataset.key) return; S.cmpKey = li.dataset.key; renderCompare(); const n = cl.querySelector('[data-key="' + CSS.escape(S.cmpKey) + '"]'); if (n) n.focus(); };
    cl.addEventListener('click', (e) => pick(e.target.closest('li')));
    cl.addEventListener('keydown', (e) => {
      const li = e.target.closest('li'); if (!li) return;
      let n = null;
      if (e.key === 'ArrowDown') n = li.nextElementSibling; else if (e.key === 'ArrowUp') n = li.previousElementSibling;
      else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(li); return; }
      if (n && n.dataset.key) { e.preventDefault(); li.tabIndex = -1; n.tabIndex = 0; n.focus(); }
    });
    $('cq').addEventListener('input', (e) => { S.cq = e.target.value; S.cmpKey = null; renderCompare(); });
    $('c-multi').addEventListener('change', (e) => { S.cmpMulti = e.target.checked; S.cmpKey = null; renderCompare(); });

    const tabs = [$('tab-monitor'), $('tab-compare')];
    tabs.forEach((t, i) => {
      t.addEventListener('click', () => setTab(i ? 'compare' : 'monitor'));
      t.addEventListener('keydown', (e) => {
        if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
        const j = (i + 1) % 2; setTab(j ? 'compare' : 'monitor'); tabs[j].focus();
      });
    });

    $('btn-refresh').addEventListener('click', refresh);
    $('btn-theme').addEventListener('click', () => { S.theme = { system: 'dark', dark: 'light', light: 'system' }[S.theme]; applyTheme(); persist(); });
    $('provider-all').addEventListener('click', () => { S.f.providerId = null; if (S.sel && S.sel.type === 'provider') S.sel = null; renderDetail(); renderAccounts(); onFilters(); closeAccountsDrawer(); });
    $('btn-detail-close').addEventListener('click', closeDetail);
    $('btn-accounts').addEventListener('click', () => { const o = $('accounts').classList.toggle('open'); $('btn-accounts').setAttribute('aria-expanded', o ? 'true' : 'false'); updateScrim(); if (o) $('btn-accounts-close').focus(); });
    $('btn-accounts-close').addEventListener('click', () => { closeAccountsDrawer(); $('btn-accounts').focus(); });
    $('scrim').addEventListener('click', () => { closeAccountsDrawer(); if (midw.matches && S.sel) closeDetail(); });
    narrow.addEventListener('change', updateScrim); midw.addEventListener('change', updateScrim);

    document.addEventListener('keydown', (e) => {
      const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement && document.activeElement.tagName);
      if (e.key === '/' && !typing) { e.preventDefault(); if (S.tab === 'compare') $('cq').focus(); else $('q').focus(); }
      else if (e.key === 'Escape') {
        if ($('accounts').classList.contains('open')) closeAccountsDrawer();
        else if (S.sel) closeDetail();
        else if (typing && document.activeElement.type === 'search' && document.activeElement.value) { document.activeElement.value = ''; document.activeElement.dispatchEvent(new Event('input')); }
      }
    });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
    let lastStatusTick = Date.now();
    setInterval(() => {
      if (!S.snap) return;
      const nowMs = Date.now();
      if (S.snap.refresh.running || nowMs - lastStatusTick >= 30000) {
        lastStatusTick = nowMs;
        renderStatus();
      }
    }, 1000);
  }

  function renderFoot() {
    const f = $('foot'); f.replaceChildren();
    const snap = S.snap;
    f.append(h('span', null, 'Yenileme salt okunur · ücretsiz model testi elle başlatılır'));
    if (!snap) return;
    f.append(h('span', null, 'Anlık görüntü: ' + (snap.generated_at ? fmtDate(snap.generated_at) : 'bilinmiyor')));
    if (snap.refresh.finished_at) f.append(h('span', null, 'Son yenileme: ' + fmtDate(snap.refresh.finished_at)));
    if (snap.notes.length) f.append(h('details', null, h('summary', null, 'Fiyat notları (' + snap.notes.length + ')'), h('ul', null, snap.notes.map((n) => h('li', null, n)))));
    if (snap.errors.length) f.append(h('details', null, h('summary', null, 'Sağlayıcı sorunları (' + snap.errors.length + ')'),
      h('ul', null, snap.errors.map((x) => h('li', null, provName(x.provider_id) + ': ' + (x.message || 'bilinmeyen hata'))))));
  }

  function renderAll() {
    renderStatus(); renderAccounts(); renderTable(); renderDetail(); renderFoot();
    if (S.tab === 'compare') renderCompare();
  }

  /* ---------- başlat ---------- */
  applyTheme(); syncControls(); bind();
  if (location.hash === '#karsilastir') setTab('compare');
  renderStatus(); renderAccounts(); renderTable(); renderFoot();
  load();
})();
