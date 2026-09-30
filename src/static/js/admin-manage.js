/* Trang quản trị — tài khoản, mô hình AI, nhật ký và khởi động (dùng các hàm chung trong admin.js). */

/* =====================================================================
   TÀI KHOẢN
   ===================================================================== */
let ROLES = [], USERS = [], editingUser = null;
const fmtTime = v => v ? new Date(v).toLocaleString('vi-VN', { dateStyle: 'short', timeStyle: 'short' }) : '—';
async function getJson(url) {
  const res = await fetchWithTimeout(url, {}, 20000);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Máy chủ trả lỗi ' + res.status);
  return data;
}
async function sendJson(url, method, payload) {
  const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: payload === undefined ? undefined : JSON.stringify(payload) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(d => d.msg).join('; ') : data.detail;
    throw new Error(detail || 'Máy chủ trả lỗi ' + res.status);
  }
  return data;
}

async function loadRoles() {
  if (ROLES.length) return ROLES;
  const data = await getJson('/api/admin/users/roles');
  ROLES = data.roles;
  const perms = data.permissions;
  $('#permTable').innerHTML = `<thead><tr><th>Quyền</th>${ROLES.map(r => `<th class="role-col"><span class="role-badge ${esc(r.key)}">${esc(r.label)}</span></th>`).join('')}</tr></thead>
    <tbody>${Object.entries(perms).map(([key, label]) => `<tr><td>${esc(label)}</td>${ROLES.map(r => r.permissions.includes(key)
      ? '<td class="mark yes" aria-label="Có">✓</td>' : '<td class="mark no" aria-label="Không">–</td>').join('')}</tr>`).join('')}</tbody>`;
  return ROLES;
}

async function loadUsers() {
  const body = $('#usersBody');
  try {
    await loadRoles();
    const q = $('#userQuery').value.trim();
    const data = await getJson('/api/admin/users' + (q ? '?q=' + encodeURIComponent(q) : ''));
    USERS = data.users;
    const active = USERS.filter(u => u.is_active).length;
    const byRole = ROLES.map(r => [r.label, USERS.filter(u => u.role === r.key).length]).filter(([, n]) => n);
    $('#userStats').innerHTML = `<span class="pill">${fmt(USERS.length)} tài khoản</span><span class="pill">${fmt(active)} đang hoạt động</span>` +
      byRole.map(([label, n]) => `<span class="pill">${esc(label)}: ${fmt(n)}</span>`).join('');
    body.innerHTML = USERS.length ? USERS.map(u => `
      <tr class="${u.is_active ? '' : 'inactive'}">
        <td><div class="user-cell"><span class="avatar" aria-hidden="true">${esc(window.UI.initials(u.full_name || u.username))}</span>
          <div><strong>${esc(u.full_name || u.username)}${u.is_self ? '<span class="you">Bạn</span>' : ''}</strong><small>${esc(u.username)}${u.email ? ' · ' + esc(u.email) : ''}</small></div></div></td>
        <td><span class="role-badge ${esc(u.role)}">${esc(u.role_label)}</span></td>
        <td>${u.is_active ? '<span class="status good">Hoạt động</span>' : '<span class="status neutral">Đã khoá</span>'}
            ${u.must_change_password ? '<div class="sub">Chờ đổi mật khẩu</div>' : ''}</td>
        <td class="num">${u.daily_quota ? `${fmt(u.used_today)} / ${fmt(u.daily_quota)}` : `${fmt(u.used_today)} / không giới hạn`}</td>
        <td>${fmtTime(u.last_login_at)}${u.sessions ? `<div class="sub">${fmt(u.sessions)} phiên đang mở</div>` : ''}</td>
        <td><div class="row-actions">${u.manageable || u.is_self ? `<button class="btn btn-sm" type="button" data-edit="${u.id}">Sửa</button>` : ''}
          ${u.manageable && !u.is_self ? `<button class="btn btn-sm btn-ghost" type="button" data-toggle="${u.id}">${u.is_active ? 'Khoá' : 'Mở khoá'}</button>
          <button class="btn btn-sm btn-ghost danger-text" type="button" data-delete="${u.id}">Xoá</button>` : ''}</div></td>
      </tr>`).join('') : '<tr><td colspan="6" class="empty">Không có tài khoản phù hợp.</td></tr>';
  } catch (err) {
    body.innerHTML = `<tr><td colspan="6" class="error">${esc(err.message)}</td></tr>`;
  }
}

