/* DocNexus UI — dùng chung cho trang tra cứu, đăng nhập và quản trị.
   - Tự gắn header chống giả mạo (X-Requested-With) cho mọi yêu cầu thay đổi dữ liệu tới máy chủ.
   - Hiệu ứng gợn sóng khi bấm, thông báo (toast), hiện dần khi cuộn, đếm số.
   - Trạng thái đăng nhập: UI.auth.load() / sự kiện "auth:change" và "auth:required". */
(function () {
    "use strict";

    const nativeFetch = window.fetch.bind(window);
    window.fetch = function (input, init) {
        init = init || {};
        const url = new URL(typeof input === "string" ? input : input.url, location.href);
        const method = (init.method || (typeof input !== "string" && input.method) || "GET").toUpperCase();
        if (url.origin === location.origin && url.pathname.startsWith("/api/")) {
            const headers = new Headers(init.headers || (typeof input !== "string" ? input.headers : undefined));
            if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers.set("X-Requested-With", "DocNexus");
            init = {...init, headers, credentials: "same-origin"};
            return nativeFetch(input, init).then(response => {
                if (response.status === 401 && !url.pathname.startsWith("/api/auth/")) {
                    window.dispatchEvent(new CustomEvent("auth:required", {detail: {path: url.pathname}}));
                }
                return response;
            });
        }
        return nativeFetch(input, init);
    };

    const esc = s => (s == null ? "" : String(s)).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
    const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    /* ---------- gợn sóng khi bấm ---------- */
    const RIPPLE = ".btn, .chip, .tab, .card.interactive, .feature, .utility-link, .subtab, .seg button, .icon-btn";
    document.addEventListener("pointerdown", event => {
        const target = event.target.closest && event.target.closest(RIPPLE);
        if (!target || target.disabled || reduceMotion()) return;
        const rect = target.getBoundingClientRect();
        const size = Math.max(rect.width, rect.height) * 1.2;
        const wave = document.createElement("span");
        wave.className = "ripple";
        wave.style.cssText = `width:${size}px;height:${size}px;left:${event.clientX - rect.left - size / 2}px;top:${event.clientY - rect.top - size / 2}px`;
        if (getComputedStyle(target).position === "static") target.style.position = "relative";
        target.classList.add("has-ripple");
        target.appendChild(wave);
        wave.addEventListener("animationend", () => wave.remove());
    }, {passive: true});

    /* ---------- thông báo ---------- */
    let stack = null;
    const ICONS = {
        ok: '<path d="M20 6 9 17l-5-5"/>',
        warn: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
        bad: '<circle cx="12" cy="12" r="9"/><path d="m15 9-6 6M9 9l6 6"/>',
        info: '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/>',
    };
    function toast(message, type = "info", options = {}) {
        if (!stack) {
            stack = document.createElement("div");
            stack.className = "toast-stack";
            stack.setAttribute("role", "status");
            stack.setAttribute("aria-live", "polite");
            document.body.appendChild(stack);
        }
        const el = document.createElement("div");
        el.className = "toast toast-" + type;
        el.innerHTML = `<svg class="t-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">${ICONS[type] || ICONS.info}</svg><span class="t-msg"></span>`;
        el.querySelector(".t-msg").textContent = message;
        if (options.action) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "btn btn-soft btn-sm";
            button.textContent = options.action.label;
            button.onclick = () => { options.action.run(); close(); };
            el.appendChild(button);
        }
        const x = document.createElement("button");
        x.type = "button";
        x.className = "t-close";
        x.setAttribute("aria-label", "Đóng thông báo");
        x.textContent = "×";
        x.onclick = () => close();
        el.appendChild(x);
        stack.appendChild(el);
        const timer = setTimeout(close, options.duration || (type === "bad" ? 7000 : 4200));
        function close() {
            clearTimeout(timer);
            el.classList.add("out");
            setTimeout(() => el.remove(), 220);
        }
        return close;
    }

    /* ---------- hiện dần khi cuộn tới ---------- */
    function reveal(root = document) {
        const items = root.querySelectorAll(".reveal:not(.in)");
        if (!("IntersectionObserver" in window) || reduceMotion()) { items.forEach(el => el.classList.add("in")); return; }
        const observer = new IntersectionObserver(entries => entries.forEach(entry => {
            if (entry.isIntersecting) { entry.target.classList.add("in"); observer.unobserve(entry.target); }
        }), {rootMargin: "0px 0px -40px 0px", threshold: .08});
        items.forEach((el, index) => { el.style.transitionDelay = Math.min(index % 6, 5) * 50 + "ms"; observer.observe(el); });
    }

    /* ---------- đếm số ---------- */
    const fmt = n => Math.round(n).toLocaleString("vi-VN");
    function countTo(el, value, format = fmt) {
        if (!el) return;
        const target = Number(value) || 0;
        const from = Number(el.dataset.v || 0);
        el.dataset.v = target;
        if (reduceMotion() || from === target) { el.textContent = format(target); return; }
        const start = performance.now(), duration = 700;
        (function frame(now) {
            const p = Math.min(1, (now - start) / duration), e = 1 - Math.pow(1 - p, 3);
            el.textContent = format(from + (target - from) * e);
            if (p < 1) requestAnimationFrame(frame);
        })(start);
    }

    /* ---------- đăng nhập ---------- */
    const auth = {
        state: null,
        async load(force) {
            if (this.state && !force) return this.state;
            try {
                const response = await fetch("/api/auth/me", {cache: "no-store"});
                this.state = response.ok ? await response.json() : {user: null};
            } catch {
                this.state = {user: null, offline: true};
            }
            window.dispatchEvent(new CustomEvent("auth:change", {detail: this.state}));
            return this.state;
        },
        get user() { return this.state && this.state.user; },
        can(permission) { const user = this.user; return !!(user && user.permissions.includes(permission)); },
        async logout() {
            try { await fetch("/api/auth/logout", {method: "POST"}); } catch { /* vẫn chuyển trang */ }
            location.href = "/login";
        },
        loginUrl() { return "/login?next=" + encodeURIComponent(location.pathname + location.hash); },
    };

    const initials = name => (name || "?").trim().split(/\s+/).slice(-2).map(part => part[0]).join("").toUpperCase();

    /* ---------- hộp thoại đổi mật khẩu (dùng chung) ---------- */
    function passwordDialog(forced) {
        let dialog = document.getElementById("pwDialog");
        if (!dialog) {
            dialog = document.createElement("dialog");
            dialog.id = "pwDialog";
            dialog.className = "modal glass";
            dialog.innerHTML = `
<form method="dialog" class="modal-body" novalidate>
  <h3>Đổi mật khẩu</h3>
  <p class="modal-sub" id="pwReason">Mật khẩu mới tối thiểu 10 ký tự, gồm cả chữ và số.</p>
  <label class="field"><span>Mật khẩu hiện tại</span><input class="input-field" type="password" name="current" autocomplete="current-password" required></label>
  <label class="field"><span>Mật khẩu mới</span><input class="input-field" type="password" name="next" autocomplete="new-password" required minlength="10"></label>
  <label class="field"><span>Nhập lại mật khẩu mới</span><input class="input-field" type="password" name="again" autocomplete="new-password" required></label>
  <div class="form-error" role="alert" hidden></div>
  <div class="modal-actions">
    <button class="btn btn-ghost" type="button" data-close>Để sau</button>
    <button class="btn btn-primary" type="submit">Lưu mật khẩu</button>
  </div>
</form>`;
            document.body.appendChild(dialog);
            dialog.querySelector("[data-close]").onclick = () => dialog.close();
            dialog.querySelector("form").onsubmit = async event => {
                event.preventDefault();
                const form = event.target, error = form.querySelector(".form-error"), submit = form.querySelector("[type=submit]");
                const show = message => { error.textContent = message; error.hidden = false; };
                error.hidden = true;
                if (form.next.value !== form.again.value) return show("Hai lần nhập mật khẩu mới không khớp.");
                submit.disabled = true;
                submit.innerHTML = '<span class="spin"></span>Đang lưu…';
                try {
                    const response = await fetch("/api/auth/password", {method: "POST", headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({current_password: form.current.value, new_password: form.next.value})});
                    const data = await response.json().catch(() => ({}));
                    if (!response.ok) return show(typeof data.detail === "string" ? data.detail : "Không đổi được mật khẩu.");
                    form.reset();
                    dialog.close();
                    toast("Đã đổi mật khẩu. Các phiên đăng nhập khác đã bị đăng xuất.", "ok");
                    auth.load(true);
                } catch {
                    show("Không kết nối được máy chủ.");
                } finally {
                    submit.disabled = false;
                    submit.textContent = "Lưu mật khẩu";
                }
            };
        }
        dialog.querySelector("#pwReason").textContent = forced
            ? "Quản trị viên yêu cầu bạn đổi mật khẩu trước khi tiếp tục sử dụng."
            : "Mật khẩu mới tối thiểu 10 ký tự, gồm cả chữ và số.";
        dialog.querySelector("[data-close]").hidden = !!forced;
        dialog.showModal();
        dialog.querySelector("input").focus();
    }

    /* ---------- khối tài khoản (menu người dùng) ---------- */
    function renderAccount(container, state, options = {}) {
        if (!container) return;
        const user = state && state.user;
        if (!user) {
            container.innerHTML = `<a class="btn btn-primary btn-block" href="${esc(auth.loginUrl())}">
                <svg class="ico" viewBox="0 0 24 24"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><path d="m10 17 5-5-5-5M15 12H3"/></svg>Đăng nhập</a>`;
            return;
        }
        const quota = user.daily_quota ? `${state.used_today || 0}/${user.daily_quota} câu hỏi hôm nay` : user.role_label;
        container.innerHTML = `
<details class="account-menu">
  <summary class="account-chip" aria-label="Tài khoản ${esc(user.username)}">
    <span class="avatar" aria-hidden="true">${esc(initials(user.full_name || user.username))}</span>
    <span class="acc-text"><strong>${esc(user.full_name || user.username)}</strong><small>${esc(quota)}</small></span>
    <svg class="ico chev" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>
  </summary>
  <div class="account-pop glass" role="menu">
    <div class="acc-head"><strong>${esc(user.username)}</strong><span class="pill">${esc(user.role_label)}</span></div>
    ${user.admin_area && options.adminLink !== false ? `<a role="menuitem" class="menu-item" href="/admin"><svg class="ico" viewBox="0 0 24 24"><path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/></svg>Trang quản trị</a>` : ""}
    ${options.homeLink ? `<a role="menuitem" class="menu-item" href="/"><svg class="ico" viewBox="0 0 24 24"><path d="M3 11.5 12 4l9 7.5"/><path d="M5 10v10h14V10"/></svg>Trang tra cứu</a>` : ""}
    <button role="menuitem" class="menu-item" type="button" data-act="password"><svg class="ico" viewBox="0 0 24 24"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>Đổi mật khẩu</button>
    <button role="menuitem" class="menu-item danger" type="button" data-act="logout"><svg class="ico" viewBox="0 0 24 24"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/></svg>Đăng xuất</button>
  </div>
</details>`;
        const menu = container.querySelector("details");
        container.querySelector('[data-act="password"]').onclick = () => { menu.open = false; passwordDialog(false); };
        container.querySelector('[data-act="logout"]').onclick = () => auth.logout();
        document.addEventListener("click", event => { if (menu.open && !menu.contains(event.target)) menu.open = false; });
        if (user.must_change_password) passwordDialog(true);
    }

    window.UI = {esc, toast, reveal, countTo, fmt, auth, renderAccount, passwordDialog, initials};
    document.addEventListener("DOMContentLoaded", () => reveal());
})();
