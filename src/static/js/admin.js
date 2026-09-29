const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const esc = s => (s == null ? '' : s).toString().replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const runningStates = ['starting', 'waiting_login', 'logging_in', 'crawling', 'stopping'];
let pollInterval = null;
let lastStatus = 'idle';

async function fetchWithTimeout(url, options = {}, timeoutMs = 8000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function setTheme(theme) {
  const isLight = theme === 'light';
  document.documentElement.setAttribute('data-theme', isLight ? 'light' : 'dark');
  localStorage.setItem('qlvb-theme', isLight ? 'light' : 'dark');
  $('#themeIcon').textContent = isLight ? '☾' : '☀';
  $('#themeText').textContent = isLight ? 'Giao diện Tối' : 'Giao diện Sáng';
}
function toggleTheme() {
  setTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
}
setTheme(localStorage.getItem('qlvb-theme') || 'light');

const heads = {
  'p-dashboard': ['Admin Console', 'Quan sát dữ liệu và vận hành crawler', 'Theo dõi trạng thái kho văn bản, kiểm tra phân bố loại văn bản và khởi chạy crawler khi cần nạp dữ liệu mới.'],
  'p-crawler': ['Crawler', 'Điều khiển luồng nạp dữ liệu', 'Chọn phạm vi chạy, theo dõi trạng thái và hoàn tất xác thực SSO ngay trong giao diện quản trị.'],
  'p-docs': ['Kho văn bản', 'Tra cứu văn bản đã nạp', 'Quản lý và tìm kiếm các văn bản (theo số ký hiệu, trích yếu) hiện đang lưu trữ trong cơ sở dữ liệu.']
};
$$('.tab[data-target]').forEach(tab => {
  tab.addEventListener('click', () => {
    $$('.tab[data-target]').forEach(item => item.classList.remove('active'));
    tab.classList.add('active');
    $$('.panel').forEach(panel => panel.classList.remove('active'));
    const target = tab.dataset.target;
    $('#' + target).classList.add('active');
    const [eyebrow, title, desc] = heads[target];
    $('#pageHead').innerHTML = `<div class="eyebrow">${eyebrow}</div><h2>${title}</h2><p>${desc}</p>`;
  });
});

function setApiState(ok) {
  $('#apiDot').classList.toggle('bad', !ok);
  $('#apiState').textContent = ok ? 'API sẵn sàng' : 'API chưa phản hồi';
}
function fmt(n) {
  return Number(n || 0).toLocaleString('vi-VN');
}
function setStatsLoading() {
  ['s-docs','s-vecs','s-types','s-tags'].forEach(id => $('#' + id).textContent = '--');
  $('#list-loai').innerHTML = '<div class="shimmer" style="width:85%"></div><div class="shimmer" style="width:70%"></div><div class="shimmer" style="width:92%"></div>';
  $('#list-tags').innerHTML = '<div class="shimmer" style="width:80%"></div><div class="shimmer" style="width:72%"></div><div class="shimmer" style="width:88%"></div>';
}
function renderList(items, emptyText, nameKey) {
  if (!items.length) return `<div class="empty">${emptyText}</div>`;
  return items.map(item => {
    const name = nameKey === 'tag' ? '#' + esc(item.tag || 'khac') : esc(item.loai_vb || 'khac');
    return `<div class="list-item"><span class="list-name">${name}</span><span class="tag">${fmt(item.cnt)} VB</span></div>`;
  }).join('');
}
async function loadStats() {
  setStatsLoading();
  try {
    const res = await fetchWithTimeout('/api/admin/stats');
    if (!res.ok) throw new Error('stats');
    const data = await res.json();
    const byLoai = Array.isArray(data.by_loai) ? data.by_loai : [];
    const tags = Array.isArray(data.tags) ? data.tags : [];
    $('#s-docs').textContent = fmt(data.total_docs);
    $('#s-vecs').textContent = fmt(data.total_vectors);
    $('#s-types').textContent = fmt(byLoai.length);
    $('#s-tags').textContent = fmt(tags.length);
    $('#loaiHint').textContent = `${fmt(byLoai.length)} nhóm`;
    $('#tagHint').textContent = `${fmt(tags.length)} tags`;
    $('#list-loai').innerHTML = renderList(byLoai, 'Chưa có dữ liệu loại văn bản.', 'loai_vb');
    $('#list-tags').innerHTML = renderList(tags, 'Chưa có dữ liệu chuyên đề.', 'tag');
    setApiState(true);
  } catch (err) {
    $('#list-loai').innerHTML = '<div class="error">Không tải được thống kê. Kiểm tra phiên đăng nhập hoặc dịch vụ backend.</div>';
    $('#list-tags').innerHTML = '<div class="error">Dữ liệu chuyên đề chưa phản hồi.</div>';
    $('#loaiHint').textContent = 'Lỗi tải';
    $('#tagHint').textContent = 'Lỗi tải';
    setApiState(false);
  }
}

async function searchDocs() {
    const q = $('#adminSearchInput').value.trim();
    const tbody = $('#adminDocsBody');
    const btn = $('#b-docs-search');

    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span>Đang tìm...';
    tbody.innerHTML = '<tr><td colspan="4" class="empty"><div class="shimmer" style="width: 100%;"></div></td></tr>';

    try {
        const res = await fetchWithTimeout(`/api/admin/docs?q=${encodeURIComponent(q)}`);
        if (!res.ok) throw new Error('Lỗi mạng');
        const data = await res.json();

        if (!data || data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="empty">Không tìm thấy văn bản nào phù hợp.</td></tr>';
        } else {
            tbody.innerHTML = data.map(row => `
                <tr>
                    <td><span class="ky">${esc(row.so_ky_hieu || 'Chưa rõ')}</span></td>
                    <td class="mono" style="color: var(--muted); font-size: 13px;">${esc(row.ngay_ban_hanh || '---')}</td>
                    <td><span class="loai">${esc(row.loai_vb || 'Khác')}</span></td>
                    <td style="color: var(--muted); line-height: 1.4;">${esc(row.trich_yeu || 'Không có trích yếu')}
                      <div style="font-size:11.5px;color:var(--faint);margin-top:4px">${fmt(row.n_files)} tệp · ${fmt(row.n_chunks)} đoạn · ${esc(row.ingest_status === 'ready' ? 'sẵn sàng' : (row.ingest_status || ''))}${row.index_version ? ' (' + esc(row.index_version) + ')' : ''}</div></td>
                </tr>
            `).join('');
        }
    } catch (err) {
        tbody.innerHTML = '<tr><td colspan="4" class="error" style="border:none;">Có lỗi xảy ra khi tải dữ liệu từ Postgres.</td></tr>';
    }

    btn.disabled = false;
    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>Tìm kiếm';
}

// Tự động load 100 văn bản mới nhất khi người dùng click vào tab Kho văn bản
document.querySelector('.tab[data-target="p-docs"]').addEventListener('click', () => {
    if ($('#adminDocsBody').innerHTML.includes('Đang kết nối') || $('#adminDocsBody').innerHTML.includes('Bấm "Tìm kiếm"')) {
        searchDocs();
    }
});

function renderStatus(data) {
  const status = data.status || 'idle';
  const message = data.message || 'Chưa có tiến trình đang chạy.';
  const dot = $('#statusDot');
  const label = $('#statusLabel');
  dot.className = 'dot';
  if (status === 'error') dot.classList.add('bad');
  else if (runningStates.includes(status)) dot.classList.add('warn');
  const labels = { idle: 'Sẵn sàng', starting: 'Đang khởi động', waiting_login: 'Chờ SSO', logging_in: 'Đang đăng nhập', crawling: 'Đang chạy', stopping: 'Đang dừng', done: 'Hoàn tất', cancelled: 'Đã dừng', error: 'Có lỗi' };
  label.textContent = labels[status] || status;
  $('#statusMessage').textContent = message;
  const busy = runningStates.includes(status);
  $$('.crawl-action').forEach(btn => btn.disabled = busy);
  const stop = $('#b-stop');
  if (stop) stop.disabled = !busy;
  const progress = data.progress || {};
  const box = $('#progressBox');
  if (box) {
    const total = Number(progress.total) || 0, done = Number(progress.done) || 0;
    box.hidden = !(busy && total > 0);
    if (total > 0) {
      $('#progressBar').style.width = Math.min(100, Math.round(done * 100 / total)) + '%';
      const phase = progress.phase === 'inventory' ? 'Kiểm kê' : 'Tải và nạp';
      const extra = progress.phase === 'download' ? ` · ${fmt(progress.ingested || 0)} đã nạp, ${fmt(progress.failed || 0)} lỗi, ${fmt(progress.skipped || 0)} bỏ qua` : '';
      $('#progressText').textContent = `${phase}: ${fmt(done)}/${fmt(total)}${extra}`;
    }
  }
  $$('.step').forEach(step => {
    const key = step.dataset.step;
    step.classList.remove('active', 'done');
    if (key === status || (status === 'logging_in' && key === 'waiting_login')) step.classList.add('active');
    if ((status === 'crawling' || status === 'done') && ['starting','waiting_login'].includes(key)) step.classList.add('done');
    if (status === 'done' && key !== 'done') step.classList.add('done');
    if (status === 'done' && key === 'done') step.classList.add('active');
  });
}

/* ---------- đăng nhập SSO: đếm ngược, đổi mã, huỷ ---------- */
let loginModal = { open: false, version: 0, expiresAt: 0, timer: null };

function captchaCountdownText() {
  const left = Math.max(0, Math.round((loginModal.expiresAt - Date.now()) / 1000));
  if (!left) return 'Mã đã hết hạn, đang tạo mã mới...';
  return `Mã hết hạn sau ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')} — tự đổi mã khi hết hạn`;
}
function updateLoginModal(data) {
  if (!loginModal.open) return;
  if (data.status !== 'waiting_login') {
    if (data.status !== 'logging_in') Swal.close();
    return;
  }
  if ((data.captcha_version || 0) !== loginModal.version && data.captcha_b64) {
    loginModal.version = data.captcha_version || 0;
    const img = $('#swal-captcha-img');
    if (img) img.src = 'data:image/png;base64,' + data.captcha_b64;
    const input = $('#swal-captcha');
    if (input) { input.value = ''; input.focus(); }
  }
  if (data.captcha_expires_in != null) loginModal.expiresAt = Date.now() + data.captcha_expires_in * 1000;
}
async function postAdmin(url, payload) {
  const res = await fetch(url, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload || {}) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.status === 'error') throw new Error(data.message || data.detail || 'Máy chủ từ chối yêu cầu.');
  return data;
}
function openLoginModal(data) {
  loginModal = { open: true, version: data.captcha_version || 0, timer: null,
                 expiresAt: Date.now() + (data.captcha_expires_in ?? 180) * 1000 };
  Swal.fire({
    title: 'Xác thực SSO',
    html: `
      <p style="font-size:13px;color:var(--muted);margin:0 0 14px">Nhập thông tin để crawler tiếp tục. Mật khẩu chỉ dùng cho phiên này và không được lưu.</p>
      <input id="swal-user" class="swal2-input" autocomplete="username" placeholder="Tài khoản SSO">
      <input id="swal-pass" class="swal2-input" type="password" autocomplete="current-password" placeholder="Mật khẩu">
      <div style="margin:14px 0 4px;text-align:center"><img id="swal-captcha-img" src="data:image/png;base64,${esc(data.captcha_b64 || '')}" alt="Captcha" style="width:160px;height:auto;border:1px solid var(--line);border-radius:6px;background:#fff"></div>
      <div id="swal-countdown" style="font-size:12px;color:var(--muted);margin-bottom:4px"></div>
      <input id="swal-captcha" class="swal2-input" placeholder="Nhập mã captcha" style="text-align:center;letter-spacing:2px">`,
    confirmButtonText: 'Gửi và tiếp tục',
    showDenyButton: true,
    denyButtonText: 'Đổi mã',
    showCancelButton: true,
    cancelButtonText: 'Huỷ đăng nhập',
    allowOutsideClick: false,
    allowEscapeKey: false,
    didOpen: () => {
      const tick = () => { const el = $('#swal-countdown'); if (el) el.textContent = captchaCountdownText(); };
      tick();
      loginModal.timer = setInterval(tick, 1000);
    },
    willClose: () => { clearInterval(loginModal.timer); loginModal.open = false; },
    preDeny: async () => {
      try {
        await postAdmin('/api/admin/crawl/captcha/refresh');
        const el = $('#swal-countdown'); if (el) el.textContent = 'Đang tạo mã mới...';
      } catch (err) { Swal.showValidationMessage(err.message); }
      return false; // giữ hộp thoại mở; ảnh mới được cập nhật qua trạng thái
    },
    preConfirm: () => {
      const username = $('#swal-user').value.trim();
      const password = $('#swal-pass').value;
      const captcha = $('#swal-captcha').value.trim();
      if (!username || !password || !captcha) {
        Swal.showValidationMessage('Vui lòng nhập đầy đủ tài khoản, mật khẩu và captcha.');
        return false;
      }
      return { username, password, captcha };
    }
  }).then(async result => {
    if (result.isConfirmed) {
      await fetch('/api/admin/crawl/submit_login', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(result.value) });
      Swal.fire({ title: 'Đã gửi thông tin', text: 'Đang xác thực với SSO...', icon: 'info', showConfirmButton: false, timer: 1800 });
    } else if (result.dismiss === Swal.DismissReason.cancel) {
      try { await postAdmin('/api/admin/crawl/cancel'); } catch (err) { /* đã kết thúc */ }
      checkCrawlerStatus();
    }
  });
}
async function checkCrawlerStatus({ notify = false } = {}) {
  try {
    const res = await fetchWithTimeout('/api/admin/crawl/status');
    if (!res.ok) throw new Error('status');
    const data = await res.json();
    renderStatus(data);
    setApiState(true);
    if (data.status === 'waiting_login') {
      if (!document.querySelector('.swal2-container')) openLoginModal(data);
      else updateLoginModal(data);
    } else if (loginModal.open) {
      updateLoginModal(data);
    }
    if (['done', 'error', 'cancelled'].includes(data.status) && runningStates.includes(lastStatus)) {
      stopPolling();
      if (notify) {
        const icon = { done: 'success', error: 'error', cancelled: 'info' }[data.status];
        const title = { done: 'Hoàn tất', error: 'Crawler dừng do lỗi', cancelled: 'Đã dừng' }[data.status];
        Swal.fire(title, data.message || '', icon);
      }
      loadStats();
      loadInventory();
      loadItems();
    }
    if (runningStates.includes(data.status)) startPolling();
    if (data.job && data.job.startsWith('api') && runningStates.includes(data.status)) loadInventoryThrottled();
    lastStatus = data.status || 'idle';
  } catch (err) {
    setApiState(false);
  }
}
function startPolling() {
  if (!pollInterval) pollInterval = setInterval(() => checkCrawlerStatus({ notify: true }), 2000);
}
function stopPolling() {
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = null;
}

