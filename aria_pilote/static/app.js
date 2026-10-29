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
        const error = new Error(data.detail || `Erreur HTTP ${resp.status}`);
        error.status = resp.status;  // ex. 501 : la fenêtre de choix n'existe pas ici
        throw error;
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

// --- Fenêtre « Choisir un dossier » de Windows (ouverte par le serveur) --------------------------

// Chemin choisi, ou null si tu as annulé. Erreur 501 : la fenêtre n'existe pas ici (plan B : saisie du chemin).
async function pickFolder(initial = "", title = "Choisir un dossier") {
    return (await api("/api/pick-folder", { method: "POST", body: JSON.stringify({ initial, title }) })).path;
}

function shortPath(path) {
    // Les deux derniers niveaux suffisent dans une puce : le chemin complet est dans l'infobulle
    const parts = path.split(/[\\/]+/).filter(Boolean);
    return parts.length > 2 ? `…\\${parts.slice(-2).join("\\")}` : path;
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
    }
    // En tête : les réglages par défaut, pour tous les modèles qui n'ont pas les leurs
    const defaults = el("li", { className: `defaults-item${state.selectedId === DEFAULTS_ID ? " selected" : ""}` },
        el("div", { className: "title" }, "⚙ Réglages par défaut"),
        el("div", { className: "sub" }, "Pour les modèles sans réglage propre"));
    defaults.addEventListener("click", () => selectModel(DEFAULTS_ID));
    list.append(defaults);
    for (const m of visible) {
        const badges = el("div", { className: "badges" });
        if (m.quant) badges.append(el("span", { className: "badge" }, m.quant));
        badges.append(el("span", { className: "badge" }, formatSize(m.size_bytes)));
        if (m.size_label) badges.append(el("span", { className: "badge" }, m.size_label));
        if (m.engine_auto === "prism") badges.append(el("span", { className: "badge prism" }, "PrismML"));
        if (m.mmproj) badges.append(el("span", { className: "badge" }, "vision"));
        if (m.error) badges.append(el("span", { className: "badge err" }, "illisible"));
        if (m.custom) badges.append(el("span", { className: "badge", title: "Réglages propres à ce modèle" }, "réglé"));
        if (m.id === loadedId) badges.append(el("span", { className: "badge loaded" }, "chargé"));

        const item = el(
            "li",
            { className: m.id === state.selectedId ? "selected" : "" },
            el("div", { className: "title" }, m.name),
            el("div", { className: "sub" }, `${m.publisher} / ${m.repo}`),
            badges
        );
        item.addEventListener("click", () => selectModel(m.id));
        list.append(item);
    }
}

const DEFAULTS_ID = "__defaults__";

