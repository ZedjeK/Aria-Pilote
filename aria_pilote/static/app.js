const $ = (sel) => document.querySelector(sel);

const state = {
    models: [],
    selectedId: null,
    status: null,
    logSeq: 0,
};

const ENGINE_LABELS = { "llama.cpp": "llama.cpp", prism: "PrismML" };

async function api(path, options = {}) {
    const resp = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
        throw new Error(data.detail || `Erreur HTTP ${resp.status}`);
    }
    return data;
}

function formatSize(bytes) {
    return `${(bytes / 1024 ** 3).toFixed(2)} Go`;
}

function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    Object.assign(node, attrs);
    node.append(...children);
    return node;
}

// --- Modèles -------------------------------------------------------------

async function loadModels(refresh = false) {
    state.models = await api(`/api/models${refresh ? "?refresh=1" : ""}`);
    renderList();
    renderDetail();
    renderPicker();
}

// --- Sélecteur de modèle (barre du haut) -------------------------------------

function renderPicker() {
    const pick = $("#model-pick");
    if (document.activeElement === pick) return;  // pas pendant que tu choisis
    const active = ["loading", "ready"].includes(state.status?.state);
    const current = active ? state.status.model?.id : "";
    const usable = state.models.filter((m) => !m.error && !/mmproj/i.test(m.file));
    const placeholder = el("option", { value: "" }, usable.length ? "Choisir un modèle…" : "Aucun modèle (Réglages › Découvrir)");
    pick.replaceChildren(placeholder, ...usable.map((m) =>
        el("option", { value: m.id }, `${m.id === state.config?.default_model ? "⭐ " : ""}${m.name}`)));
    pick.value = current && usable.some((m) => m.id === current) ? current : "";
}

$("#model-pick").addEventListener("change", async (event) => {
    const id = event.target.value;
    event.target.blur();
    if (!id || id === state.status?.model?.id) return;
    try {
        state.status = await api("/api/load", { method: "POST", body: JSON.stringify({ model_id: id }) });
    } catch (err) {
        alert(err.message);
    }
    renderStatus();
    renderList();
});

function renderList() {
    const filter = $("#filter").value.toLowerCase();
    const list = $("#model-list");
    list.replaceChildren();
    const active = ["loading", "ready"].includes(state.status?.state);
    const loadedId = active ? state.status.model?.id : null;
    const visible = state.models.filter((m) =>
        `${m.name} ${m.publisher} ${m.repo} ${m.file}`.toLowerCase().includes(filter)
    );
    if (!visible.length) {
        list.append(el("li", { className: "muted" }, "Aucun modèle trouvé dans les dossiers configurés."));
        return;
    }
    for (const m of visible) {
        const badges = el("div", { className: "badges" });
        if (m.quant) badges.append(el("span", { className: "badge" }, m.quant));
        badges.append(el("span", { className: "badge" }, formatSize(m.size_bytes)));
        if (m.size_label) badges.append(el("span", { className: "badge" }, m.size_label));
        if (m.engine_auto === "prism") badges.append(el("span", { className: "badge prism" }, "PrismML"));
        if (m.mmproj) badges.append(el("span", { className: "badge" }, "vision"));
        if (m.error) badges.append(el("span", { className: "badge err" }, "illisible"));
        if (m.id === loadedId) badges.append(el("span", { className: "badge loaded" }, "chargé"));

        const item = el(
            "li",
            { className: m.id === state.selectedId ? "selected" : "" },
            el("div", { className: "title" }, m.name),
            el("div", { className: "sub" }, `${m.publisher} / ${m.repo}`),
            badges
        );
        item.addEventListener("click", () => {
            state.selectedId = m.id;
            renderList();
            renderDetail();
        });
        list.append(item);
    }
}