/* ---------- crawler API: kiểm kê, tải, danh sách theo dõi ---------- */
const STATUS_TEXT = { pending: 'Chờ tải', processing: 'Đang xử lý', done: 'Đã nạp', failed: 'Lỗi', skipped: 'Bỏ qua' };
let inventoryLoadedAt = 0;
function loadInventoryThrottled() { if (Date.now() - inventoryLoadedAt > 8000) loadInventory(); }
async function loadInventory() {
  inventoryLoadedAt = Date.now();
  try {
    const res = await fetchWithTimeout('/api/admin/qlvb/summary');
    if (!res.ok) throw new Error('summary');
    const data = await res.json();
    const rows = ['di', 'den'].map(key => {
      const d = data[key] || {};
      const src = d.source_total == null ? '—' : fmt(d.source_total);
      return `<tr><th>${esc(d.label || key)}</th><td>${src}</td><td>${fmt(d.inventoried)}</td><td class="ok">${fmt(d.done)}</td><td>${fmt(d.pending)}</td><td class="${d.failed ? 'bad' : ''}">${fmt(d.failed)}</td><td>${fmt(d.skipped)}</td></tr>`;
    });
    $('#invBody').innerHTML = rows.join('');
    const notes = ['di', 'den'].map(key => {
      const d = data[key] || {};
      if (!d.last_sweep_at) return `${esc(d.label || key)}: chưa kiểm kê`;
      const when = new Date(d.last_sweep_at).toLocaleString('vi-VN');
      return `${esc(d.label || key)}: kiểm kê ${d.last_sweep_mode === 'quick' ? 'nhanh' : 'đầy đủ'} lúc ${esc(when)}${d.last_sweep_completed === false ? ' (chưa trọn vẹn)' : ''}`;
    });
    $('#invNote').innerHTML = notes.join(' · ');
  } catch (err) {
    $('#invBody').innerHTML = '<tr><td colspan="7" class="error">Không tải được số liệu kiểm kê.</td></tr>';
  }
}
async function loadItems() {
  const status = $('#itemStatus').value, q = $('#itemQuery').value.trim();
  const params = new URLSearchParams({ limit: '100' });
  if (status) params.set('status', status);
  if (q) params.set('q', q);
  $('#csvLink').href = '/api/admin/qlvb/items.csv' + (status ? '?status=' + encodeURIComponent(status) : '');
  try {
    const res = await fetchWithTimeout('/api/admin/qlvb/items?' + params.toString());
    if (!res.ok) throw new Error('items');
    const rows = await res.json();
    $('#itemsBody').innerHTML = rows.length ? rows.map(r => `
      <tr>
        <td><span class="ky">${esc(r.so_ky_hieu || '—')}</span><div class="sub">${r.direction === 'di' ? 'Đi' : 'Đến'}${r.co_quan ? ' · ' + esc(r.co_quan) : ''}</div></td>
        <td class="mono">${esc(r.ngay || '')}</td>
        <td>${esc(r.trich_yeu || '')}${(r.last_error || r.skip_reason) ? `<div class="sub ${r.status === 'failed' ? 'bad' : ''}">${esc(r.last_error || r.skip_reason)}</div>` : ''}</td>
        <td><span class="st st-${esc(r.status)}">${esc(STATUS_TEXT[r.status] || r.status)}</span>${r.attempts ? `<div class="sub">${fmt(r.attempts)} lần thử</div>` : ''}</td>
        <td>${['failed', 'done', 'skipped'].includes(r.status) ? `<button class="btn btn-sm" type="button" onclick="retryItem(${Number(r.id)})">Tải lại</button>` : ''}</td>
      </tr>`).join('') : '<tr><td colspan="5" class="empty">Không có văn bản nào ở trạng thái này.</td></tr>';
  } catch (err) {
    $('#itemsBody').innerHTML = '<tr><td colspan="5" class="error">Không tải được danh sách.</td></tr>';
  }
}
async function retryItem(id) {
  try { await postAdmin(`/api/admin/qlvb/items/${id}/retry`); loadItems(); loadInventory(); }
  catch (err) { Swal.fire('Không thể đưa lại hàng chờ', err.message, 'error'); }
}
async function launchJob(url, payload, message) {
  try {
    const data = await postAdmin(url, payload);
    renderStatus({ status: 'starting', message: message || data.message });
    lastStatus = 'starting';
    startPolling();
    checkCrawlerStatus({ notify: true });
  } catch (err) {
    Swal.fire('Không thể bắt đầu', err.message || 'Kiểm tra backend rồi thử lại.', 'error');
  }
}
function apiSync(mode) {
  launchJob('/api/admin/qlvb/sync', { direction: $('#apiDirection').value, mode },
            mode === 'quick' ? 'Đang kiểm tra văn bản mới...' : 'Đang kiểm kê đầy đủ...');
}
async function apiDownload() {
  const limit = parseInt($('#apiLimit').value, 10);
  if (!Number.isFinite(limit) || limit < 0) { Swal.fire('Số lượng chưa hợp lệ', 'Nhập 0 để tải tất cả, hoặc số văn bản cần tải.', 'error'); return; }
  if (limit === 0) {
    const ok = await Swal.fire({ title: 'Tải tất cả văn bản còn thiếu?', text: 'Có thể mất nhiều giờ. Bạn có thể bấm Dừng bất cứ lúc nào và chạy tiếp sau.', icon: 'warning', showCancelButton: true, confirmButtonText: 'Bắt đầu', cancelButtonText: 'Hủy' });
    if (!ok.isConfirmed) return;
  }
  launchJob('/api/admin/qlvb/download', { direction: $('#apiDirection').value, limit, retry_failed: $('#apiRetry').checked });
}
async function stopCrawl() {
  try { await postAdmin('/api/admin/crawl/cancel'); checkCrawlerStatus({ notify: true }); }
  catch (err) { Swal.fire('Không thể dừng', err.message, 'error'); }
}
async function startCrawl(payload) {
  try {
    const res = await fetch('/api/admin/crawl/start', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    const data = await res.json();
    if (!res.ok || data.status === 'error') throw new Error(data.message || 'Không khởi động được crawler.');
    renderStatus({ status: 'starting', message: data.message || 'Đã khởi động crawler.' });
    lastStatus = 'starting';
    startPolling();
    checkCrawlerStatus({ notify: true });
  } catch (err) {
    Swal.fire('Không thể bắt đầu', err.message || 'Kiểm tra backend rồi thử lại.', 'error');
  }
}
async function runFullCrawl(mode = 'all') {
  const names = { all: 'văn bản đi và đến', di: 'văn bản đi', den: 'văn bản đến' };
  const result = await Swal.fire({
    title: `Chạy toàn bộ ${names[mode]}?`,
    text: 'Tác vụ có thể mất nhiều thời gian và có thể cần nhập captcha ở bước đầu.',
    icon: 'warning',
    showCancelButton: true,
    confirmButtonText: 'Bắt đầu',
    cancelButtonText: 'Hủy'
  });
  if (result.isConfirmed) startCrawl({ limit: 0, mode });
}
function runDemoCrawl(mode) {
  const limit = parseInt($('#demoLimit').value, 10);
  if (!Number.isFinite(limit) || limit < 1 || limit > 1000) {
    Swal.fire('Giới hạn chưa hợp lệ', 'Nhập số từ 1 đến 1000 để chạy kiểm thử.', 'error');
    return;
  }
  startCrawl({ limit, mode });
}

window.addEventListener('DOMContentLoaded', () => {
  loadStats();
  checkCrawlerStatus();
  loadInventory();
  loadItems();
});