// Les réglages de chargement, par groupe, chacun avec ce qu'il change. `when` : seulement pour les modèles concernés.
const LOAD_GROUPS = [
    { title: "Moteur et mémoire", fields: [
        { key: "engine", label: "Moteur", type: "select",
            options: [["auto", "Auto (selon le fichier)"], ["llama.cpp", "llama.cpp officiel"], ["prism", "PrismML (Bonsai)"]],
            help: "Auto choisit PrismML pour les formats ternaires (Bonsai), llama.cpp officiel pour le reste." },
        { key: "gpu_layers", label: "Couches sur la carte graphique", type: "number", min: 0,
            help: "99 = tout sur la carte graphique (le plus rapide). Moins de couches : le reste tourne sur le processeur, bien plus lentement, mais la VRAM se libère." },
        { key: "n_cpu_moe", label: "Experts gardés en RAM", type: "number", min: 0, when: (m) => !m || m.experts,
            help: "Modèles MoE : les experts des N premières couches restent en RAM. Un gros modèle tient alors sur la carte graphique en perdant peu de vitesse. 0 = tout sur la carte." },
        { key: "load_mode", label: "Chargement du fichier", type: "select",
            options: [["auto", "Auto (mmap)"], ["mmap", "mmap"], ["none", "Lecture simple"], ["mlock", "Verrouillé en RAM (mlock)"], ["mmap+mlock", "mmap + verrouillé en RAM"]],
            help: "mmap lit le fichier à la demande (démarrage rapide). « Verrouillé en RAM » empêche Windows de le décharger : utile si le modèle déborde en RAM, mais la mémoire reste prise." },
        { key: "threads", label: "Threads du processeur", type: "number", min: 0, placeholder: "0 = auto",
            help: "Utilisés pour ce qui tourne sur le processeur. 0 laisse le moteur choisir (en général le bon choix)." },
    ] },
    { title: "Contexte", fields: [
        { key: "ctx_size", label: "Taille du contexte (tokens)", type: "number", min: 512, step: 512, max: (m) => m?.context_length,
            help: "La mémoire de la conversation. Plus grand : de plus longues tâches, mais plus de VRAM (le cache grossit avec)." },
        { key: "cache_type", label: "Cache K", type: "select",
            options: [["f16", "Normal (f16)"], ["q8_0", "Compressé ×2 (q8_0)"], ["q4_0", "Compressé ×4 (q4_0, perte sensible)"]],
            help: "Compresser le cache de contexte loge un contexte plus grand dans la même VRAM. q8_0 : quasiment sans perte." },
        { key: "cache_type_v", label: "Cache V", type: "select",
            options: [["", "Comme le cache K"], ["f16", "Normal (f16)"], ["q8_0", "Compressé ×2 (q8_0)"], ["q4_0", "Compressé ×4 (q4_0)"]],
            help: "La partie V tolère souvent mieux la compression que la K. Compressée, elle active d'office la flash attention." },
        { key: "flash_attn", label: "Flash attention", type: "select", options: [["auto", "Auto"], ["on", "Activée"], ["off", "Désactivée"]],
            help: "Un calcul d'attention plus économe en VRAM et souvent plus rapide. Auto la laisse au moteur." },
    ] },
    { title: "Vitesse", fields: [
        { key: "batch_size", label: "Batch (tokens)", type: "number", min: 0, placeholder: "0 = 2048",
            help: "Tokens du prompt lus d'un coup. Plus grand : un long prompt est lu plus vite, au prix d'un peu de VRAM." },
        { key: "ubatch_size", label: "Micro-batch (tokens)", type: "number", min: 0, placeholder: "0 = 512",
            help: "Le morceau réellement envoyé à la carte graphique. 512 à 2048 accélère souvent la lecture du prompt ; ne dépasse pas le batch." },
        { key: "mtp", label: "Prédiction multi-tokens (MTP)", type: "check", when: (m) => !m || m.mtp_layers,
            help: "Le modèle devine les tokens suivants avec sa couche MTP, puis les vérifie : la génération peut aller nettement plus vite, sans changer le résultat. Expérimental : vérifie qu'il charge bien." },
        { key: "mtp_draft", label: "Tokens devinés d'avance", type: "number", min: 0, placeholder: "0 = auto", when: (m) => !m || m.mtp_layers,
            help: "Combien de tokens la MTP propose à chaque tour (0 : valeur du moteur)." },
    ] },
    { title: "Vision", fields: [
        { key: "use_mmproj", label: "Vision (projecteur mmproj)", type: "check", when: (m) => m, disabled: (m) => !m.mmproj,
            help: "Permet au modèle de voir les images jointes. Prend un peu de VRAM : décoche-le si tu n'en as pas besoin." },
    ] },
];

let detailInitial = null;

function selectModel(id) {
    state.selectedId = id;
    renderList();
    renderDetail();
}

function loadFieldNode(field, model, value) {
    let input;
    if (field.type === "select") {
        input = el("select", { name: field.key }, ...field.options.map(([v, label]) => el("option", { value: v }, label)));
        input.value = value ?? field.options[0][0];
    } else if (field.type === "check") {
        input = el("input", { type: "checkbox", name: field.key, checked: Boolean(value) });
    } else {
        input = el("input", { type: "number", name: field.key, min: field.min ?? 0, step: field.step ?? 1,
            placeholder: field.placeholder || "", value: value || (field.placeholder ? "" : value ?? "") });
        const max = typeof field.max === "function" ? field.max(model) : field.max;
        if (max) input.max = max;
    }
    if (field.disabled?.(model)) input.disabled = true;
    input.addEventListener("input", syncLoadActions);
    input.addEventListener("change", syncLoadActions);
    const head = field.type === "check"
        ? el("label", { className: "check" }, input, ` ${field.label}`)
        : el("label", {}, field.label, input);
    return el("div", { className: "load-field" }, head, el("div", { className: "muted small" }, field.help));
}

