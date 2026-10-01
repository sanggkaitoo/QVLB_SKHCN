const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const esc = s => (s == null ? '' : s).toString().replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const runningStates = ['starting', 'waiting_login', 'logging_in', 'crawling', 'stopping'];
const toast = (...args) => window.UI.toast(...args);
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
  'p-dashboard': ['Thống kê', 'Quan sát kho dữ liệu', 'Theo dõi số văn bản, vector và phân bố loại văn bản trong kho.'],
  'p-docs': ['Kho văn bản', 'Tra cứu văn bản đã nạp', 'Tìm văn bản đang lưu trong cơ sở dữ liệu theo số ký hiệu hoặc trích yếu.'],
  'p-crawler': ['Crawler', 'Đồng bộ văn bản từ QLVB', 'Kiểm kê, tải văn bản còn thiếu và hoàn tất xác thực SSO ngay trong giao diện quản trị.'],
  'p-users': ['Tài khoản', 'Quản lý tài khoản và phân quyền', 'Thêm, sửa, khoá hoặc xoá tài khoản; mỗi vai trò có một bộ quyền cố định.'],
  'p-ai': ['Mô hình AI', 'Chọn mô hình cho từng tính năng', 'Kết nối OpenRouter, ChatGPT, Claude, Gemini; gán mô hình theo tính năng và chọn mô hình hiển thị ở bảng giá.'],
  'p-audit': ['Nhật ký', 'Nhật ký thao tác', 'Đăng nhập, thay đổi tài khoản và cấu hình AI gần đây.']
};
const PANEL_LOADERS = {
  'p-dashboard': () => loadStats(),
  'p-docs': () => searchDocs(),
  'p-crawler': () => { checkCrawlerStatus(); loadInventory(); loadItems(); },
  'p-users': () => loadUsers(),
  'p-ai': () => loadAi(),
  'p-audit': () => loadAudit(),
};
const loadedPanels = new Set();
function showPanel(target, { push = true } = {}) {
  const tab = $(`.tab[data-target="${target}"]`);
  if (!tab || tab.hidden) target = ($$('.tab[data-target]').find(t => !t.hidden) || {}).dataset?.target || 'p-dashboard';
  $$('.tab[data-target]').forEach(item => item.classList.toggle('active', item.dataset.target === target));
  $$('.panel').forEach(panel => panel.classList.toggle('active', panel.id === target));
  const [eyebrow, title, desc] = heads[target];
  $('#pageHead').innerHTML = `<div class="eyebrow">${eyebrow}</div><h2>${title}</h2><p>${desc}</p>`;
  if (push) history.replaceState(null, '', '#' + target.slice(2));
  if (!loadedPanels.has(target)) { loadedPanels.add(target); PANEL_LOADERS[target] && PANEL_LOADERS[target](); }
}
$$('.tab[data-target]').forEach(tab => tab.addEventListener('click', () => showPanel(tab.dataset.target)));
$('#refreshBtn').addEventListener('click', () => {
  const active = ($('.panel.active') || {}).id;
  if (PANEL_LOADERS[active]) PANEL_LOADERS[active]();
  toast('Đã làm mới', 'ok', { duration: 1500 });
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
// Mã loại văn bản trong kho → tên hiển thị.
const TYPE_LABELS = {
  nghi_quyet: 'Nghị quyết', quyet_dinh: 'Quyết định', chi_thi: 'Chỉ thị', quy_che: 'Quy chế', quy_dinh: 'Quy định',
  thong_cao: 'Thông cáo', thong_bao: 'Thông báo', huong_dan: 'Hướng dẫn', chuong_trinh: 'Chương trình',
  ke_hoach: 'Kế hoạch', phuong_an: 'Phương án', de_an: 'Đề án', du_an: 'Dự án', bao_cao: 'Báo cáo',
  bien_ban: 'Biên bản', to_trinh: 'Tờ trình', hop_dong: 'Hợp đồng', cong_van: 'Công văn', cong_dien: 'Công điện',
  ban_ghi_nho: 'Bản ghi nhớ', ban_thoa_thuan: 'Bản thỏa thuận', giay_uy_quyen: 'Giấy ủy quyền', giay_moi: 'Giấy mời',
  giay_gioi_thieu: 'Giấy giới thiệu', giay_nghi_phep: 'Giấy nghỉ phép', phieu_gui: 'Phiếu gửi',
  phieu_chuyen: 'Phiếu chuyển', phieu_bao: 'Phiếu báo', thu_cong: 'Thư công', khac: 'Khác',
};
const typeLabel = code => TYPE_LABELS[code] || (code ? String(code).replace(/_/g, ' ') : 'Khác');

function renderList(items, emptyText, nameKey) {
  if (!items.length) return `<div class="empty">${emptyText}</div>`;
  return items.map(item => {
    const name = nameKey === 'tag' ? '#' + esc(item.tag || 'khác') : esc(typeLabel(item.loai_vb));
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
                    <td><span class="loai">${esc(typeLabel(row.loai_vb))}</span></td>
                    <td style="color: var(--muted); line-height: 1.4;">${esc(row.trich_yeu || 'Không có trích yếu')}
                      <div style="font-size:11.5px;color:var(--faint);margin-top:4px">${fmt(row.n_files)} tệp · ${fmt(row.n_chunks)} đoạn · ${esc(row.ingest_status === 'ready' ? 'sẵn sàng' : (row.ingest_status || ''))}${row.index_version ? ' (' + esc(row.index_version) + ')' : ''}</div></td>
                </tr>
            `).join('');
        }
    } catch (err) {
        tbody.innerHTML = '<tr><td colspan="4" class="error" style="border:none;">Có lỗi xảy ra khi tải dữ liệu từ Postgres.</td></tr>';
    }

    btn.disabled = false;
    btn.innerHTML = '<svg class="ico"><use href="#a-search"/></svg>Tìm kiếm';
}


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

/* ---------- đăng nhập SSO: đếm ngược, đổi mã, huỷ ----------
   Hộp thoại KHÔNG tự đóng khi bấm gửi: hiện "Đang xác thực…", nếu sai thì báo lỗi và hiện captcha mới
   (giữ tài khoản/mật khẩu đã nhập); chỉ đóng khi đăng nhập thành công hoặc người dùng tự đóng. */
let loginModal = { open: false, version: 0, expiresAt: 0, timer: null, submitted: false, dismissed: false, closing: false };

function captchaCountdownText() {
  const left = Math.max(0, Math.round((loginModal.expiresAt - Date.now()) / 1000));
  if (!left) return 'Mã đã hết hạn, đang tạo mã mới...';
  return `Mã hết hạn sau ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')} — tự đổi mã khi hết hạn`;
}
function setSsoState(kind, text) {
  const el = $('#sso-state');
  if (!el) return;
  el.hidden = !kind;
  el.className = 'sso-state ' + (kind || '');
  el.innerHTML = kind === 'busy' ? `<span class="spin"></span>${esc(text)}` : esc(text || '');
}
function setSsoBusy(busy) {
  const form = $('#sso-form');
  if (form) form.setAttribute('aria-busy', busy ? 'true' : 'false');
  const confirm = Swal.getConfirmButton(), deny = Swal.getDenyButton();
  if (confirm) { confirm.disabled = busy; confirm.textContent = busy ? 'Đang xác thực…' : 'Gửi và tiếp tục'; }
  if (deny) deny.disabled = busy;
}
function updateLoginModal(data) {
  if (!loginModal.open || loginModal.closing) return;
  const status = data.status;
  if (status === 'logging_in') {
    setSsoBusy(true);
    setSsoState('busy', 'Đang xác thực với cổng SSO…');
    return;
  }
  if (status === 'waiting_login') {
    if ((data.captcha_version || 0) !== loginModal.version && data.captcha_b64) {
      loginModal.version = data.captcha_version || 0;
      const img = $('#swal-captcha-img');
      if (img) { img.src = 'data:image/png;base64,' + data.captcha_b64; img.classList.remove('stale'); }
      const input = $('#swal-captcha');
      if (input) { input.value = ''; if (loginModal.submitted) input.focus(); }
    }
    if (data.captcha_expires_in != null) loginModal.expiresAt = Date.now() + data.captcha_expires_in * 1000;
    if (loginModal.submitted && data.login_error) {
      setSsoBusy(false);
      setSsoState('bad', data.login_error + ' Nhập lại mã captcha mới để thử tiếp.');
      loginModal.submitted = false;
    } else if (!loginModal.submitted) {
      setSsoBusy(false);
    }
    return;
  }
  // Rời trạng thái đăng nhập: thành công (đang chạy/hoàn tất) hoặc kết thúc vì lỗi/huỷ.
  loginModal.closing = true;
  if (['crawling', 'done', 'starting'].includes(status)) {
    setSsoBusy(true);
    setSsoState('ok', 'Đăng nhập thành công. Crawler đang tiếp tục…');
    setTimeout(() => { Swal.close(); toast('Đăng nhập SSO thành công', 'ok'); }, 1100);
  } else {
    Swal.close();
    if (status === 'error') toast(data.message || 'Đăng nhập SSO thất bại.', 'bad');
  }
}
async function postAdmin(url, payload) {
  const res = await fetch(url, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload || {}) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.status === 'error') throw new Error(data.message || (typeof data.detail === 'string' ? data.detail : '') || 'Máy chủ từ chối yêu cầu.');
  return data;
}
function reopenSsoLogin() {
  loginModal.dismissed = false;
  checkCrawlerStatus();
}
function openLoginModal(data) {
  loginModal = { open: true, version: data.captcha_version || 0, timer: null, submitted: false, dismissed: false, closing: false,
                 expiresAt: Date.now() + (data.captcha_expires_in ?? 180) * 1000 };
  $('#reopenLogin').hidden = true;
  Swal.fire({
    title: 'Xác thực SSO',
    html: `
      <div id="sso-form" class="sso-form">
      <p style="font-size:13px;margin:0 0 12px">Nhập thông tin để crawler tiếp tục. Mật khẩu chỉ dùng cho phiên này và không được lưu.</p>
      <div id="sso-state" class="sso-state" hidden></div>
      <input id="swal-user" class="swal2-input" autocomplete="username" placeholder="Tài khoản SSO">
      <input id="swal-pass" class="swal2-input" type="password" autocomplete="current-password" placeholder="Mật khẩu">
      <div class="sso-captcha"><img id="swal-captcha-img" src="data:image/png;base64,${esc(data.captcha_b64 || '')}" alt="Mã captcha"></div>
      <div id="swal-countdown" class="sso-count"></div>
      <input id="swal-captcha" class="swal2-input" placeholder="Nhập mã captcha" style="text-align:center;letter-spacing:2px" autocomplete="off">
      </div>`,
    confirmButtonText: 'Gửi và tiếp tục',
    showDenyButton: true,
    denyButtonText: 'Đổi mã',
    showCancelButton: true,
    cancelButtonText: 'Huỷ đăng nhập',
    showCloseButton: true,
    closeButtonAriaLabel: 'Đóng (crawler vẫn chờ đăng nhập)',
    allowOutsideClick: false,
    allowEscapeKey: true,
    didOpen: () => {
      const tick = () => { const el = $('#swal-countdown'); if (el) el.textContent = captchaCountdownText(); };
      tick();
      loginModal.timer = setInterval(tick, 1000);
      $('#swal-captcha').addEventListener('keydown', e => { if (e.key === 'Enter') Swal.clickConfirm(); });
      if (data.login_error) setSsoState('bad', data.login_error);
      $('#swal-user').focus();
    },
    willClose: () => { clearInterval(loginModal.timer); loginModal.open = false; },
    preDeny: async () => {
      try {
        await postAdmin('/api/admin/crawl/captcha/refresh');
        const img = $('#swal-captcha-img'); if (img) img.classList.add('stale');
        const el = $('#swal-countdown'); if (el) el.textContent = 'Đang tạo mã mới...';
      } catch (err) { Swal.showValidationMessage(err.message); }
      return false; // giữ hộp thoại mở; ảnh mới được cập nhật qua trạng thái
    },
    preConfirm: async () => {
      const username = $('#swal-user').value.trim();
      const password = $('#swal-pass').value;
      const captcha = $('#swal-captcha').value.trim();
      if (!username || !password || !captcha) {
        Swal.showValidationMessage('Vui lòng nhập đầy đủ tài khoản, mật khẩu và captcha.');
        return false;
      }
      Swal.resetValidationMessage();
      try {
        await postAdmin('/api/admin/crawl/submit_login', { username, password, captcha });
        loginModal.submitted = true;
        setSsoBusy(true);
        setSsoState('busy', 'Đang xác thực với cổng SSO…');
        startPolling(1000);
      } catch (err) {
        Swal.showValidationMessage(err.message);
      }
      return false; // không đóng: chờ kết quả đăng nhập
    }
  }).then(async result => {
    if (result.dismiss === Swal.DismissReason.cancel) {
      try { await postAdmin('/api/admin/crawl/cancel'); } catch (err) { /* đã kết thúc */ }
      checkCrawlerStatus();
    } else if (result.dismiss === Swal.DismissReason.close || result.dismiss === Swal.DismissReason.esc) {
      // Người dùng tự đóng: không bật lại mỗi lần làm mới trạng thái.
      if (!loginModal.closing) {
        loginModal.dismissed = true;
        $('#reopenLogin').hidden = false;
        toast('Crawler vẫn đang chờ đăng nhập SSO. Bấm “Mở lại hộp đăng nhập” khi sẵn sàng.', 'info');
      }
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
    const waiting = ['waiting_login', 'logging_in'].includes(data.status);
    if (!waiting) { loginModal.dismissed = false; $('#reopenLogin').hidden = true; }
    if (loginModal.open) {
      updateLoginModal(data);
    } else if (data.status === 'waiting_login' && !loginModal.dismissed && !document.querySelector('.swal2-container')) {
      openLoginModal(data);
    } else if (waiting && loginModal.dismissed) {
      $('#reopenLogin').hidden = false;
    }
    if (['done', 'error', 'cancelled'].includes(data.status) && runningStates.includes(lastStatus)) {
      stopPolling();
      if (notify) {
        const type = { done: 'ok', error: 'bad', cancelled: 'info' }[data.status];
        const title = { done: 'Hoàn tất', error: 'Crawler dừng do lỗi', cancelled: 'Đã dừng' }[data.status];
        toast(`${title}${data.message ? ': ' + data.message : ''}`, type, { duration: 7000 });
      }
      loadStats();
      loadInventory();
      loadItems();
    }
    if (runningStates.includes(data.status)) startPolling(data.status === 'logging_in' ? 1000 : 2000);
    if (data.job && data.job.startsWith('api') && runningStates.includes(data.status)) loadInventoryThrottled();
    lastStatus = data.status || 'idle';
  } catch (err) {
    setApiState(false);
  }
}
let pollEvery = 0;
function startPolling(ms = 2000) {
  if (pollInterval && pollEvery === ms) return;
  if (pollInterval) clearInterval(pollInterval);
  pollEvery = ms;
  pollInterval = setInterval(() => checkCrawlerStatus({ notify: true }), ms);
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
      const src = d.source_documents == null ? '—' : fmt(d.source_documents);
      return `<tr><th>${esc(d.label || key)}</th><td>${src}</td><td>${fmt(d.inventoried)}</td><td class="ok">${fmt(d.done)}</td><td>${fmt(d.pending)}</td><td class="${d.failed ? 'bad' : ''}">${fmt(d.failed)}</td><td>${fmt(d.skipped)}</td></tr>`;
    });
    $('#invBody').innerHTML = rows.join('');
    const failedTotal = ['di', 'den'].reduce((sum, key) => sum + Number((data[key] || {}).failed || 0) + Number((data[key] || {}).skipped || 0), 0);
    const retryAll = $('#b-retry-all');
    retryAll.hidden = !failedTotal;
    retryAll.textContent = `Tải lại lỗi & bỏ qua (${fmt(failedTotal)})`;
    const notes = ['di', 'den'].map(key => {
      const d = data[key] || {};
      if (!d.last_sweep_at) return `${esc(d.label || key)}: chưa kiểm kê`;
      const when = new Date(d.last_sweep_at).toLocaleString('vi-VN');
      const dup = (d.source_rows || 0) - (d.source_documents || 0);
      return `${esc(d.label || key)}: kiểm kê ${d.last_sweep_mode === 'quick' ? 'nhanh' : 'đầy đủ'} lúc ${esc(when)}${d.last_sweep_completed === false ? ' (chưa trọn vẹn)' : ''}${dup > 0 ? ` — danh sách QLVB có ${fmt(dup)} dòng lặp` : ''}`;
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
        <td><span class="ky">${esc(r.so_ky_hieu || '—')}</span><div class="sub">${esc(r.ngay ? r.ngay.slice(0, 10).split('-').reverse().join('/') : '')} · ${r.direction === 'di' ? 'Đi' : 'Đến'}</div>${r.co_quan ? `<div class="sub ellipsis">${esc(r.co_quan)}</div>` : ''}</td>
        <td>${esc(r.trich_yeu || '')}${noteHtml(r)}</td>
        <td class="st-cell"><span class="st st-${esc(r.status)}">${esc(STATUS_TEXT[r.status] || r.status)}</span>${r.attempts ? `<div class="sub">${fmt(r.attempts)} lần thử</div>` : ''}
          ${['failed', 'done', 'skipped'].includes(r.status) ? `<button class="btn btn-sm" type="button" onclick="retryItem(${Number(r.id)}, this)">Tải lại</button>` : ''}</td>
      </tr>`).join('') : '<tr><td colspan="3" class="empty">Không có văn bản nào ở trạng thái này.</td></tr>';
  } catch (err) {
    $('#itemsBody').innerHTML = '<tr><td colspan="3" class="error">Không tải được danh sách.</td></tr>';
  }
}
// Ghi chú lỗi/bỏ qua: rút gọn lỗi kỹ thuật dài (vết ngăn xếp của QLVB), rê chuột để xem đầy đủ.
function noteHtml(r) {
  const note = r.last_error || r.skip_reason;
  if (!note) return '';
  const short = note.split(/\r?\n|\s+at\s+/)[0].slice(0, 180) + (note.length > 180 ? '…' : '');
  return `<div class="sub ${r.status === 'failed' ? 'bad' : ''}" title="${esc(note)}">${esc(short)}</div>`;
}

async function retryAllFailed(button) {
  const ok = await Swal.fire({
    title: 'Tải lại văn bản lỗi và bị bỏ qua?',
    text: 'Mọi văn bản đang ở trạng thái Lỗi hoặc Bỏ qua sẽ được xét lại theo quy tắc hiện hành và tải lại ngay (mới nhất trước). Có thể bấm Dừng bất cứ lúc nào.',
    icon: 'question', showCancelButton: true, confirmButtonText: 'Tải lại', cancelButtonText: 'Huỷ',
  });
  if (!ok.isConfirmed) return;
  button.disabled = true;
  try {
    const data = await postAdmin('/api/admin/qlvb/retry_failed');
    toast(data.message, data.started ? 'info' : (data.count ? 'warn' : 'ok'), { duration: 6000 });
    if (data.started) {
      lastStatus = 'starting';
      startPolling();
      checkCrawlerStatus({ notify: true });
    }
    loadInventory();
    loadItems();
  } catch (err) {
    toast('Không thể tải lại: ' + err.message, 'bad');
  } finally {
    button.disabled = false;
  }
}

async function retryItem(id, button) {
  if (button) { button.disabled = true; button.innerHTML = '<span class="spin dark"></span>Đang gửi'; }
  try {
    const data = await postAdmin(`/api/admin/qlvb/items/${id}/retry`);
    // Giữ dòng trong danh sách, đổi trạng thái để người dùng thấy văn bản đang được xử lý.
    const row = button && button.closest('tr');
    if (row) {
      const cell = row.querySelector('.st');
      if (cell) { cell.className = 'st st-processing'; cell.textContent = data.started ? 'Đang tải lại' : 'Chờ tải'; }
      button.remove();
    }
    toast(data.message || 'Đã đưa văn bản vào hàng chờ.', data.started ? 'info' : 'warn', { duration: 6000 });
    if (data.started) {
      lastStatus = 'starting';
      startPolling();
      checkCrawlerStatus({ notify: true });
    }
    loadInventory();
  } catch (err) {
    if (button) { button.disabled = false; button.textContent = 'Tải lại'; }
    toast('Không thể tải lại: ' + err.message, 'bad');
  }
}
async function launchJob(url, payload, message) {
  try {
    const data = await postAdmin(url, payload);
    renderStatus({ status: 'starting', message: message || data.message });
    lastStatus = 'starting';
    startPolling();
    checkCrawlerStatus({ notify: true });
  } catch (err) {
    toast('Không thể bắt đầu: ' + (err.message || 'kiểm tra backend rồi thử lại.'), 'bad');
  }
}
function apiSync(mode) {
  const pageSize = parseInt($('#apiPageSize').value, 10) || 100;
  try { localStorage.setItem('qlvb-page-size', String(pageSize)); } catch (err) { /* không bắt buộc */ }
  launchJob('/api/admin/qlvb/sync', { direction: $('#apiDirection').value, mode, page_size: pageSize },
            mode === 'quick' ? 'Đang kiểm tra văn bản mới...' : 'Đang kiểm kê đầy đủ...');
}
async function apiDownload() {
  const limit = parseInt($('#apiLimit').value, 10);
  if (!Number.isFinite(limit) || limit < 0) { toast('Số lượng chưa hợp lệ: nhập 0 để tải tất cả, hoặc số văn bản cần tải.', 'warn'); return; }
  if (limit === 0) {
    const ok = await Swal.fire({ title: 'Tải tất cả văn bản còn thiếu?', text: 'Có thể mất nhiều giờ. Bạn có thể bấm Dừng bất cứ lúc nào và chạy tiếp sau.', icon: 'warning', showCancelButton: true, confirmButtonText: 'Bắt đầu', cancelButtonText: 'Hủy' });
    if (!ok.isConfirmed) return;
  }
  launchJob('/api/admin/qlvb/download', { direction: $('#apiDirection').value, limit, retry_failed: $('#apiRetry').checked });
}
async function stopCrawl() {
  try { await postAdmin('/api/admin/crawl/cancel'); checkCrawlerStatus({ notify: true }); }
  catch (err) { toast('Không thể dừng: ' + err.message, 'bad'); }
}
async function startCrawl(payload) {
  try {
    const data = await postAdmin('/api/admin/crawl/start', payload);
    renderStatus({ status: 'starting', message: data.message || 'Đã khởi động crawler.' });
    lastStatus = 'starting';
    startPolling();
    checkCrawlerStatus({ notify: true });
  } catch (err) {
    toast('Không thể bắt đầu: ' + (err.message || 'kiểm tra backend rồi thử lại.'), 'bad');
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
    toast('Giới hạn chưa hợp lệ: nhập số từ 1 đến 1000.', 'warn');
    return;
  }
  startCrawl({ limit, mode });
}

