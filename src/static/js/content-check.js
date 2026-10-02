/* Kiểm tra dự thảo → Nội dung & căn cứ.
   Kết quả nhận theo luồng: kiểm tra bằng code (dẫn chiếu, đối chiếu kho, khối căn cứ, logic) có ngay,
   tra cứu CSDL quốc gia về pháp luật (vbpl.vn) hiện dần từng văn bản, thẩm định AI đến sau.
   Bốn nhóm: Căn cứ · Dẫn chiếu · Logic · Thẩm định AI. */
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
        link: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3h7v7M21 3l-9 9M19 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5"/></svg>',
    };
    const STATUS_TEXT = {pass: "Đạt", fail: "Không đạt", warn: "Cần xem xét", info: "Chưa đối chiếu", off: "Bỏ qua"};
    const badge = (status, text) => `<span class="dc-badge ${status}">${ICON[status] || ""}${esc(text || STATUS_TEXT[status])}</span>`;
    const isProblem = s => s === "fail" || s === "warn";
    const link = (url, text) => url && /^https?:\/\//.test(url)
        ? `<a class="btn btn-ghost btn-sm cc-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${ICON.link}${esc(text)}</a>` : "";

    const state = {report: null, legal: null, related: null, ai: null, filter: "errors", running: false};

    /* ---------- thiết lập ---------- */
    ["ccLegal", "ccAi"].forEach(id => {
        const saved = LS("cc-" + id);
        if (saved !== null) $("#" + id).checked = saved;
        $("#" + id).addEventListener("change", e => SAVE("cc-" + id, e.target.checked));
    });

    async function loadInfo() {
        try {
            const data = await (await fetch("/api/check/content_info")).json();
            const c = data.coverage;
            $("#ccCoverage").innerHTML =
                `<div class="dc-cov-h">Kiểm tra bằng code</div><ul>${c.code.map(t => `<li>${ICON.pass}${esc(t)}</li>`).join("")}</ul>` +
                `<div class="dc-cov-h">Thẩm định bằng AI</div><ul>${c.ai.map(t => `<li>${ICON.pass}${esc(t)}</li>`).join("")}</ul>` +
                `<div class="dc-cov-h">Cần xem thủ công</div><ul class="manual">${c.manual.map(t => `<li>${ICON.info}${esc(t)}</li>`).join("")}</ul>`;
            if (data.model) $("#ccAiNote").textContent = `Gửi phần nội dung và trích đoạn văn bản được dẫn tới mô hình ${data.model}.`;
            const legal = data.legal || {};
            if (!legal.enabled) {
                $("#ccLegal").checked = false;
                $("#ccLegal").disabled = true;
                $("#ccLegalNote").textContent = "Quản trị viên đã tắt đối chiếu vbpl.vn.";
            } else if (legal.built_at) {
                const total = legal.counts ? (legal.counts.central || 0) + (legal.counts.local || 0) : 0;
                $("#ccLegalNote").textContent = `Tra hiệu lực, ngày ban hành văn bản quy phạm trên vbpl.vn (chỉ gửi số ký hiệu). `
                    + `Danh mục ${nf(total)} văn bản, cập nhật ${new Date(legal.built_at).toLocaleDateString("vi-VN")}.`;
            } else {
                $("#ccLegalNote").textContent = legal.building ? "Đang tạo danh mục vbpl.vn lần đầu…" : "Chưa có danh mục vbpl.vn; sẽ tự tạo ở lần kiểm tra đầu tiên.";
            }
        } catch { $("#ccCoverage").textContent = "Không tải được phạm vi kiểm tra."; }
    }

    /* ---------- tải tệp và nhận kết quả ---------- */
    const drop = $("#ccDrop"), input = $("#ccFile");
    drop.addEventListener("click", () => input.click());
    drop.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
    drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", e => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) run(e.dataTransfer.files[0]); });
    input.addEventListener("change", () => { if (input.files[0]) run(input.files[0]); input.value = ""; });

    const gate = (status, title, text) => `<div class="gate">${ICON[status]}<div><strong>${esc(title)}</strong>${text ? "<br>" + esc(text) : ""}</div></div>`;

    async function run(file) {
        if (state.running) return;
        const out = $("#ccOut");
        if (!/\.(docx|doc|pdf)$/i.test(file.name)) {
            out.innerHTML = gate("warn", "Chỉ kiểm tra tệp .docx, .doc hoặc .pdf.");
            return;
        }
        Object.assign(state, {running: true, report: null, legal: null, related: null, ai: null, aiRequested: $("#ccAi").checked});
        $("#ccFileName").textContent = "📄 " + file.name;
        drop.classList.add("busy");
        out.innerHTML = `<div class="dc-progress glass"><div class="dc-step run"><span class="spin dark"></span><span>Đang đọc tệp, nhận diện văn bản được dẫn và đối chiếu kho…</span></div></div>`;
        const form = new FormData();
        form.append("file", file);
        form.append("ai", $("#ccAi").checked ? "true" : "false");
        form.append("legal", $("#ccLegal").checked && !$("#ccLegal").disabled ? "true" : "false");
        try {
            const response = await fetch("/api/check/content_stream", {method: "POST", body: form, headers: {Accept: "text/event-stream"}});
            if (!response.ok || !response.body) {
                const data = await response.json().catch(() => ({}));
                throw new Error(typeof data.detail === "string" ? data.detail : (response.status === 503 ? "Hệ thống đang bận, vui lòng thử lại sau." : "Máy chủ trả lỗi " + response.status));
            }
            await readSse(response.body.getReader(), event => {
                const r = state.report;
                if (event.type === "report") { state.report = event.data; }
                else if (event.type === "legal_status") { state.legal = {status: "running", message: event.message, total: event.total, done: 0}; }
                else if (event.type === "legal_item" && r) {
                    const index = r.references.findIndex(ref => ref.id === event.data.id);
                    if (index >= 0) r.references[index] = event.data;
                    if (state.legal) state.legal.done += 1;
                }
                else if (event.type === "legal") { state.legal = event.data; }
                else if (event.type === "related") { state.related = event.data; }
                else if (event.type === "ai_status") { state.ai = {status: "running", message: event.message}; }
                else if (event.type === "ai") { state.ai = event.data; }
                else if (event.type === "error") { out.innerHTML = gate("fail", event.message); return; }
                if (state.report) render();
            });
            if (state.report && state.ai && state.ai.status === "running") {
                state.ai = {status: "error", message: "Kết nối bị gián đoạn trước khi AI trả kết quả.", issues: []};
            }
            if (state.report && state.legal && state.legal.status === "running") state.legal.status = "done";
            if (state.report) render();
        } catch (err) {
            out.innerHTML = gate("fail", "Không kiểm tra được tệp.", err.message || "");
        } finally {
            state.running = false;
            drop.classList.remove("busy");
        }
    }

    /* ---------- tổng hợp ---------- */
    function counts() {
        const r = state.report;
        const items = [...r.references.map(ref => ref.status), ...r.basis.map(b => b.status), ...r.logic.map(l => l.status)];
        const ai = state.ai && state.ai.issues ? state.ai.issues : [];
        return {
            fail: items.filter(s => s === "fail").length + ai.filter(i => i.severity === "fail").length,
            warn: items.filter(s => s === "warn").length + ai.filter(i => i.severity === "warn").length,
            expired: r.references.filter(ref => ref.checks.some(c => c.rule === "force" && c.status === "fail")).length,
            legalChecked: r.references.filter(ref => ref.legal && ref.legal.status === "found").length,
        };
    }

    function steps() {
        const legal = state.legal, ai = state.ai, out = [["pass", "Kiểm tra bằng code"]];
        if (legal) {
            if (legal.status === "running") out.push(["run", `vbpl.vn: ${nf(legal.done)}/${nf(legal.total)} văn bản`]);
            else if (legal.status === "done") out.push(["pass", `vbpl.vn: tìm thấy ${nf(legal.found)}/${nf(legal.checked)} văn bản`]);
            else out.push([legal.status === "unavailable" ? "warn" : "off", legal.message || "vbpl.vn"]);
        } else if (state.running) out.push(["run", "vbpl.vn"]);
        if (ai) {
            if (ai.status === "running") out.push(["run", "Thẩm định AI"]);
            else if (ai.status === "done") out.push(["pass", `Thẩm định AI (${String(ai.seconds || 0).replace(".", ",")} giây)`]);
            else out.push([ai.status === "error" ? "warn" : "off", ai.status === "off" ? "Không bật AI" : "Thẩm định AI"]);
        }
        return `<div class="cc-steps">${out.map(([s, t]) => `<span class="cc-step ${s}">${s === "run" ? '<span class="spin dark"></span>' : ICON[s] || ""}${esc(t)}</span>`).join("")}</div>`;
    }

    function summaryCard() {
        const r = state.report, m = r.meta, c = counts();
        const status = c.fail ? "fail" : c.warn ? "warn" : "pass";
        const verdict = status === "pass" ? "Không phát hiện vấn đề" : status === "fail" ? "Có lỗi cần sửa" : "Cần xem xét";
        const desc = [m.doc_type, m.number ? "số " + m.number : null, m.date_text ? "ngày " + m.date_text : "chưa ghi ngày"].filter(Boolean).join(" · ");
        const aiCount = state.ai && state.ai.issues ? state.ai.issues.length : null;
        const aiOff = state.ai ? state.ai.status === "off" : !state.aiRequested;
        const s = r.summary;
        return `<section class="dc-summary glass ${status}">
            <div class="dc-sum-head">
                <div class="dc-verdict ${status}">${ICON[status]}</div>
                <div class="dc-sum-title"><h3>${esc(verdict)}</h3><div class="muted">${esc(r.file || "")} · ${esc(desc)}</div></div>
            </div>
            <div class="dc-tiles">
                <div class="dc-tile ${c.fail ? "fail" : "pass"}"><strong>${nf(c.fail)}</strong><span>Lỗi</span></div>
                <div class="dc-tile ${c.warn ? "warn" : "pass"}"><strong>${nf(c.warn)}</strong><span>Cần xem xét</span></div>
                <div class="dc-tile ${c.expired ? "fail" : "pass"}"><strong>${nf(s.references)}</strong><span>Văn bản được dẫn · ${nf(s.in_kho)} trong kho · ${nf(c.legalChecked)} trên vbpl.vn</span></div>
                <div class="dc-tile ${aiCount === null || aiOff ? "off" : aiCount ? "warn" : "pass"}"><strong>${aiOff ? "—" : aiCount === null ? "…" : nf(aiCount)}</strong><span>${aiOff ? "Không bật AI" : "Thẩm định AI"}</span></div>
            </div>
            ${steps()}
            ${m.dated ? "" : `<div class="dc-note info">${ICON.info}<span>Dự thảo chưa ghi ngày ban hành; thời hạn và ngày của văn bản được dẫn được so với hôm nay (${esc(r.draft_date)}).</span></div>`}
        </section>`;
    }

    function toolbar() {
        return `<div class="dc-toolbar">
            <div class="seg" role="group" aria-label="Lọc"><button type="button" data-filter="errors" class="${state.filter === "errors" ? "on" : ""}">Chỉ vấn đề</button><button type="button" data-filter="all" class="${state.filter === "all" ? "on" : ""}">Tất cả</button></div>
            <button class="btn btn-ghost btn-sm" type="button" data-toggle-all>Mở/thu gọn tất cả</button>
        </div>`;
    }

    /* ---------- thẻ văn bản được dẫn: bảng đối chiếu ---------- */
    function compareTable(ref) {
        const kho = ref.kho, legal = ref.legal && ref.legal.status === "found" ? ref.legal : null;
        if (!kho && !legal) return "";
        const bad = rule => ref.checks.some(c => c.rule === rule && isProblem(c.status));
        const vbDate = legal && legal.issued ? legal.issued.split("-").reverse().join("/") : null;
        const cols = [kho ? "Trong kho" : null, legal ? "CSDL quốc gia (vbpl.vn)" : null].filter(Boolean);
        const row = (label, draft, khoValue, legalValue, rule) => {
            if (!draft && !khoValue && !legalValue) return "";
            const cls = rule && bad(rule) ? ' class="bad"' : "";
            return `<tr><th>${esc(label)}</th><td>${esc(draft || "—")}</td>${kho ? `<td${cls}>${esc(khoValue || "—")}</td>` : ""}${legal ? `<td${cls}>${esc(legalValue || "—")}</td>` : ""}</tr>`;
        };
        const force = legal ? `${legal.force_label}` : null;
        return `<div class="cc-wrap"><table class="cc-table">
            <thead><tr><th></th><th>Dự thảo ghi</th>${cols.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead>
            <tbody>
                ${row("Số ký hiệu", ref.identifier || "(không ghi số)", kho && kho.so_ky_hieu, legal && legal.identifier)}
                ${row("Ngày ban hành", ref.date, kho && kho.ngay_ban_hanh, vbDate, "date")}
                ${row("Cơ quan ban hành", ref.issuer, kho && kho.co_quan, legal && legal.issuer, "issuer")}
                ${row("Trích yếu", ref.title, kho && kho.trich_yeu, legal && legal.name, "title")}
                ${legal ? `<tr><th>Hiệu lực</th><td>—</td>${kho ? "<td>—</td>" : ""}<td class="${legal.force_level === "fail" ? "bad" : legal.force_level === "warn" ? "warnc" : "good"}">${esc(force)}</td></tr>` : ""}
            </tbody></table>
            <div class="cc-links">${kho ? link(kho.url, "Mở văn bản trong kho") : ""}${legal ? link(legal.url, "Mở trên vbpl.vn") : ""}</div></div>`;
    }

    function checkLine(c) {
        const values = c.status === "pass"
            ? `<span class="dc-val">${esc(c.current || c.expected || "")}</span>`
            : `<span class="dc-val">${c.expected ? `Đúng: <b>${esc(c.expected)}</b>` : ""}${c.current ? `${c.expected ? " · " : ""}Dự thảo / kết quả: <b class="${c.status === "info" ? "" : "bad"}">${esc(c.current)}</b>` : ""}</span>`;
        return `<li class="dc-issue ${c.status === "fail" ? "fail" : ""}">${badge(c.status)}<span class="dc-label">${esc(c.label)}</span>${values}
            ${c.hint ? `<span class="dc-msg">${esc(c.hint)}</span>` : ""}${c.link && c.status !== "pass" ? link(c.link, "Mở") : ""}</li>`;
    }

    function refCard(ref) {
        const problems = ref.checks.filter(c => c.status !== "pass");
        const pending = state.legal && state.legal.status === "running" && ["qppl", "ten_luat"].includes(ref.kind) && !ref.legal;
        const tags = [`<span class="tag">${esc(ref.kind_label)}</span>`, ref.date ? `<span class="dc-where">ngày ${esc(ref.date)}</span>` : ""];
        const force = ref.legal && ref.legal.status === "found" ? badge(ref.legal.force_level, ref.legal.force_label) : pending ? `<span class="dc-where"><span class="spin dark"></span> đang tra vbpl.vn</span>` : "";
        const mentions = ref.mentions.length > 1 ? `<span class="dc-count">nhắc ${nf(ref.mentions.length)} lần</span>` : "";
        return `<details class="dc-check ${ref.status}" ${isProblem(ref.status) ? "open" : ""}>
            <summary>${badge(ref.status)}<span class="dc-label">${esc(ref.label)}</span>${tags.join("")}${mentions}<span class="dc-val">${force}</span></summary>
            <div class="cc-ref-body">
                <div class="cc-snippet"><span class="dc-pno">Đoạn ${ref.paragraph}</span><span>${esc(ref.snippet)}</span></div>
                ${compareTable(ref)}
                ${problems.length ? `<ul class="dc-issues">${problems.map(checkLine).join("")}</ul>` : ""}
                ${!problems.length && !ref.kho && !(ref.legal && ref.legal.status === "found") ? `<div class="dc-hint">Chưa đối chiếu được: văn bản không có trong kho và không thuộc CSDL quốc gia về pháp luật.</div>` : ""}
            </div></details>`;
    }

    function basisLine(b) {
        return `<div class="dc-check ${b.status}"><div class="dc-row">${badge(b.status)}<span class="dc-label">${esc(b.label)}</span>
            <span class="dc-pno">Đoạn ${b.paragraph}</span><span class="dc-val">${b.status === "pass" ? esc(b.expected) : `Đúng: <b>${esc(b.expected)}</b>${b.current ? ` · Hiện tại: <b class="bad">${esc(b.current)}</b>` : ""}`}</span></div>
            ${b.hint ? `<div class="dc-hint">${esc(b.hint)}</div>` : ""}</div>`;
    }

    function group(key, title, items, status, extra, emptyText) {
        const body = items.length ? items.join("") : `<div class="dc-empty">${ICON.pass}${esc(emptyText || "Không có vấn đề trong nhóm này.")}</div>`;
        return `<details class="dc-group ${status}" data-group="${key}" ${isProblem(status) || status === "info" ? "open" : ""}>
            <summary>${badge(status, status === "info" ? "Đang chạy" : undefined)}<h4>${esc(title)}</h4><span class="dc-count">${esc(extra || "")}</span></summary>
            <div class="dc-group-body">${body}</div></details>`;
    }

    const worst = statuses => statuses.includes("fail") ? "fail" : statuses.includes("warn") ? "warn" : "pass";
    const keep = s => state.filter === "all" || isProblem(s);
    const countText = statuses => {
        const f = statuses.filter(s => s === "fail").length, w = statuses.filter(s => s === "warn").length;
        return [f ? `${f} lỗi` : "", w ? `${w} cần xem` : "", `${statuses.length} mục`].filter(Boolean).join(" · ");
    };

    function basisGroup() {
        const r = state.report;
        const refs = r.references.filter(ref => ref.in_basis);
        const statuses = [...r.basis.map(b => b.status), ...refs.map(ref => ref.status)];
        const items = [...r.basis.filter(b => keep(b.status)).map(basisLine), ...refs.filter(ref => keep(ref.status)).map(refCard)];
        const empty = r.basis.length || refs.length ? "Khối căn cứ và các văn bản làm căn cứ không có vấn đề." : "Dự thảo không có dòng “Căn cứ…”.";
        return group("basis", "Căn cứ", items, statuses.length ? worst(statuses) : "pass", statuses.length ? countText(statuses) : "", empty);
    }

    function relatedItem(doc) {
        return `<li class="dc-issue">${badge("info", "Gợi ý")}<span class="dc-label">${esc(doc.so_ky_hieu)}</span><span class="dc-where">ngày ${esc(doc.ngay_ban_hanh)}${doc.co_quan ? " · " + esc(doc.co_quan) : ""}</span>
            <span class="dc-msg">${esc(doc.trich_yeu || "")}</span>${link(doc.url, "Mở")}</li>`;
    }

    function referenceGroup() {
        const r = state.report;
        const refs = r.references.filter(ref => !ref.in_basis);
        const internal = r.logic.filter(l => l.group === "internal");
        const statuses = [...refs.map(ref => ref.status), ...internal.map(l => l.status)];
        const items = [...internal.filter(l => keep(l.status)).map(basisLine), ...refs.filter(ref => keep(ref.status)).map(refCard)];
        if (state.related && state.related.length) {
            items.push(`<div class="cc-related"><div class="dc-hint">Văn bản đến trong kho có nội dung gần với dự thảo nhưng chưa được dẫn chiếu — kiểm tra xem dự thảo có đang trả lời/thực hiện văn bản nào không:</div>
                <ul class="dc-issues">${state.related.map(relatedItem).join("")}</ul></div>`);
        }
        return group("refs", "Dẫn chiếu trong nội dung", items, statuses.length ? worst(statuses) : "pass",
            statuses.length ? countText(statuses) : "", "Không có văn bản được dẫn trong nội dung có vấn đề.");
    }

    function logicGroup() {
        const items = state.report.logic.filter(l => l.group !== "internal");
        const statuses = items.map(l => l.status);
        return group("logic", "Logic thời gian & số liệu", items.filter(l => keep(l.status)).map(basisLine),
            statuses.length ? worst(statuses) : "pass", statuses.length ? countText(statuses) : "",
            "Không phát hiện ngày không hợp lệ, sai thứ, thời hạn đã qua hay số viết bằng chữ sai.");
    }

    function aiIssue(i) {
        const source = i.source ? `<div class="cc-evidence"><span class="dc-where">Theo ${esc(i.source.label)}:</span> <q>${esc(i.evidence)}</q> ${link(i.source.url, "Mở")}</div>` : "";
        return `<li class="dc-issue ${i.severity} cc-ai">${badge(i.severity, i.severity === "fail" ? "Sai so với văn bản dẫn" : "Cần xem xét")}
            <span class="dc-pno">Đoạn ${i.index}</span><span class="tag">${esc(i.category_label)}</span>
            <span class="dc-snippet">${esc(i.quote)}</span>
            <span class="dc-msg">${esc(i.problem || "")}${i.suggestion ? ` <b>Gợi ý:</b> ${esc(i.suggestion)}` : ""}</span>${source}</li>`;
    }

    function aiGroup() {
        const ai = state.ai;
        if (!ai) return "";
        if (ai.status === "running") {
            return group("ai", "Thẩm định AI", [`<div class="dc-step run"><span class="spin dark"></span><span>${esc(ai.message)}</span></div>`], "info");
        }
        if (ai.status !== "done") {
            const st = ai.status === "error" ? "warn" : "off";
            return group("ai", "Thẩm định AI", [`<div class="dc-note ${st === "warn" ? "warn" : "info"}">${ICON[st === "warn" ? "warn" : "info"]}<span>${esc(ai.message || "Đã bỏ qua.")}</span></div>`], st);
        }
        const issues = ai.issues || [];
        const grounded = (ai.grounded || []).map(g => g.so_ky_hieu).join(", ");
        const meta = `<div class="dc-hint">Mô hình ${esc(ai.model)} · ${nf(ai.paragraphs)} đoạn, ${nf(ai.chunks)} phần`
            + (grounded ? ` · đối chiếu nguyên văn: ${esc(grounded)}` : "")
            + (ai.verified && ai.verified.length ? ` · ${nf(ai.verified.length)} nội dung khớp văn bản được dẫn` : "")
            + (ai.dropped ? ` · đã lọc ${nf(ai.dropped)} gợi ý không đáng tin` : "")
            + `. Mục “Sai so với văn bản dẫn” có trích dẫn nguyên văn đã được kiểm lại; các mục khác là gợi ý của AI, chuyên viên quyết định.</div>`;
        const items = issues.length ? [`<ul class="dc-issues">${issues.map(aiIssue).join("")}</ul>`] : [];
        const statuses = issues.map(i => i.severity);
        const errors = ai.errors && ai.errors.length ? `<div class="dc-note warn">${ICON.warn}<span>Một phần chưa thẩm định được: ${esc(ai.errors[0])}</span></div>` : "";
        return group("ai", "Thẩm định AI", [meta + errors, ...items], statuses.length ? worst(statuses) : "pass",
            statuses.length ? countText(statuses) : "", "AI không phát hiện vấn đề nội dung.");
    }

    function render() {
        const out = $("#ccOut");
        const closed = new Set([...out.querySelectorAll(".dc-results > details:not([open])")].map(d => d.dataset.group));
        out.innerHTML = summaryCard() + toolbar() + `<div class="dc-results">${basisGroup()}${referenceGroup()}${logicGroup()}${aiGroup()}</div>`;
        out.querySelectorAll(".dc-results > details").forEach(d => { if (closed.has(d.dataset.group)) d.open = false; });
        window.UI.reveal(out);
    }

    $("#ccOut").addEventListener("click", e => {
        const filter = e.target.closest("[data-filter]");
        if (filter) { state.filter = filter.dataset.filter; render(); return; }
        if (e.target.closest("[data-toggle-all]")) {
            const all = [...$("#ccOut").querySelectorAll(".dc-results details")];
            const openAll = all.some(d => !d.open);
            all.forEach(d => { d.open = openAll; });
        }
    });

    loadInfo();
})();