function openUserDialog(user) {
  editingUser = user || null;
  const dialog = $('#userDialog'), form = $('#userForm');
  form.reset();
  form.querySelector('.form-error').hidden = true;
  $('#userDialogTitle').textContent = user ? `Sửa tài khoản ${user.username}` : 'Thêm tài khoản';
  form.username.value = user ? user.username : '';
  form.username.disabled = !!user;
  form.full_name.value = (user && user.full_name) || '';
  form.email.value = (user && user.email) || '';
  form.daily_quota.value = user ? user.daily_quota : 0;
  form.must_change_password.checked = user ? user.must_change_password : true;
  form.is_active.checked = user ? user.is_active : true;
  const assignable = ROLES.filter(r => r.assignable || (user && r.key === user.role));
  $('#roleSelect').innerHTML = assignable.map(r => `<option value="${esc(r.key)}">${esc(r.label)}</option>`).join('');
  const fallback = assignable.length ? assignable[assignable.length - 1].key : '';
  form.role.value = user ? user.role : (assignable.some(r => r.key === 'staff') ? 'staff' : fallback);
  const self = !!(user && user.is_self);
  form.role.disabled = self;
  form.is_active.disabled = self;
  form.daily_quota.disabled = self && !window.UI.auth.can('settings.manage');
  $('#pwLabel').textContent = user ? 'Đặt lại mật khẩu (để trống nếu không đổi)' : 'Mật khẩu (tối thiểu 10 ký tự)';
  updateRoleHelp();
  dialog.showModal();
  (user ? form.full_name : form.username).focus();
}
function updateRoleHelp() {
  const role = ROLES.find(r => r.key === $('#roleSelect').value);
  $('#roleHelp').textContent = role ? role.description : '';
}
$('#roleSelect').addEventListener('change', updateRoleHelp);
$('#userDialog [data-close]').addEventListener('click', () => $('#userDialog').close());
$('#addUserBtn').addEventListener('click', async () => {
  try { await loadRoles(); openUserDialog(null); } catch (err) { toast(err.message, 'bad'); }
});
let userSearchTimer = null;
$('#userQuery').addEventListener('input', () => { clearTimeout(userSearchTimer); userSearchTimer = setTimeout(loadUsers, 250); });

$('#userForm').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.target, error = form.querySelector('.form-error'), submit = $('#userSubmit');
  error.hidden = true;
  const payload = {
    full_name: form.full_name.value.trim(), email: form.email.value.trim(), role: form.role.value,
    daily_quota: parseInt(form.daily_quota.value, 10) || 0,
    must_change_password: form.must_change_password.checked, is_active: form.is_active.checked,
  };
  if (form.password.value) payload.password = form.password.value;
  if (editingUser && editingUser.is_self) {
    delete payload.role;
    delete payload.is_active;
    if (form.daily_quota.disabled) delete payload.daily_quota;
  }
  if (!editingUser) {
    payload.username = form.username.value.trim();
    if (!payload.password) { error.textContent = 'Vui lòng đặt mật khẩu cho tài khoản mới.'; error.hidden = false; return; }
  }
  submit.disabled = true;
  submit.innerHTML = '<span class="spin"></span>Đang lưu…';
  try {
    if (editingUser) await sendJson(`/api/admin/users/${editingUser.id}`, 'PATCH', payload);
    else await sendJson('/api/admin/users', 'POST', payload);
    $('#userDialog').close();
    toast(editingUser ? 'Đã cập nhật tài khoản.' : `Đã tạo tài khoản ${payload.username}.`, 'ok');
    loadUsers();
    if (editingUser && editingUser.is_self) window.UI.auth.load(true);
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  } finally {
    submit.disabled = false;
    submit.textContent = 'Lưu';
  }
});

