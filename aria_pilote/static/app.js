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
}

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
    const text = {
        idle: "Aucun modèle chargé",
        loading: `Chargement de ${name} (${engine})…`,
        ready: `${name} · ${engine} · ${s.api_url}`,
        error: s.error || "Erreur",
    }[s.state];
    $("#status-text").textContent = text;
    $("#btn-unload").hidden = !["loading", "ready"].includes(s.state);
}

async function pollStatus() {
    try {
        const previous = state.status?.model?.id;
        state.status = await api("/api/status");
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

// --- Réglages --------------------------------------------------------------

$("#btn-settings").addEventListener("click", async () => {
    const [cfg, hooksInfo] = await Promise.all([api("/api/config"), api("/api/hooks")]);
    $("#settings-form").model_dirs.value = cfg.model_dirs.join("\n");
    $("#settings-form").hooks.value = hooksInfo.global;
    $("#settings-error").hidden = true;
    $("#settings").showModal();
});

$("#settings").addEventListener("close", async () => {
    if ($("#settings").returnValue !== "save") return;
    const form = $("#settings-form");
    const dirs = form.model_dirs.value
        .split("\n")
        .map((d) => d.trim())
        .filter(Boolean);
    await api("/api/config", { method: "PUT", body: JSON.stringify({ model_dirs: dirs }) });
    try {
        await api("/api/hooks/global", { method: "PUT", body: JSON.stringify({ content: form.hooks.value }) });
    } catch (err) {
        // hooks.json invalide : on rouvre les réglages avec l'erreur, sans perdre la saisie
        $("#settings-error").textContent = err.message;
        $("#settings-error").hidden = false;
        $("#settings").showModal();
        return;
    }
    await loadModels();
});

// --- Onglets ---------------------------------------------------------------

function showView(name) {
    for (const view of ["chat", "models", "discover"]) {
        $(`#view-${view}`).hidden = view !== name;
        $(`.tab[data-view="${view}"]`).classList.toggle("active", view === name);
    }
    try {
        localStorage.setItem("aria-view", name);
    } catch {
        // stockage indisponible : on garde juste l'onglet courant
    }
}

for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => showView(tab.dataset.view));
}

// --- Démarrage --------------------------------------------------------------

(async () => {
    let view = "chat";
    try {
        view = localStorage.getItem("aria-view") || "chat";
    } catch {
        // idem
    }
    showView(view);
    await pollStatus();
    await Promise.all([loadModels(), loadEngines(), pollLogs()]);
    setInterval(pollStatus, 1000);
    setInterval(pollLogs, 1000);
})();
