/* Trang tổng quan: số liệu trực tiếp (/api/overview) và bảng giá mô hình AI (/api/models/featured).
   Biểu đồ vẽ bằng SVG thuần: cột mảnh (≤24px, đầu bo 4px bám đáy), lưới mờ, tooltip khi di chuột/focus,
   bảng ẩn cho trình đọc màn hình; tiến độ đồng bộ có chú thích vì nhiều trạng thái. */
(function () {
    "use strict";
    const $ = s => document.querySelector(s);
    const {esc, countTo} = window.UI;
    const tip = $("#vizTip");
    const NS = "http://www.w3.org/2000/svg";
    const nf = n => Number(n || 0).toLocaleString("vi-VN");
    const REFRESH_MS = 15000;
    let last = null, timer = null;

    /* ---------- tooltip ---------- */
    function showTip(event, title, value) {
        if (!tip) return;
        tip.replaceChildren();
        const strong = document.createElement("strong");
        strong.textContent = value;
        const span = document.createElement("span");
        span.textContent = title;
        tip.append(strong, span);
        const rect = event.currentTarget && event.type === "focus" ? event.currentTarget.getBoundingClientRect() : null;
        const x = rect ? rect.left + rect.width / 2 : event.clientX;
        const y = rect ? rect.top : event.clientY;
        tip.classList.add("show");
        const w = tip.offsetWidth, h = tip.offsetHeight;
        tip.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, x - w / 2)) + "px";
        tip.style.top = Math.max(8, y - h - 12) + "px";
    }
    const hideTip = () => tip && tip.classList.remove("show");
    function bindTip(el, title, value) {
        el.addEventListener("mousemove", e => showTip(e, title, value));
        el.addEventListener("mouseleave", hideTip);
        el.addEventListener("focus", e => showTip(e, title, value));
        el.addEventListener("blur", hideTip);
    }

    const svgEl = (tag, attrs) => {
        const el = document.createElementNS(NS, tag);
        for (const key in attrs) el.setAttribute(key, attrs[key]);
        return el;
    };
    function niceMax(value) {
        if (value <= 4) return 4;
        const power = Math.pow(10, Math.floor(Math.log10(value)));
        const step = [1, 2, 2.5, 5, 10].find(s => s * power * 4 >= value) * power;
        return step * 4;
    }
    // Cột có đầu trên bo 4px, đáy phẳng bám trục.
    function colPath(x, y, w, h, r) {
        r = Math.min(r, w / 2, h);
        return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
    }
    function srTable(caption, head, rows) {
        // Bọc trong div: bảng không co về 1px nên tự nó vẫn làm trang tràn ngang trên điện thoại.
        return `<div class="sr-only"><table><caption>${esc(caption)}</caption><thead><tr>${head.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead>
            <tbody>${rows.map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    }

    /* ---------- biểu đồ cột (một chuỗi: không cần chú thích) ---------- */
    function columns(host, items, {label, title, unit, height = 150, labelEvery = 1}) {
        if (!host) return;
        const width = Math.max(260, host.clientWidth || 320);
        const pad = {l: 30, r: 4, t: 10, b: 22};
        const plotW = width - pad.l - pad.r, plotH = height - pad.t - pad.b;
        const max = niceMax(Math.max(...items.map(i => i.n), 0));
        const slot = plotW / items.length;
        const barW = Math.max(3, Math.min(24, slot - 2));
        const svg = svgEl("svg", {viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": title});
        for (let k = 0; k <= 4; k++) {
            const y = pad.t + plotH - (plotH * k) / 4;
            svg.appendChild(svgEl("line", {class: "grid-line", x1: pad.l, x2: width - pad.r, y1: y, y2: y}));
            const t = svgEl("text", {class: "axis-text", x: pad.l - 6, y: y + 4, "text-anchor": "end"});
            t.textContent = nf((max * k) / 4);
            svg.appendChild(t);
        }
        items.forEach((item, i) => {
            const cx = pad.l + slot * i + slot / 2;
            const h = item.n ? Math.max(2, (item.n / max) * plotH) : 0;
            const g = svgEl("g", {class: "col", tabindex: "0", role: "listitem", "aria-label": `${title(item)}: ${nf(item.n)} ${unit}`});
            g.appendChild(svgEl("rect", {class: "bar-hit", x: pad.l + slot * i, y: pad.t, width: slot, height: plotH}));
            if (h) g.appendChild(svgEl("path", {class: "bar", d: colPath(cx - barW / 2, pad.t + plotH - h, barW, h, 4)}));
            svg.appendChild(g);
            bindTip(g, title(item), `${nf(item.n)} ${unit}`);
            if (i % labelEvery === 0 || i === items.length - 1) {
                const t = svgEl("text", {class: "axis-text", x: cx, y: height - 6, "text-anchor": "middle"});
                t.textContent = label(item);
                svg.appendChild(t);
            }
        });
        svg.setAttribute("role", "list");
        host.replaceChildren(svg);
        host.insertAdjacentHTML("beforeend", srTable(svg.getAttribute("aria-label") || "", ["Mốc", "Số lượng"], items.map(i => [title(i), i.n])));
    }

    const monthLabel = m => "T" + Number(m.slice(5)) ;
    const monthTitle = m => `Tháng ${Number(m.slice(5))}/${m.slice(0, 4)}`;

    function renderMonths(data) {
        const host = $("#chartMonths");
        const items = data.by_month || [];
        if (!items.some(i => i.n)) {
            host.innerHTML = '<div class="empty-viz">Chưa có văn bản nào sẵn sàng tra cứu.<br>Biểu đồ sẽ tự cập nhật khi crawler nạp dữ liệu.</div>';
            return;
        }
        columns(host, items, {label: i => monthLabel(i.month), title: i => monthTitle(i.month), unit: "văn bản",
            labelEvery: host.clientWidth < 360 ? 2 : 1});
    }

    function renderQueries(data) {
        const host = $("#chartQueries");
        const items = data.queries_24h || [];
        if (!items.some(i => i.n)) {
            host.innerHTML = '<div class="empty-viz">Chưa có lượt tra cứu nào trong 24 giờ qua.</div>';
        } else {
            columns(host, items, {label: i => i.hour.slice(11) + "h", title: i => `${i.hour.slice(11)}:00 ngày ${i.hour.slice(8, 10)}/${i.hour.slice(5, 7)}`,
                unit: "lượt", height: 130, labelEvery: 6});
        }
        const lat = data.latency_7d || {};
        const sec = ms => ms == null ? "—" : (ms / 1000).toLocaleString("vi-VN", {maximumFractionDigits: 1}) + " giây";
        $("#ovP50").textContent = sec(lat.p50_ms);
        $("#ovP95").textContent = sec(lat.p95_ms);
    }

    function renderTypes(data) {
        const host = $("#chartTypes");
        const items = data.by_type || [];
        if (!items.length) { host.innerHTML = '<div class="empty-viz">Chưa có dữ liệu phân loại.</div>'; return; }
        const max = Math.max(...items.map(i => i.n));
        host.innerHTML = items.map(i => `<div class="hbar-row" tabindex="0" aria-label="${esc(i.label)}: ${nf(i.n)} văn bản">
            <span class="hbar-label">${esc(i.label)}</span><span class="hbar-track"><span class="hbar-fill" data-w="${(i.n / max) * 100}"></span></span>
            <span class="hbar-val">${nf(i.n)}</span></div>`).join("") + srTable("Loại văn bản trong kho", ["Loại", "Số văn bản"], items.map(i => [i.label, i.n]));
        requestAnimationFrame(() => host.querySelectorAll(".hbar-fill").forEach(el => { el.style.width = el.dataset.w + "%"; }));
        host.querySelectorAll(".hbar-row").forEach((row, index) => bindTip(row, items[index].label, `${nf(items[index].n)} văn bản`));
    }

    const SYNC_PARTS = [["done", "Đã nạp", "s-done"], ["pending", "Chờ tải", "s-pending"], ["failed", "Lỗi", "s-failed"], ["skipped", "Bỏ qua", "s-skipped"]];
    function renderSync(data) {
        const host = $("#chartSync");
        const sync = data.sync;
        if (!sync) { host.innerHTML = '<div class="empty-viz">Chưa kiểm kê QLVB.</div>'; return; }
        let html = "", done = 0, total = 0;
        const rows = [];
        for (const key of ["di", "den"]) {
            const s = sync[key] || {};
            const inv = s.inventoried || 0;
            done += s.done || 0; total += inv;
            const parts = SYNC_PARTS.map(([k, label, cls]) => ({label, cls, n: s[k] || 0})).filter(p => p.n);
            rows.push([s.label || key, s.source_documents ?? "—", inv, s.done || 0, s.pending || 0, s.failed || 0, s.skipped || 0]);
            html += `<div class="sync-row"><div class="sync-head"><strong>${esc(s.label || key)}</strong>
                <span>${nf(s.done)} / ${nf(inv)} đã nạp${s.source_documents ? ` · QLVB có ${nf(s.source_documents)}` : ""}</span></div>
                <div class="stack" role="img" aria-label="${esc(s.label || key)}: ${parts.map(p => `${p.label} ${nf(p.n)}`).join(", ") || "chưa kiểm kê"}">
                ${inv ? parts.map(p => `<i class="${p.cls}" data-w="${(p.n / inv) * 100}" data-label="${esc(p.label)}" data-n="${p.n}" tabindex="0"></i>`).join("") : ""}</div></div>`;
        }
        html += `<div class="legend">${SYNC_PARTS.map(([, label, cls]) => `<span><i class="${cls}"></i>${label}</span>`).join("")}</div>`;
        html += srTable("Tiến độ đồng bộ QLVB", ["Hướng", "Trên QLVB", "Đã kiểm kê", "Đã nạp", "Chờ tải", "Lỗi", "Bỏ qua"], rows);
        host.innerHTML = html;
        host.querySelectorAll(".stack > i").forEach(el => {
            el.style.width = "0";
            requestAnimationFrame(() => { el.style.width = el.dataset.w + "%"; });
            bindTip(el, el.dataset.label, nf(el.dataset.n) + " văn bản");
        });
        $("#ovSync").textContent = total ? Math.round((done / total) * 100) + "%" : "—";
    }

    function renderStamp(data) {
        const crawler = data.crawler || {};
        const active = ["crawling", "syncing", "downloading", "logging_in", "starting", "waiting_captcha"].includes(crawler.status);
        const dot = $("#liveDot");
        dot.className = "dot live" + (data.error ? " bad" : "");
        const time = new Date((data.generated_at || Date.now() / 1000) * 1000).toLocaleTimeString("vi-VN", {hour: "2-digit", minute: "2-digit", second: "2-digit"});
        $("#liveStamp").textContent = data.error ? "Không đọc được số liệu" :
            active ? `Đang đồng bộ QLVB${crawler.total ? ` · ${nf(crawler.done)}/${nf(crawler.total)}` : ""} · cập nhật ${time}` :
            `Trực tiếp · cập nhật ${time}`;
    }

    function render(data) {
        last = data;
        const docs = data.documents || {};
        countTo($("#ovDocs"), docs.docs);
        countTo($("#ovFiles"), docs.files);
        countTo($("#ovChunks"), docs.chunks);
        renderStamp(data);
        renderMonths(data);
        renderTypes(data);
        renderSync(data);
        renderQueries(data);
    }

    async function load() {
        const refresh = $("#liveRefresh");
        refresh && refresh.classList.add("busy");
        try {
            const response = await fetch("/api/overview", {cache: "no-store"});
            render(await response.json());
        } catch {
            renderStamp({error: true});
        } finally {
            refresh && refresh.classList.remove("busy");
        }
    }

    function visible() { return document.visibilityState === "visible" && document.body.dataset.tab === "overview"; }
    function schedule() {
        clearTimeout(timer);
        timer = setTimeout(async () => { if (visible()) await load(); schedule(); }, REFRESH_MS);
    }

    /* ---------- bảng giá mô hình AI ---------- */
    const PROVIDER_LABEL = {openrouter: "qua OpenRouter", openai: "OpenAI trực tiếp", anthropic: "Anthropic trực tiếp", gemini: "Google trực tiếp"};
    const INPUT_LABEL = {text: "Văn bản", image: "Ảnh", audio: "Âm thanh", file: "PDF", video: "Video"};
    const usd = v => v == null ? "—" : "$" + Number(v).toLocaleString("en-US", {maximumFractionDigits: v < 1 ? 3 : 2});
    const ctx = n => !n ? "" : n >= 1e6 ? (n / 1e6).toLocaleString("vi-VN", {maximumFractionDigits: 1}) + "M" : Math.round(n / 1000) + "K";

    async function loadPricing() {
        const host = $("#pricing");
        if (!host) return;
        try {
            const response = await fetch("/api/models/featured", {cache: "no-store"});
            const data = await response.json();
            const items = data.items || [];
            if (!items.length) {
                host.innerHTML = '<div class="card empty">Quản trị viên chưa chọn mô hình nổi bật để hiển thị.</div>';
                return;
            }
            const answerIndex = items.findIndex(item => (item.used_for || []).includes("Tra cứu"));
            host.innerHTML = items.map((item, index) => {
                const featured = index === answerIndex;
                const uses = item.used_for || [];
                return `<article class="card price reveal${featured ? " featured" : ""}">
                    ${featured ? '<div class="p-badge">Đang dùng để trả lời</div>' : ""}
                    <div class="p-vendor">${esc(item.vendor || "")} · <span>${esc(PROVIDER_LABEL[item.provider] || item.provider)}</span></div>
                    <div class="p-name">${esc(item.name)}</div>
                    <div class="p-val">${item.question_vnd == null ? "—" : `<span>~${nf(item.question_vnd)}</span>đ`}</div>
                    <div class="p-unit">mỗi câu hỏi tra cứu (ước tính)</div>
                    <div class="p-rates"><div><small>Đầu vào</small><strong>${usd(item.prompt)}</strong></div><div><small>Đầu ra</small><strong>${usd(item.completion)}</strong></div><div><small>Ngữ cảnh</small><strong>${ctx(item.context) || "—"}</strong></div></div>
                    <div class="p-io">${(item.inputs || []).map(k => `<span class="io-tag">${esc(INPUT_LABEL[k] || k)}</span>`).join("")}</div>
                    <ul>${uses.length ? uses.map(u => `<li><svg class="ico"><use href="#i-check"/></svg>Đang dùng cho: ${esc(u)}</li>`).join("")
                        : '<li><svg class="ico"><use href="#i-check"/></svg>Sẵn sàng khi quản trị viên chọn</li>'}
                        ${item.note ? `<li><svg class="ico"><use href="#i-check"/></svg>${esc(item.note)}</li>` : ""}</ul>
                </article>`;
            }).join("");
            const [pt, ct] = data.question_tokens || [6000, 700];
            $("#pricingNote").textContent = `Giá niêm yết USD cho 1 triệu token, lấy trực tiếp từ OpenRouter. Chi phí mỗi câu hỏi ước tính với ~${nf(pt)} token ngữ cảnh và ~${nf(ct)} token trả lời, tỷ giá ${nf(data.usd_to_vnd)}đ/USD; chi phí thực tế thay đổi theo độ dài văn bản.`;
            window.UI.reveal(host);
        } catch {
            host.innerHTML = '<div class="card empty">Không tải được bảng giá mô hình. Vui lòng thử lại sau.</div>';
        }
    }

    let resizeTimer = null;
    window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => last && render(last), 150); });
    document.addEventListener("visibilitychange", () => { if (visible()) load(); });
    window.addEventListener("tab:change", event => { if (event.detail === "overview") { load(); if (last) render(last); } });
    $("#liveRefresh")?.addEventListener("click", () => { load(); window.UI.toast("Đã làm mới số liệu", "ok", {duration: 1800}); });

    load();
    loadPricing();
    schedule();
})();