$('#usersBody').addEventListener('click', async event => {
  const button = event.target.closest('button');
  if (!button) return;
  const id = Number(button.dataset.edit || button.dataset.toggle || button.dataset.delete);
  const user = USERS.find(u => u.id === id);
  if (!user) return;
  if (button.dataset.edit) return openUserDialog(user);
  if (button.dataset.toggle) {
    try {
      await sendJson(`/api/admin/users/${id}`, 'PATCH', { is_active: !user.is_active });
      toast(user.is_active ? `Đã khoá ${user.username}; các phiên đăng nhập của tài khoản này đã bị đóng.` : `Đã mở khoá ${user.username}.`, 'ok');
      loadUsers();
    } catch (err) { toast(err.message, 'bad'); }
    return;
  }
  const confirm = await Swal.fire({
    title: `Xoá tài khoản ${user.username}?`,
    text: 'Không thể hoàn tác. Lịch sử tra cứu vẫn được giữ nhưng không còn gắn với tài khoản này. Nếu chỉ muốn tạm ngừng, hãy dùng “Khoá”.',
    icon: 'warning', showCancelButton: true, confirmButtonText: 'Xoá tài khoản', cancelButtonText: 'Huỷ', focusCancel: true,
  });
  if (!confirm.isConfirmed) return;
  try {
    await sendJson(`/api/admin/users/${id}`, 'DELETE');
    toast(`Đã xoá tài khoản ${user.username}.`, 'ok');
    loadUsers();
  } catch (err) { toast(err.message, 'bad'); }
});

/* =====================================================================
   MÔ HÌNH AI
   ===================================================================== */
let AI = null, enabledModels = [], defaultModel = null, catalogProvider = 'openrouter';
const INPUT_LABEL = { text: 'Văn bản', image: 'Ảnh', audio: 'Âm thanh', file: 'PDF', video: 'Video' };
const usd = v => v == null ? '—' : '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: v < 1 ? 3 : 2 });
const vnd = v => v == null ? '—' : '~' + fmt(v) + 'đ';
const ioTags = (inputs, need = []) => (inputs || []).map(k => `<span class="io-tag${need.includes(k) ? ' need' : ''}">${esc(INPUT_LABEL[k] || k)}</span>`).join(' ');
const canManageAi = () => window.UI.auth.can('settings.manage');

async function loadAi() {
  try {
    AI = await getJson('/api/admin/ai/overview');
    enabledModels = AI.models.map(m => ({ ...m }));
    defaultModel = AI.default_model;
    $('#anonSearch').checked = !!AI.anonymous_search;
    renderProviders();
    renderTasks();
    renderEnabled();
    loadCatalog();
    const readOnly = !canManageAi();
    ['#saveTasks', '#saveModels'].forEach(sel => { $(sel).hidden = readOnly; });
    $('#anonSearch').disabled = readOnly;
  } catch (err) {
    $('#tasksBody').innerHTML = `<tr><td colspan="5" class="error">${esc(err.message)}</td></tr>`;
  }
}

