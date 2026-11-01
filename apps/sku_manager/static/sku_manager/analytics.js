/* India-map analytics (products + customers). Needs india_map_data.js first. */
(function () {
  'use strict';
  var root = document.getElementById('analytics-root');
  if (!root || !window.INDIA_MAP) return;

  var KIND = root.dataset.kind, URL = root.dataset.url;
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (t) { return String(t == null ? '' : t).replace(/[&<>"']/g, function (c) {
    return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); };
  var nf = new Intl.NumberFormat('en-IN');
  var num = function (n) { return nf.format(Math.round(n || 0)); };
  var rupee = function (n) { return '₹' + nf.format(Math.round(n || 0)); };
  var pct = function (n) { return (Math.round((n || 0) * 10) / 10) + '%'; };

  var METRICS = {
    products: [
      {key: 'units', label: 'Units sold', fmt: num, rgb: '245,158,11'},
      {key: 'revenue', label: 'Revenue', fmt: rupee, rgb: '16,185,129'},
      {key: 'orders', label: 'Orders', fmt: num, rgb: '59,130,246'},
      {key: 'rto_pct', label: 'Return %', fmt: pct, rgb: '244,63,94'}
    ],
    customers: [
      {key: 'customers', label: 'Customers', fmt: num, rgb: '139,92,246'},
      {key: 'orders', label: 'Orders', fmt: num, rgb: '59,130,246'},
      {key: 'aov', label: 'Avg order', fmt: rupee, rgb: '20,184,166'},
      {key: 'repeat_pct', label: 'Repeat %', fmt: pct, rgb: '236,72,153'}
    ]
  }[KIND];
  var metric = METRICS[0], data = null, selected = null;
  var ALIAS = {dd: 'dn'};   // Daman & Diu is drawn separately but merged in the data

  /* ---------- map ---------- */
  var svg = $('india-svg');
  svg.setAttribute('viewBox', window.INDIA_MAP.viewBox);
  var NS = 'http://www.w3.org/2000/svg';
  window.INDIA_MAP.locations.forEach(function (loc) {
    var p = document.createElementNS(NS, 'path');
    p.setAttribute('d', loc.path);
    p.setAttribute('data-id', loc.id);
    p.setAttribute('class', 'india-state empty');
    svg.appendChild(p);
  });
  var paths = Array.prototype.slice.call(svg.querySelectorAll('path'));
  var codeOf = function (el) { var id = el.getAttribute('data-id'); return ALIAS[id] || id; };

  var tip = $('map-tip');
  svg.addEventListener('mousemove', function (e) {
    var el = e.target.closest('path'); if (!el || !data) { tip.classList.add('hidden'); return; }
    var code = codeOf(el), s = data.states[code];
    var name = s ? s.name : (window.STATE_NAMES[code] || el.getAttribute('data-id'));
    tip.innerHTML = '<b>' + esc(name) + '</b><br>' + esc(metric.label) + ': ' + (s ? metric.fmt(s[metric.key]) : 'no orders');
    var box = svg.parentElement.getBoundingClientRect();
    tip.style.left = (e.clientX - box.left + 14) + 'px';
    tip.style.top = (e.clientY - box.top + 14) + 'px';
    tip.classList.remove('hidden');
  });
  svg.addEventListener('mouseleave', function () { tip.classList.add('hidden'); });
  svg.addEventListener('click', function (e) {
    var el = e.target.closest('path'); if (!el) return;
    select(codeOf(el) === selected ? null : codeOf(el));
  });

  function paint() {
    var max = 0;
    Object.keys(data.states).forEach(function (c) { max = Math.max(max, data.states[c][metric.key] || 0); });
    paths.forEach(function (el) {
      var code = codeOf(el), s = data.states[code], v = s ? (s[metric.key] || 0) : 0;
      el.classList.toggle('empty', !v);
      el.classList.toggle('selected', code === selected);
      el.style.fill = v ? 'rgba(' + metric.rgb + ',' + (0.2 + 0.8 * Math.sqrt(v / max)).toFixed(2) + ')' : '';
      if (code === selected) svg.appendChild(el);   // draw outline on top
    });
    $('legend-bar').style.background = 'linear-gradient(90deg, rgba(' + metric.rgb + ',.2), rgba(' + metric.rgb + ',1))';
    $('legend-max').textContent = max ? metric.fmt(max) : '-';
    $('legend-label').textContent = metric.label;
  }

  /* ---------- panels ---------- */
  function kpis() {
    var t = data.totals, tiles = KIND === 'products' ? [
      ['fa-cubes', 'Units sold', num(t.units), 'amber'], ['fa-indian-rupee-sign', 'Revenue', rupee(t.revenue), 'emerald'],
      ['fa-chart-line', 'Est. profit', rupee(t.profit), 'blue'], ['fa-rotate-left', 'Return rate', pct(t.rto_pct), 'rose']
    ] : [
      ['fa-users', 'Customers', num(t.customers), 'violet'], ['fa-bag-shopping', 'Orders', num(t.orders), 'blue'],
      ['fa-receipt', 'Avg order', rupee(t.aov), 'teal'], ['fa-repeat', 'Repeat customers', pct(t.repeat_pct), 'pink']
    ];
    $('kpis').innerHTML = tiles.map(function (k) {
      return '<div class="bg-white dark:bg-slate-800/80 border border-slate-200 dark:border-slate-700/60 rounded-2xl p-4 shadow-sm">' +
        '<div class="flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-slate-500"><span class="w-6 h-6 rounded-lg bg-' + k[3] + '-500/10 text-' + k[3] + '-500 flex items-center justify-center"><i class="fa-solid ' + k[0] + ' text-[11px]"></i></span>' + k[1] + '</div>' +
        '<div class="text-xl font-black text-slate-900 dark:text-white mt-2">' + k[2] + '</div></div>';
    }).join('');
    var c = data.coverage, all = c.located + c.unlocated;
    $('coverage').innerHTML = all ? '<i class="fa-solid fa-location-dot"></i> State found for ' + num(c.located) + ' of ' + num(all) + ' orders' +
      (c.unlocated ? ' &middot; ' + num(c.unlocated) + ' without a recognisable address' : '') : 'No orders in this period.';
  }

  function bars(rows, labelKey, valueKey, fmt, color) {
    var max = Math.max.apply(null, rows.map(function (r) { return r[valueKey]; }).concat([1]));
    return rows.map(function (r) {
      return '<div class="py-1"><div class="flex justify-between text-[11px] gap-3"><span class="truncate font-semibold text-slate-700 dark:text-slate-200">' + esc(r[labelKey]) + '</span>' +
        '<span class="font-mono text-slate-500 shrink-0">' + fmt(r[valueKey]) + '</span></div>' +
        '<div class="h-1.5 rounded-full bg-slate-100 dark:bg-slate-700 mt-1"><div class="h-1.5 rounded-full bg-' + color + '-500" style="width:' + Math.max(3, r[valueKey] * 100 / max) + '%"></div></div></div>';
    }).join('');
  }
  function section(title, icon, body) {
    return '<div class="pt-3 mt-3 border-t border-slate-100 dark:border-slate-700"><div class="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1"><i class="fa-solid ' + icon + ' mr-1"></i>' + title + '</div>' + body + '</div>';
  }
  function chips(list) {
    return list.length ? '<div class="flex flex-wrap gap-1.5">' + list.map(function (p) {
      return '<span class="px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-700 text-[11px] font-bold text-slate-600 dark:text-slate-300">' + esc(p.name) + ' &middot; ' + p.orders + '</span>';
    }).join('') + '</div>' : '<p class="text-xs text-slate-400">No data</p>';
  }
  function trend(months) {
    if (!months.length) return '';
    var max = Math.max.apply(null, months.map(function (m) { return m.orders; }).concat([1]));
    return '<div class="flex gap-1 h-16">' + months.map(function (m) {
      return '<div class="flex-1 flex flex-col justify-end items-center" title="' + esc(m.month) + ': ' + m.orders + ' orders, ' + rupee(m.revenue) + '">' +
        '<div class="w-full rounded-t bg-amber-500/80" style="height:' + Math.max(6, m.orders * 100 / max) + '%"></div></div>';
    }).join('') + '</div><div class="flex justify-between text-[9px] text-slate-400 font-mono mt-1"><span>' + esc(months[0].month) + '</span><span>' + esc(months[months.length - 1].month) + '</span></div>';
  }

  function detail() {
    var s = selected ? data.states[selected] : null, box = $('detail');
    if (selected && !s) {
      box.innerHTML = '<h3 class="text-base font-black text-slate-900 dark:text-white">' + esc(window.STATE_NAMES[selected] || selected) + '</h3>' +
        '<p class="text-xs text-slate-500 mt-2">No orders from this state in the selected period.</p>' +
        '<button data-clear class="mt-3 text-xs font-bold text-amber-600 hover:underline"><i class="fa-solid fa-arrow-left"></i> Back to all states</button>';
      return;
    }
    if (!s) {   // overview: ranking
      var ranked = Object.keys(data.states).map(function (c) { return data.states[c]; })
        .sort(function (a, b) { return (b[metric.key] || 0) - (a[metric.key] || 0); }).slice(0, 10);
      box.innerHTML = '<h3 class="text-base font-black text-slate-900 dark:text-white"><i class="fa-solid fa-ranking-star text-amber-500 mr-1"></i> Top states by ' + esc(metric.label.toLowerCase()) + '</h3>' +
        '<p class="text-[11px] text-slate-500 mb-2">Tap a state on the map for its full breakdown.</p>' +
        (ranked.length ? ranked.map(function (r, i) {
          return '<button data-pick="' + r.code + '" class="w-full text-left flex items-center gap-2 py-1.5 px-2 -mx-2 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-700/50">' +
            '<span class="w-5 text-[10px] font-mono text-slate-400">' + (i + 1) + '</span><span class="flex-1 text-xs font-semibold text-slate-700 dark:text-slate-200 truncate">' + esc(r.name) + '</span>' +
            '<span class="text-xs font-mono font-bold text-slate-900 dark:text-white">' + metric.fmt(r[metric.key]) + '</span></button>';
        }).join('') : '<p class="text-xs text-slate-400">No state data yet.</p>');
      return;
    }
    var tiles = (KIND === 'products'
      ? [['Units', num(s.units)], ['Revenue', rupee(s.revenue)], ['Orders', num(s.orders)], ['Returns', s.cancelled + ' (' + pct(s.rto_pct) + ')']]
      : [['Customers', num(s.customers)], ['Repeat', s.repeat_customers + ' (' + pct(s.repeat_pct) + ')'], ['Orders', num(s.orders)], ['Avg order', rupee(s.aov)]]);
    var html = '<div class="flex items-start justify-between gap-2"><div><div class="text-[10px] font-bold uppercase tracking-wider text-amber-600">Selected state</div>' +
      '<h3 class="text-lg font-black text-slate-900 dark:text-white leading-tight">' + esc(s.name) + '</h3></div>' +
      '<button data-clear class="text-xs font-bold text-slate-500 hover:text-amber-600 shrink-0"><i class="fa-solid fa-xmark"></i> Clear</button></div>' +
      '<div class="grid grid-cols-2 gap-2 mt-3">' + tiles.map(function (t) {
        return '<div class="rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 p-2.5"><div class="text-[10px] font-bold uppercase text-slate-400">' + t[0] + '</div><div class="text-sm font-black text-slate-900 dark:text-white mt-0.5">' + t[1] + '</div></div>';
      }).join('') + '</div>';
    if (KIND === 'products') {
      html += '<div class="mt-2 text-[11px] text-slate-500">' + pct(s.share_pct) + ' of all units &middot; est. profit ' + rupee(s.profit) + '</div>';
      html += section('Best sellers here', 'fa-gem', s.top_products.length ? bars(s.top_products.map(function (p) { return {n: p.name, u: p.units}; }), 'n', 'u', num, 'amber') : '<p class="text-xs text-slate-400">No sales</p>');
    } else {
      html += section('Top customers here', 'fa-crown', s.top_customers.length ? bars(s.top_customers.map(function (c) { return {n: c.name, u: c.orders}; }), 'n', 'u', function (v) { return v + ' orders'; }, 'violet') : '<p class="text-xs text-slate-400">No identified customers</p>');
      html += section('Favourite products', 'fa-gem', s.top_products.length ? bars(s.top_products.map(function (p) { return {n: p.name, u: p.units}; }), 'n', 'u', num, 'amber') : '<p class="text-xs text-slate-400">None</p>');
    }
    html += section('Marketplaces', 'fa-store', chips(s.platforms));
    html += section('Top PIN codes', 'fa-location-dot', chips(s.top_pincodes.map(function (p) { return {name: p.pin, orders: p.orders}; })));
    html += section('Orders by month', 'fa-chart-column', trend(s.monthly));
    box.innerHTML = html;
  }

  function table(rows, cols, empty) {
    if (!rows.length) return '<p class="text-xs text-slate-400 py-3">' + empty + '</p>';
    return '<div class="overflow-x-auto"><table class="w-full text-xs"><thead><tr class="text-left text-[10px] uppercase tracking-wider text-slate-400">' +
      cols.map(function (c) { return '<th class="py-2 pr-3 ' + (c.right ? 'text-right' : '') + '">' + c.label + '</th>'; }).join('') + '</tr></thead><tbody>' +
      rows.map(function (r) { return '<tr class="border-t border-slate-100 dark:border-slate-700">' + cols.map(function (c) {
        return '<td class="py-2 pr-3 ' + (c.right ? 'text-right font-mono' : 'font-semibold text-slate-700 dark:text-slate-200') + '">' + c.get(r) + '</td>'; }).join('') + '</tr>'; }).join('') +
      '</tbody></table></div>';
  }
  var card = function (title, icon, body) {
    return '<div class="bg-white dark:bg-slate-800/80 border border-slate-200 dark:border-slate-700/60 rounded-3xl p-5 shadow-sm"><h3 class="text-sm font-black uppercase tracking-wider text-slate-700 dark:text-slate-300 mb-2 flex items-center gap-2"><i class="fa-solid ' + icon + ' text-amber-500"></i>' + title + '</h3>' + body + '</div>';
  };

  function lower() {
    var out = [];
    if (KIND === 'products') {
      out.push(card('Top products', 'fa-trophy', table(data.top_products, [
        {label: 'Product', get: function (r) { return esc(r.name) + '<div class="text-[10px] font-mono text-slate-400">' + esc(r.sku) + '</div>'; }},
        {label: 'Units', right: 1, get: function (r) { return num(r.units); }},
        {label: 'Revenue', right: 1, get: function (r) { return rupee(r.revenue); }}], 'No sales in this period.')));
      out.push(card('Running out soon', 'fa-hourglass-half', data.days ? table(data.stock_cover, [
        {label: 'Product', get: function (r) { return esc(r.name); }},
        {label: 'In stock', right: 1, get: function (r) { return num(r.stock); }},
        {label: 'Sells/day', right: 1, get: function (r) { return r.per_day; }},
        {label: 'Lasts', right: 1, get: function (r) { return '<span class="' + (r.cover_days <= 14 ? 'text-rose-500 font-black' : '') + '">' + r.cover_days + ' d</span>'; }}], 'No sales to estimate from.') :
        '<p class="text-xs text-slate-400 py-3">Pick a period (not "All time") to estimate stock cover.</p>'));
      out.push(card('In stock but not selling', 'fa-snowflake', table(data.slow_movers, [
        {label: 'Product', get: function (r) { return esc(r.name) + '<div class="text-[10px] font-mono text-slate-400">' + esc(r.sku) + '</div>'; }},
        {label: 'Stock', right: 1, get: function (r) { return num(r.stock); }}], 'Every product in stock has sold in this period.')));
    } else {
      out.push(card('Top customers', 'fa-crown', table(data.top_customers, [
        {label: 'Customer', get: function (r) { return esc(r.name); }},
        {label: 'Orders', right: 1, get: function (r) { return r.orders; }},
        {label: 'Spent', right: 1, get: function (r) { return rupee(r.spent); }}], 'No identified customers yet.')));
      out.push(card('Marketplace mix', 'fa-store', data.platform_mix.length ? bars(data.platform_mix, 'name', 'orders', num, 'violet') : '<p class="text-xs text-slate-400">No data</p>'));
      out.push(card('New vs repeat by month', 'fa-chart-column', table(data.monthly.slice().reverse().slice(0, 8), [
        {label: 'Month', get: function (r) { return esc(r.month); }},
        {label: 'New', right: 1, get: function (r) { return r.new_customers; }},
        {label: 'Repeat orders', right: 1, get: function (r) { return r.repeat_orders; }}], 'No data.')));
    }
    $('lower').innerHTML = out.join('');
    $('insights').innerHTML = data.insights.map(function (t) {
      return '<li class="flex gap-2 text-xs text-slate-600 dark:text-slate-300"><i class="fa-solid fa-lightbulb text-amber-500 mt-0.5"></i><span>' + esc(t) + '</span></li>'; }).join('');
  }

  function renderMetricButtons() {
    $('metric-buttons').innerHTML = METRICS.map(function (m) {
      return '<button type="button" data-metric="' + m.key + '" class="px-3 py-1.5 rounded-full text-[11px] font-bold border transition ' +
        (m.key === metric.key ? 'bg-amber-500 border-amber-500 text-slate-950' : 'bg-white dark:bg-slate-800 border-slate-300 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:border-amber-400') + '">' + m.label + '</button>';
    }).join('');
  }

  function select(code) { selected = code; paint(); detail(); }

  document.addEventListener('click', function (e) {
    var b;
    if ((b = e.target.closest('[data-metric]'))) { metric = METRICS.filter(function (m) { return m.key === b.dataset.metric; })[0]; renderMetricButtons(); if (data) { paint(); if (!selected) detail(); } }
    else if ((b = e.target.closest('[data-pick]'))) select(b.dataset.pick);
    else if (e.target.closest('[data-clear]')) select(null);
  });

  function load() {
    var q = new URLSearchParams();
    ['days', 'platform', 'sku'].forEach(function (k) { var el = $('f-' + k); if (el && el.value) q.set(k, el.value); });
    root.classList.add('opacity-60');
    fetch(URL + '?' + q.toString(), {headers: {'X-Requested-With': 'fetch'}, credentials: 'same-origin'})
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (json) { data = json; if (selected && !data.states[selected]) selected = null; kpis(); paint(); detail(); lower(); })
      .catch(function () { $('detail').innerHTML = '<p class="text-xs text-rose-500">Could not load analytics. Refresh the page and try again.</p>'; })
      .finally(function () { root.classList.remove('opacity-60'); });
  }
  ['days', 'platform', 'sku'].forEach(function (k) { var el = $('f-' + k); if (el) el.addEventListener('change', load); });
  renderMetricButtons();
  load();
})();