function renderDetail() {
    const m = state.models.find((x) => x.id === state.selectedId);
    $("#empty").hidden = Boolean(m);
    $("#detail").hidden = !m;
    if (!m) return;

    $("#d-name").textContent = m.name;
    $("#d-path").textContent = m.path;

    const meta = $("#d-meta");
    meta.replaceChildren();
    const rows = [
        ["Fichier", m.file],
        ["Quantification", m.quant || "inconnue"],
        ["Architecture", m.arch || "?"],
        ["Contexte max", m.context_length ? m.context_length.toLocaleString("fr-FR") : "?"],
        ["Couches", m.layers ?? "?"],
        ["Taille", formatSize(m.size_bytes)],
        ["Moteur auto", ENGINE_LABELS[m.engine_auto]],
        ["Projecteur vision", m.mmproj ? m.mmproj.split(/[\\/]/).pop() : "aucun"],
    ];
    if (m.error) rows.push(["Erreur", m.error]);
    for (const [k, v] of rows) meta.append(el("dt", {}, k), el("dd", {}, String(v)));

    const form = $("#load-form");
    const s = m.settings;
    form.engine.value = s.engine;
    form.ctx_size.value = s.ctx_size;
    form.ctx_size.max = m.context_length || "";
    form.gpu_layers.value = s.gpu_layers;
    form.threads.value = s.threads || "";
    form.flash_attn.value = s.flash_attn;
    form.cache_type.value = s.cache_type || "f16";
    form.use_mmproj.checked = s.use_mmproj;
    form.use_mmproj.disabled = !m.mmproj;
    form.remember.checked = false;
    $("#form-error").hidden = true;
}

$("#load-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const body = {
        model_id: state.selectedId,
        engine: form.engine.value,
        ctx_size: Number(form.ctx_size.value),
        gpu_layers: Number(form.gpu_layers.value),
        threads: Number(form.threads.value || 0),
        flash_attn: form.flash_attn.value,
        cache_type: form.cache_type.value,
        use_mmproj: form.use_mmproj.checked,
        remember: form.remember.checked,
    };
    $("#form-error").hidden = true;
    try {
        state.status = await api("/api/load", { method: "POST", body: JSON.stringify(body) });
        renderStatus();
        if (body.remember) await loadModels();
    } catch (err) {
        $("#form-error").textContent = err.message;
        $("#form-error").hidden = false;
    }
});

// --- Statut, moteurs et journal -------------------------------------------

function renderStatus() {
    const s = state.status;
    if (s.version) $("#app-version").textContent = `v${s.version}`;
    const box = $("#status");
    box.className = `status ${s.state}`;
    const name = s.model?.name;
    const engine = ENGINE_LABELS[s.engine] || s.engine;
    box.title = {
        idle: "Aucun modèle chargé",
        loading: `Chargement de ${name} (${engine})…`,
        ready: `${name} · ${engine} · ${s.api_url}`,
        error: s.error || "Erreur",
    }[s.state];
    // Le nom est dans le sélecteur : le texte ne sert qu'aux états de passage
    $("#status-text").textContent = { loading: "chargement…", error: s.error || "erreur" }[s.state] || "";
    $("#btn-unload").hidden = !["loading", "ready"].includes(s.state);
    renderPicker();
}

async function pollStatus() {
    try {
        const previous = state.status?.model?.id;
        const wasLoading = state.status?.state === "loading";
        state.status = await api("/api/status");
        if (wasLoading && state.status.state === "ready") notify("model", "Modèle prêt", state.status.model?.name || "");
        if (wasLoading && state.status.state === "error") notify("model", "Chargement impossible", state.status.error || "");
        renderStatus();
        if (state.status.model?.id !== previous) renderList();
    } catch {
        $("#status").className = "status error";
        $("#status-text").textContent = "Aria Pilote injoignable";
    }
}

async function pollLogs() {
    try {
        const data = await api(`/api/logs?since=${state.logSeq}`);
        if (data.seq < state.logSeq) state.logSeq = 0;
        if (data.lines.length) {
            const box = $("#logs");
            const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;
            box.textContent += data.lines.join("\n") + "\n";
            if (box.textContent.length > 200000) box.textContent = box.textContent.slice(-150000);
            if (atBottom) box.scrollTop = box.scrollHeight;
        }
        state.logSeq = data.seq;
    } catch {
        // le statut affiche déjà l'erreur
    }
}

async function loadEngines() {
    const list = $("#engines");
    list.replaceChildren();
    for (const e of await api("/api/engines")) {
        const detail = e.installed
            ? `${e.release?.tag ?? "version ?"}${e.release?.backend ? ` · ${e.release.backend}` : ""}`
            : "non installé";
        list.append(el("li", {}, el("span", {}, e.label), el("span", { className: e.installed ? "mono" : "error" }, detail)));
    }
}