function renderProviders() {
  const manage = canManageAi();
  $('#providers').innerHTML = AI.providers.map(p => `
    <div class="provider" data-provider="${esc(p.provider)}">
      <div><strong>${esc(p.label)}</strong><div class="sub">${p.configured
        ? `<span class="status good">Đã kết nối</span> <span class="masked">${esc(p.masked || '')}</span> · ${p.source === 'env' ? 'từ tệp .env' : 'lưu trên trang quản trị'}`
        : '<span class="status neutral">Chưa có khoá</span>'}</div></div>
      ${manage ? `<div class="row-actions"><button class="btn btn-sm" type="button" data-key-edit>${p.configured ? 'Đổi khoá' : 'Nhập khoá'}</button>
        ${p.source === 'admin' ? '<button class="btn btn-sm btn-ghost" type="button" data-key-remove>Gỡ</button>' : ''}</div>` : ''}
      <div class="key-row"><input class="input-field" type="password" autocomplete="off" placeholder="Dán khoá API ${esc(p.label)}" aria-label="Khoá API ${esc(p.label)}">
        <button class="btn btn-primary btn-sm" type="button" data-key-save>Lưu</button><button class="btn btn-ghost btn-sm" type="button" data-key-cancel>Huỷ</button></div>
    </div>`).join('');
}
$('#providers').addEventListener('click', async event => {
  const row = event.target.closest('.provider');
  if (!row) return;
  const provider = row.dataset.provider;
  if (event.target.closest('[data-key-edit]')) { row.classList.add('editing'); row.querySelector('input').focus(); }
  if (event.target.closest('[data-key-cancel]')) row.classList.remove('editing');
  const save = event.target.closest('[data-key-save]'), remove = event.target.closest('[data-key-remove]');
  if (!save && !remove) return;
  if (remove) {
    const ok = await Swal.fire({ title: 'Gỡ khoá API?', text: 'Các tính năng dùng nhà cung cấp này sẽ quay về khoá trong .env (nếu có).', icon: 'warning', showCancelButton: true, confirmButtonText: 'Gỡ khoá', cancelButtonText: 'Huỷ' });
    if (!ok.isConfirmed) return;
  }
  const value = save ? row.querySelector('input').value.trim() : null;
  if (save && !value) return toast('Vui lòng dán khoá API.', 'warn');
  try {
    await sendJson(`/api/admin/ai/keys/${provider}`, 'PUT', { api_key: value });
    toast(save ? 'Đã lưu khoá API (đã mã hoá).' : 'Đã gỡ khoá API.', 'ok');
    loadAi();
  } catch (err) { toast(err.message, 'bad'); }
});

$('#anonSearch').addEventListener('change', async event => {
  try {
    await sendJson('/api/admin/ai/access', 'PUT', { anonymous_search: event.target.checked });
    toast(event.target.checked ? 'Khách chưa đăng nhập có thể tra cứu.' : 'Tra cứu yêu cầu đăng nhập.', 'ok');
  } catch (err) { event.target.checked = !event.target.checked; toast(err.message, 'bad'); }
});

