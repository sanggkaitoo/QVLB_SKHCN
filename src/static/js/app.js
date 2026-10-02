const API = ""; // cùng origin khi FastAPI phục vụ file này
            let DEMO = false;
            const $ = s => document.querySelector(s);
            const esc = s => (s == null ? "" : s)
                .toString()
                .replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
            /* Thông báo lỗi từ API: {error} của ứng dụng hoặc {detail} của FastAPI (413, 415, 503...). */
            const apiError = (r, d) => (d && (d.error || (typeof d.detail === "string" ? d.detail : ""))) ||
                (r.status === 503 ? "Hệ thống đang bận, vui lòng thử lại sau." : "Máy chủ trả lỗi " + r.status + ".");
            const safeHtml = html => (window.DOMPurify ? DOMPurify.sanitize(html) : esc(html));

            /* ---------- light / dark mode toggle ---------- */
            function setTheme(theme) {
                const isLight = theme === "light";
                document.documentElement.setAttribute("data-theme", isLight ? "light" : "dark");
                localStorage.setItem("qlvb-theme", isLight ? "light" : "dark");
                const icon = document.getElementById("themeIcon");
                const text = document.getElementById("themeText");
                if (icon) icon.textContent = isLight ? "☾" : "☀";
                if (text) text.textContent = isLight ? "Giao diện Tối" : "Giao diện Sáng";
            }
            function toggleTheme() {
                const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
                setTheme(next);
            }
            setTheme(localStorage.getItem("qlvb-theme") || "light");
            let deferredInstallPrompt = null;
            const installPwaBtn = document.getElementById("installPwaBtn");
            function isStandalonePwa() {
                return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
            }
            function isIosSafari() {
                const ua = window.navigator.userAgent;
                const isIos = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
                const isSafari = /^((?!CriOS|FxiOS|EdgiOS|OPiOS).)*Safari/i.test(ua);
                return isIos && isSafari;
            }
            function showIosInstallGuide() {
                const guide = document.getElementById("iosInstallGuide");
                if (guide) guide.classList.add("show");
            }
            document.getElementById("iosGuideClose")?.addEventListener("click", () => {
                document.getElementById("iosInstallGuide")?.classList.remove("show");
            });
            function updateInstallPwaButton() {
                if (!installPwaBtn) return;
                if (isStandalonePwa()) {
                    installPwaBtn.hidden = true;
                    document.getElementById("iosInstallGuide")?.classList.remove("show");
                    return;
                }
                installPwaBtn.disabled = false;
                installPwaBtn.title = isIosSafari() ? "Bấm để xem cách cài trên iPhone" : (deferredInstallPrompt ? "Cài ứng dụng DocNexus" : "Cài từ menu trình duyệt");
            }
            window.addEventListener("beforeinstallprompt", (event) => {
                event.preventDefault();
                deferredInstallPrompt = event;
                updateInstallPwaButton();
            });
            window.addEventListener("appinstalled", () => {
                deferredInstallPrompt = null;
                updateInstallPwaButton();
            });
            if (installPwaBtn) {
                installPwaBtn.addEventListener("click", async () => {
                    if (!deferredInstallPrompt) { if (isIosSafari()) showIosInstallGuide(); else alert("Bạn có thể cài DocNexus từ menu của trình duyệt."); return; }
                    deferredInstallPrompt.prompt();
                    await deferredInstallPrompt.userChoice;
                    deferredInstallPrompt = null;
                    updateInstallPwaButton();
                });
                updateInstallPwaButton();
            }

            /* ---------- health / telemetry ---------- */
            async function health() {
                try {
                    const r = await fetch(API + "/api/health");
                    const j = await r.json();
                    if (!r.ok || !j.ok) 
                        throw 0;
                    setTel(true);
                } catch {
                    DEMO = true;
                    $("#demo")
                        .classList
                        .add("on");
                    setTel(false);
                }
            }
            function setTel(ok) {
                const v = ok
                    ? ["sẵn sàng", "sẵn sàng", "sẵn sàng"]
                    : ["xem thử", "xem thử", "xem thử"];
                ["q", "p", "l"].forEach((k, i) => {
                    $("#s-" + k).textContent = v[i];
                    $("#d-" + k)
                        .classList
                        .toggle("warn", !ok);
                });
            }

            /* ---------- tabs ---------- */
            const HEAD = {
                overview: ["Tổng quan", "Kho văn bản trong một cái nhìn", "Số liệu trực tiếp từ kho dữ liệu."],
                search: [
                    "Tra cứu", "Tìm trong toàn bộ kho văn bản", "Tìm theo từ khóa hoặc ý nghĩa trên văn bản đi và đến. Mọi câu trả lời đều dẫn nguồn số ký hiệu."
                ],
                agg: [
                    "Tổng hợp", "Cộng số liệu trên nhiều văn bản (thử nghiệm)", "Đặt câu hỏi đếm hoặc cộng; hệ thống trích số liệu có trích dẫn từ từng văn bản, tính bằng code và tách riêng dữ kiện cần cán bộ xác nhận."
                ],
                check: [
                    "Kiểm tra", "Rà soát dự thảo trước khi trình ký", "Kiểm tra thể thức theo Nghị định 30/2020 hoặc quy định của đơn vị, chính tả bằng code và AI; đối chiếu căn cứ pháp lý với kho văn bản."
                ],
                audio: ["Tiện ích", "Gỡ băng & tổng hợp ghi âm", "Chuyển file ghi âm thành văn bản rồi tóm tắt thành ghi chú mạch lạc, bằng các mô hình AI do quản trị viên chọn cho từng bước."],
                ocr: ["Tiện ích", "Nhận dạng văn bản (OCR)", "Trích xuất nội dung từ PDF scan hoặc ảnh chụp thành văn bản Markdown."]
            };
            /* Quyền cần cho từng tab (tab tổng quan công khai). */
            const TAB_PERM = {search: "tools.search", agg: "tools.aggregate", check: "tools.check", audio: "tools.audio", ocr: "tools.ocr"};

            function showTab(k, options = {}) {
                if (!HEAD[k]) k = "overview";
                document.querySelectorAll(".tab").forEach(x => {
                    const on = x.dataset.tab === k;
                    x.classList.toggle("active", on);
                    x.setAttribute("aria-current", on ? "page" : "false");
                });
                document.querySelectorAll(".panel").forEach(p => p.classList.toggle("active", p.id === "p-" + k));
                document.body.dataset.tab = k;
                const [e, h, p] = HEAD[k];
                $("#hd").innerHTML = `<div class="eyebrow">${e}</div><h2>${h}</h2><p>${p}</p>`;
                if (options.push !== false && location.hash.slice(1) !== k) history.pushState(null, "", k === "overview" ? location.pathname : "#" + k);
                if (options.scroll !== false) window.scrollTo({top: 0, behavior: "smooth"});
                window.dispatchEvent(new CustomEvent("tab:change", {detail: k}));
            }
            document.querySelectorAll(".tab").forEach(t => t.onclick = () => showTab(t.dataset.tab));
            document.querySelectorAll("[data-go]").forEach(el => el.addEventListener("click", event => {
                event.preventDefault();
                showTab(el.dataset.go);
                if (el.dataset.go === "search") setTimeout(() => $("#q-search").focus(), 250);
            }));
            window.addEventListener("popstate", () => showTab(location.hash.slice(1) || "overview", {push: false, scroll: false}));

            /* Ô tìm kiếm ở hero và câu hỏi gợi ý: chuyển sang tab tra cứu rồi chạy luôn. */
            function searchFromHero(q) {
                if (!q) { showTab("search"); $("#q-search").focus(); return; }
                $("#q-search").value = q;
                showTab("search");
                runSearch();
            }
            $("#heroSearch")?.addEventListener("submit", event => { event.preventDefault(); searchFromHero($("#heroQ").value.trim()); });
            document.querySelectorAll(".chip.prompt").forEach(chip => chip.onclick = () => searchFromHero(chip.textContent.trim()));

            /* ---------- tài khoản & phân quyền ---------- */
            let AUTH = {user: null};
            function allowed(k) {
                const perm = TAB_PERM[k];
                if (!perm) return true;
                if (UI.auth.can(perm)) return true;
                return k === "search" && !AUTH.user && AUTH.anonymous_search;
            }
            function applyAuth(state) {
                AUTH = state || {user: null};
                UI.renderAccount($("#account"), AUTH);
                Object.keys(TAB_PERM).forEach(k => {
                    const ok = allowed(k);
                    document.querySelector(`.tab[data-tab="${k}"]`)?.classList.toggle("locked", !ok);
                    const panel = $("#p-" + k);
                    let gate = panel.querySelector(":scope > .gate");
                    if (ok) { gate?.remove(); return; }
                    if (!gate) { gate = document.createElement("div"); gate.className = "gate"; panel.prepend(gate); }
                    gate.innerHTML = AUTH.user
                        ? `<svg class="ico"><use href="#i-lock"/></svg><div><strong>Tài khoản của bạn chưa được cấp quyền dùng chức năng này.</strong><br>Liên hệ quản trị viên nếu bạn cần sử dụng.</div>`
                        : `<svg class="ico"><use href="#i-lock"/></svg><div><strong>Vui lòng đăng nhập để sử dụng chức năng này.</strong><br>Tài khoản do quản trị viên cấp.</div><a class="btn btn-primary btn-sm" href="${UI.esc(UI.auth.loginUrl())}">Đăng nhập</a>`;
                });
                loadModelChoices();
            }
            window.addEventListener("auth:change", event => applyAuth(event.detail));
            window.addEventListener("auth:required", () => {
                UI.toast("Phiên đăng nhập đã hết hoặc bạn chưa đăng nhập.", "warn", {action: {label: "Đăng nhập", run: () => { location.href = UI.auth.loginUrl(); }}});
            });

            async function loadModelChoices() {
                const pick = $("#modelPick"), sel = $("#modelSel");
                if (!pick || !AUTH.user) { if (pick) pick.hidden = true; return; }
                try {
                    const data = await (await fetch("/api/models/choices", {cache: "no-store"})).json();
                    const items = data.items || [];
                    if (!data.can_choose || !items.length) { pick.hidden = true; return; }
                    let saved = null;
                    try { saved = localStorage.getItem("qlvb-model"); } catch { /* bỏ qua */ }
                    sel.innerHTML = items.map(m => `<option value="${UI.esc(m.spec)}">${UI.esc(m.name)}${m.question_vnd != null ? ` · ~${m.question_vnd.toLocaleString("vi-VN")}đ/câu` : ""}</option>`).join("");
                    const initial = items.some(m => m.spec === saved) ? saved : (items.some(m => m.spec === data.default) ? data.default : items[0].spec);
                    sel.value = initial;
                    sel.onchange = () => { try { localStorage.setItem("qlvb-model", sel.value); } catch { /* bỏ qua */ } };
                    pick.hidden = false;
                } catch {
                    pick.hidden = true;
                }
            }

            /* ---------- SEARCH (stream + demo) ---------- */
            const filters = {
                huong: [],
                loai_vb: [],
                linh_vuc: []
            };

            document
                .querySelectorAll(".chip[data-f]")
                .forEach(c => c.onclick = () => {
                    const f = c.dataset.f,
                        v = c.dataset.v;

                    c
                        .classList
                        .toggle("on");
                    c.setAttribute("aria-pressed", c.classList.contains("on") ? "true" : "false");
                    if (c.classList.contains("on")) {
                        if (!filters[f].includes(v)) 
                            filters[f].push(v);
                        }
                    else {
                        filters[f] = filters[f].filter(item => item !== v);
                    }
                });

            // Xử lý Select Dropdown kết hợp với mảng
            let lastLoaiSelect = "";
            const moreLoai = $("#more-loai");
            if (moreLoai) 
                moreLoai.onchange = () => {
                    const v = moreLoai.value;

                    // Rút lại lựa chọn cũ khỏi mảng (nếu có)
                    if (lastLoaiSelect) {
                        filters.loai_vb = filters
                            .loai_vb
                            .filter(item => item !== lastLoaiSelect);
                    }

                    if (v) {
                        filters
                            .loai_vb
                            .push(v);
                        moreLoai
                            .classList
                            .add("on");
                        lastLoaiSelect = v;
                    } else {
                        moreLoai
                            .classList
                            .remove("on");
                        lastLoaiSelect = "";
                    }
                };

            $("#b-search").onclick = runSearch;
            $("#q-search").onkeydown = e => {
                if (e.key === "Enter") 
                    runSearch()
            };

            async function runSearch() {
                const q = $("#q-search").value.trim();
                if (!q) return;
                const ans = $("#answer"), src = $("#sources"), btn = $("#b-search");
                btn.disabled = true;
                btn.innerHTML = '<span class="spin"></span>Đang tra…';
                ans.innerHTML = '<div class="stage" id="stage">Đang gửi câu hỏi…</div><div class="shimmer" style="width:90%"></div>' +
                    '<div class="shimmer" style="width:80%"></div><div class="shimmer" style="width:60%"></div>';
                src.innerHTML = '';
                lastSources = {};

                try {
                    if (DEMO) {
                        await demoSearch(q, ans, src);
                    } else {
                        const url = new URL(API + "/api/search_stream", location.origin);
                        url.searchParams.set("q", q);
                        // Nhiều lựa chọn gửi dạng "a,b"; máy chủ kiểm tra hợp lệ.
                        Object.keys(filters).forEach(k => {
                            if (filters[k].length > 0) url.searchParams.set(k, filters[k].join(","));
                        });
                        if (!$("#modelPick").hidden && $("#modelSel").value) url.searchParams.set("model", $("#modelSel").value);
                        const r = await fetch(url, { headers: { Accept: "text/event-stream" } });
                        if (r.status === 503) {
                            ans.innerHTML = '<div class="ph">Hệ thống đang bận xử lý câu hỏi khác. Vui lòng thử lại sau vài giây.</div>';
                        } else if ([401, 403, 429].includes(r.status)) {
                            const d = await r.json().catch(() => ({}));
                            ans.innerHTML = `<div class="ph">${esc(apiError(r, d))}</div>`;
                        } else if (!r.ok || !r.body) {
                            throw new Error("HTTP " + r.status);
                        } else {
                            await consumeSearchStream(r.body.getReader(), ans, src);
                        }
                    }
                } catch (e) {
                    ans.innerHTML = '<div class="ph">Không tra cứu được. Kiểm tra kết nối tới máy chủ rồi thử lại.</div>';
                }
                btn.disabled = false;
                btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">' +
                        '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>Tra cứu';
            }

            /* Đọc Server-Sent Events: status, sources, token (bản nháp), answer (bản đã kiểm chứng), done, error. */
            async function readSse(reader, onEvent) {
                const dec = new TextDecoder();
                let buf = "";
                while (true) {
                    const {done, value} = await reader.read();
                    if (done) break;
                    buf += dec.decode(value, {stream: true});
                    let idx;
                    while ((idx = buf.indexOf("\n\n")) >= 0) {
                        const block = buf.slice(0, idx);
                        buf = buf.slice(idx + 2);
                        const data = block.split("\n").filter(l => l.startsWith("data:")).map(l => l.slice(5).trim()).join("\n");
                        if (!data) continue; // keepalive
                        try { onEvent(JSON.parse(data)); } catch (err) { /* bỏ qua khối lỗi */ }
                    }
                }
            }

            async function consumeSearchStream(reader, ans, src) {
                let draft = "", final = null, stage = "Đang xử lý…", pending = false;
                const paint = () => {
                    pending = false;
                    if (final) return;
                    if (!draft) {
                        ans.innerHTML = `<div class="stage">${esc(stage)}</div><div class="shimmer" style="width:85%"></div><div class="shimmer" style="width:70%"></div>`;
                        return;
                    }
                    ans.innerHTML = `<div class="stage">${esc(stage)}</div><div class="draft">${formatCitations(draft)}<span class="caret"></span></div>`;
                };
                const schedule = () => { if (!pending) { pending = true; requestAnimationFrame(paint); } };
                await readSse(reader, ev => {
                    if (ev.type === "status") { stage = ev.message || stage; schedule(); }
                    else if (ev.type === "sources") { renderSources(ev.sources || [], src); }
                    else if (ev.type === "token") { draft += ev.text || ""; schedule(); }
                    else if (ev.type === "answer") {
                        final = ev;
                        ans.innerHTML = verificationBadge(ev) + formatCitations(ev.answer || "");
                    } else if (ev.type === "error") {
                        final = ev;
                        ans.innerHTML = `<div class="ph">${esc(ev.message || "Hệ thống gặp lỗi khi xử lý yêu cầu.")}</div>`;
                    }
                });
                if (!final) {
                    ans.innerHTML = draft
                        ? '<div class="stage warn">Kết nối bị gián đoạn trước khi kiểm chứng xong; nội dung dưới đây chưa được kiểm chứng.</div>' + formatCitations(draft)
                        : '<div class="ph">Kết nối bị gián đoạn. Vui lòng thử lại.</div>';
                }
            }

            function verificationBadge(ev) {
                const conf = {cao: "cao", trung_binh: "trung bình", thap: "thấp"}[ev.confidence] || ev.confidence || "";
                const cls = ev.verified ? "ok" : "warn";
                const label = ev.verified ? "Đã kiểm chứng với nguồn" : "Chưa kiểm chứng được";
                return `<div class="verify ${cls}">${label}${conf ? " · độ tin cậy " + esc(conf) : ""}</div>`;
            }

            let lastSources = {};
            function renderSources(list, el) {
                lastSources = {};
                if (!list || !list.length) {
                    el.innerHTML = '<div class="empty" style="padding:30px 10px">Không có nguồn phù hợp.</div>';
                    return;
                }
                el.innerHTML = list
                    .map(s => {
                        const m = s.metadata || {},
                            sc = Math.max(0, Math.min(100, Math.round((s.score == null ? 0.7 : s.score) * 100)));
                        if (m.evidence_id) lastSources[m.evidence_id] = m;
                        const file = m.file_role && m.file_role !== "chinh"
                            ? `<div class="file">${esc(m.file_role_label || "Tệp đính kèm")}: ${esc(m.file_name || "")}</div>` : "";
                        const link = m.source_url && /^https?:\/\//.test(m.source_url)
                            ? ` <a class="open" href="${esc(m.source_url)}" target="_blank" rel="noopener noreferrer">Mở</a>` : "";
                        return `<div class="src" id="src-${esc(m.evidence_id || "")}"><div class="meta">${m.evidence_id ? `<span class="eid">${esc(m.evidence_id)}</span>` : ""}<span class="tag">${esc(m.so_ky_hieu || "?")}</span>
      <span class="score">${esc(String(m.ngay_ban_hanh || "").slice(0, 10))}${link}</span></div>
      ${file}<div class="bar"><i style="width:${sc}%"></i></div>
      <div class="txt">${esc((s.text || "").slice(0, 320))}</div></div>`;
                    })
                    .join("");
            }

            /* Chuyển trích dẫn [E1] thành biểu tượng nguồn (kèm số ký hiệu khi biết). Markdown được làm sạch trước khi hiển thị. */
            function formatCitations(mdText) {
                if (!mdText) return "";
                const html = safeHtml(marked.parse(mdText));
                return html.replace(/<code>\[([^\]<]+)\]<\/code>|\[([^\]<]{1,250})\]/g, (match, g1, g2) => {
                    const info = g1 || g2;
                    const parts = info.split(/,\s*|;\s*|\s+và\s+/i).map(x => x.trim()).filter(Boolean);
                    return parts.map(part => {
                        const m = lastSources[part];
                        const label = m ? `${part} · ${m.so_ky_hieu || "?"}${m.file_role && m.file_role !== "chinh" ? " · " + (m.file_name || "tệp đính kèm") : ""}` : part;
                        return `<span class="cite-icon" data-info="Nguồn: ${esc(label)}" data-eid="${esc(part)}">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                <circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>
                            </svg>
                        </span>`;
                    }).join("");
                });
            }

            document.addEventListener("click", e => {
                const icon = e.target.closest && e.target.closest(".cite-icon[data-eid]");
                if (!icon) return;
                const card = document.getElementById("src-" + icon.dataset.eid);
                if (card) { card.scrollIntoView({behavior: "smooth", block: "nearest"}); card.classList.add("flash"); setTimeout(() => card.classList.remove("flash"), 1200); }
            });

            /* ---------- AGGREGATE v2 (stream tiến độ + minh chứng) ---------- */
            $("#b-agg").onclick = runAgg;
            $("#q-agg").onkeydown = e => {
                if (e.key === "Enter")
                    runAgg()
            };
            async function runAgg() {
                const q = $("#q-agg").value.trim();
                if (!q) return;
                const out = $("#agg-out"), btn = $("#b-agg");
                btn.disabled = true;
                btn.innerHTML = '<span class="spin"></span>Đang quét…';
                out.innerHTML = '<div class="stage" id="agg-stage">Đang lập kế hoạch tổng hợp…</div><div class="agg-total" style="margin-top:14px"><div class="shimmer" style="width:120px;height:50px"></div></div>';
                try {
                    if (DEMO) {
                        renderAgg(demoAgg(q), out);
                    } else {
                        const r = await fetch(API + "/api/aggregate_stream?q=" + encodeURIComponent(q), { headers: { Accept: "text/event-stream" } });
                        if (r.status === 503) {
                            out.innerHTML = '<div class="empty">Hệ thống đang bận. Vui lòng thử lại sau vài giây.</div>';
                        } else if ([401, 403, 429].includes(r.status)) {
                            const d = await r.json().catch(() => ({}));
                            out.innerHTML = `<div class="empty">${esc(apiError(r, d))}</div>`;
                        } else if (!r.ok || !r.body) {
                            throw new Error("HTTP " + r.status);
                        } else {
                            let result = null;
                            await readSse(r.body.getReader(), ev => {
                                if (ev.type === "status") {
                                    const st = $("#agg-stage");
                                    if (st) st.textContent = ev.message + (ev.total ? ` (${ev.done || 0}/${ev.total})` : "");
                                } else if (ev.type === "aggregation_result") {
                                    result = ev.data;
                                } else if (ev.type === "error") {
                                    out.innerHTML = `<div class="empty">${esc(ev.message || "Không tổng hợp được.")}</div>`;
                                    result = false;
                                }
                            });
                            if (result) renderAgg(result, out);
                            else if (result === null) out.innerHTML = '<div class="empty">Kết nối bị gián đoạn trước khi có kết quả.</div>';
                        }
                    }
                } catch {
                    out.innerHTML = '<div class="empty">Không tổng hợp được. Kiểm tra kết nối máy chủ.</div>';
                }
                btn.disabled = false;
                btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">' +
                        '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>Tổng hợp';
            }

            const OP_LABEL = {sum: "Phép cộng", count: "Phép đếm dữ kiện", count_documents: "Đếm văn bản", avg: "Trung bình",
                              min: "Giá trị nhỏ nhất", max: "Giá trị lớn nhất", distinct_count: "Đếm đối tượng khác nhau"};
            const STATUS_LABEL = {ke_hoach: "kế hoạch", thuc_hien: "thực hiện", luy_ke: "lũy kế", khong_ro: "không rõ"};
            const REVIEW_LABEL = {
                trich_dan_khong_khop_nguon: "Trích dẫn không khớp nguồn", so_khong_co_trong_trich_dan: "Số không có trong trích dẫn",
                khong_ro_doan_nguon: "Không rõ đoạn nguồn", khong_doc_duoc_so: "Không đọc được số", do_tin_cay_thap: "Độ tin cậy thấp",
                dau_phan_cach_mo_ho: "Dấu phân cách mơ hồ", trung_lap: "Trùng lặp", trung_lap_giua_van_ban: "Trùng giữa các văn bản",
                chi_tiet_da_co_dong_tong: "Dòng chi tiết (đã dùng dòng tổng)", khac_don_vi: "Khác đơn vị",
                gia_tri_khong_hop_le: "Giá trị không phải số hợp lệ", trich_dan_thieu_ngu_canh: "Trích dẫn thiếu ngữ cảnh"
            };
            const fmtNum = v => v == null ? "—" : Number(v).toLocaleString("vi-VN", {maximumFractionDigits: 2});

            function factRows(list, withReason) {
                return list.map(r => `<tr>
    <td><span class="ky">${esc(r.so_ky_hieu || "?")}</span><div class="sub">${esc(String(r.ngay_ban_hanh || "").slice(0, 10))}</div></td>
    <td>${esc(r.label || "")}${r.file_name && r.file_role === "dinh_kem" ? `<div class="sub">Tệp: ${esc(r.file_name)}</div>` : ""}</td>
    <td><span class="val">${esc(fmtNum(r.value))}</span> <span class="unit">${esc(r.unit || "")}</span>${r.status && r.status !== "khong_ro" ? `<div class="sub">${esc(STATUS_LABEL[r.status] || r.status)}</div>` : ""}</td>
    <td class="ev">${esc(r.quote || "")}${withReason ? `<div class="reason">${esc(reviewLabel(r.review_reason))}</div>` : ""}</td></tr>`).join("");
            }
            function reviewLabel(reason) {
                if (!reason) return "";
                if (reason.startsWith("khac_trang_thai_")) return "Khác trạng thái (" + (STATUS_LABEL[reason.slice(16)] || reason.slice(16)) + ")";
                return REVIEW_LABEL[reason] || reason;
            }

            function renderAgg(d, out) {
                const included = d.included || [], review = d.review || [], warnings = d.warnings || [];
                const groups = (d.groups || []).map(g => `<tr><td>${esc(g.key)}</td><td class="num">${esc(fmtNum(g.value))} ${esc(d.unit || "")}</td><td class="num">${g.n_docs}</td></tr>`).join("");
                out.innerHTML = `
    <div class="agg-total">
      <div class="agg-num" id="cu" data-t="${Number(d.total) || 0}">0</div>
      <div class="agg-meta"><div class="m1">${esc(d.metric || d.question || "")}${d.unit ? ` <span class="unit">(${esc(d.unit)})</span>` : ""}</div>
        <div class="m2">${d.operation === "count_documents"
                    ? esc(d.scope ? d.scope.charAt(0).toUpperCase() + d.scope.slice(1) : "Đếm văn bản")
                    : esc(OP_LABEL[d.operation] || "Tổng hợp") + " trên " + (d.n_docs_with_facts || 0) + " văn bản có số liệu"}</div></div>
      <div class="agg-stats">
        <div><div class="n">${d.n_docs_in_scope || 0}</div><div class="l">Trong phạm vi</div></div>
        <div><div class="n">${d.n_docs_scanned || 0}</div><div class="l">Đã quét</div></div>
        <div><div class="n">${d.n_docs_with_facts || 0}</div><div class="l">${d.operation === "count_documents" ? "Khớp" : "Có số liệu"}</div></div>
      </div>
    </div>
    ${warnings.map(w => `<div class="agg-warn">⚠ ${esc(w)}</div>`).join("")}
    ${groups ? `<div class="section-h">Theo nhóm</div><table class="evid"><thead><tr><th>Nhóm</th><th>Giá trị</th><th>Số văn bản</th></tr></thead><tbody>${groups}</tbody></table>` : ""}
    <div class="section-h">${d.operation === "count_documents" ? "Danh sách văn bản" : "Minh chứng được tính"} (${included.length})</div>
    <table class="evid"><thead><tr><th>Số ký hiệu</th><th>Dữ kiện</th><th>Giá trị</th><th>Trích dẫn nguyên văn</th></tr></thead>
    <tbody>${factRows(included, false) || '<tr><td colspan="4" style="color:var(--faint)">Không văn bản nào nêu số liệu này.</td></tr>'}</tbody></table>
    ${review.length ? `<details class="agg-review"><summary>${review.length} dữ kiện cần cán bộ xác nhận (chưa cộng vào kết quả)</summary>
    <table class="evid"><thead><tr><th>Số ký hiệu</th><th>Dữ kiện</th><th>Giá trị</th><th>Trích dẫn / lý do</th></tr></thead><tbody>${factRows(review, true)}</tbody></table></details>` : ""}
    <p class="agg-note">Số được tính bằng code từ trích dẫn nguyên văn; cần kiểm tra đơn vị, kỳ báo cáo và phạm vi trước khi dùng cho báo cáo chính thức.</p>`;
                countUp($("#cu"));
            }
            function countUp(el) {
                const t = parseFloat(el.dataset.t) || 0;
                const dur = 750;
                const st = performance.now();
                const fmt = n => Number.isInteger(t)
                    ? Math
                        .round(n)
                        .toLocaleString("vi")
                    : n.toFixed(1);
                (function f(now) {
                    const p = Math.min(1, (now - st) / dur);
                    const e = 1 - Math.pow(1 - p, 3);
                    el.textContent = fmt(t * e);
                    if (p < 1) 
                        requestAnimationFrame(f);
                    }
                )(st);
            }

            /* ---------- CHECK ---------- */
            // Hai tab con: "Thể thức & chính tả" (docx-check.js) và "Nội dung & căn cứ" (content-check.js).
            document
                .querySelectorAll(".subtab")
                .forEach(s => s.onclick = () => {
                    document.querySelectorAll(".subtab").forEach(x => {
                        x.classList.toggle("active", x === s);
                        x.setAttribute("aria-selected", x === s ? "true" : "false");
                    });
                    $("#check-format").hidden = s.dataset.sub !== "format";
                    $("#check-content").hidden = s.dataset.sub !== "content";
                });

            /* ---------- AUDIO ---------- */
            const dropAudio = $("#drop-audio"), inpAudio = $("#inp-audio");
            dropAudio.onclick = () => inpAudio.click();
            dropAudio.ondragover = e => { e.preventDefault(); dropAudio.classList.add("over"); };
            dropAudio.ondragleave = () => dropAudio.classList.remove("over");
            dropAudio.ondrop = e => {
                e.preventDefault(); dropAudio.classList.remove("over");
                if (e.dataTransfer.files[0]) handleAudioFile(e.dataTransfer.files[0]);
            };
            inpAudio.onchange = () => { if (inpAudio.files[0]) handleAudioFile(inpAudio.files[0]); };

            async function handleAudioFile(f) {
                $("#audio-file").textContent = "🎵 " + f.name;
                const ta = $("#ta-transcript");
                const btn = $("#b-summarize");
                const loading = $("#audio-loading"); // Trỏ đến thẻ loading

                // 1. Bật spinner, khóa textarea không cho bấm lung tung
                loading.style.display = "flex";
                ta.value = "";
                ta.disabled = true;
                btn.disabled = true;
                $("#audio-out").innerHTML = '<div class="empty" style="padding: 20px 0;">Đang chờ văn bản gốc...</div>';

                const fd = new FormData();
                fd.append("file", f);

                try {
                    if (DEMO) {
                        await new Promise(r => setTimeout(r, 1500));
                        ta.value = "ờm... kính thưa các đồng chí thì... hôm nay chúng ta họp về vấn đề là giải ngân vốn đầu tư công. ờ... tiến độ hiện nay đang rất là chậm...";
                    } else {
                        const r = await fetch(API + "/api/audio/transcribe", { method: "POST", body: fd });
                        const d = await r.json().catch(() => ({}));
                        if (!r.ok || d.error) throw new Error(apiError(r, d));

                        // Gán text trả về, có dự phòng chuỗi cảnh báo nếu text bị undefined
                        ta.value = d.text || "⚠️ File âm thanh trống hoặc không nhận diện được giọng nói.";
                    }
                    btn.disabled = false;
                } catch (e) {
                    ta.value = "Lỗi khi convert audio: " + e.message;
                } finally {
                    // 2. Xử lý xong thì tắt spinner, mở khóa textarea lại
                    loading.style.display = "none";
                    ta.disabled = false;
                }
            }

            $("#b-summarize").onclick = async () => {
                const text = $("#ta-transcript").value.trim();
                if (!text) return;

                const btn = $("#b-summarize");
                const out = $("#audio-out");
                btn.disabled = true;
                btn.innerHTML = '<span class="spin"></span>Đang tổng hợp...';
                out.innerHTML = '<div class="shimmer" style="width:90%"></div><div class="shimmer" style="width:75%"></div><div class="shimmer" style="width:85%"></div>';

                try {
                    if (DEMO) {
                        await new Promise(r => setTimeout(r, 1500));
                        out.innerHTML = formatCitations("- **Vấn đề chính:** Họp bàn về giải ngân vốn đầu tư công.\n- **Thực trạng:** Tiến độ hiện đang rất chậm.");
                    } else {
                        const r = await fetch(API + "/api/audio/summarize", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ text: text })
                        });
                        const d = await r.json().catch(() => ({}));
                        if (!r.ok || d.error) throw new Error(apiError(r, d));

                        // Ép kiểu: d.summary hoặc chuỗi báo lỗi, tránh ném Null vào formatCitations
                        out.innerHTML = formatCitations(d.summary || "*Không có nội dung tóm tắt được trả về từ máy chủ.*");
                    }
                } catch (e) {
                    out.innerHTML = `<div class="empty error">Lỗi tóm tắt: ${esc(e.message)}</div>`;
                }

                btn.disabled = false;
                btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>Tóm tắt & Tổng hợp';
            };

            /* ================= DEMO DATA (xem thử khi chưa nối backend) ================= */
            async function demoSearch(q, ans, src) {
                renderSources(
                    [
                        {
                            metadata: {
                                evidence_id: "E1",
                                so_ky_hieu: "215/KH-UBND",
                                ngay_ban_hanh: "2025-12-30"
                            },
                            score: .94,
                            text: "Kế hoạch triển khai Đề án 06 trên địa bàn tỉnh; giao Sở Khoa học và Công nghệ " +
                                    "chủ trì xây dựng nền tảng dữ liệu dùng chung, hoàn thành trong quý II..."
                        }, {
                            metadata: {
                                evidence_id: "E2",
                                so_ky_hieu: "01/KH-TU",
                                ngay_ban_hanh: "2026-07-06"
                            },
                            score: .88,
                            text: "Căn cứ Nghị quyết 57-NQ/TW, Tỉnh ủy ban hành kế hoạch về phát triển khoa học, " +
                                    "công nghệ, đổi mới sáng tạo và chuyển đổi số..."
                        }, {
                            metadata: {
                                evidence_id: "E3",
                                so_ky_hieu: "1442/BC-SKHCN",
                                ngay_ban_hanh: "2026-03-31"
                            },
                            score: .81,
                            text: "Báo cáo kết quả quý I: đã tổ chức 12 buổi tập huấn chuyển đổi số cho cán bộ cấ" +
                                    "p xã, hoàn thành 78% nhiệm vụ được giao..."
                        }
                    ],
                    src
                );
                const md = `Theo kế hoạch **215/KH-UBND** ngày 30/12/2025, Sở Khoa học và Công nghệ được giao chủ trì xây dựng nền tảng dữ liệu dùng chung, hạn hoàn thành **quý II** [E1].

Tính đến hết quý I/2026, tiến độ đạt **78%** nhiệm vụ, đã tổ chức **12 buổi tập huấn** chuyển đổi số cho cán bộ cấp xã [E3].

Kế hoạch này căn cứ trực tiếp vào kế hoạch **01/KH-TU** của Tỉnh ủy, vốn cụ thể hóa **Nghị quyết 57-NQ/TW** của Bộ Chính trị [E2].`;
                ans.innerHTML = "";
                let i = 0;
                await new Promise(res => {
                    const t = setInterval(() => {
                        i += 4;
                        ans.innerHTML = formatCitations(md.slice(0, i)) + '<span class="caret"></span>';
                        if (i >= md.length) {
                            clearInterval(t);
                            ans.innerHTML = formatCitations(md);
                            res();
                        }
                    }, 14);
                });
            }

            /* ---------- OCR ---------- */
            const dropOcr = $("#drop-ocr"), inpOcr = $("#inp-ocr");
            dropOcr.onclick = () => inpOcr.click();
            dropOcr.ondragover = e => { e.preventDefault(); dropOcr.classList.add("over"); };
            dropOcr.ondragleave = () => dropOcr.classList.remove("over");
            dropOcr.ondrop = e => {
                e.preventDefault(); dropOcr.classList.remove("over");
                if (e.dataTransfer.files[0]) handleOcrFile(e.dataTransfer.files[0]);
            };
            inpOcr.onchange = () => { if (inpOcr.files[0]) handleOcrFile(inpOcr.files[0]); };

            async function handleOcrFile(f) {
                $("#ocr-file").textContent = "📄 " + f.name;
                const ta = $("#ta-ocr");
                const btnDl = $("#b-download-ocr");
                const loading = $("#ocr-loading");

                loading.style.display = "flex";
                ta.value = "";
                ta.disabled = true;
                btnDl.disabled = true;

                const fd = new FormData();
                fd.append("file", f);

                try {
                    const r = await fetch(API + "/api/ocr/process", { method: "POST", body: fd });
                    const d = await r.json().catch(() => ({}));
                    if (!r.ok || d.error) throw new Error(apiError(r, d));

                    ta.value = d.text || "⚠️ Không trích xuất được nội dung.";

                    // Kích hoạt nút download khi có kết quả
                    if (d.text) {
                        btnDl.disabled = false;
                        btnDl.onclick = () => {
                            const blob = new Blob([d.text], {type: "text/markdown;charset=utf-8"});
                            const url = URL.createObjectURL(blob);
                            const a = document.createElement('a');
                            a.href = url;
                            a.download = f.name.replace(/\.(pdf|png|jpe?g)$/i, "") + ".md";
                            a.click();
                            URL.revokeObjectURL(url);
                        };
                    }
                } catch (e) {
                    ta.value = "Lỗi khi chạy OCR: " + e.message;
                } finally {
                    loading.style.display = "none";
                    ta.disabled = false;
                }
            }

            function demoAgg(q) {
                const fact = (so, date, label, value, quote) => ({so_ky_hieu: so, ngay_ban_hanh: date, label, value, unit: "buổi", status: "thuc_hien", quote});
                return {
                    question: q, metric: "Số buổi tập huấn chuyển đổi số", operation: "sum", unit: "buổi", total: 47,
                    n_docs_in_scope: 23, n_docs_scanned: 23, n_docs_with_facts: 5, groups: [], warnings: [],
                    included: [
                        fact("1442/BC-SKHCN", "2026-03-31", "Tập huấn CĐS cán bộ cấp xã", 12, "đã tổ chức 12 buổi tập huấn chuyển đổi số cho cán bộ cấp xã"),
                        fact("58/BC-UBND", "2026-03-28", "Tập huấn kỹ năng số cho người dân", 9, "phối hợp tổ chức 09 buổi tập huấn kỹ năng số cho người dân"),
                        fact("77/BC-UBND", "2026-03-25", "Lớp tập huấn CĐS cấp xã", 11, "tổ chức 11 lớp tập huấn về chuyển đổi số cấp xã"),
                        fact("102/BC-PGD", "2026-03-20", "Bồi dưỡng CĐS cho giáo viên", 8, "08 buổi bồi dưỡng chuyển đổi số cho giáo viên"),
                        fact("39/BC-UBND", "2026-03-18", "Tập huấn nền tảng số cộng đồng", 7, "đã tổ chức 7 buổi tập huấn nền tảng số cộng đồng")
                    ],
                    review: [{...fact("39/BC-UBND", "2026-03-18", "Kế hoạch tập huấn quý II", 10, "dự kiến tổ chức 10 buổi trong quý II"), status: "ke_hoach", review_reason: "khac_trang_thai_ke_hoach"}]
                };
            }

            health();
            showTab(location.hash.slice(1) || "overview", {push: false, scroll: false});
            UI.auth.load();
            if (new URLSearchParams(location.search).get("denied") === "admin") {
                UI.toast("Tài khoản của bạn không có quyền vào trang quản trị.", "warn");
                history.replaceState(null, "", location.pathname + location.hash);
            }