$("#btn-unload").addEventListener("click", async () => {
    state.status = await api("/api/unload", { method: "POST" });
    renderStatus();
    renderList();
});

$("#btn-refresh").addEventListener("click", () => {
    loadModels(true);
    loadEngines();
});

$("#filter").addEventListener("input", renderList);

$("#btn-delete-model").addEventListener("click", async () => {
    const m = state.models.find((x) => x.id === state.selectedId);
    if (!m) return;
    const vision = m.mmproj ? "\n(son projecteur vision partira aussi s'il ne sert plus à aucun autre modèle)" : "";
    if (!confirm(`Envoyer « ${m.name} » (${formatSize(m.size_bytes)}) à la corbeille ?${vision}`)) return;
    try {
        await api(`/api/models/${m.id}`, { method: "DELETE" });
        state.selectedId = null;
        await loadModels();
    } catch (err) {
        $("#form-error").textContent = err.message;
        $("#form-error").hidden = false;
    }
});

$("#btn-quit").addEventListener("click", async () => {
    if (!confirm("Arrêter Aria Pilote ? Le modèle sera éjecté et SearXNG coupé.")) return;
    try {
        await api("/api/shutdown", { method: "POST" });
    } catch (err) {
        alert(err.message);
        return;
    }
    document.body.replaceChildren(
        el("div", { className: "goodbye" }, el("div", {},
            el("h1", {}, "À bientôt 👋"),
            el("p", {}, "Aria Pilote est arrêtée. Tu peux fermer cet onglet."),
            el("p", { className: "small" }, "Pour la relancer : double-clic sur « Aria-Pilote.exe »."),
        ))
    );
});

// --- Matériel et optimisation des modèles ----------------------------------

const gio = (bytes) => `${(bytes / 1024 ** 3).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} Go`;

async function renderHardware() {
    const box = $("#hw-info");
    box.textContent = "Scan en cours…";
    try {
        const hw = await api("/api/hardware");
        const gpus = hw.gpus.length
            ? hw.gpus.map((g) => `${g.name} : ${gio(g.vram_total)} de VRAM, ${gio(g.vram_free)} libres`).join(" · ")
            : "Aucune carte NVIDIA détectée";
        const cpu = hw.cpu.name ? `${hw.cpu.name} (${hw.cpu.cores} cœurs, ${hw.cpu.threads} threads)` : `${hw.cpu.threads} threads`;
        const ram = hw.ram.total ? `${gio(hw.ram.total)} de RAM (${gio(hw.ram.free)} libres)` : "";
        box.replaceChildren(el("div", {}, `🎮 ${gpus}`), el("div", {}, `🧠 ${cpu}${ram ? " · " + ram : ""}`));
    } catch (err) {
        box.textContent = `Scan impossible : ${err.message}`;
    }
}

function describeSettings(s) {
    const cache = s.cache_type && s.cache_type !== "f16" ? ` · cache ${s.cache_type}` : "";
    const gpu = s.gpu_layers >= 99 ? "tout sur le GPU" : `${s.gpu_layers} couches GPU`;
    return `${Math.round(s.ctx_size / 1024)}k · ${gpu}${cache}`;
}

let optimizePlans = [];
let optimizeResult = null;

function renderPlans() {
    $("#optimize-rows").replaceChildren(...optimizePlans.map((p, i) => {
        // Cochée d'office si un changement est proposé ; cochable aussi pour vérifier les réglages actuels
        const pick = el("input", { type: "checkbox", checked: p.changed });
        pick.dataset.index = i;
        const notes = p.notes.length ? el("div", { className: "muted small" }, p.notes.join(" ")) : "";
        return el("tr", {},
            el("td", {}, pick),
            el("td", {}, el("strong", {}, p.name), notes),
            el("td", {}, describeSettings(p.current)),
            el("td", { className: p.changed ? "changed" : "" }, p.changed ? describeSettings(p.proposed) : "déjà optimal"),
            el("td", {}, p.vram_estimate ? gio(p.vram_estimate) : "—"),
            el("td", { className: "trial muted small" }, "—"));
    }));
}