function fitsTask(model, task) {
  // Mô hình phải nhận được đúng loại dữ liệu tính năng cần (vd gỡ băng cần âm thanh, OCR cần ảnh).
  if (!task.inputs.every(k => (model.inputs || []).includes(k))) return false;
  if (model.provider === 'local' && task.task !== 'ocr') return false;
  const speechOnly = /transcribe|whisper/.test(model.spec);
  return !(speechOnly && task.task !== 'transcribe');
}
function taskOptions(task) {
  const list = enabledModels.filter(m => fitsTask(m, task));
  if (!list.some(m => m.spec === task.spec)) list.unshift({ spec: task.spec, name: task.model.name + ' (đang dùng)' });
  return '<option value="">Theo .env</option>' + list.map(m => `<option value="${esc(m.spec)}"${m.spec === task.spec ? ' selected' : ''}>${esc(m.name)} — ${esc(m.spec)}</option>`).join('');
}
function renderTasks() {
  const manage = canManageAi();
  $('#tasksBody').innerHTML = AI.tasks.map(t => `
    <tr data-task="${esc(t.task)}">
      <td><div class="task-label">${esc(t.label)}</div><div class="task-src">${t.source === 'admin' ? 'Đã chọn trên trang quản trị' : 'Mặc định từ .env'}</div></td>
      <td>${ioTags(t.inputs, t.inputs)}</td>
      <td>${manage ? `<select class="input-field" aria-label="Mô hình cho ${esc(t.label)}">${taskOptions(t)}</select>` : `<span class="mono">${esc(t.spec)}</span>`}
        ${t.compatible ? '' : `<div class="problem">${esc(t.problem)}</div>`}</td>
      <td class="price-cell">${usd(t.model.prompt)} / ${usd(t.model.completion)}</td>
      <td>${manage && !['transcribe', 'ocr'].includes(t.task) ? `<button class="btn btn-sm btn-ghost" type="button" data-test="${esc(t.spec)}">Gọi thử</button>` : ''}</td>
    </tr>`).join('');
}
$('#tasksBody').addEventListener('click', async event => {
  const button = event.target.closest('[data-test]');
  if (!button) return;
  const select = button.closest('tr').querySelector('select');
  const spec = (select && select.value) || button.dataset.test;
  button.disabled = true;
  button.innerHTML = '<span class="spin dark"></span>Đang gọi';
  try {
    const data = await sendJson('/api/admin/ai/test', 'POST', { spec });
    if (data.ok) toast(`${spec} trả lời “${data.reply}” sau ${fmt(data.latency_ms)} ms.`, 'ok');
    else toast(`${spec}: ${data.error}`, 'bad');
  } catch (err) { toast(err.message, 'bad'); }
  button.disabled = false;
  button.textContent = 'Gọi thử';
});
$('#saveTasks').addEventListener('click', async () => {
  const payload = {};
  $$('#tasksBody tr[data-task]').forEach(row => {
    const select = row.querySelector('select');
    if (!select) return;
    const task = AI.tasks.find(t => t.task === row.dataset.task);
    const value = select.value || null;
    if (value !== (task.source === 'admin' ? task.spec : null)) payload[row.dataset.task] = value;
  });
  if (!Object.keys(payload).length) return toast('Không có thay đổi.', 'info');
  try {
    const data = await sendJson('/api/admin/ai/tasks', 'PUT', payload);
    AI.tasks = data.tasks;
    renderTasks();
    toast('Đã lưu phân công mô hình cho từng tính năng.', 'ok');
  } catch (err) { toast(err.message, 'bad'); }
});

function renderEnabled() {
  const manage = canManageAi();
  $('#enabledBody').innerHTML = enabledModels.length ? enabledModels.map((m, i) => `
    <tr>
      <td><strong>${esc(m.name)}</strong><div class="sub mono">${esc(m.spec)}</div></td>
      <td>${ioTags(m.inputs)}</td>
      <td class="price-cell">${usd(m.prompt)} / ${usd(m.completion)}</td>
      <td class="price-cell">${vnd(m.question_vnd)}</td>
      <td class="center"><input type="checkbox" data-i="${i}" data-f="featured" ${m.featured ? 'checked' : ''} ${manage ? '' : 'disabled'} aria-label="Hiển thị ở bảng giá"></td>
      <td class="center"><input type="checkbox" data-i="${i}" data-f="selectable" ${m.selectable ? 'checked' : ''} ${manage && (m.inputs || []).includes('text') ? '' : 'disabled'} aria-label="Cho người dùng chọn"></td>
      <td class="center"><input type="radio" name="defaultModel" data-i="${i}" ${defaultModel === m.spec ? 'checked' : ''} ${manage && m.selectable ? '' : 'disabled'} aria-label="Mô hình mặc định"></td>
      <td>${manage ? `<button class="btn btn-sm btn-ghost" type="button" data-remove="${i}">Bỏ</button>` : ''}</td>
    </tr>`).join('') : '<tr><td colspan="8" class="empty">Chưa bật mô hình nào. Thêm từ danh mục bên dưới.</td></tr>';
}
$('#enabledBody').addEventListener('change', event => {
  const i = Number(event.target.dataset.i);
  if (event.target.type === 'radio') { defaultModel = enabledModels[i].spec; return; }
  enabledModels[i][event.target.dataset.f] = event.target.checked;
  if (event.target.dataset.f === 'selectable' && !event.target.checked && defaultModel === enabledModels[i].spec) defaultModel = null;
  renderEnabled();
});
$('#enabledBody').addEventListener('click', event => {
  const button = event.target.closest('[data-remove]');
  if (!button) return;
  const [removed] = enabledModels.splice(Number(button.dataset.remove), 1);
  if (removed.spec === defaultModel) defaultModel = null;
  renderEnabled();
  renderCatalog();
});
$('#saveModels').addEventListener('click', async () => {
  try {
    await sendJson('/api/admin/ai/models', 'PUT', {
      models: enabledModels.map(m => ({ spec: m.spec, featured: !!m.featured, selectable: !!m.selectable, note: m.note || null })),
      default_model: defaultModel,
    });
    toast('Đã lưu danh sách mô hình. Bảng giá trang chủ đã cập nhật.', 'ok');
    loadAi();
  } catch (err) { toast(err.message, 'bad'); }
});