function readLoadForm() {
    const form = $("#load-form");
    const values = {};
    for (const group of LOAD_GROUPS) {
        for (const field of group.fields) {
            const input = form.elements[field.key];
            if (!input) continue;
            values[field.key] = field.type === "check" ? input.checked
                : field.type === "number" ? Number(input.value || 0) : input.value;
        }
    }
    return values;
}

function syncLoadActions() {
    const dirty = detailInitial !== null && JSON.stringify(readLoadForm()) !== detailInitial;
    for (const b of document.querySelectorAll("#load-actions [data-dirty]")) b.disabled = !dirty;
    const flag = $("#load-dirty");
    if (flag) flag.hidden = !dirty;
}

function actionButton(label, className, handler, needsChange = false) {
    const b = el("button", { type: "button", className }, label);
    if (needsChange) b.dataset.dirty = "1";
    b.addEventListener("click", async () => {
        $("#form-error").hidden = true;
        b.disabled = true;
        try {
            await handler();
        } catch (err) {
            $("#form-error").textContent = err.message;
            $("#form-error").hidden = false;
        } finally {
            b.disabled = false;
            syncLoadActions();
        }
    });
    return b;
}

async function renderModelFile(m) {
    const meta = $("#d-meta");
    meta.replaceChildren(el("dt", {}, "Lecture…"), el("dd", {}));
    const rows = [["Fichier", m.file, ""], ["Taille", formatSize(m.size_bytes), ""]];
    try {
        const details = await api(`/api/models/${m.id}/details`);
        rows.push(...details.rows.map((r) => [r.label, r.value, r.help]));
    } catch {
        rows.push(["Architecture", m.arch || "?", ""], ["Couches", m.layers ?? "?", ""]);
    }
    if (m.error) rows.push(["Erreur", m.error, ""]);
    if (state.selectedId !== m.id) return;  // une autre fiche a été ouverte entre-temps
    meta.replaceChildren(...rows.flatMap(([k, v, help]) => [
        el("dt", { title: help || "" }, help ? `${k} ⓘ` : k), el("dd", { title: help || "" }, String(v))]));
}

function renderDetail() {
    const isDefaults = state.selectedId === DEFAULTS_ID;
    const m = isDefaults ? null : state.models.find((x) => x.id === state.selectedId);
    const shown = isDefaults || Boolean(m);
    $("#empty").hidden = shown;
    $("#detail").hidden = !shown;
    if (!shown) return;
    $("#form-error").hidden = true;
    $("#btn-delete-model").hidden = isDefaults;
    $("#d-file").hidden = isDefaults;

    const values = isDefaults ? { ...state.config.load_defaults } : { ...m.settings };
    $("#d-name").textContent = isDefaults ? "⚙ Réglages par défaut" : m.name;
    $("#d-path").textContent = isDefaults ? "" : m.path;
    $("#d-state").replaceChildren(isDefaults
        ? el("p", { className: "muted small" }, "Ils valent pour chaque modèle qui n'a pas ses propres réglages (ceux d'⚡ Optimiser ou de 💾 Enregistrer dans sa fiche).")
        : el("p", { className: "small" }, m.custom
            ? el("span", { className: "badge" }, "Réglages propres à ce modèle")
            : el("span", { className: "muted" }, "Ce modèle suit les réglages par défaut."),
            el("span", { id: "load-dirty", className: "warn-text", hidden: true }, "  · modifications non enregistrées")));
    if (!isDefaults) renderModelFile(m);

    $("#load-form").replaceChildren(...LOAD_GROUPS.map((group) => {
        const fields = group.fields.filter((f) => !f.when || f.when(m));
        if (!fields.length) return "";
        return el("fieldset", { className: "load-group" }, el("legend", {}, group.title),
            ...fields.map((f) => loadFieldNode(f, m, values[f.key])));
    }));
    detailInitial = JSON.stringify(readLoadForm());

    const actions = $("#load-actions");
    if (isDefaults) {
        actions.replaceChildren(actionButton("💾 Enregistrer", "primary", async () => {
            await saveConfig({ load_defaults: readLoadForm() });
            detailInitial = JSON.stringify(readLoadForm());
            await loadModels();
        }, true));
    } else {
        const save = async () => {
            const updated = await api(`/api/models/${m.id}/settings`, { method: "PUT", body: JSON.stringify({ settings: readLoadForm() }) });
            Object.assign(m, updated);
            detailInitial = JSON.stringify(readLoadForm());
            flashSaved();
        };
        const load = async (body) => {
            state.status = await api("/api/load", { method: "POST", body: JSON.stringify({ model_id: m.id, ...body }) });
            renderStatus();
        };
        const buttons = [
            actionButton("💾 Enregistrer", "primary", async () => { await save(); await loadModels(); }, true),
            actionButton("Enregistrer et charger", "ghost", async () => { await save(); await load({}); await loadModels(); }, true),
            actionButton("▶ Charger", "ghost", () => load(readLoadForm())),
        ];
        if (m.custom) {
            buttons.push(actionButton("↺ Revenir aux réglages par défaut", "ghost", async () => {
                if (!confirm("Oublier les réglages propres à ce modèle ? Il suivra les réglages par défaut.")) return;
                await api(`/api/models/${m.id}/settings`, { method: "DELETE" });
                flashSaved();
                await loadModels();
            }));
        }
        actions.replaceChildren(...buttons);
        actions.querySelector("button:nth-child(3)").title = "Charge avec les réglages affichés, sans les enregistrer";
    }
    syncLoadActions();
}

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