function selectProfile(id) {
    // Trois choix : chacun montre ses conséquences et ce qu'il donne pour tes modèles
    const profile = optimizeResult.profiles.find((p) => p.id === id);
    optimizePlans = profile.plans.map((p) => ({ ...p, proposed: { ...p.proposed } }));
    for (const card of $("#optimize-profiles").children) card.classList.toggle("selected", card.dataset.id === id);
    renderPlans();
}

function renderVramTip(result) {
    // Les autres applications qui occupent la carte graphique faussent le calcul et l'essai : on les nomme
    const box = $("#optimize-tip");
    if (result.others_used === null) {
        box.hidden = true;
        return;
    }
    const heavy = result.others_used > 1.5 * 1024 ** 3;
    const apps = result.gpu_apps.length ? ` Applications détectées sur la carte graphique : ${result.gpu_apps.join(", ")}.` : "";
    const again = el("button", { type: "button", className: "ghost" }, "↻ Recalculer");
    again.addEventListener("click", () => $("#btn-optimize").click());
    // « Ignorer » ne vaut que pour cette fois : le conseil revient à chaque optimisation, car il compte
    const skip = el("button", { type: "button", className: "ghost" }, "Ignorer");
    skip.addEventListener("click", () => { box.hidden = true; });
    box.className = `optimize-tip small${heavy ? " heavy" : ""}`;
    box.replaceChildren(
        el("span", {}, `💡 Pour un résultat fiable, ferme d'abord les applications inutiles qui utilisent la carte graphique `
            + "(jeux, ComfyUI, navigateur chargé, fonds d'écran animés…) : elles prennent de la VRAM et faussent le calcul "
            + `comme l'essai. En ce moment, elles occupent environ ${gio(result.others_used)}.${apps} `),
        again, skip);
    box.hidden = false;
}

function profileCard(profile) {
    const big = profile.plans.filter((p) => p.vram_estimate && p.vram_estimate > 2 * 1024 ** 3);
    const shown = big.length ? big : profile.plans;
    const card = el("button", { type: "button", className: "profile-card" },
        el("div", { className: "profile-title" }, profile.title),
        el("div", { className: "small" }, profile.summary),
        el("div", { className: "small ok-text" }, `＋ ${profile.pros}`),
        el("div", { className: "small warn-text" }, `－ ${profile.cons}`),
        el("div", { className: "profile-models small" },
            shown.map((p) => `${p.name.split(/[ -]/)[0]} : ${describeSettings(p.proposed)}`).join(" · ")));
    card.dataset.id = profile.id;
    card.addEventListener("click", () => selectProfile(profile.id));
    return card;
}

$("#btn-optimize").addEventListener("click", async () => {
    const button = $("#btn-optimize");
    button.disabled = true;
    $("#optimize-error").hidden = true;
    try {
        const result = await api("/api/optimize", { method: "POST", body: "{}" });
        optimizeResult = result;
        const intro = [result.budget === null ? "Pas de carte NVIDIA : réglages pour le processeur."
            : `VRAM disponible pour un modèle : ${gio(result.budget)}.`];
        if (result.loaded) intro.push(`Le modèle chargé (${result.loaded}) est compté comme libéré.`);
        intro.push("Choisis une option, vérifie-la par un essai réel si tu veux, puis applique.");
        $("#optimize-intro").textContent = intro.join(" ");
        renderVramTip(result);
        $("#optimize-profiles").replaceChildren(...result.profiles.map(profileCard));
        selectProfile(result.default);
        if (!$("#optimize-dialog").open) $("#optimize-dialog").showModal();
    } catch (err) {
        $("#hw-info").textContent = `Optimisation impossible : ${err.message}`;
    } finally {
        button.disabled = false;
    }
});

$("#btn-optimize-close").addEventListener("click", () => {
    trialController?.abort();
    $("#optimize-dialog").close();
});

// Essai réel : chaque modèle coché est chargé avec ses réglages proposés (palier inférieur si ça ne tient pas)
let trialController = null;

function trialCell(modelId) {
    const index = optimizePlans.findIndex((p) => p.model_id === modelId);
    return $("#optimize-rows").children[index]?.querySelector("td.trial");
}