let catalogItems = [], catalogTotal = 0, catalogTimer = null;
async function loadCatalog(refresh = false) {
  const host = $('#catalog');
  host.innerHTML = '<div class="shimmer"></div><div class="shimmer"></div><div class="shimmer"></div>';
  const params = new URLSearchParams({ provider: catalogProvider, limit: '60' });
  const q = $('#catalogQuery').value.trim(), input = $('#catalogInput').value;
  if (q) params.set('q', q);
  if (input) params.set('input', input);
  if (refresh) params.set('refresh', 'true');
  try {
    const data = await getJson('/api/admin/ai/catalog?' + params);
    catalogItems = data.items;
    catalogTotal = data.total;
    $('#catalogHint').textContent = `${fmt(data.total)} mô hình${catalogProvider === 'openrouter' ? ' · giá lấy trực tiếp từ OpenRouter' : ''}`;
    renderCatalog();
    if (refresh) toast('Đã cập nhật danh mục và giá mới nhất.', 'ok');
  } catch (err) {
    host.innerHTML = `<div class="error">${esc(err.message)}</div>`;
  }
}
function renderCatalog() {
  const manage = canManageAi();
  const on = new Set(enabledModels.map(m => m.spec));
  $('#catalog').innerHTML = catalogItems.length ? catalogItems.map((m, i) => `
    <div class="model-card${on.has(m.spec) ? ' on' : ''}">
      <div class="mc-head"><div><strong>${esc(m.name)}</strong><div class="mc-id">${esc(m.spec)}</div></div><span class="pill">${esc(m.vendor || '')}</span></div>
      <div class="mc-price"><span>Vào <b>${usd(m.prompt)}</b></span><span>Ra <b>${usd(m.completion)}</b></span><span>Câu hỏi <b>${vnd(m.question_vnd)}</b></span></div>
      <div class="mc-io">${ioTags(m.inputs)}</div>
      ${m.unknown ? '<div class="sub">Chưa có giá tham chiếu cho mô hình này.</div>' : ''}
      ${manage ? (on.has(m.spec) ? '<button class="btn btn-sm btn-soft" type="button" disabled>Đã bật</button>'
        : `<button class="btn btn-sm" type="button" data-add="${i}">Bật mô hình</button>`) : ''}
    </div>`).join('') + (catalogTotal > catalogItems.length ? `<div class="catalog-more">Hiển thị ${fmt(catalogItems.length)}/${fmt(catalogTotal)} mô hình mới nhất — gõ tên để tìm thêm.</div>` : '')
    : '<div class="empty">Không có mô hình phù hợp.</div>';
}
$('#catalog').addEventListener('click', event => {
  const button = event.target.closest('[data-add]');
  if (!button) return;
  const m = catalogItems[Number(button.dataset.add)];
  enabledModels.push({ ...m, featured: false, selectable: (m.inputs || []).includes('text') && m.provider !== 'local' });
  renderEnabled();
  renderCatalog();
  toast(`Đã thêm ${m.name}. Bấm “Lưu danh sách mô hình” để áp dụng.`, 'info');
});
$('#providerSeg').addEventListener('click', event => {
  const button = event.target.closest('button[data-p]');
  if (!button) return;
  $$('#providerSeg button').forEach(b => b.classList.toggle('on', b === button));
  catalogProvider = button.dataset.p;
  loadCatalog();
});
$('#catalogInput').addEventListener('change', () => loadCatalog());
$('#catalogQuery').addEventListener('input', () => { clearTimeout(catalogTimer); catalogTimer = setTimeout(() => loadCatalog(), 300); });
$('#catalogRefresh').addEventListener('click', () => loadCatalog(true));

