/* Kiểm tra dự thảo → Thể thức & chính tả (.docx).
   Kết quả nhận theo luồng: báo cáo thể thức + chính tả bằng code có ngay, chính tả AI đến sau.
   Hai cách xem: theo lỗi (gộp cùng lỗi trên nhiều đoạn) và theo đoạn (mọi lỗi của một đoạn trong một thẻ). */
(function () {
    "use strict";
    const $ = s => document.querySelector(s);
    const esc = window.UI.esc;
    const nf = n => Number(n || 0).toLocaleString("vi-VN");
    const LS = key => { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; } };
    const SAVE = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* bỏ qua */ } };

    const ICON = {
        pass: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>',
        fail: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>',
        warn: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8v5M12 17h.01"/></svg>',
        info: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 11v6M12 7h.01"/></svg>',
        off: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/></svg>',
    };
    const STATUS_TEXT = {pass: "Đạt", fail: "Không đạt", warn: "Cần xem xét", info: "Lưu ý", off: "Bỏ qua"};
    const statusBadge = (status, text) => `<span class="dc-badge ${status}">${ICON[status] || ""}${esc(text || STATUS_TEXT[status])}</span>`;

    const state = {profiles: {}, profile: LS("dc-profile") || "skhcn", report: null, ai: null, view: "issue",
                   filter: "errors", file: null, running: false, words: []};

    /* ---------- bộ thể thức ---------- */
    const fmt = v => String(v).replace(".", ",");
    const range = (spec, unit) => spec && typeof spec === "object" && !Array.isArray(spec)
        ? `${fmt(spec.min)}–${fmt(spec.max)} ${unit}` : `${fmt(spec)} ${unit}`;
    const list = (v, unit) => (Array.isArray(v) ? v : [v]).map(x => fmt(x)).join(" / ") + " " + unit;
    function lineText(line) {
        const t = v => v === "single" ? "Single" : fmt(v) + " lines";
        return line.min === line.max ? t(line.min) : `${t(line.min)} – ${t(line.max)}`;
    }

    function specChips(p) {
        const m = p.page.margins_mm, b = p.body;
        const spacing = b.spacing.mode === "exact" ? `Cách đoạn ${fmt(b.spacing.before_pt)}/${fmt(b.spacing.after_pt)} pt`
            : `Cách đoạn ≥ ${fmt(b.spacing.min_pt)} pt`;
        return [
            "A4", `Lề T/D/Tr/P: ${[m.top, m.bottom, m.left, m.right].map(v => typeof v === "object" ? `${v.min}–${v.max}` : v).join(" / ")} mm`,
            p.font, `Nội dung ${list(b.size_pt, "pt")}`, `Thụt đầu dòng ${list(b.first_line_cm, "cm")}`, spacing,
            `Giãn dòng ${lineText(b.line)}`, `Ký ${list(p.closing.signer_pt, "pt")}`,
            p.end_mark === "required" ? "Kết thúc ./." : null,
            p.reference && p.reference.agency ? `Ký hiệu ${p.reference.agency}-…` : null,
        ].filter(Boolean).map(t => `<span class="io-tag">${esc(t)}</span>`).join("");
    }

    function renderProfile() {
        document.querySelectorAll("#dcProfiles button").forEach(b => {
            const on = b.dataset.profile === state.profile;
            b.classList.toggle("on", on);
            b.setAttribute("aria-checked", on ? "true" : "false");
        });
        const custom = state.profile === "custom";
        $("#dcCustom").hidden = !custom;
        const p = state.profiles[state.profile] || (custom ? null : state.profiles.skhcn);
        if (custom) {
            $("#dcProfileDesc").textContent = "Nhập thông số riêng; các mục không nhập lấy theo bộ gốc đã chọn. Thông số được lưu trên trình duyệt này.";
            $("#dcSpec").innerHTML = "";
        } else if (p) {
            $("#dcProfileDesc").textContent = p.description || "";
            $("#dcSpec").innerHTML = specChips(p);
        }
    }

    function fillCustom(values) {
        const f = $("#dcCustom");
        const base = state.profiles[values.base || "skhcn"] || state.profiles.skhcn;
        if (!base) return;
        const m = base.page.margins_mm, one = v => typeof v === "object" ? v.min : v;
        const val = (name, fallback) => values[name] !== undefined ? values[name] : fallback;
        f.base.value = values.base || "skhcn";
        f.m_top.value = val("m_top", one(m.top)); f.m_bottom.value = val("m_bottom", one(m.bottom));
        f.m_left.value = val("m_left", one(m.left)); f.m_right.value = val("m_right", one(m.right));
        f.font.value = val("font", base.font);
        f.size_pt.value = val("size_pt", base.body.size_pt.map(fmt).join("; "));
        f.first_line_cm.value = val("first_line_cm", base.body.first_line_cm.map(fmt).join("; "));
        f.line.value = val("line", base.body.line.max === "single" ? "single" : String(base.body.line.max));
        f.before_pt.value = val("before_pt", base.body.spacing.before_pt ?? 6);
        f.after_pt.value = val("after_pt", base.body.spacing.after_pt ?? 6);
        f.noi_nhan_title_pt.value = val("noi_nhan_title_pt", base.closing.noi_nhan_title_pt.map(fmt).join("; "));
        f.noi_nhan_items_pt.value = val("noi_nhan_items_pt", base.closing.noi_nhan_items_pt.map(fmt).join("; "));
        f.signer_pt.value = val("signer_pt", base.closing.signer_pt.map(fmt).join("; "));
        f.end_mark.value = val("end_mark", base.end_mark || "required");
        f.agency.value = val("agency", (base.reference && base.reference.agency) || "");
        f.units.value = val("units", Object.entries((base.reference && base.reference.units) || {}).map(([k, v]) => `${k}: ${v}`).join("\n"));
    }

    const nums = text => String(text || "").split(/[;\s]+|,(?=\s)/).map(x => x.trim().replace(",", ".")).filter(Boolean).map(Number);

    function customSpec() {
        const f = $("#dcCustom");
        const bad = [];
        const check = (name, label, values, low, high) => {
            if (!values.length || values.some(v => !Number.isFinite(v) || v < low || v > high)) bad.push(label);
            return values;
        };
        const units = {};
        f.units.value.split("\n").forEach(line => {
            const [code, ...rest] = line.split(":");
            if (code && code.trim()) units[code.trim()] = rest.join(":").trim() || code.trim();
        });
        const spec = {
            base: f.base.value,
            margins_mm: {top: +f.m_top.value, bottom: +f.m_bottom.value, left: +f.m_left.value, right: +f.m_right.value},
            font: f.font.value.trim() || "Times New Roman",
            size_pt: check("size_pt", "Cỡ chữ nội dung", nums(f.size_pt.value), 6, 30),
            first_line_cm: check("first_line_cm", "Thụt dòng đầu", nums(f.first_line_cm.value), 0, 5),
            line: f.line.value, before_pt: +f.before_pt.value, after_pt: +f.after_pt.value,
            noi_nhan_title_pt: check("nn", "Cỡ chữ “Nơi nhận”", nums(f.noi_nhan_title_pt.value), 6, 30),
            noi_nhan_items_pt: check("nni", "Cỡ chữ danh sách nơi nhận", nums(f.noi_nhan_items_pt.value), 6, 30),
            signer_pt: check("signer", "Cỡ chữ người ký", nums(f.signer_pt.value), 6, 30),
            end_mark: f.end_mark.value, agency: f.agency.value.trim(), units,
        };
        Object.entries(spec.margins_mm).forEach(([side, v]) => { if (!(v >= 5 && v <= 60)) bad.push("Lề " + side); });
        const error = $("#dcCustomError");
        error.hidden = !bad.length;
        error.textContent = bad.length ? "Kiểm tra lại: " + bad.join(", ") + "." : "";
        return bad.length ? null : spec;
    }

    function saveCustom() {
        const f = $("#dcCustom"), values = {};
        [...f.elements].forEach(el => { if (el.name) values[el.name] = el.value; });
        SAVE("dc-custom", values);
    }

    async function loadProfiles() {
        try {
            const data = await (await fetch("/api/check/profiles")).json();
            data.profiles.forEach(p => { state.profiles[p.key] = p; });
            $("#dcCoverage").innerHTML =
                `<div class="dc-cov-h">Đã kiểm tra bằng code</div><ul>${data.coverage.checked.map(t => `<li>${ICON.pass}${esc(t)}</li>`).join("")}</ul>` +
                `<div class="dc-cov-h">Cần xem thủ công</div><ul class="manual">${data.coverage.manual.map(t => `<li>${ICON.info}${esc(t)}</li>`).join("")}</ul>`;
            fillCustom(LS("dc-custom") || {});
            renderProfile();
        } catch {
            $("#dcProfileDesc").textContent = "Không tải được danh sách bộ thể thức.";
        }
    }

    document.querySelectorAll("#dcProfiles button").forEach(b => b.addEventListener("click", () => {
        state.profile = b.dataset.profile;
        SAVE("dc-profile", state.profile);
        renderProfile();
    }));
    $("#dcCustom").addEventListener("input", () => { saveCustom(); customSpec(); });
    $("#dcCustom").base.addEventListener("change", () => { fillCustom({base: $("#dcCustom").base.value}); saveCustom(); });
    $("#dcCustomReset").addEventListener("click", () => { fillCustom({base: $("#dcCustom").base.value}); saveCustom(); customSpec(); });
    const aiSaved = LS("dc-ai");
    if (aiSaved !== null) $("#dcAi").checked = aiSaved;
    $("#dcAi").addEventListener("change", e => SAVE("dc-ai", e.target.checked));

    /* ---------- từ điển riêng ---------- */
    function renderDict() {
        $("#dcDictCount").textContent = state.words.length ? `(${nf(state.words.length)} từ)` : "";
        $("#dcDict").innerHTML = state.words.length
            ? state.words.map(w => `<span class="chip dc-word">${esc(w)}<button type="button" data-remove="${esc(w)}" aria-label="Xoá ${esc(w)}">×</button></span>`).join("")
            : '<span class="muted small">Chưa có từ nào.</span>';
    }
    async function loadDict() {
        try { state.words = (await (await fetch("/api/check/dictionary")).json()).words || []; } catch { state.words = []; }
        renderDict();
    }
    async function addWord(word) {
        const response = await fetch("/api/check/dictionary", {method: "POST", headers: {"Content-Type": "application/json"},
                                                               body: JSON.stringify({word})});
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Không thêm được từ.");
        state.words = data.words || state.words;
        renderDict();
    }
    $("#dcDictForm").addEventListener("submit", async e => {
        e.preventDefault();
        const input = e.target.word, word = input.value.trim();
        if (!word) return;
        try { await addWord(word); input.value = ""; window.UI.toast(`Đã thêm “${word}” vào từ điển riêng.`, "ok"); }
        catch (err) { window.UI.toast(err.message, "bad"); }
    });
    $("#dcDict").addEventListener("click", async e => {
        const button = e.target.closest("[data-remove]");
        if (!button) return;
        const response = await fetch("/api/check/dictionary/" + encodeURIComponent(button.dataset.remove), {method: "DELETE"});
        if (response.ok) { state.words = (await response.json()).words || []; renderDict(); }
    });

    /* ---------- tải tệp và nhận kết quả ---------- */
    const drop = $("#dcDrop"), input = $("#dcFile");
    drop.addEventListener("click", () => input.click());
    drop.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
    drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", e => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) run(e.dataTransfer.files[0]); });
    input.addEventListener("change", () => { if (input.files[0]) run(input.files[0]); input.value = ""; });

    function progress(steps) {
        return `<div class="dc-progress glass">${steps.map(([status, text]) =>
            `<div class="dc-step ${status}">${status === "run" ? '<span class="spin dark"></span>' : (ICON[status] || "")}<span>${esc(text)}</span></div>`).join("")}</div>`;
    }

    async function run(file) {
        if (state.running) return;
        const out = $("#dcOut");
        if (!/\.docx$/i.test(file.name)) {
            const legacy = /\.doc$/i.test(file.name);
            out.innerHTML = `<div class="gate">${ICON.warn}<div><strong>${legacy ? "Định dạng Word cũ (.doc) chưa hỗ trợ kiểm tra thể thức." : "Chỉ kiểm tra tệp Word .docx."}</strong><br>`
                + `${legacy ? "Mở tệp trong Word, chọn File → Save As → Word Document (.docx) rồi kiểm tra lại." : "Tệp PDF và các định dạng khác không kiểm tra thể thức; dùng tab “Nội dung & căn cứ” để rà soát nội dung."}</div></div>`;
            return;
        }
        let spec = null;
        if (state.profile === "custom" && !(spec = customSpec())) {
            window.UI.toast("Thông số tùy chỉnh chưa hợp lệ.", "warn");
            return;
        }
        state.running = true;
        state.report = state.ai = null;
        state.file = file.name;
        $("#dcFileName").textContent = "📄 " + file.name;
        drop.classList.add("busy");
        out.innerHTML = progress([["run", "Đang đọc tệp và nhận diện cấu trúc văn bản…"]]);
        const form = new FormData();
        form.append("file", file);
        form.append("profile", state.profile);
        form.append("ai", $("#dcAi").checked ? "true" : "false");
        if (spec) form.append("custom", JSON.stringify(spec));
        try {
            const response = await fetch("/api/check/docx", {method: "POST", body: form, headers: {Accept: "text/event-stream"}});
            if (!response.ok || !response.body) {
                const data = await response.json().catch(() => ({}));
                throw new Error(typeof data.detail === "string" ? data.detail : (response.status === 503 ? "Hệ thống đang bận, vui lòng thử lại sau." : "Máy chủ trả lỗi " + response.status));
            }
            await readSse(response.body.getReader(), event => {
                if (event.type === "report") { state.report = event.data; render(); }
                else if (event.type === "ai_status") { state.ai = {status: "running", message: event.message, paragraphs: event.paragraphs}; render(); }
                else if (event.type === "ai") { state.ai = event.data; render(); }
                else if (event.type === "error") { out.innerHTML = `<div class="gate">${ICON.fail}<div><strong>${esc(event.message)}</strong></div></div>`; }
            });
            if (state.report && state.ai && state.ai.status === "running") {
                state.ai = {status: "error", message: "Kết nối bị gián đoạn trước khi AI trả kết quả.", issues: []};
                render();
            }
        } catch (err) {
            out.innerHTML = `<div class="gate">${ICON.fail}<div><strong>Không kiểm tra được tệp.</strong><br>${esc(err.message || "")}</div></div>`;
        } finally {
            state.running = false;
            drop.classList.remove("busy");
        }
    }

    /* ---------- hiển thị ---------- */
    function summaryCard(r) {
        const s = r.summary, st = r.structure;
        const aiCount = state.ai && state.ai.issues ? state.ai.issues.length : null;
        const verdict = s.status === "pass" ? "Đạt thể thức" : s.status === "fail" ? "Có lỗi cần sửa" : "Cần xem xét";
        const scope = st.start
            ? `${esc(st.doc_type_label || "Văn bản")} · nội dung từ đoạn ${st.start.index} (“${esc(st.start.excerpt)}”)`
              + (st.end ? ` đến đoạn ${st.end.index}` : "") + (st.appendix ? ` · phụ lục từ đoạn ${st.appendix.index}` : "")
            : "Chưa xác định được phần nội dung";
        const notes = [
            st.party ? `<div class="dc-note info">${ICON.info}<span>Văn bản của tổ chức Đảng (trình bày theo Hướng dẫn 36-HD/VPTW): không áp quy tắc phần đầu, số ký hiệu, nơi nhận và chữ ký của Nghị định 30.</span></div>` : "",
            ...(st.uncertain || []).map(t => `<div class="dc-note warn">${ICON.warn}<span>${esc(t)} Các quy tắc phần nội dung chưa được áp dụng.</span></div>`),
        ].join("");
        return `<section class="dc-summary glass ${s.status}">
            <div class="dc-sum-head">
                <div class="dc-verdict ${s.status}">${ICON[s.status]}</div>
                <div class="dc-sum-title"><h3>${esc(verdict)}</h3><div class="muted">${esc(r.file || "")} · ${esc(r.profile.label)} · ${nf(r.stats.nonempty)} đoạn, ${nf(r.stats.sections)} section</div></div>
            </div>
            <div class="dc-tiles">
                <div class="dc-tile ${s.fail ? "fail" : "pass"}"><strong>${nf(s.fail)}</strong><span>Lỗi thể thức</span></div>
                <div class="dc-tile ${s.warn ? "warn" : "pass"}"><strong>${nf(s.warn)}</strong><span>Cần xem xét</span></div>
                <div class="dc-tile ${s.spelling_fail ? "fail" : s.spelling_warn ? "warn" : "pass"}"><strong>${nf(s.spelling_fail + s.spelling_warn)}</strong><span>Chính tả (code)</span></div>
                <div class="dc-tile ${aiCount === null ? "off" : aiCount ? "warn" : "pass"}"><strong>${aiCount === null ? "…" : nf(aiCount)}</strong><span>Chính tả (AI)</span></div>
            </div>
            <div class="dc-scope">${ICON.info}<span>${scope}</span></div>
            ${notes}
        </section>`;
    }

    function toolbar() {
        return `<div class="dc-toolbar">
            <div class="seg" role="group" aria-label="Cách xem"><button type="button" data-view="issue" class="${state.view === "issue" ? "on" : ""}">Theo lỗi</button><button type="button" data-view="paragraph" class="${state.view === "paragraph" ? "on" : ""}">Theo đoạn</button></div>
            <div class="seg" role="group" aria-label="Lọc"><button type="button" data-filter="errors" class="${state.filter === "errors" ? "on" : ""}">Chỉ lỗi</button><button type="button" data-filter="all" class="${state.filter === "all" ? "on" : ""}">Tất cả</button></div>
            <button class="btn btn-ghost btn-sm" type="button" data-toggle-all>Mở/thu gọn tất cả</button>
        </div>`;
    }

    const paraList = items => items && items.length
        ? `<ul class="dc-paras">${items.slice(0, 60).map(i => `<li><span class="dc-pno">Đoạn ${i.index}</span><span>${esc(i.excerpt)}</span></li>`).join("")}${items.length > 60 ? `<li class="muted">… và ${nf(items.length - 60)} đoạn khác</li>` : ""}</ul>` : "";

    function checkRow(c) {
        const where = c.where ? `<span class="dc-where">${esc(c.where)}</span>` : "";
        const count = c.items && c.items.length ? `<span class="dc-count">${nf(c.count)} đoạn</span>` : "";
        const values = c.status === "pass"
            ? `<span class="dc-val">${esc(c.expected || "")}</span>`
            : `<span class="dc-val">Yêu cầu: <b>${esc(c.expected || "—")}</b>${c.current ? ` · Hiện tại: <b class="bad">${esc(c.current)}</b>` : ""}</span>`;
        const hint = c.hint && c.status !== "pass" ? `<div class="dc-hint">${esc(c.hint)}</div>` : "";
        const body = c.status !== "pass" ? paraList(c.items) : "";
        return body
            ? `<details class="dc-check ${c.status}"><summary>${statusBadge(c.status)}<span class="dc-label">${esc(c.label)}</span>${where}${count}${values}</summary>${hint}${body}</details>`
            : `<div class="dc-check ${c.status}"><div class="dc-row">${statusBadge(c.status)}<span class="dc-label">${esc(c.label)}</span>${where}${count}${values}</div>${hint}</div>`;
    }

    function groupCard(g) {
        let checks = g.checks.filter(c => !c.hidden);
        if (state.filter === "errors") checks = checks.filter(c => c.status === "fail" || c.status === "warn");
        const fails = g.checks.filter(c => c.status === "fail" && !c.hidden).length;
        const warns = g.checks.filter(c => c.status === "warn" && !c.hidden).length;
        const passes = g.checks.filter(c => c.status === "pass" && !c.hidden).length;
        const counts = [fails ? `${fails} lỗi` : "", warns ? `${warns} cần xem` : "", passes ? `${passes} đạt` : ""].filter(Boolean).join(" · ");
        const body = checks.length ? checks.map(checkRow).join("")
            : `<div class="dc-empty">${ICON.pass}Không có lỗi trong nhóm này.</div>`;
        const layout = g.key === "layout" && state.report.layout_patterns.length > 1
            ? `<div class="dc-hint">${nf(state.report.stats.sections)} section được gộp thành ${state.report.layout_patterns.length} mẫu bố cục: ${state.report.layout_patterns.map(p => `${esc(p.where)} (${p.fail ? p.fail + " lỗi" : "đạt"})`).join("; ")}.</div>` : "";
        return `<details class="dc-group ${g.status}" ${g.status === "fail" || g.status === "warn" ? "open" : ""}>
            <summary>${statusBadge(g.status)}<h4>${esc(g.label)}</h4><span class="dc-count">${esc(counts)}</span></summary>
            <div class="dc-group-body">${layout}${body}</div></details>`;
    }

    function issueItem(i, showPara) {
        const canAdd = i.category === "spelling" && i.original && !/\s/.test(i.original);
        // Gạch bỏ chỉ khi có chữ sai cần thay; lỗi khoảng trắng/dấu câu hiện đoạn trích để dễ tìm.
        const replaceable = i.suggestion || ["spelling", "repeat", "ai"].includes(i.category);
        const change = !i.original ? "" : replaceable
            ? `<span class="dc-change"><del>${esc(i.original)}</del>${i.suggestion ? `<span class="dc-arrow">→</span><ins>${esc(i.suggestion)}</ins>` : ""}</span>`
            : `<span class="dc-snippet">${esc(i.original)}</span>`;
        const conf = i.confidence != null ? `<span class="dc-where">tin cậy ${Math.round(i.confidence * 100)}%</span>` : "";
        const para = showPara ? `<span class="dc-pno">Đoạn ${i.index}</span>` : "";
        return `<li class="dc-issue ${i.severity}">${statusBadge(i.severity, i.severity === "fail" ? (i.source === "ai" ? "Khả năng sai cao" : "Sai") : (i.source === "ai" ? "Cần xem xét" : "Có thể sai"))}
            ${para}<span class="tag">${esc(i.category_label || (i.source === "ai" ? "AI" : i.category))}</span>${change}<span class="dc-msg">${esc(i.message || "")}</span>${conf}
            ${canAdd ? `<button class="btn btn-ghost btn-sm" type="button" data-add-word="${esc(i.original)}">Thêm vào từ điển</button>` : ""}</li>`;
    }

    function byParagraph(issues) {
        const map = new Map();
        issues.forEach(i => {
            if (!map.has(i.index)) map.set(i.index, {index: i.index, excerpt: i.excerpt, items: []});
            map.get(i.index).items.push(i);
        });
        return [...map.values()].sort((a, b) => a.index - b.index);
    }

    function spellingCard(title, issues, extra, status) {
        let shown = issues;
        if (state.filter === "errors") shown = issues;  // chính tả: luôn là lỗi/cảnh báo
        const groups = byParagraph(shown);
        const fails = issues.filter(i => i.severity === "fail").length;
        const counts = issues.length ? `${nf(issues.length)} mục · ${nf(groups.length)} đoạn` : "";
        const body = groups.length
            ? groups.map(g => `<div class="dc-pcard"><div class="dc-phead"><span class="dc-pno">Đoạn ${g.index}</span><span class="dc-excerpt">${esc(g.excerpt)}</span></div><ul class="dc-issues">${g.items.map(i => issueItem(i, false)).join("")}</ul></div>`).join("")
            : `<div class="dc-empty">${ICON.pass}Không phát hiện lỗi.</div>`;
        const st = status || (fails ? "fail" : issues.length ? "warn" : "pass");
        return `<details class="dc-group ${st}" ${st === "fail" || st === "warn" || st === "info" ? "open" : ""}>
            <summary>${statusBadge(st, st === "info" ? "Đang chạy" : undefined)}<h4>${esc(title)}</h4><span class="dc-count">${esc(counts)}</span></summary>
            <div class="dc-group-body">${extra || ""}${status === "info" || status === "off" ? "" : body}</div></details>`;
    }

    function aiCard() {
        const ai = state.ai;
        if (!ai) return "";
        if (ai.status === "running") {
            return spellingCard("Chính tả (AI)", [], `<div class="dc-step run"><span class="spin dark"></span><span>${esc(ai.message)} (${nf(ai.paragraphs)} đoạn nội dung)</span></div>`, "info");
        }
        if (ai.status === "off" || ai.status === "skipped" || ai.status === "error") {
            const st = ai.status === "error" ? "warn" : "off";
            return spellingCard("Chính tả (AI)", [], `<div class="dc-note ${ai.status === "error" ? "warn" : "info"}">${ICON[ai.status === "error" ? "warn" : "info"]}<span>${esc(ai.message || "Đã bỏ qua.")}</span></div>`, st);
        }
        const meta = `<div class="dc-hint">Mô hình ${esc(ai.model)} · ${nf(ai.paragraphs)} đoạn trong ${String(ai.seconds).replace(".", ",")} giây${ai.dropped ? ` · đã lọc bỏ ${nf(ai.dropped)} gợi ý không đáng tin (trùng, không có trong đoạn, ./. …)` : ""}. AI chỉ gợi ý — hãy đối chiếu trước khi sửa.</div>`;
        return spellingCard("Chính tả (AI)", ai.issues || [], meta);
    }

    function paragraphView(r) {
        const all = [];
        r.groups.forEach(g => g.checks.forEach(c => {
            if (c.hidden || c.status === "pass" || c.status === "info") return;
            (c.items || []).forEach(item => all.push({index: item.index, excerpt: item.excerpt, severity: c.status,
                category: "format", category_label: g.label.split(" ")[0] === "Nội" ? "Thể thức" : g.label,
                message: `${c.label}: yêu cầu ${c.expected}${c.current ? `, hiện tại ${c.current}` : ""}`}));
        }));
        (r.spelling.issues || []).forEach(i => all.push(i));
        ((state.ai && state.ai.issues) || []).forEach(i => all.push(i));
        const docLevel = r.groups.flatMap(g => g.checks.filter(c => !c.hidden && (c.status === "fail" || c.status === "warn") && !(c.items || []).length)
            .map(c => ({...c, group: g.label})));
        const groups = byParagraph(all);
        const docCard = docLevel.length ? `<section class="dc-group fail open-static"><div class="dc-static-head">${statusBadge("fail", "Toàn văn bản")}<h4>Lỗi không gắn với đoạn cụ thể</h4></div><div class="dc-group-body">${docLevel.map(checkRow).join("")}</div></section>` : "";
        return docCard + (groups.length ? groups.map(g => {
            const fails = g.items.filter(i => i.severity === "fail").length;
            return `<details class="dc-pcard dc-group ${fails ? "fail" : "warn"}" open><summary><span class="dc-pno">Đoạn ${g.index}</span><span class="dc-excerpt">${esc(g.excerpt)}</span><span class="dc-count">${nf(g.items.length)} lỗi</span></summary>
                <ul class="dc-issues">${g.items.map(i => issueItem(i, false)).join("")}</ul></details>`;
        }).join("") : `<div class="dc-empty">${ICON.pass}Không có lỗi gắn với đoạn văn.</div>`);
    }

    function render() {
        const r = state.report;
        if (!r) return;
        const out = $("#dcOut");
        const open = new Set([...out.querySelectorAll("details[open]")].map(d => d.querySelector("h4, .dc-pno")?.textContent));
        const content = state.view === "paragraph" ? paragraphView(r)
            : r.groups.map(groupCard).join("") + spellingCard("Chính tả (kiểm tra bằng code)", r.spelling.issues || [],
                r.spelling.total > (r.spelling.issues || []).length ? `<div class="dc-hint">Hiển thị ${nf(r.spelling.issues.length)}/${nf(r.spelling.total)} mục đầu tiên.</div>` : "") + aiCard();
        out.innerHTML = summaryCard(r) + toolbar() + `<div class="dc-results">${content}</div>`;
        if (state.running) out.querySelectorAll("details").forEach(d => {
            const key = d.querySelector("h4, .dc-pno")?.textContent;
            if (open.size && key && !open.has(key) && d.classList.contains("pass")) d.open = false;
        });
        window.UI.reveal(out);
    }

    $("#dcOut").addEventListener("click", async e => {
        const view = e.target.closest("[data-view]"), filter = e.target.closest("[data-filter]");
        if (view) { state.view = view.dataset.view; render(); return; }
        if (filter) { state.filter = filter.dataset.filter; render(); return; }
        if (e.target.closest("[data-toggle-all]")) {
            const all = [...$("#dcOut").querySelectorAll(".dc-results > details")];
            const openAll = all.some(d => !d.open);
            all.forEach(d => { d.open = openAll; });
            return;
        }
        const add = e.target.closest("[data-add-word]");
        if (add) {
            add.disabled = true;
            try {
                await addWord(add.dataset.addWord);
                window.UI.toast(`Đã thêm “${add.dataset.addWord}” vào từ điển riêng; lần kiểm tra sau sẽ không báo từ này.`, "ok");
                add.closest(".dc-issue").classList.add("dismissed");
                add.remove();
            } catch (err) { add.disabled = false; window.UI.toast(err.message, "bad"); }
        }
    });

    window.addEventListener("auth:change", event => { if (event.detail && event.detail.user) loadDict(); });
    loadProfiles();
})();
