/* Roblowe SPA – vanilla JS, žiadny build step. Nikdy innerHTML s dátami. */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  let MODE = document.body.dataset.mode;

  // -- DOM helpers ------------------------------------------------------------
  function el(tag, attrs = {}, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'html') e.innerHTML = v; // len statické SVG ikony
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else if (k === 'style' && typeof v === 'object') for (const [p, val] of Object.entries(v)) e.style.setProperty(p, val);
      else if (k === 'dataset') Object.assign(e.dataset, v);
      else e.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) if (c != null && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c)));
    return e;
  }
  function mount(root, ...children) { root.replaceChildren(...children.flat().filter(c => c != null && c !== false)); }

  let toastT;
  function toast(msg, err = false) {
    const t = $('#toast'); t.textContent = msg; t.className = 'toast' + (err ? ' err' : ''); t.hidden = false;
    clearTimeout(toastT); toastT = setTimeout(() => { t.hidden = true; }, err ? 5000 : 2800);
  }

  function openSheet(title, body, actions) {
    const wrap = $('#sheet');
    const sheet = el('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true' }, el('h2', {}, title), body,
      el('div', { class: 'row' }, ...actions));
    mount(wrap, sheet); wrap.hidden = false;
    wrap.onclick = (e) => { if (e.target === wrap) closeSheet(); };
    const first = sheet.querySelector('input:not([type=hidden]),textarea'); if (first) first.focus();
  }
  function closeSheet() { $('#sheet').hidden = true; mount($('#sheet')); }
  function confirmSheet(title, text, { danger = false, typed = null, ok = 'Potvrdiť' } = {}) {
    return new Promise((resolve) => {
      const input = typed ? el('input', { placeholder: `Napíš ${typed}`, autocomplete: 'off' }) : null;
      const okBtn = el('button', { class: 'btn ' + (danger ? 'danger' : 'primary'), disabled: !!typed,
        onclick: () => { closeSheet(); resolve(true); } }, ok);
      if (input) input.addEventListener('input', () => { okBtn.disabled = input.value.trim() !== typed; });
      openSheet(title, el('div', {}, el('p', {}, text), input), [
        el('button', { class: 'btn', onclick: () => { closeSheet(); resolve(false); } }, 'Zrušiť'), okBtn]);
    });
  }

  async function api(path, opts = {}) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), opts.timeout || 45000);
    let r;
    try {
      r = await fetch('/api' + path, {
        method: opts.method || 'GET', headers: { 'X-Requested-With': 'roblowe', ...(opts.body ? { 'Content-Type': 'application/json' } : {}) },
        body: opts.body ? JSON.stringify(opts.body) : undefined, signal: ctl.signal,
      });
    } catch (e) {
      throw new Error(e.name === 'AbortError' ? 'Server neodpovedá. Skús obnoviť stránku o chvíľu.' : 'Spojenie so serverom zlyhalo.');
    } finally { clearTimeout(timer); }
    if (r.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname + location.hash); throw new Error('401'); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `Chyba ${r.status}`);
    return data;
  }

  // -- formátovanie ----------------------------------------------------------------
  const fmtUsd = (v, d = 0) => v == null ? '–' : new Intl.NumberFormat('sk-SK', { style: 'currency', currency: 'USD', maximumFractionDigits: d, minimumFractionDigits: d }).format(v);
  const fmtNum = (v, d = 2) => v == null ? '–' : Number(v).toLocaleString('sk-SK', { maximumFractionDigits: d, minimumFractionDigits: d });
  const fmtPct = (v, d = 2) => v == null ? '–' : (v > 0 ? '+' : '') + fmtNum(v, d) + ' %';
  const fmtTime = (iso) => iso ? new Date(iso).toLocaleString('sk-SK', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '–';
  const fmtClock = (iso) => iso ? new Date(iso).toLocaleTimeString('sk-SK', { hour: '2-digit', minute: '2-digit' }) : '–';
  const signCls = (v) => v > 0 ? 'up' : v < 0 ? 'down' : '';
  const ACTION = { buy: 'kúpa', sell: 'predaj', hold: 'držím', skip: 'preskočené', entry: 'vstup', exit: 'výstup', flatten: 'zatvorenie' };

  // -- navigácia ------------------------------------------------------------------------
  const ICON = {
    home: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/></svg>',
    list: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 12h16M4 18h10"/></svg>',
    news: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 5h12a2 2 0 0 1 2 2v12H6a2 2 0 0 1-2-2zM18 9h2v8a2 2 0 0 1-2 2M8 9h6M8 13h6"/></svg>',
    cog: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
  };
  const ROUTES = [
    { hash: '#/', label: 'Prehľad', icon: 'home', view: viewOverview },
    { hash: '#/obchody', label: 'Obchody', icon: 'list', view: viewTrades },
    { hash: '#/spravy', label: 'Správy', icon: 'news', view: viewNews },
    { hash: '#/nastavenia', label: 'Nastavenia', icon: 'cog', view: viewSettings },
  ];
  function renderNav() {
    const cur = location.hash || '#/';
    mount($('#tabbar'), ROUTES.map(r => el('a', { href: r.hash, class: cur === r.hash ? 'active' : '' }, el('span', { html: ICON[r.icon] }), r.label)));
    mount($('#deskNav'), ROUTES.map(r => el('a', { href: r.hash, class: cur === r.hash ? 'active' : '' }, r.label)));
  }

  let me = null;
  let viewAccount = null;
  function loadViewAccount() {
    let seenCur = null;
    try { viewAccount = localStorage.getItem('roblowe:account'); seenCur = localStorage.getItem('roblowe:account-cur'); } catch { viewAccount = null; }
    // po prepnutí brokera ukáž najprv nový aktuálny účet
    if (me && seenCur !== me.account) { viewAccount = me.account; try { localStorage.setItem('roblowe:account-cur', me.account); localStorage.setItem('roblowe:account', me.account); } catch {} }
    if (!me || !me.accounts.some(a => a.key === viewAccount)) viewAccount = me ? me.account : null;
  }
  function setViewAccount(k) { viewAccount = k; try { localStorage.setItem('roblowe:account', k); } catch {} route(); }
  function accountPicker() {
    if (!me || me.accounts.length < 2) return null;
    return el('div', { class: 'row' }, el('span', { class: 'muted' }, 'Účet:'),
      el('select', { style: { width: 'auto' }, onchange: (e) => setViewAccount(e.target.value) },
        ...me.accounts.map(a => el('option', { value: a.key, selected: a.key === viewAccount }, a.label + (a.current ? ' (aktuálny)' : '')))));
  }
  async function loadMe() {
    me = await api('/me');
    loadViewAccount();
    MODE = me.mode;
    const badge = $('#modeBadge'); badge.textContent = me.mode; badge.className = 'mode mode-' + me.mode;
    document.body.dataset.mode = me.mode;
    const sw = el('button', { class: 'switch', role: 'switch', 'aria-checked': String(me.agent_enabled), title: 'Agent zapnutý/vypnutý',
      onclick: toggleAgent });
    mount($('#topRight'), el('span', { class: 'muted agent-lbl', style: { 'font-size': '.85rem' } }, me.agent_enabled ? 'Agent beží' : 'Agent vypnutý'), sw,
      el('button', { class: 'btn small', onclick: () => $('#logoutForm').submit() }, 'Odhlásiť'));
  }
  async function toggleAgent() {
    const enable = !me.agent_enabled;
    if (enable && MODE === 'live') {
      const ok = await confirmSheet('Zapnúť agenta v LIVE režime', 'Agent bude obchodovať so skutočnými peniazmi. Napíš LIVE pre potvrdenie.',
        { danger: true, typed: 'LIVE', ok: 'Zapnúť' });
      if (!ok) return;
    }
    try { await api('/agent/toggle', { method: 'POST', body: { enabled: enable, confirm: 'LIVE' } }); toast(enable ? 'Agent zapnutý.' : 'Agent vypnutý.'); }
    catch (e) { toast(e.message, true); }
    await loadMe(); route();
  }

  // -- Prehľad ------------------------------------------------------------------------------
  function sparkline(points) {
    const w = 600, h = 160, pad = 6;
    if (!points || points.length < 2) return el('p', { class: 'empty' }, 'Krivka equity sa vykreslí po prvých cykloch.');
    const ys = points.map(p => p.equity); let min = Math.min(...ys), max = Math.max(...ys);
    if (max === min) { min -= 1; max += 1; } const span = max - min;
    const xy = points.map((p, i) => [pad + i * (w - 2 * pad) / (points.length - 1), h - pad - (p.equity - min) / span * (h - 2 * pad)]);
    const d = xy.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`); svg.setAttribute('class', 'chart'); svg.setAttribute('preserveAspectRatio', 'none');
    const area = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    area.setAttribute('class', 'area'); area.setAttribute('d', d + ` L${xy.at(-1)[0].toFixed(1)} ${h} L${xy[0][0].toFixed(1)} ${h} Z`);
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'path'); line.setAttribute('d', d);
    svg.append(area, line);
    return svg;
  }
  function statusLine() {
    const c = me.clock || {}; const d = me.day;
    let cls = 'closed', txt = 'Burza zatvorená';
    if (c.is_open) { cls = 'on'; txt = 'Burza otvorená, zatvára ' + fmtClock(c.next_close); }
    if (c.is_open === false && c.next_open) txt = 'Burza zatvorená, otvára ' + fmtTime(c.next_open);
    if (c.error) { txt = c.error; cls = 'halt'; }
    if (d && d.halted) { cls = 'halt'; txt = 'Dnes zastavené: ' + (d.halt_reason || ''); }
    return el('div', { class: 'status' }, el('span', { class: 'dot ' + cls }), el('span', {}, txt),
      me.last_cycle_at ? el('span', { class: 'muted' }, '· posledný cyklus ' + fmtClock(me.last_cycle_at)) : null,
      me.last_error ? el('span', { class: 'down' }, '· ' + me.last_error) : null);
  }
  async function viewOverview(root) {
    mount(root, el('div', { class: 'skeleton' }));
    const ov = await api('/overview?account=' + encodeURIComponent(viewAccount || ''));
    const a = ov.account; const d = ov.today;
    const dayBase = a && a.last_equity > 0 ? a.last_equity : (d && d.start_equity);
    const dayPnl = ov.live && a && dayBase ? (a.equity / dayBase - 1) * 100 : null;
    const brokerTxt = me.broker_label ? ' (' + me.broker_label + ')' : '';
    const banner = MODE === 'live' ? el('div', { class: 'banner live' }, `LIVE režim – agent obchoduje so skutočnými peniazmi${brokerTxt}.`)
      : MODE === 'dry' ? el('div', { class: 'banner warn' }, `Režim DRY – agent len rozhoduje a loguje, na burzu nič neposiela${me.broker === 'FakeBroker' ? ' (syntetické dáta, bez Alpaca kľúčov)' : ''}.`) : null;
    mount(root,
      accountPicker(),
      ov.live ? banner : el('div', { class: 'banner warn' }, `Prezeráš históriu účtu ${ov.account_label}. Agent teraz obchoduje na účte ${me.account_label}.`),
      el('div', { class: 'card' }, statusLine(), ov.market_note ? el('p', { class: 'muted', style: { margin: '8px 0 0' } }, 'Claude: ' + ov.market_note) : null),
      ov.error ? el('div', { class: 'banner warn' }, ov.error) : null,
      el('div', { class: 'tiles' },
        el('div', { class: 'tile' }, el('div', { class: 'lbl' }, ov.live && !(a && a.snapshot_at) ? 'Equity · ' + ov.account_label : 'Posledná equity ' + (a && a.snapshot_at ? fmtTime(a.snapshot_at) : '')), el('div', { class: 'val' }, fmtUsd(a && a.equity))),
        el('div', { class: 'tile' }, el('div', { class: 'lbl' }, 'Dnes'), el('div', { class: 'val ' + signCls(dayPnl) }, fmtPct(dayPnl))),
        el('div', { class: 'tile' }, el('div', { class: 'lbl' }, 'Hotovosť'), el('div', { class: 'val' }, fmtUsd(a && a.cash))),
        el('div', { class: 'tile' }, el('div', { class: 'lbl' }, 'Obchody dnes'), el('div', { class: 'val' }, ov.live ? String(ov.trades_today || 0) : '–'))),
      el('div', { class: 'card' }, el('h2', {}, 'Equity (30 dní)'), sparkline(ov.curve)),
      !ov.live ? null : el('div', { class: 'card' }, el('h2', {}, 'Otvorené pozície', el('span', { class: 'chip' }, String(ov.positions.length))),
        ov.positions.length ? el('div', { class: 'table-wrap' }, el('table', {}, el('thead', {}, el('tr', {}, el('th', {}, 'Ticker'), el('th', { class: 'num' }, 'Ks'), el('th', { class: 'num' }, 'Vstup'), el('th', { class: 'num' }, 'Cena'), el('th', { class: 'num' }, 'P/L'), el('th', {}))),
          el('tbody', {}, ov.positions.map(p => el('tr', {}, el('td', {}, el('strong', {}, p.symbol)), el('td', { class: 'num' }, fmtNum(p.qty, 0)), el('td', { class: 'num' }, fmtNum(p.avg_entry)),
            el('td', { class: 'num' }, fmtNum(p.current_price)), el('td', { class: 'num ' + signCls(p.unrealized_pl) }, fmtUsd(p.unrealized_pl) + ' (' + fmtPct(p.unrealized_plpc * 100, 1) + ')'),
            el('td', {}, el('button', { class: 'btn small', onclick: () => closePos(p.symbol, ov.account_key) }, 'Zavrieť')))))))
          : el('p', { class: 'empty' }, 'Žiadne otvorené pozície.')),
      !ov.live ? null : el('div', { class: 'card' }, el('h2', {}, 'Rýchle akcie'),
        el('div', { class: 'row' },
          el('button', { class: 'btn', onclick: runCycle }, 'Vyhodnotiť teraz'),
          el('button', { class: 'btn danger', onclick: panic }, 'STOP – zavrieť všetko')),
        el('p', { class: 'note' }, 'STOP zavrie všetky pozície, zruší objednávky, zastaví dnešný deň a vypne agenta.')),
      el('div', { class: 'card' }, el('h2', {}, 'Posledné dni'),
        ov.days.length ? el('div', { class: 'table-wrap' }, el('table', {}, el('thead', {}, el('tr', {}, el('th', {}, 'Deň'), el('th', { class: 'num' }, 'Štart'), el('th', { class: 'num' }, 'Koniec'), el('th', { class: 'num' }, 'Výsledok'), el('th', {}, 'Stav'))),
          el('tbody', {}, ov.days.map(x => el('tr', {}, el('td', { class: 'time' }, x.date), el('td', { class: 'num' }, fmtUsd(x.start_equity)), el('td', { class: 'num' }, fmtUsd(x.end_equity)),
            el('td', { class: 'num ' + signCls(x.pnl_pct) }, fmtPct(x.pnl_pct)),
            el('td', {}, x.halted ? el('span', { class: 'chip sell' }, 'zastavené') : x.flattened ? el('span', { class: 'chip' }, 'uzavreté') : el('span', { class: 'chip buy' }, 'beží')))))))
          : el('p', { class: 'empty' }, 'Zatiaľ žiadny obchodný deň.')));
  }
  async function closePos(symbol, account) {
    if (!await confirmSheet(`Zavrieť ${symbol}`, 'Pozícia sa predá za trhovú cenu.', { danger: true, ok: 'Zavrieť' })) return;
    try { await api(`/positions/${symbol}/close?account=${encodeURIComponent(account || '')}`, { method: 'POST', body: {} }); toast(`${symbol} zatvorené.`); route(); } catch (e) { toast(e.message, true); }
  }
  async function runCycle() {
    try {
      const r = await api('/agent/cycle', { method: 'POST', body: {}, timeout: 120000 });
      const label = (k) => (me.accounts.find(a => a.key === k) || { label: k }).label;
      toast(r.reports.map(x => (r.reports.length > 1 ? label(x.account) + ': ' : 'Cyklus: ') + x.status + (x.notes && x.notes[0] ? ' · ' + x.notes[0] : '')).join(' | '));
      await loadMe(); route();
    }
    catch (e) { toast(e.message, true); }
  }
  async function panic() {
    if (!await confirmSheet('STOP', 'Zavrie všetky pozície, zruší objednávky, zastaví dnešný deň a vypne agenta. Napíš STOP.', { danger: true, typed: 'STOP', ok: 'STOP' })) return;
    try { const r = await api('/agent/panic', { method: 'POST', body: { confirm: 'STOP' } }); toast(`Zatvorené ${r.closed} pozícií, agent vypnutý.`); await loadMe(); route(); }
    catch (e) { toast(e.message, true); }
  }

  // -- Obchody ---------------------------------------------------------------------------------
  async function viewTrades(root) {
    mount(root, el('div', { class: 'skeleton' }));
    const q = '&account=' + encodeURIComponent(viewAccount || '');
    const [o, d] = await Promise.all([api('/orders?limit=100' + q), api('/decisions?limit=150' + q)]);
    const filter = el('select', { onchange: () => renderDecisions(filter.value) }, el('option', { value: '' }, 'Všetky rozhodnutia'),
      ...['buy', 'sell', 'skip', 'hold'].map(a => el('option', { value: a }, ACTION[a])));
    const decBody = el('tbody');
    function renderDecisions(act) {
      const items = d.items.filter(x => !act || x.action === act);
      mount(decBody, items.length ? items.map(x => el('tr', {}, el('td', { class: 'time' }, fmtClock(x.at)), el('td', {}, el('strong', {}, x.symbol)),
        el('td', {}, el('span', { class: 'chip ' + x.action }, ACTION[x.action] || x.action)), el('td', { class: 'num' }, fmtNum(x.price)),
        el('td', { class: 'num ' + signCls(x.score) }, x.score == null ? '–' : (x.score > 0 ? '+' : '') + fmtNum(x.score)),
        el('td', {}, el('div', {}, x.reason), x.details ? el('div', { class: 'decision-reason' }, detailsText(x.details)) : null)))
        : el('tr', {}, el('td', { colspan: 6, class: 'empty' }, 'Žiadne rozhodnutia.')));
    }
    renderDecisions('');
    mount(root,
      accountPicker(),
      el('div', { class: 'card' }, el('h2', {}, 'Objednávky', el('span', { class: 'chip' }, String(o.items.length))),
        o.items.length ? el('div', { class: 'table-wrap' }, el('table', {}, el('thead', {}, el('tr', {}, el('th', {}, 'Čas'), el('th', {}, 'Ticker'), el('th', {}, 'Typ'), el('th', { class: 'num' }, 'Ks'), el('th', { class: 'num' }, 'Cena'), el('th', { class: 'num' }, 'Stop / Cieľ'), el('th', {}, 'Stav'), el('th', {}, 'Poznámka'))),
          el('tbody', {}, o.items.map(x => el('tr', {}, el('td', { class: 'time' }, fmtTime(x.at)), el('td', {}, el('strong', {}, x.symbol)),
            el('td', {}, el('span', { class: 'chip ' + (x.side === 'buy' ? 'buy' : 'sell') }, (x.side === 'buy' ? 'kúpa' : 'predaj') + ' · ' + (ACTION[x.kind] || x.kind))),
            el('td', { class: 'num' }, fmtNum(x.qty, 0)), el('td', { class: 'num' }, fmtNum(x.price)),
            el('td', { class: 'num' }, x.stop_price ? fmtNum(x.stop_price) + ' / ' + fmtNum(x.take_profit) : '–'),
            el('td', {}, el('span', { class: 'chip ' + (x.status === 'rejected' ? 'sell' : '') }, x.status + (x.mode === 'dry' ? '' : ' · ' + x.mode))),
            el('td', { class: 'decision-reason' }, x.note || ''))))))
          : el('p', { class: 'empty' }, 'Zatiaľ žiadne objednávky.')),
      el('div', { class: 'card' }, el('h2', {}, 'Rozhodnutia agenta', filter),
        el('div', { class: 'table-wrap' }, el('table', {}, el('thead', {}, el('tr', {}, el('th', {}, 'Čas'), el('th', {}, 'Ticker'), el('th', {}, 'Akcia'), el('th', { class: 'num' }, 'Cena'), el('th', { class: 'num' }, 'Skóre'), el('th', {}, 'Dôvod'))), decBody))));
  }
  function detailsText(json) {
    try { const d = JSON.parse(json); const parts = [];
      if (d.tech) parts.push(d.tech.join(', ')); if (d.rsi != null) parts.push('RSI ' + Math.round(d.rsi)); if (d.news) parts.push('správy: ' + d.news); if (d.sizing) parts.push(d.sizing);
      return parts.join(' · '); } catch { return ''; }
  }

  // -- Správy -----------------------------------------------------------------------------------
  async function viewNews(root) {
    mount(root, el('div', { class: 'skeleton' }));
    const a = await api('/analyses?limit=20');
    mount(root,
      !me.analyst ? el('div', { class: 'banner warn' }, 'Analytik (Claude) je vypnutý alebo chýba Anthropic API kľúč (Nastavenia → Broker) – agent beží len na technických signáloch.') : null,
      a.items.length ? a.items.map(x => { let r = {}; try { r = JSON.parse(x.result); } catch {}
        return el('div', { class: 'card' }, el('h2', {}, fmtTime(x.at), el('span', { class: 'chip' }, `${x.headlines} správ · ${x.model} · ${(x.input_tokens || 0) + (x.output_tokens || 0)} tok.`)),
          el('p', { class: 'muted' }, r.market_note || ''),
          (r.symbols || []).length ? el('div', { class: 'table-wrap' }, el('table', {}, el('thead', {}, el('tr', {}, el('th', {}, 'Ticker'), el('th', { class: 'num' }, 'Sentiment'), el('th', { class: 'num' }, 'Istota'), el('th', {}, 'Katalyzátor'))),
            el('tbody', {}, r.symbols.map(s => el('tr', {}, el('td', {}, el('strong', {}, s.symbol), s.stale ? el('span', { class: 'chip', style: { 'margin-left': '6px' } }, 'staré') : null),
              el('td', { class: 'num ' + signCls(s.sentiment) }, (s.sentiment > 0 ? '+' : '') + fmtNum(s.sentiment)), el('td', { class: 'num' }, fmtNum(s.confidence)), el('td', {}, s.catalyst))))))
            : el('p', { class: 'empty' }, 'Bez relevantných správ.')); })
        : el('div', { class: 'card' }, el('p', { class: 'empty' }, 'Zatiaľ žiadna analýza správ. Prebehne, keď sa počas otvorenej burzy objavia nové správy k sledovaným tickerom.')));
  }

  // -- Nastavenia --------------------------------------------------------------------------------
  const LABEL = {
    watchlist: 'Sledované tickery', cycle_minutes: 'Interval vyhodnotenia (min)', bar_timeframe: 'Sviečky',
    risk_per_trade_pct: 'Riziko na obchod (% equity)', max_position_pct: 'Max. pozícia (% equity)', max_positions: 'Max. počet pozícií',
    daily_loss_limit_pct: 'Denný limit straty (%)', atr_stop_mult: 'Stop-loss (× ATR)', reward_risk: 'Pomer cieľ : riziko',
    flatten_before_close_min: 'Zavrieť všetko pred koncom (min)', no_entry_first_min: 'Bez vstupov po otvorení (min)',
    no_entry_after_close_min: 'Bez vstupov pred koncom (min)',
    buy_threshold: 'Hranica kúpy', sell_threshold: 'Hranica predaja', tech_weight: 'Váha techniky', news_weight: 'Váha správ',
    news_min_confidence: 'Min. istota správy', require_news_for_entry: 'Vstup len s katalyzátorom',
    analyst_enabled: 'Analytik zapnutý', analyst_model: 'Model', analyst_effort: 'Hĺbka uvažovania', analyst_max_headlines: 'Max. správ na analýzu',
    analyst_daily_budget_calls: 'Max. volaní za deň', ntfy_server: 'ntfy server', ntfy_topic: 'ntfy téma', notify_mode: 'Čo posielať',
    backup_enabled: 'Denná záloha', backup_time: 'Čas zálohy', backup_keep: 'Ponechať lokálnych záloh', b2_key_id: 'B2 keyID',
    b2_app_key: 'B2 applicationKey', b2_bucket: 'B2 bucket', b2_prefix: 'B2 prefix',
  };
  const GROUPS = [
    ['Agent', ['watchlist', 'cycle_minutes', 'bar_timeframe']],
    ['Riziko', ['risk_per_trade_pct', 'max_position_pct', 'max_positions', 'daily_loss_limit_pct', 'atr_stop_mult', 'reward_risk', 'flatten_before_close_min', 'no_entry_first_min', 'no_entry_after_close_min']],
    ['Signály', ['buy_threshold', 'sell_threshold', 'tech_weight', 'news_weight', 'news_min_confidence', 'require_news_for_entry']],
    ['Claude analytik', ['analyst_enabled', 'analyst_model', 'analyst_effort', 'analyst_max_headlines', 'analyst_daily_budget_calls']],
    ['Notifikácie', ['ntfy_server', 'ntfy_topic', 'notify_mode']],
    ['Zálohy', ['backup_enabled', 'backup_time', 'backup_keep', 'b2_key_id', 'b2_app_key', 'b2_bucket', 'b2_prefix']],
  ];
  async function viewSettings(root) {
    mount(root, el('div', { class: 'skeleton' }));
    const [s, b] = await Promise.all([api('/settings'), api('/backups')]);
    const S = s.settings; const inputs = {};
    function field(key) {
      const m = S[key]; const id = 'f_' + key;
      let input;
      if (m.kind === 'bool') input = el('input', { type: 'checkbox', id, checked: m.value === true });
      else if (m.kind === 'secret') input = el('input', { type: 'password', id, placeholder: m.set ? '•••••• (uložené, prázdne = nemeniť)' : 'nenastavené', autocomplete: 'new-password' });
      else if (key === 'notify_mode') input = el('select', { id }, ...[['trade', 'každý obchod'], ['daily', 'jeden súhrn po zatvorení burzy'], ['both', 'každý obchod aj denný súhrn']].map(([v, t]) => el('option', { value: v, selected: m.value === v }, t)));
      else if (key === 'analyst_effort') input = el('select', { id }, ...['low', 'medium', 'high'].map(v => el('option', { value: v, selected: m.value === v }, v)));
      else if (key === 'bar_timeframe') input = el('select', { id }, ...['1Min', '5Min', '15Min', '30Min', '1Hour'].map(v => el('option', { value: v, selected: m.value === v }, v)));
      else input = el('input', { id, value: String(m.value), inputmode: m.kind === 'float' || m.kind === 'int' ? 'decimal' : 'text' });
      inputs[key] = input;
      return el('label', { class: 'f', for: id }, el('span', {}, LABEL[key] || key, m.source === 'db' ? el('span', { class: 'chip', style: { 'margin-left': '6px' } }, 'z appky') : null), input, el('small', {}, m.desc));
    }
    async function save() {
      const values = {};
      for (const [k, inp] of Object.entries(inputs)) values[k] = inp.type === 'checkbox' ? inp.checked : inp.value;
      try { await api('/settings', { method: 'PUT', body: { values } }); clearDrafts('f_'); toast('Nastavenia uložené.'); route(); } catch (e) { toast(e.message, true); }
    }
    // --- Broker: režim + kľúče, chránené heslom ---
    const B = S;
    const keyPh = (k, ph) => B[k].set ? `uložené (${B[k].value}), prázdne = nemeniť` : ph;
    const secPh = (k) => B[k].set ? '•••••• (uložené, prázdne = nemeniť)' : 'nenastavené';
    const modeSel = el('select', { id: 'b_mode' }, ...['dry', 'paper', 'live'].map(v => el('option', { value: v, selected: s.wanted_mode === v }, v === 'dry' ? 'dry – len loguje, nič neposiela' : v === 'paper' ? 'paper – fiktívne peniaze (Alpaca paper)' : 'live – SKUTOČNÉ peniaze')));
    const bIn = {
      anthropic_api_key: el('input', { type: 'password', placeholder: B.anthropic_api_key.set ? '•••••• (uložené, prázdne = nemeniť)' : 'sk-ant-…', autocomplete: 'new-password' }),
      alpaca_paper_key_id: el('input', { placeholder: keyPh('alpaca_paper_key_id', 'PK…'), autocomplete: 'off' }),
      alpaca_paper_secret: el('input', { type: 'password', placeholder: secPh('alpaca_paper_secret'), autocomplete: 'new-password' }),
      alpaca_live_key_id: el('input', { placeholder: keyPh('alpaca_live_key_id', 'AK…'), autocomplete: 'off' }),
      alpaca_live_secret: el('input', { type: 'password', placeholder: secPh('alpaca_live_secret'), autocomplete: 'new-password' }),
    };
    const bPw = el('input', { type: 'password', autocomplete: 'current-password', placeholder: 'Tvoje heslo do appky' });
    async function saveBroker() {
      const values = { trading_mode: modeSel.value };
      for (const [k, inp] of Object.entries(bIn)) if (inp.value.trim()) values[k] = inp.value.trim();
      if (!bPw.value) { toast('Zadaj heslo.', true); return; }
      let confirm = null;
      if (modeSel.value === 'live') {
        const ok = await confirmSheet('Prepnúť na LIVE', 'Agent bude po zapnutí obchodovať so skutočnými peniazmi na tvojom Alpaca účte. Napíš LIVE.', { danger: true, typed: 'LIVE', ok: 'Prepnúť na live' });
        if (!ok) return;
        confirm = 'LIVE';
      }
      try {
        const r = await api('/broker', { method: 'POST', body: { values, password: bPw.value, confirm } });
        clearDrafts('b_');
        toast(`Režim ${r.mode}, účet: ${fmtUsd(r.account.equity)}. Agent je vypnutý – zapni ho v hlavičke.`);
        await loadMe(); route();
      } catch (e) { toast(e.message, true); await loadMe(); route(); }
    }
    const runningLabel = s.broker === 'FakeBroker' ? 'syntetické dáta (chýbajú kľúče)' : (s.broker_label || 'Alpaca');
    const brokerCard = el('div', { class: 'card' }, el('h2', {}, 'Broker a kľúče', el('strong', { class: 'mode mode-' + s.mode }, s.mode)),
      el('p', { class: 'note' }, `Beží: ${runningLabel} · Alpaca paper: ${s.alpaca_paper_set ? 'áno' : 'chýba'} · Alpaca live: ${s.alpaca_live_set ? 'áno' : 'chýba'} · Claude: ${s.analyst_key_set ? 'áno' : 'chýba'}`),
      el('div', { class: 'fields' },
        el('label', { class: 'f' }, 'Režim obchodovania', modeSel, el('small', {}, 'dry nič neposiela; paper = Alpaca paper účet; live = skutočný Alpaca účet.')),
        el('label', { class: 'f' }, 'Anthropic API kľúč', bIn.anthropic_api_key, el('small', {}, 'Claude analytik správ. Prázdne = len technické signály.')),
        el('label', { class: 'f' }, 'Alpaca paper Key ID', bIn.alpaca_paper_key_id, el('small', {}, 'Alpaca → Paper Trading → API Keys.')),
        el('label', { class: 'f' }, 'Alpaca paper Secret', bIn.alpaca_paper_secret),
        el('label', { class: 'f' }, 'Alpaca live Key ID', bIn.alpaca_live_key_id, el('small', {}, 'Iné kľúče než paper! Alpaca → Live Trading → API Keys.')),
        el('label', { class: 'f' }, 'Alpaca live Secret', bIn.alpaca_live_secret),
        el('label', { class: 'f' }, 'Potvrď heslom', bPw, el('small', {}, 'Zmena režimu alebo kľúčov vyžaduje heslo. Po zmene sa agent vypne, zapneš ho vedome znova.'))),
      el('div', { class: 'row', style: { 'justify-content': 'flex-end' } }, el('button', { class: 'btn ' + (modeSel.value === 'live' ? 'danger' : 'primary'), onclick: saveBroker }, 'Uložiť broker a kľúče')));
    modeSel.addEventListener('change', () => { brokerCard.querySelector('.row .btn').className = 'btn ' + (modeSel.value === 'live' ? 'danger' : 'primary'); });

    const pwOld = el('input', { type: 'password', autocomplete: 'current-password', placeholder: 'Staré heslo' });
    const pwNew = el('input', { type: 'password', autocomplete: 'new-password', placeholder: 'Nové heslo (aspoň 8 znakov)' });
    mount(root,
      brokerCard,
      ...GROUPS.map(([title, keys]) => el('div', { class: 'card' }, el('h2', {}, title), el('div', { class: 'fields' }, keys.map(field)),
        title === 'Notifikácie' ? el('div', { class: 'row' },
          el('button', { class: 'btn small', onclick: async () => { try { const r = await api('/notify/test', { method: 'POST', body: { server: inputs.ntfy_server.value, topic: inputs.ntfy_topic.value } }); toast(r.message); } catch (e) { toast(e.message, true); } } }, 'Poslať skúšobnú notifikáciu'),
          el('button', { class: 'btn small', onclick: async () => { try { const r = await api('/notify/summary', { method: 'POST', body: {} }); toast('Odoslané: ' + r.title); } catch (e) { toast(e.message, true); } } }, 'Poslať súhrn dňa teraz'),
          el('span', { class: 'note' }, 'STOP, denný limit a chyby chodia vždy, bez ohľadu na voľbu.')) : null,
        title === 'Zálohy' ? el('div', { class: 'row' },
          el('button', { class: 'btn small', onclick: async () => { try { const r = await api('/backups/test-b2', { method: 'POST', body: {} }); toast(r.message); } catch (e) { toast(e.message, true); } } }, 'Otestovať B2'),
          el('button', { class: 'btn small', onclick: async () => { try { const r = await api('/backups', { method: 'POST', body: {} }); toast(r.warning ? 'Lokálna záloha OK, B2: ' + r.warning : 'Záloha hotová: ' + r.result.local, !!r.warning); route(); } catch (e) { toast(e.message, true); } } }, 'Zálohovať teraz'),
          el('span', { class: 'note' }, b.items.length ? `Posledná: ${fmtTime(b.items[0].at)} (${b.items.length} lokálnych)` : 'Zatiaľ žiadna záloha')) : null)),
      el('div', { class: 'row', style: { 'justify-content': 'flex-end' } }, el('button', { class: 'btn primary', onclick: save }, 'Uložiť nastavenia')),
      el('div', { class: 'card' }, el('h2', {}, 'Heslo'), el('div', { class: 'fields' }, el('label', { class: 'f' }, 'Staré heslo', pwOld), el('label', { class: 'f' }, 'Nové heslo', pwNew)),
        el('button', { class: 'btn small', onclick: async () => { try { await api('/password', { method: 'POST', body: { old: pwOld.value, new: pwNew.value } }); toast('Heslo zmenené, prihlás sa znova.'); setTimeout(() => location.href = '/login', 800); } catch (e) { toast(e.message, true); } } }, 'Zmeniť heslo')));
    draftify(root);
  }

  // -- neuložené zmeny vo formulári ----------------------------------------------------------------
  const DRAFT = 'roblowe:draft:';
  function draftify(root) {
    let restored = 0;
    root.querySelectorAll('input[id], select[id]').forEach(inp => {
      if (inp.type === 'password') return;  // heslá a tajné kľúče nikdy neukladáme
      let v = null; try { v = sessionStorage.getItem(DRAFT + inp.id); } catch {}
      if (v != null) { if (inp.type === 'checkbox') inp.checked = v === '1'; else inp.value = v; inp.dispatchEvent(new Event('change')); restored++; }
      const save = () => { try { sessionStorage.setItem(DRAFT + inp.id, inp.type === 'checkbox' ? (inp.checked ? '1' : '0') : inp.value); } catch {} };
      inp.addEventListener('input', save); inp.addEventListener('change', save);
    });
    if (restored) toast('Obnovil som tvoje neuložené zmeny.');
  }
  function clearDrafts(prefix) {
    try { Object.keys(sessionStorage).filter(k => k.startsWith(DRAFT + (prefix || ''))).forEach(k => sessionStorage.removeItem(k)); } catch {}
  }

  // -- router --------------------------------------------------------------------------------------
  async function route() {
    renderNav();
    const r = ROUTES.find(x => x.hash === (location.hash || '#/')) || ROUTES[0];
    try { await r.view($('#main')); } catch (e) { if (e.message !== '401') mount($('#main'), el('div', { class: 'card' }, el('p', { class: 'error' }, e.message))); }
  }
  window.addEventListener('hashchange', route);
  // Návrat do appky / periodická obnova: prekresli len stránky bez formulárov a nikdy, keď je otvorený dialóg.
  const AUTO_REFRESH = ['#/', '#/obchody', '#/spravy'];
  function softRefresh() {
    const h = location.hash || '#/';
    loadMe().then(() => { if (AUTO_REFRESH.includes(h) && $('#sheet').hidden) route(); }).catch(() => {});
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) softRefresh(); });
  setInterval(() => { if (!document.hidden && (location.hash || '#/') === '#/') softRefresh(); }, 60000);
  loadMe().then(route).catch(e => { if (e.message !== '401') toast(e.message, true); });
})();