/* =====================================================================
   NHẬT KÝ
   ===================================================================== */
const ACTION_LABEL = {
  login: 'Đăng nhập', login_failed: 'Đăng nhập thất bại', logout: 'Đăng xuất', password_changed: 'Đổi mật khẩu',
  user_created: 'Tạo tài khoản', user_updated: 'Sửa tài khoản', user_deleted: 'Xoá tài khoản',
  ai_models_saved: 'Lưu danh sách mô hình', ai_tasks_saved: 'Phân công mô hình', ai_key_saved: 'Lưu khoá API',
  ai_key_removed: 'Gỡ khoá API', access_changed: 'Đổi quyền truy cập',
};
async function loadAudit() {
  const body = $('#auditBody');
  try {
    const data = await getJson('/api/admin/users/audit?limit=200');
    body.innerHTML = data.items.length ? data.items.map(a => {
      const detail = a.detail && Object.keys(a.detail).length
        ? Object.entries(a.detail).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(', ') : '';
      const bad = a.action === 'login_failed';
      return `<tr><td class="mono">${fmtTime(a.created_at)}</td><td>${esc(a.username || '—')}</td>
        <td><span class="status ${bad ? 'bad' : 'neutral'}">${esc(ACTION_LABEL[a.action] || a.action)}</span></td>
        <td>${esc(a.target || '')}</td><td class="sub">${esc(detail)}</td><td class="mono">${esc(a.ip || '')}</td></tr>`;
    }).join('') : '<tr><td colspan="6" class="empty">Chưa có thao tác nào.</td></tr>';
  } catch (err) {
    body.innerHTML = `<tr><td colspan="6" class="error">${esc(err.message)}</td></tr>`;
  }
}

/* =====================================================================
   KHỞI ĐỘNG: ẩn tab theo quyền, mở đúng tab theo địa chỉ
   ===================================================================== */
window.addEventListener('auth:change', event => {
  const state = event.detail || {};
  window.UI.renderAccount($('#account'), state, { adminLink: false, homeLink: true });
  if (!state.user) { if (!state.offline) location.href = window.UI.auth.loginUrl(); return; }
  const perms = state.user.permissions;
  $$('.tab[data-perm]').forEach(tab => { tab.hidden = !perms.includes(tab.dataset.perm); });
  $$('.nav-label').forEach(label => {
    let next = label.nextElementSibling, any = false;
    while (next && !next.classList.contains('nav-label')) { if (!next.hidden) any = true; next = next.nextElementSibling; }
    label.hidden = !any;
  });
  if (!loadedPanels.size) showPanel('p-' + (location.hash.slice(1) || 'dashboard'), { push: false });
  if (perms.includes('crawler.run')) checkCrawlerStatus();
});
window.addEventListener('auth:required', () => { location.href = window.UI.auth.loginUrl(); });

window.addEventListener('DOMContentLoaded', () => {
  window.UI.auth.load();
  try {
    const saved = localStorage.getItem('qlvb-page-size');
    if (saved && $(`#apiPageSize option[value="${saved}"]`)) $('#apiPageSize').value = saved;
  } catch (err) { /* không bắt buộc */ }
});