// Une tuile par composant : la carte graphique (et sa VRAM), le processeur, la mémoire vive
function hwTile(icon, title, detail, used, total) {
    const tile = el("div", { className: "hw-tile" },
        el("div", { className: "hw-title" }, `${icon} ${title}`),
        el("div", { className: "muted small" }, detail));
    if (total) {
        const share = Math.min(100, Math.round((used / total) * 100));
        tile.append(
            el("div", { className: `hw-bar${share > 85 ? " full" : ""}` }, el("span", { style: `width: ${share}%` })),
            el("div", { className: "small" }, `${gio(total - used)} libres sur ${gio(total)} · ${share} % occupé`));
    }
    return tile;
}

async function renderHardware() {
    const box = $("#hw-info");
    box.replaceChildren(el("p", { className: "muted" }, "Scan en cours…"));
    try {
        const hw = await api("/api/hardware");
        const tiles = hw.gpus.length
            ? hw.gpus.map((g) => hwTile("🎮", g.name, `Carte graphique${g.driver ? ` · pilote ${g.driver}` : ""} · VRAM`,
                g.vram_total - g.vram_free, g.vram_total))
            : [hwTile("🎮", "Aucune carte NVIDIA détectée", "Les modèles tourneront sur le processeur, beaucoup plus lentement.")];
        tiles.push(hwTile("🧠", hw.cpu.name || "Processeur",
            `Processeur · ${hw.cpu.cores ? `${hw.cpu.cores} cœurs, ` : ""}${hw.cpu.threads} threads`));
        if (hw.ram.total) tiles.push(hwTile("💾", "Mémoire vive", "RAM", hw.ram.total - hw.ram.free, hw.ram.total));
        box.replaceChildren(...tiles);
    } catch (err) {
        box.replaceChildren(el("p", { className: "error" }, `Scan impossible : ${err.message}`));
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
    $("#optimize-fail").hidden = true;
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
        $("#optimize-panel").hidden = false;
        button.textContent = "↻ Recalculer";
    } catch (err) {
        $("#optimize-fail").textContent = `Optimisation impossible : ${err.message}`;
        $("#optimize-fail").hidden = false;
    } finally {
        button.disabled = false;
    }
});

function hideOptimize() {
    $("#optimize-panel").hidden = true;
    $("#btn-optimize").textContent = "⚡ Calculer";
}

$("#btn-optimize-close").addEventListener("click", () => {
    trialController?.abort();
    hideOptimize();
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
        $("#optimize-error").textContent = "Coche au moins un modèle à régler.";
        $("#optimize-error").hidden = false;
        return;
    }
    try {
        await api("/api/optimize/apply", { method: "POST", body: JSON.stringify({ items }) });
        hideOptimize();
        flashSaved();
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