$("#btn-optimize-test").addEventListener("click", async () => {
    const picked = [...$("#optimize-rows").querySelectorAll("input:checked")].map((box) => optimizePlans[Number(box.dataset.index)]);
    if (!picked.length) return;
    $("#optimize-error").hidden = true;
    for (const b of ["#btn-optimize-test", "#btn-optimize-apply", "#btn-optimize"]) $(b).disabled = true;
    for (const card of $("#optimize-profiles").children) card.disabled = true;
    $("#btn-optimize-stop").hidden = false;
    for (const p of picked) trialCell(p.model_id).textContent = "en attente…";
    trialController = new AbortController();
    try {
        const resp = await fetch("/api/optimize/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ items: picked.map((p) => ({ model_id: p.model_id, settings: p.proposed })) }),
            signal: trialController.signal,
        });
        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.detail || `Erreur HTTP ${resp.status}`);
        }
        await readEvents(resp, (ev) => {
            const cell = ev.model_id ? trialCell(ev.model_id) : null;
            if (ev.type === "trial_start" || ev.type === "trial_step") {
                cell.className = "trial small";
                cell.textContent = `⏳ ${ev.message || "préparation…"}`;
            } else if (ev.type === "trial_result" && ev.ok) {
                const plan = optimizePlans.find((p) => p.model_id === ev.model_id);
                plan.proposed = { ...plan.proposed, ...ev.settings };  // « Appliquer » prendra les réglages vérifiés
                const speed = ev.tok_s ? ` · ${Math.round(ev.tok_s)} tok/s` : " · vitesse non mesurée";
                const used = ev.vram_used ? `${gio(ev.vram_used)} utilisés` : "chargé";
                const free = ev.vram_free !== null ? `, ${gio(ev.vram_free)} libres` : "";
                cell.className = "trial ok small";
                cell.textContent = `${ev.adjusted ? "↓ ajusté : " + describeSettings(ev.settings) + " · " : "✓ "}${used}${free}${speed}`;
                const proposedCell = cell.previousElementSibling.previousElementSibling;
                proposedCell.textContent = describeSettings(plan.proposed);
            } else if (ev.type === "trial_result") {
                cell.className = "trial error small";
                cell.textContent = `✗ ${ev.message}`;
                cell.closest("tr").querySelector("input").checked = false;
            } else if (ev.type === "trial_done" && ev.restored) {
                $("#optimize-intro").textContent += ` Essai terminé : ${ev.restored} est rechargé avec ses réglages d'origine.`;
            }
        });
    } catch (err) {
        if (err.name !== "AbortError") {
            $("#optimize-error").textContent = err.message;
            $("#optimize-error").hidden = false;
        }
        for (const cell of $("#optimize-rows").querySelectorAll("td.trial")) {
            if (cell.textContent.startsWith("⏳") || cell.textContent === "en attente…") cell.textContent = "interrompu";
        }
    } finally {
        trialController = null;
        for (const b of ["#btn-optimize-test", "#btn-optimize-apply", "#btn-optimize"]) $(b).disabled = false;
        for (const card of $("#optimize-profiles").children) card.disabled = false;
        $("#btn-optimize-stop").hidden = true;
        loadModels();
    }
});

$("#btn-optimize-stop").addEventListener("click", () => trialController?.abort());

$("#btn-optimize-apply").addEventListener("click", async () => {
    const items = [...$("#optimize-rows").querySelectorAll("input:checked")].map((box) => {
        const p = optimizePlans[Number(box.dataset.index)];
        return { model_id: p.model_id, settings: p.proposed };
    });
    if (!items.length) {
        $("#optimize-dialog").close();
        return;
    }
    try {
        await api("/api/optimize/apply", { method: "POST", body: JSON.stringify({ items }) });
        $("#optimize-dialog").close();
        await loadModels();
    } catch (err) {
        $("#optimize-error").textContent = err.message;
        $("#optimize-error").hidden = false;
    }
});

// --- Démarrage --------------------------------------------------------------

(async () => {
    state.config = await api("/api/config");
    await pollStatus();
    await Promise.all([loadModels(), loadEngines(), pollLogs()]);
    setInterval(pollStatus, 1000);
    setInterval(pollLogs, 1000);
})();
