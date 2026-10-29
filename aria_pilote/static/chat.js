// Onglet Chat : conversations, presets, streaming des réponses et mode Agent.
// S'appuie sur $, api, el et state définis dans app.js, et renderMarkdown dans markdown.js.

const chat = {
    conversations: [],
    current: null,
    presets: [],
    controller: null,
    pendingAgent: { enabled: false, workspace: "" },  // réglages d'une conversation pas encore créée (voir resetPendingAgent)
    ctxAlertDismissed: new Set(),  // conversations où tu as fermé l'alerte de contexte
};

// --- Presets ---------------------------------------------------------------

const MANAGE_PRESETS = "__gerer__";

// --- Persona de la conversation (puce 🎭) ----------------------------------------------------------

async function loadPersonas() {
    let data;
    try {
        data = await api("/api/personas");
    } catch {
        return;
    }
    chat.personas = data;
    const select = $("#persona-select");
    const wanted = chat.current ? (chat.current.persona_id || data.default) : (select.dataset.value || data.default);
    select.replaceChildren(
        ...data.personas.map((p) => el("option", { value: p.id }, `🎭 ${p.avatar ? p.avatar + " " : ""}${p.name}`)),
        el("option", { value: "none" }, "🎭 Sans persona"),
        el("option", { value: MANAGE_PRESETS }, "⚙ Gérer les personas…"));
    select.value = wanted === "none" || data.personas.some((p) => p.id === wanted) ? wanted : data.default;
    select.dataset.value = select.value;
}

$("#persona-select").addEventListener("change", async () => {
    const select = $("#persona-select");
    if (select.value === MANAGE_PRESETS) {
        select.value = select.dataset.value;
        location.hash = "#/reglages/persona";
        return;
    }
    select.dataset.value = select.value;
    if (!chat.current) return;
    try {
        chat.current = await api(`/api/conversations/${chat.current.id}`, {
            method: "PATCH", body: JSON.stringify({ persona_id: select.value }),
        });
    } catch (err) {
        showChatError(err.message);
    }
});

// Bannière « Présente-toi » : tant que la fiche Toi est vide, sauf si tu l'as fermée
async function refreshProfileBanner() {
    let dismissed = false;
    try {
        dismissed = localStorage.getItem("aria-profile-banner") === "ferme";
    } catch {
        // stockage indisponible : la bannière reste possible
    }
    let profile = {};
    try {
        profile = await api("/api/profile");
    } catch {
        return;
    }
    const empty = !(profile.name || profile.job || profile.likes || profile.about);
    $("#profile-banner").hidden = dismissed || !empty;
}

$("#btn-profile-banner-close").addEventListener("click", () => {
    try {
        localStorage.setItem("aria-profile-banner", "ferme");
    } catch {
        // tant pis : elle reviendra au prochain chargement
    }
    $("#profile-banner").hidden = true;
});

// Puce 🎛 près de la zone de saisie : le preset de la conversation, et l'accès à leur gestion dans les Réglages
async function loadPresets() {
    chat.presets = await api("/api/presets");
    const select = $("#preset-select");
    const fallback = state.config?.default_preset || "default";
    const wanted = chat.current?.preset_id || select.dataset.value || fallback;
    select.replaceChildren(...chat.presets.map((p) => el("option", { value: p.id }, `🎛 ${p.name}`)),
        el("option", { value: MANAGE_PRESETS }, "⚙ Gérer les presets…"));
    select.value = chat.presets.some((p) => p.id === wanted) ? wanted
        : chat.presets.some((p) => p.id === fallback) ? fallback : chat.presets[0]?.id;
    select.dataset.value = select.value;
}

$("#preset-select").addEventListener("change", async () => {
    const select = $("#preset-select");
    if (select.value === MANAGE_PRESETS) {
        select.value = select.dataset.value;  // le choix ne change pas : on va seulement aux Réglages
        location.hash = "#/reglages/presets";
        return;
    }
    select.dataset.value = select.value;
    if (!chat.current) return;
    chat.current = await api(`/api/conversations/${chat.current.id}`, {
        method: "PATCH",
        body: JSON.stringify({ preset_id: select.value }),
    });
});

// --- Mode Agent ---------------------------------------------------------------

// Une conversation pas encore créée part des réglages de départ (Réglages › Comportement) :
// le dossier proposé est pré-rempli, jamais attaché tout seul
function resetPendingAgent() {
    const d = state.config?.new_conversation || {};
    chat.pendingAgent = { enabled: false, workspace: "", auto_web: Boolean(d.auto_web) };
    if (!chat.current) syncAgentBar();
}

function agentSettings() {
    return chat.current ? chat.current.agent || { enabled: false, workspace: "" } : chat.pendingAgent;
}

function syncAgentBar() {
    const a = agentSettings();
    // Le dossier attaché donne les outils de fichiers ; sans lui, c'est une discussion
    const attached = Boolean(a.enabled && a.workspace);
    const chip = $("#btn-folder");
    if (!chip.disabled) {  // pas pendant que la fenêtre de choix est ouverte
        chip.textContent = attached ? `📂 ${shortPath(a.workspace)}` : "📂 Attacher un dossier…";
        chip.title = attached ? `${a.workspace}\nClique pour choisir un autre dossier.`
            : "Attacher un dossier donne à l'agent l'accès à ses fichiers (avec ton accord pour chaque modification ou commande)";
    }
    chip.classList.toggle("attached", attached);
    $("#btn-agent-detach").hidden = !attached;
    // Plan B : saisie du chemin, seulement si la fenêtre de Windows n'existe pas ici
    $("#agent-root").hidden = !chat.manualFolder || attached;
    $("#btn-agent-root").hidden = !chat.manualFolder || attached;
    $(".agent-bar").classList.toggle("on", attached);
    $("#btn-rules").hidden = !(attached && chat.current);
    const info = [];
    const plan = chat.current?.plan;
    if (!attached) {
        info.push("discussion : aucun accès à tes fichiers", a.auto_web ? "web autorisé" : "web demandé");
    } else {
        if (plan && ["required", "pending"].includes(plan.status) && plan.task_id === chat.current?.task_id) {
            info.push("🔒 plan à valider avant toute modification");
        }
        info.push(a.auto_write ? "modifs autorisées" : "modifs demandées");
        info.push(a.auto_web ? "web autorisé" : "web demandé");
        info.push("commandes demandées");
        if (chat.memoryLoaded) info.push("ARIA.md chargé");
    }
    $("#agent-info").textContent = info.join(" · ");
    renderTodo(chat.current?.todo?.items || a.todo || null);
    syncScreenBar();
    syncMcpChip();
    syncSidePanel();  // sans choix de ta part, le panneau suit le mode Agent
}

// Contrôle du PC : le bouton n'apparaît que s'il est activé dans les Réglages ; le bandeau montre l'état de la main
function syncScreenBar() {
    const on = Boolean(state.config?.screen_control);
    const active = on && chat.current?.screen_control;
    const pc = active && chat.current.screen_mode === "pc";
    $("#btn-screen").hidden = !(on && chat.current);
    $("#btn-screen").classList.toggle("attached", Boolean(active));
    const banner = $("#screen-banner");
    banner.hidden = !active;
    banner.classList.toggle("pc", Boolean(pc));
    if (!active) return;
    if (pc) {
        const hand = el("button", { className: "ghost", title: "Rendre la main : l'agent ne voit plus l'écran" }, "Rendre la main");
        hand.addEventListener("click", releaseScreen);
        banner.replaceChildren(el("span", {}, "🖥 L'agent a la main sur tout ton PC (sauf réglages, sécurité, terminaux et "
            + "Aria). Il agit sans te demander, sauf pour les actions sensibles (envoyer, payer, supprimer…), les "
            + "raccourcis à risque et les répétitions."), hand);
        return;
    }
    const allow = chat.current.screen_allow;
    const apps = Array.isArray(allow)
        ? (allow.length ? allow.join(", ") : "aucune fenêtre pour l'instant — clique sur « Prendre la main »")
        : "toutes les fenêtres (sauf réglages système, sécurité et terminaux)";
    const hand = el("button", { className: "ghost", title: "Rendre la main : l'agent ne voit plus l'écran" }, "Rendre la main");
    hand.addEventListener("click", releaseScreen);
    banner.replaceChildren(el("span", {}, `🖱 L'agent a la main sur : ${apps}. Il demande ton accord à chaque action.`), hand);
}

async function releaseScreen() {
    if (!chat.current) return;
    chat.current = await api(`/api/conversations/${chat.current.id}`, {
        method: "PATCH", body: JSON.stringify({ screen_control: false }),
    });
    syncAgentBar();
}

async function openScreenPicker() {
    if (!chat.current) return;
    const dialog = $("#screen-dialog");
    const list = $("#screen-windows");
    $("#screen-dialog-error").hidden = true;
    list.replaceChildren(el("li", { className: "muted" }, "Lecture des fenêtres ouvertes…"));
    dialog.showModal();
    let windows;
    try {
        windows = await api("/api/screen/windows");
    } catch (err) {
        $("#screen-dialog-error").textContent = err.message;
        $("#screen-dialog-error").hidden = false;
        list.replaceChildren();
        return;
    }
    const mode = chat.current.screen_control && chat.current.screen_mode === "pc" ? "pc" : "fenetres";
    for (const radio of dialog.querySelectorAll('input[name="screen-mode"]')) radio.checked = radio.value === mode;
    syncScreenMode();
    const already = new Set(Array.isArray(chat.current.screen_allow) ? chat.current.screen_allow : []);
    list.replaceChildren(...windows.map((w) => {
        const box = el("input", { type: "checkbox", checked: already.has(w.processus) });
        box.dataset.processus = w.processus;
        return el("li", { className: "static" },
            el("label", { className: "check-line" }, box,
                el("div", { className: "skill-text" },
                    el("div", { className: "skill-name" }, w.processus),
                    el("div", { className: "skill-desc" }, w.fenetres.join(" · ")))));
    }));
    if (!windows.length) list.replaceChildren(el("li", { className: "muted" }, "Aucune fenêtre éligible n'est ouverte."));
}

function syncScreenMode() {
    const pc = $('#screen-dialog input[name="screen-mode"]:checked')?.value === "pc";
    $("#screen-windows").hidden = pc;
    $("#btn-screen-confirm").textContent = pc ? "Lui donner la main sur tout le PC" : "Autoriser ces fenêtres";
}

for (const radio of document.querySelectorAll('#screen-dialog input[name="screen-mode"]')) {
    radio.addEventListener("change", syncScreenMode);
}

$("#btn-screen").addEventListener("click", openScreenPicker);
$("#btn-screen-close").addEventListener("click", () => $("#screen-dialog").close());
$("#btn-screen-confirm").addEventListener("click", async () => {
    const pc = $('#screen-dialog input[name="screen-mode"]:checked')?.value === "pc";
    const allow = [...$("#screen-windows").querySelectorAll("input:checked")].map((b) => b.dataset.processus);
    try {
        chat.current = await api(`/api/conversations/${chat.current.id}`, {
            method: "PATCH",
            body: JSON.stringify(pc ? { screen_control: true, screen_mode: "pc" } : { screen_control: true, screen_allow: allow }),
        });
        $("#screen-dialog").close();
        syncAgentBar();
    } catch (err) {
        $("#screen-dialog-error").textContent = err.message;
        $("#screen-dialog-error").hidden = false;
    }
});

// Serveurs MCP : la puce 🔌 montre ceux dont l'agent voit les outils ici ; la fenêtre permet d'en choisir d'autres
async function loadMcpState() {
    try {
        state.mcp = await api("/api/mcp");
    } catch {
        state.mcp = null;
    }
    syncMcpChip();
}

function mcpActive(conv) {
    const usable = (state.mcp?.servers || []).filter((s) => s.enabled);
    const chosen = Array.isArray(conv?.mcp) ? conv.mcp : null;
    return usable.filter((s) => (chosen ? chosen.includes(s.name) : s.default_active));
}

function syncMcpChip() {
    const chip = $("#btn-mcp");
    const usable = (state.mcp?.servers || []).filter((s) => s.enabled);
    chip.hidden = !(usable.length && chat.current);
    if (chip.hidden) return;
    const active = mcpActive(chat.current);
    // Un serveur en erreur reste coché, mais ses outils manquent à l'agent : ⚠ le signale
    chip.textContent = active.length
        ? `🔌 ${active.map((s) => s.name + (s.state === "erreur" ? " ⚠" : "")).join(", ")}` : "🔌 MCP";
    chip.title = active.length
        ? `Outils MCP proposés à l'agent : ${active.map((s) => s.name).join(", ")}.\nClique pour en choisir d'autres.`
        : "Aucun serveur MCP dans cette conversation : clique pour en choisir.";
    chip.classList.toggle("attached", active.length > 0);
}

async function openMcpPicker() {
    if (!chat.current) return;
    await loadMcpState();
    const active = new Set(mcpActive(chat.current).map((s) => s.name));
    const total = () => {
        const sum = [...$("#mcp-choices").querySelectorAll("input:checked")]
            .reduce((n, b) => n + Number(b.dataset.tokens || 0), 0);
        $("#mcp-total").textContent = `Contexte pris par les outils cochés : ≈ ${sum} tokens.`;
    };
    $("#mcp-dialog-error").hidden = true;
    $("#mcp-choices").replaceChildren(...(state.mcp?.servers || []).filter((s) => s.enabled).map((s) => {
        const box = el("input", { type: "checkbox", checked: active.has(s.name) });
        box.dataset.name = s.name;
        box.dataset.tokens = s.tokens;
        box.addEventListener("change", total);
        const what = s.state === "pret" ? `${s.tools.length} outil${s.tools.length > 1 ? "s" : ""} · ≈ ${s.tokens} tokens`
            : (s.state === "erreur" ? "🔴 en erreur (voir les Réglages)" : "pas encore prêt");
        return el("li", { className: "static" },
            el("label", { className: "check-line" }, box,
                el("div", { className: "skill-text" },
                    el("div", { className: "skill-name" }, s.name, " ", s.default_active ? el("span", { className: "badge" }, "d'office") : ""),
                    el("div", { className: "skill-desc" }, what))));
    }));
    total();
    $("#mcp-dialog").showModal();
}

async function applyMcpChoice(body) {
    try {
        chat.current = await api(`/api/conversations/${chat.current.id}`, { method: "PATCH", body: JSON.stringify(body) });
        $("#mcp-dialog").close();
        syncMcpChip();
        refreshContext();
    } catch (err) {
        $("#mcp-dialog-error").textContent = err.message;
        $("#mcp-dialog-error").hidden = false;
    }
}

$("#btn-mcp").addEventListener("click", openMcpPicker);
$("#btn-mcp-close").addEventListener("click", () => $("#mcp-dialog").close());
$("#btn-mcp-settings").addEventListener("click", () => $("#mcp-dialog").close());
$("#btn-mcp-apply").addEventListener("click", () => applyMcpChoice({
    mcp: [...$("#mcp-choices").querySelectorAll("input:checked")].map((b) => b.dataset.name),
}));
$("#btn-mcp-default").addEventListener("click", () => applyMcpChoice({ mcp_default: true }));
loadMcpState();

const TODO_MARKS = { pending: "☐", in_progress: "▶", completed: "☑" };

// Plan de la tâche en cours, sous la conversation : ✓ fait, roue en cours, cercle pointillé à venir.
// Un plan d'une tâche précédente ne s'affiche plus (il reste dans l'onglet Plan du panneau latéral).
function currentPlan() {
    const todo = chat.current?.todo;
    if (!todo?.items?.length) return null;
    return todo.task_id && todo.task_id !== chat.current.task_id ? null : todo.items;
}

function renderPlanCard({ reset = false } = {}) {
    const card = $("#plan-card");
    const items = currentPlan();
    card.hidden = !items;
    if (!items) return;
    const done = items.filter((i) => i.status === "completed").length;
    const current = items.find((i) => i.status === "in_progress");
    const plural = items.length > 1 ? "s" : "";
    card.querySelector("summary").textContent = done === items.length
        ? `✓ Plan terminé : ${items.length} étape${plural}`
        : `Plan : ${done}/${items.length} étape${plural}${current ? ` · ${current.content}` : ""}`;
    card.querySelector("ul").replaceChildren(...items.map((i) => el("li", { className: `step ${i.status}` },
        el("span", { className: "step-mark", ariaHidden: "true" }), el("span", {}, i.content))));
    // Ouvert pendant la tâche, replié une fois tout fait ; entre les deux, ton dernier choix est gardé
    card.classList.toggle("running", Boolean(chat.controller));  // la roue ne tourne que si l'agent travaille
    if (chat.controller) card.open = true;
    else if (reset) card.open = done < items.length;
}

function renderTodo(items) {
    // Onglet Plan du panneau latéral
    const list = $("#agent-todo");
    list.hidden = !items?.length;
    $("#side-plan-empty").hidden = Boolean(items?.length);
    list.replaceChildren(...(items || []).map((it) => el("li", { className: it.status }, `${TODO_MARKS[it.status] || "☐"} ${it.content}`)));
}

// --- Panneau latéral (façon Claude Code) ----------------------------------------------

const side = { tab: "changes", active: 0, running: 0 };

function sidePref(key, value) {
    // Préférence d'affichage (ouvert / onglet), propre à ce navigateur : rien d'important n'y est stocké
    try {
        if (value === undefined) return localStorage.getItem(key);
        localStorage.setItem(key, value);
    } catch {
        return null;
    }
    return value;
}

function sideOpen() {
    const stored = sidePref("aria-side-open");
    return stored === null ? Boolean(agentSettings().enabled) : stored === "1";
}

function syncSidePanel() {
    const open = sideOpen();
    $("#side-panel").hidden = !open;
    $("#view-chat").classList.toggle("with-side", open);
    $("#btn-side").classList.toggle("active", open);
    const tab = sidePref("aria-side-tab") || side.tab;
    for (const b of document.querySelectorAll(".side-tab")) b.classList.toggle("active", b.dataset.tab === tab);
    for (const body of document.querySelectorAll(".side-body")) body.hidden = body.dataset.tab !== tab;
    $("#side-changes-count").textContent = side.active ? String(side.active) : "";
    $("#side-bg-count").textContent = side.running ? String(side.running) : "";
    const counts = [];
    if (side.active) counts.push(`${side.active} modif${side.active > 1 ? "s" : ""}`);
    if (side.running) counts.push(`⚙ ${side.running}`);
    $("#btn-side").textContent = open || !counts.length ? "▤" : `▤ ${counts.join(" · ")}`;
}

$("#btn-side").addEventListener("click", () => {
    sidePref("aria-side-open", sideOpen() ? "0" : "1");
    syncSidePanel();
});
$("#btn-side-close").addEventListener("click", () => {
    sidePref("aria-side-open", "0");
    syncSidePanel();
});
for (const button of document.querySelectorAll(".side-tab")) {
    button.addEventListener("click", () => {
        sidePref("aria-side-tab", button.dataset.tab);
        syncSidePanel();
        if (button.dataset.tab === "background") refreshBackground();
        if (button.dataset.tab === "context") refreshContext();
    });
}

let changesTimer = null;

function refreshChangesSoon() {
    // Pendant une tâche : une seule mise à jour pour plusieurs écritures rapprochées
    clearTimeout(changesTimer);
    changesTimer = setTimeout(refreshChanges, 400);
}

async function refreshChanges() {
    const box = $("#side-changes");
    if (!chat.current) {
        side.active = 0;
        box.replaceChildren(el("p", { className: "muted small" }, "Aucune conversation ouverte."));
        syncSidePanel();
        return;
    }
    let data;
    try {
        data = await api(`/api/agent/changes?conversation_id=${chat.current.id}`);
    } catch {
        return;
    }
    side.active = data.active;
    if (!data.tasks.length) {
        box.replaceChildren(el("p", { className: "muted small" },
            "Aucune modification de fichier dans cette conversation. Chaque écriture de l'agent apparaîtra ici avec son diff."));
        syncSidePanel();
        return;
    }
    // La tâche la plus récente en haut
    const tasks = [...data.tasks].reverse();
    box.replaceChildren(...tasks.map((task, i) => changeTaskNode(task, tasks.length - i)));
    syncSidePanel();
}

function changeTaskNode(task, number) {
    const head = el("div", { className: "change-task-head" }, `Tâche ${number}`);
    const live = task.changes.filter((c) => !c.undone).length;
    if (task.undone) {
        head.append(el("span", { className: "muted small" }, "↩ annulée"));
    } else if (live && task.task_id !== "sans-tache") {
        const button = el("button", { className: "ghost" }, "↩ Annuler toute la tâche");
        button.addEventListener("click", () => undoTask(task.task_id, button));
        head.append(button);
    }
    return el("div", { className: "change-task" }, head, ...task.changes.map((c, i) => changeNode(c, i === task.changes.length - 1)));
}

function changeNode(change, last) {
    const marks = [change.tool];
    if (change.created) marks.push("créé");
    if (change.syntax === true) marks.push("✓ syntaxe");
    if (change.syntax === false) marks.push("⚠ syntaxe");
    if (change.undone) marks.push("annulée");
    const node = el("details", { className: `change${change.undone ? " undone" : ""}`, open: last && !change.undone },
        el("summary", {}, `${change.path} · ${marks.join(" · ")}`), diffNode(change.diff, "write"));
    const actions = el("div", { className: "change-actions" });
    if (change.note) actions.append(el("span", { className: "muted" }, change.note));
    if (!change.undone && change.tool !== "sous-agent") {
        const undo = el("button", { className: "ghost" }, "↩ Annuler cette modif");
        undo.addEventListener("click", () => undoChange(change.backup_id, undo));
        actions.append(undo);
    }
    if (actions.children.length) node.append(actions);
    return node;
}

async function saveAgent(patch) {
    $("#chat-error").hidden = true;
    const next = { ...agentSettings(), ...patch };
    try {
        if (chat.current) {
            chat.current = await api(`/api/conversations/${chat.current.id}`, {
                method: "PATCH",
                body: JSON.stringify({ agent: { enabled: next.enabled, workspace: next.workspace || null } }),
            });
        } else {
            if (next.enabled && !next.workspace) throw new Error("Choisis d'abord un dossier de travail.");
            chat.pendingAgent = next;
        }
    } catch (err) {
        showChatError(err.message);
    }
    syncAgentBar();
    refreshHooksBanner();
}

$("#btn-folder").addEventListener("click", async () => {
    const chip = $("#btn-folder");
    const a = agentSettings();
    $("#chat-error").hidden = true;
    chip.disabled = true;
    chip.textContent = "📂 Fenêtre de choix ouverte…";
    let path = null;
    try {
        path = await pickFolder(a.workspace || state.config?.new_conversation?.workspace || "",
            "Dossier de travail de la conversation");
    } catch (err) {
        if (err.status === 501) chat.manualFolder = true;  // plan B : la saisie du chemin apparaît
        showChatError(err.message);
    } finally {
        chip.disabled = false;
    }
    if (path) await saveAgent({ enabled: true, workspace: path });
    else syncAgentBar();
});

$("#btn-agent-root").addEventListener("click", () => {
    const root = $("#agent-root").value.trim();
    if (!root) {
        showChatError("Indique d'abord le dossier à attacher.");
        return;
    }
    saveAgent({ enabled: true, workspace: root });
});

$("#btn-agent-detach").addEventListener("click", () => saveAgent({ enabled: false }));

// --- Conversations -----------------------------------------------------------

async function loadConversations() {
    chat.conversations = await api("/api/conversations");
    const list = $("#conv-list");
    list.replaceChildren();
    for (const c of chat.conversations) {
        const del = el("button", { className: "del", title: "Supprimer" }, "×");
        del.addEventListener("click", async (event) => {
            event.stopPropagation();
            if (!confirm(`Supprimer « ${c.title} » ? C'est définitif.`)) return;
            await api(`/api/conversations/${c.id}`, { method: "DELETE" });
            if (chat.current?.id === c.id) openConversation(null);
            loadConversations();
        });
        const item = el(
            "li",
            { className: c.id === chat.current?.id ? "selected" : "", title: c.title },
            el("span", { className: "conv-title" }, `${c.prive ? "🕶 " : ""}${c.title}`),
            del
        );
        item.addEventListener("click", () => openConversation(c.id));
        list.append(item);
    }
}

async function openConversation(id) {
    if (chat.controller) return;
    if (id !== chat.current?.id) chat.memoryLoaded = false;
    chat.current = id ? await api(`/api/conversations/${id}`) : null;
    if (!id) {
        resetPendingAgent();
        chat.pendingPrivate = false;
    }
    $("#conv-title").textContent = chat.current?.title || "Nouvelle conversation";
    $("#btn-rename").hidden = !chat.current;
    if (chat.personas) {
        const pid = chat.current ? (chat.current.persona_id || chat.personas.default) : chat.personas.default;
        $("#persona-select").value = pid === "none" || chat.personas.personas.some((p) => p.id === pid) ? pid : chat.personas.default;
        $("#persona-select").dataset.value = $("#persona-select").value;
    }
    if (chat.current?.preset_id && chat.presets.some((p) => p.id === chat.current.preset_id)) {
        $("#preset-select").value = chat.current.preset_id;
        $("#preset-select").dataset.value = chat.current.preset_id;
    }
    $("#chat-error").hidden = true;
    syncAgentBar();
    syncPrivateChip();
    renderGauge(chat.current?.context);
    renderMessages();
    loadConversations();
    refreshHooksBanner();
    refreshBackground();
    syncSidePanel();
    refreshChanges();
    refreshContext();
}

// --- Compétences -------------------------------------------------------------------

const SKILL_SOURCES = { "intégrée": "intégrée", perso: "perso", projet: "projet" };

function skillsQuery() {
    return chat.current ? `?conversation_id=${chat.current.id}` : "";
}

async function openSkills() {
    const list = $("#skills-list");
    list.replaceChildren(el("li", { className: "muted" }, "Chargement…"));
    $("#skills-dialog").showModal();
    const items = await api(`/api/skills${skillsQuery()}`);
    list.replaceChildren();
    for (const s of items) {
        const text = el("div", { className: "skill-text" },
            el("div", { className: "skill-name" }, `/${s.name}`),
            el("div", { className: "skill-desc" }, s.description));
        const item = el("li", { title: "Ajouter à mon message" }, text, el("span", { className: "badge" }, SKILL_SOURCES[s.source] || s.source));
        item.addEventListener("click", () => {
            const input = $("#chat-input");
            input.value = `/${s.name} ${input.value.replace(/^\/[a-z0-9-]+\s*/, "")}`;
            $("#skills-dialog").close();
            input.focus();
        });
        list.append(item);
    }
    if (!items.length) list.append(el("li", { className: "muted" }, "Aucune compétence."));
}

$("#btn-skills").addEventListener("click", openSkills);
$("#btn-skills-close").addEventListener("click", () => $("#skills-dialog").close());
$("#btn-skills-manage").addEventListener("click", () => $("#skills-dialog").close());

// Règles du projet : la section Autorisations des réglages, ouverte sur ce dossier de travail
$("#btn-rules").addEventListener("click", () => {
    if (!chat.current) return;
    state.rulesFocus = chat.current.agent?.workspace || null;
    location.hash = "#/reglages/autorisations";
});

// --- Hooks ---------------------------------------------------------------------------

const HOOK_LABELS = { avant_outil: "avant l'outil", apres_outil: "après l'outil", debut_tache: "début de tâche", fin_tache: "fin de tâche" };

function hookNote(ev) {
    const lines = ev.resultats.map((r) => `${r.source} · code ${r.code}${r.sortie ? " · " + r.sortie : ""}`);
    const title = ev.bloque ? `⚓ Action bloquée par un hook (${HOOK_LABELS[ev.evenement]})` : `⚓ Hook ${HOOK_LABELS[ev.evenement]}`;
    return el("details", { className: `hook-note${ev.bloque ? " blocked" : ""}`, open: ev.bloque },
        el("summary", {}, title), el("pre", {}, lines.join("\n")));
}

function hooksTrustNode(ev) {
    const box = el("div", { className: "approval hooks-trust" });
    const button = el("button", { className: "primary" }, "Approuver ces hooks");
    button.addEventListener("click", async () => {
        button.disabled = true;
        try {
            await api("/api/hooks/trust", { method: "POST", body: JSON.stringify({ conversation_id: chat.current.id }) });
            box.replaceChildren(el("span", { className: "muted small" }, "Hooks approuvés ✓ : ils tourneront à partir de la prochaine tâche."));
            setTimeout(refreshHooksBanner, 4000);
        } catch (err) {
            button.disabled = false;
            showChatError(err.message);
        }
    });
    box.append(
        el("div", { className: "approval-title" }, "Ce projet contient des hooks : ils ne tournent pas tant que tu ne les as pas approuvés."),
        el("p", { className: "muted small" }, `${ev.path}${ev.error ? " — " + ev.error : ""}`),
        el("pre", { className: "diff" }, ev.content),
    );
    if (!ev.error) box.append(el("div", { className: "row" }, button));
    return box;
}

async function refreshBackground() {
    // Onglet Arrière-plan : commandes qui tournent (sortie en direct, bouton Arrêter) puis les dernières terminées
    const box = $("#side-background");
    let list = [];
    if (chat.current) {
        try {
            list = await api(`/api/agent/background?conversation_id=${chat.current.id}&all=true&output=true`);
        } catch {
            return;
        }
    }
    side.running = list.filter((c) => c.running).length;
    if (!list.length) {
        box.replaceChildren(el("p", { className: "muted small" },
            "Aucune commande en arrière-plan. Quand l'agent lance un serveur ou une commande longue, sa sortie défile ici."));
        syncSidePanel();
        return;
    }
    // Garde la position de lecture de chaque sortie : collée en bas si tu y étais
    const stuck = {};
    for (const pre of box.querySelectorAll(".bg-output")) {
        stuck[pre.dataset.id] = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
    }
    box.replaceChildren(...list.map((c) => {
        const head = el("div", { className: "bg-head" }, el("strong", {}, c.id), el("code", {}, c.command.slice(0, 70)),
            el("span", { className: "muted" }, `${c.state}${c.keep ? " · gardée" : ""}`));
        if (c.running) {
            const stop = el("button", { className: "ghost danger" }, "Arrêter");
            stop.addEventListener("click", async () => {
                stop.disabled = true;
                try {
                    await api("/api/agent/background/stop", {
                        method: "POST", body: JSON.stringify({ conversation_id: chat.current.id, id: c.id }),
                    });
                } catch (err) {
                    showChatError(err.message);
                }
                refreshBackground();
            });
            head.append(stop);
        }
        const lines = (c.output || "").trimEnd().split("\n").slice(-200).join("\n");
        const pre = el("pre", { className: "bg-output" }, lines || "(pas encore de sortie)");
        pre.dataset.id = c.id;
        return el("div", { className: `bg-item${c.running ? " running" : ""}` }, head, pre);
    }));
    for (const pre of box.querySelectorAll(".bg-output")) {
        if (stuck[pre.dataset.id] !== false) pre.scrollTop = pre.scrollHeight;
    }
    syncSidePanel();
}

// Sortie en direct : seulement si le panneau est ouvert sur cet onglet et qu'une commande tourne
setInterval(() => {
    const tab = sidePref("aria-side-tab") || side.tab;
    if (chat.current && side.running && sideOpen() && tab === "background") refreshBackground();
}, 2000);

// --- Onglet Contexte ----------------------------------------------------------------------

async function refreshContext() {
    const box = $("#side-context");
    if (!chat.current) {
        box.replaceChildren(el("p", { className: "muted small" }, "Aucune conversation ouverte."));
        return;
    }
    let c;
    try {
        c = await api(`/api/agent/context?conversation_id=${chat.current.id}`);
    } catch {
        return;
    }
    const row = (label, value) => el("div", { className: "ctx-row" }, el("span", { className: "muted" }, label), el("span", {}, value));
    const section = (title, ...children) => el("div", { className: "ctx-section" }, title, ...children);
    const titled = (text, manage) => {
        const h = el("h4", {}, text);
        if (manage) {
            const b = el("button", { className: "ghost" }, "Gérer");
            b.addEventListener("click", manage);
            h.append(b);
        }
        return h;
    };
    const parts = [];
    // Occupation
    const u = c.usage;
    if (u.max) {
        const pct = Math.min(100, (100 * u.used) / u.max);
        const bar = el("div", { className: `ctx-big${pct >= 85 ? " full" : pct >= 60 ? " high" : ""}` }, el("span", { style: `width:${pct}%` }));
        parts.push(section(titled("Occupation"), bar,
            row("Utilisé", `${u.used.toLocaleString("fr-FR")} / ${u.max.toLocaleString("fr-FR")} tokens (${Math.round(pct)} %)`),
            row("Anciens échanges résumés", u.summarized ? `${u.summarized} messages` : "aucun"),
            row("Vieux résultats d'outils", u.squeeze ? `raccourcis (niveau ${u.squeeze})` : "entiers")));
    } else {
        parts.push(section(titled("Occupation"), el("span", { className: "muted small" }, "Mesurée après la première réponse du modèle.")));
    }
    // Modèle
    const m = c.model;
    parts.push(section(titled("Modèle"), ...(m ? [row("Modèle", m.name), row("Moteur", m.engine || "—"),
        row("Contexte chargé", m.ctx_size ? `${Math.round(m.ctx_size / 1024)}k tokens` : "—"),
        row("Cache de contexte", m.cache_type === "f16" ? "normal" : `compressé (${m.cache_type})`),
        row("Couches GPU", m.gpu_layers >= 99 ? "toutes" : String(m.gpu_layers ?? "—"))]
        : [el("span", { className: "muted small" }, "Aucun modèle chargé : choisis-en un en haut.")])));
    // Agent
    const a = c.agent;
    const perms = [a.auto_write ? "modifs autorisées" : "modifs demandées", a.auto_web ? "web autorisé" : "web demandé", "commandes demandées"];
    const planLabels = { required: "🔒 plan exigé avant toute modification", pending: "🔒 plan en attente de ta décision" };
    parts.push(section(titled("Agent"), ...(a.enabled
        ? [row("Dossier", a.workspace || "—"), ...(a.plan ? [row("Plan", planLabels[a.plan])] : []),
            row("Accords", perms.join(" · ")), row("Outils", String(a.tools))]
        : [el("span", { className: "muted small" }, `Discussion : aucun dossier attaché (${a.tools} outils, sans accès aux fichiers).`)])));
    // Ce que l'agent lit
    const chips = (items, label) => items.length
        ? el("div", { className: "ctx-chips" }, ...items.map((i) => el("span", { title: i.source || "" }, label(i))))
        : el("span", { className: "muted small" }, "aucun");
    const registerLabels = { naturel: "naturel", sobre: "sobre", professionnel: "professionnel" };
    parts.push(section(titled("Persona", () => { location.hash = "#/reglages/persona"; }), c.persona
        ? row(`${c.persona.avatar} ${c.persona.name}`.trim(), `registre ${registerLabels[c.persona.register]} en discussion`)
        : el("span", { className: "muted small" }, "Sans persona : un assistant neutre.")));
    const approx = (chars) => `≈ ${Math.round(chars / 4).toLocaleString("fr-FR")} tokens`;
    parts.push(section(titled("Ce que lit le modèle, par couche"),
        row("Persona", approx(c.layers.persona)), row("Fiche Toi", approx(c.layers.profil)),
        row("Aria Pilote (règles, outils, compétences)", approx(c.layers.aria)), row("Mémoire du projet", approx(c.layers.memoire)),
        row("Souvenirs", approx(c.layers.souvenirs))));
    const sv = c.souvenirs;
    parts.push(section(titled("Souvenirs", () => { location.hash = "#/reglages/general"; }),
        !sv.enabled ? el("span", { className: "muted small" }, "Coupés dans les Réglages : rien n'est retenu ni utilisé.")
            : sv.prive ? el("span", { className: "muted small" }, "🕶 Conversation privée : rien n'y est retenu, aucun souvenir n'y sert.")
                : row("Glissés dans le prompt", `${sv.injected} sur ${sv.total}`)));
    const memoryParts = [];
    if (c.memory) {
        memoryParts.push(row(c.memory.file, `${c.memory.chars.toLocaleString("fr-FR")} caractères, lus à chaque tâche`));
        if (c.memory.legacy) {
            const rename = el("button", { className: "ghost" }, "Renommer en MEMOIRE.md");
            rename.addEventListener("click", async () => {
                try {
                    await api("/api/agent/memory/rename", { method: "POST", body: JSON.stringify({ conversation_id: chat.current.id }) });
                    refreshContext();
                } catch (err) {
                    showChatError(err.message);
                }
            });
            memoryParts.push(el("div", { className: "ctx-row" },
                el("span", { className: "muted small" }, "Ancien nom : la mémoire du projet s'appelle désormais MEMOIRE.md."), rename));
        }
    } else {
        memoryParts.push(el("span", { className: "muted small" }, "Pas de fichier MEMOIRE.md dans le dossier de travail."));
    }
    parts.push(section(titled("Mémoire du projet"), ...memoryParts));
    parts.push(section(titled("Compétences", openSkills), chips(c.skills, (s) => `/${s.name}`)));
    parts.push(section(titled("Sous-agents"), chips(c.subagents, (s) => `${s.name} (${s.tools.length} outils)`)));
    if (c.mcp?.length) {
        parts.push(section(titled("Serveurs MCP", openMcpPicker),
            chips(c.mcp, (s) => `🔌 ${s.name} (${s.tools} outils, ≈ ${s.tokens} tokens)`)));
    }
    const globalHooks = Object.entries(c.hooks.global).map(([event, n]) => `${event} : ${n}`).join(" · ");
    const project = c.hooks.project;
    parts.push(section(titled("Hooks", () => { location.hash = "#/reglages/hooks"; }),
        row("Globaux", globalHooks || "aucun"),
        row("Du projet", project ? (project.error ? `⚠ ${project.error}` : project.trusted ? "approuvés" : "⚠ en attente d'approbation") : "aucun")));
    parts.push(section(titled("Commandes toujours autorisées", a.enabled && a.workspace ? () => $("#btn-rules").click() : null),
        c.rules.length ? el("div", { className: "ctx-chips" }, ...c.rules.map((r) => el("span", {}, r))) : el("span", { className: "muted small" }, "aucune")));
    box.replaceChildren(...parts);
}

async function refreshHooksBanner() {
    // Bandeau permanent tant que les hooks du projet ne sont pas approuvés
    const banner = $("#hooks-banner");
    const agentOn = chat.current?.agent?.enabled;
    const project = agentOn ? (await api(`/api/hooks?conversation_id=${chat.current.id}`)).project : null;
    banner.hidden = !project || project.trusted;
    banner.replaceChildren(...(project && !project.trusted ? [hooksTrustNode(project)] : []));
}

// --- Contexte ----------------------------------------------------------------------

// Alerte à 90 % du seuil du compactage automatique (et non du contexte : selon le preset, Aria résume dès ~75 %)
const CTX_ALERT_RATIO = 0.9;

function renderGauge(ctx) {
    const gauge = $("#ctx-gauge");
    gauge.hidden = !ctx?.max;
    $("#btn-compact").hidden = !ctx?.max || !chat.current;
    if (!ctx?.max) {
        $("#ctx-alert").hidden = true;
        return;
    }
    const pct = Math.min(100, (100 * ctx.used) / ctx.max);
    const marker = gauge.querySelector(".ctx-limit");
    const fmt = (n) => n.toLocaleString("fr-FR");
    let ratio = pct / 85;  // anciennes mesures, sans seuil connu
    if (ctx.limit) {
        ratio = ctx.used / ctx.limit;
        marker.style.left = `${Math.min(100, (100 * ctx.limit) / ctx.max)}%`;
        gauge.title = `Contexte occupé : ${fmt(ctx.used)} tokens sur ${fmt(ctx.max)}. Au-delà de ${fmt(ctx.limit)} (le repère), `
            + "Aria résume les anciens échanges avant le prochain message.";
    }
    marker.hidden = !ctx.limit;
    gauge.classList.toggle("high", ratio >= CTX_ALERT_RATIO && ratio < 1);
    gauge.classList.toggle("full", ratio >= 1);
    gauge.querySelector(".ctx-bar span").style.width = `${pct}%`;
    gauge.querySelector(".ctx-text").textContent = `${fmt(ctx.used)} / ${fmt(ctx.max)} tokens`;
    const dismissed = chat.current && chat.ctxAlertDismissed.has(chat.current.id);
    $("#ctx-alert").hidden = !(chat.current && ctx.limit && ratio >= CTX_ALERT_RATIO && !dismissed);
}

// --- Compacter à la demande (bouton 🗜, alerte, « /compact … ») ------------------------------

function openCompact() {
    if (!chat.current || chat.controller) return;
    $("#compact-pop").hidden = false;
    $("#compact-instructions").focus();
}

async function compactNow(instructions = "") {
    if (!chat.current || chat.controller) return;
    $("#chat-error").hidden = true;
    const go = $("#btn-compact-go");
    go.disabled = true;
    $("#btn-compact").disabled = true;
    go.textContent = "Résumé en cours…";
    try {
        const result = await api(`/api/conversations/${chat.current.id}/compact`, {
            method: "POST", body: JSON.stringify({ instructions }),
        });
        $("#compact-pop").hidden = true;
        $("#compact-instructions").value = "";
        chat.ctxAlertDismissed.delete(chat.current.id);
        await openConversation(chat.current.id);  // le séparateur 🗜 et son résumé apparaissent dans le fil
        renderGauge(result);
    } catch (err) {
        showChatError(err.message);
    } finally {
        go.disabled = false;
        $("#btn-compact").disabled = false;
        go.textContent = "Compacter";
    }
}

$("#btn-compact").addEventListener("click", () => ($("#compact-pop").hidden ? openCompact() : ($("#compact-pop").hidden = true)));
$("#btn-compact-cancel").addEventListener("click", () => { $("#compact-pop").hidden = true; });
$("#btn-compact-go").addEventListener("click", () => compactNow($("#compact-instructions").value.trim()));
$("#compact-instructions").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
        event.preventDefault();
        compactNow($("#compact-instructions").value.trim());
    } else if (event.key === "Escape") {
        $("#compact-pop").hidden = true;
    }
});
$("#btn-ctx-compact").addEventListener("click", openCompact);
$("#btn-ctx-new").addEventListener("click", () => $("#btn-new-conv").click());
$("#btn-ctx-close").addEventListener("click", () => {
    if (chat.current) chat.ctxAlertDismissed.add(chat.current.id);
    $("#ctx-alert").hidden = true;
});

function summaryNode(summary, count) {
    const node = el("details", { className: "summary-divider" },
        el("summary", {}, `🗜 Les ${count} messages au-dessus sont résumés pour le modèle (voir le résumé)`));
    const body = el("div", { className: "md" });
    body.innerHTML = renderMarkdown(summary.text || "");
    node.append(body);
    return node;
}

$("#btn-new-conv").addEventListener("click", () => {
    openConversation(null);
    $("#chat-input").focus();
});

$("#btn-rename").addEventListener("click", async () => {
    const title = prompt("Nouveau titre :", chat.current.title);
    if (!title || !title.trim()) return;
    chat.current = await api(`/api/conversations/${chat.current.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title }),
    });
    $("#conv-title").textContent = chat.current.title;
    loadConversations();
});

// --- Affichage des messages ------------------------------------------------

function formatStats(m) {
    const s = m.stats || {};
    const parts = [];
    // La compétence que le modèle a suivie pour cette réponse
    if (m.skill_used && !m.tool_calls) parts.push(`🧩 ${m.skill_used}`);
    if (m.model) parts.push(m.model);
    if (s.completion_tokens) parts.push(`${s.completion_tokens} tokens`);
    if (s.tok_s) parts.push(`${s.tok_s} tok/s`);
    if (s.ttft_s) parts.push(`1er token ${s.ttft_s} s`);
    if (s.finish_reason === "length") parts.push("coupé (max tokens)");
    if (s.stopped) parts.push("interrompu");
    return parts.join(" · ");
}

function messageNode(m) {
    const node = el("div", { className: `msg ${m.role}` });
    if (m.role === "user") {
        if (m.skill) node.append(el("span", { className: "badge skill" }, `📚 ${m.skill}`), " ");
        node.append(m.content);
        const pictures = messageImages(m);
        if (pictures) node.append(pictures);
        const files = messageFiles(m);
        if (files) node.append(files);
        return node;
    }
    node.append(
        el("details", { className: "reasoning" }, el("summary", {}, "Réflexion"), el("div", { className: "md" })),
        el("div", { className: "md body" }),
        el("div", { className: "stats" })
    );
    updateMessageNode(node, m);
    return node;
}

// Images d'un message : celles enregistrées (servies par l'appli), ou celles qui partent à l'instant
function messageImages(m) {
    const sources = m.images?.length && chat.current
        ? m.images.map((i) => `/api/conversations/${chat.current.id}/images/${i.file}`)
        : m.localImages || [];
    if (!sources.length) return null;
    return el("div", { className: "msg-images" }, ...sources.map((src) => {
        const img = el("img", { src, alt: "Image jointe", loading: "lazy" });
        if (!src.startsWith("data:")) img.addEventListener("click", () => window.open(src, "_blank"));
        return img;
    }));
}

// Fichiers joints d'un message : nom, type et taille du texte lu (le contenu part au modèle, pas à l'écran)
function messageFiles(m) {
    const files = m.files?.length ? m.files : m.localFiles || [];
    if (!files.length) return null;
    return el("div", { className: "msg-files" }, ...files.map((f) => {
        const info = f.chars !== undefined
            ? `${f.kind} · ${f.chars.toLocaleString("fr-FR")} caractères${f.truncated ? " (début seulement)" : ""}` : "envoi…";
        return el("span", { className: "file-chip", title: info }, `📄 ${f.name}`, el("span", { className: "muted" }, ` · ${info}`));
    }));
}

function updateMessageNode(node, m) {
    const details = node.querySelector(".reasoning");
    details.hidden = !m.reasoning;
    details.querySelector(".md").innerHTML = renderMarkdown(m.reasoning || "");
    node.querySelector(".body").innerHTML = renderMarkdown(m.content || "");
    node.querySelector(".body").hidden = !m.content && Boolean(m.reasoning);
    const stats = formatStats(m);
    node.querySelector(".stats").textContent = stats;
    node.querySelector(".stats").hidden = !stats;
}

const TOOL_STATUS = {
    running: "en cours…", ok: "ok", error: "erreur", denied: "refusé",
    pending: "en attente de ta réponse", skipped: "après ta réponse", unanswered: "sans réponse",
};

function shortText(text) {
    return text.length > 70 ? text.slice(0, 69) + "…" : text;
}

function shortArgs(args) {
    return shortText(String(args?.path ?? args?.pattern ?? args?.command ?? args?.query ?? args?.url ?? args?.name ?? args?.id ?? ""));
}

function subStepNode(step) {
    return el("li", { className: `sub-step ${step.status}` },
        `${step.name}  ${shortArgs(step.args)} · ${TOOL_STATUS[step.status] || step.status}`);
}

function subagentNode(m) {
    // Délégation à un sous-agent : ses étapes, puis son rapport
    const node = el("details", { className: `tool subagent ${m.status}` });
    const task = String(m.args?.task || "");
    node.append(el("summary", {}, `🤖 ${m.args?.agent || "sous-agent"} : ${task.length > 70 ? task.slice(0, 69) + "…" : task} · ${TOOL_STATUS[m.status] || m.status}`));
    const steps = el("ul", { className: "sub-steps" }, ...(m.subagent?.steps || []).map(subStepNode));
    node.append(steps);
    if (m.content) {
        const report = el("div", { className: "md sub-report" });
        report.innerHTML = renderMarkdown(m.content);
        node.append(report);
    }
    return node;
}

function planActions() {
    const approve = el("button", { className: "primary" }, "✅ Approuver et exécuter");
    approve.addEventListener("click", async () => {
        approve.disabled = true;
        try {
            chat.current = await api("/api/agent/plan/approve", { method: "POST", body: JSON.stringify({ conversation_id: chat.current.id }) });
        } catch (err) {
            showChatError(err.message);
            approve.disabled = false;
            return;
        }
        send("Le plan est approuvé : exécute-le étape par étape, en tenant ton plan à jour.", undefined, true);
    });
    const change = el("button", { className: "ghost" }, "✏️ Demander des changements");
    change.addEventListener("click", () => {
        const input = $("#chat-input");
        input.value = "Modifie le plan : ";
        input.focus();
    });
    const cancel = el("button", { className: "ghost danger-text" }, "Abandonner le plan");
    cancel.addEventListener("click", async () => {
        try {
            chat.current = await api("/api/agent/plan/cancel", { method: "POST", body: JSON.stringify({ conversation_id: chat.current.id }) });
        } catch (err) {
            showChatError(err.message);
            return;
        }
        syncAgentBar();
        renderMessages();
    });
    return el("div", { className: "row" }, approve, change, cancel);
}

function planNode(m, pending) {
    // Mode Plan : le plan proposé, avec les boutons tant qu'il attend ta décision
    const node = el("div", { className: "plan-card" }, el("div", { className: "approval-title" }, "🗺️ Plan proposé"));
    const body = el("div", { className: "md" });
    body.innerHTML = renderMarkdown(m.args?.plan || "");
    node.append(body);
    if (pending) node.append(planActions());
    return node;
}

function pendingQuestionIndex(messages) {
    // Question d'ask_user qui attend ta réponse : rien de ta part depuis (même règle que le serveur)
    for (let i = messages.length - 1; i >= 0; i--) {
        const m = messages[i];
        if (m.role === "user") return -1;
        if (m.role === "tool" && m.name === "ask_user" && m.status === "pending") return i;
    }
    return -1;
}

function answerQuestion(answer) {
    if (chat.controller) return;
    send(null, answer);
}

function questionNode(m, active) {
    const node = el("div", { className: "question-card" }, el("div", { className: "approval-title" }, "❓ Question de l'agent"));
    const body = el("div", { className: "md" });
    body.innerHTML = renderMarkdown(m.args?.question || "");
    node.append(body);
    const options = Array.isArray(m.args?.options) ? m.args.options : [];
    if (m.status === "pending" && active) {
        const buttons = el("div", { className: "row" });
        for (const option of options) {
            const button = el("button", { className: "primary" }, option);
            button.addEventListener("click", () => answerQuestion(option));
            buttons.append(button);
        }
        const free = el("button", { className: "ghost" }, "✏️ Autre réponse");
        free.addEventListener("click", () => $("#chat-input").focus());
        const decide = el("button", { className: "ghost" }, "🎲 Décide toi-même");
        decide.addEventListener("click", () => answerQuestion(""));
        buttons.append(free, decide);
        node.append(buttons, el("div", { className: "muted small" }, "Tu peux aussi taper ta réponse dans la zone de message."));
    } else if (m.status === "ok") {
        node.append(el("div", { className: "question-answer" }, m.answer ? `↳ Ta réponse : ${m.answer}` : "↳ Tu as laissé l'agent décider."));
    } else if (m.status !== "pending") {
        node.append(el("div", { className: "question-answer muted" }, m.status === "unanswered" ? "↳ Restée sans réponse." : m.content));
    }
    return node;
}

function toolNode(m, pendingPlan = false, activeQuestion = false) {
    if (m.name === "propose_plan") return planNode(m, pendingPlan);
    if (m.name === "ask_user" && m.status !== "running" && m.status !== "error") return questionNode(m, activeQuestion);
    if (m.name === "delegate") return subagentNode(m);
    if (m.souvenir && m.status === "ok") return souvenirNode(m);
    const node = el("details", { className: `tool ${m.status}` });
    node.dataset.callId = m.tool_call_id || "";
    const bg = m.args?.background ? (m.args.keep ? "⚙ arrière-plan, gardée · " : "⚙ arrière-plan · ") : "";
    const syntax = m.syntax === false ? " · ⚠ syntaxe" : m.syntax === true ? " · ✓ syntaxe" : "";
    const mcp = m.name?.startsWith("mcp__") ? m.name.slice(5).split("__") : null;
    const rule = mcp && m.rule ? " · ✓ autorisé d'office"
        : m.name === "agir_ecran" && m.rule ? ` · ✓ ${m.rule}`
            : m.rule ? ` · ✓ autorisée par la règle « ${m.rule} »` : "";
    const label = mcp ? `🔌 ${mcp[0]} › ${mcp.slice(1).join("__")}` : `🔧 ${m.name}`;
    // Un outil MCP a ses propres arguments : les deux premiers, en bref
    const brief = mcp ? shortText(Object.entries(m.args || {}).slice(0, 2)
        .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(", ")) : shortArgs(m.args);
    node.append(el("summary", {}, `${label}  ${brief} · ${bg}${TOOL_STATUS[m.status] || m.status}${syntax}${rule}`));
    if (m.syntax === false) node.classList.add("syntax-error");
    node.append(el("pre", {}, JSON.stringify(m.args, null, 2)));
    if (m.content) node.append(el("pre", {}, m.content));
    const shots = messageImages(m);  // capture de voir_ecran, avec ses repères numérotés
    if (shots) node.append(shots);
    if (m.backup_id) {
        const actions = el("div", { className: "tool-actions" });
        if (m.undone) {
            actions.append(el("span", { className: "muted small" }, "Modification annulée ↩"));
        } else {
            const undo = el("button", { className: "ghost" }, "↩ Annuler cette modif");
            undo.addEventListener("click", () => undoChange(m.backup_id, undo));
            actions.append(undo);
        }
        node.append(actions);
    }
    return node;
}

function diffNode(text, kind) {
    const pre = el("pre", { className: "diff" });
    if (kind === "command" || kind === "screen_action" || kind === "mcp" || kind === "memory") {
        pre.textContent = text;
        return pre;
    }
    for (const line of text.split("\n")) {
        let cls = "";
        if (line.startsWith("+") && !line.startsWith("+++")) cls = "add";
        else if (line.startsWith("-") && !line.startsWith("---")) cls = "del";
        else if (line.startsWith("@@")) cls = "hunk";
        pre.append(el("span", { className: cls }, line + "\n"));
    }
    return pre;
}

function approvalNode(ev) {
    const box = el("div", { className: "approval" });
    const title = {
        command: "L'agent veut exécuter cette commande PowerShell :",
        web: "L'agent veut accéder au web :",
        write: `L'agent veut modifier ${ev.path} :`,
        screen_action: "🖱 L'agent veut agir sur ton écran (une action à l'écran ne s'annule pas) :",
        mcp: "🔌 L'agent veut utiliser un outil MCP :",
        memory: "🧠 L'agent veut toucher à ses souvenirs :",
    }[ev.kind];
    const buttons = el("div", { className: "row" });
    const decide = async (decision, label, rule = null) => {
        for (const b of box.querySelectorAll("button")) b.disabled = true;
        try {
            await api("/api/agent/approve", {
                method: "POST",
                body: JSON.stringify({ approval_id: ev.approval_id, decision, rule }),
            });
            buttons.replaceChildren(el("span", { className: "muted small" }, label));
            ruleBox?.remove();
        } catch (err) {
            for (const b of box.querySelectorAll("button")) b.disabled = false;
            showChatError(err.message);
        }
    };
    let ruleBox = null;
    const allow = el("button", { className: "primary" }, "Autoriser");
    allow.addEventListener("click", () => decide("allow", "Autorisé ✓"));
    buttons.append(allow);
    if (ev.kind === "write" || ev.kind === "web") {
        const label = ev.kind === "write" ? "Autoriser les modifs pour cette conversation" : "Autoriser le web pour cette conversation";
        const session = el("button", { className: "ghost" }, label);
        session.addEventListener("click", () => decide("allow_session", "Autorisé pour cette conversation ✓"));
        buttons.append(session);
    }
    if (ev.kind === "screen_action" && ev.ordinary) {
        // Clics, frappe, défilement : sans redemander jusqu'à la fin de la tâche (jamais les boutons sensibles,
        // raccourcis à risque ou ouvertures d'appli, qui redemandent toujours)
        const task = el("button", { className: "ghost", title: "Les boutons sensibles, raccourcis à risque et ouvertures d'appli te seront toujours demandés" },
            "Autoriser les actions ordinaires pour cette tâche");
        task.addEventListener("click", () => decide("allow_task", "Autorisé, ainsi que les actions ordinaires de cette tâche ✓"));
        buttons.append(task);
    }
    if (ev.kind === "command" && ev.command) {
        // « Toujours autoriser dans ce projet » : la commande exacte, ou un début de commande (modifiable)
        const always = el("button", { className: "ghost" }, "Toujours autoriser dans ce projet ▾");
        always.addEventListener("click", () => {
            if (ruleBox) { ruleBox.hidden = !ruleBox.hidden; return; }
            const name = `regle-${ev.approval_id}`;
            const exact = el("input", { type: "radio", name, checked: true });
            const prefixRadio = el("input", { type: "radio", name });
            const prefix = el("input", { className: "mono", value: ev.rule_prefix || "" });
            prefix.addEventListener("focus", () => { prefixRadio.checked = true; });
            const confirmRule = el("button", { className: "primary" }, "Autoriser et retenir");
            confirmRule.addEventListener("click", () => {
                const rule = exact.checked ? ev.command : prefix.value.trim();
                decide("allow_rule", `Autorisé, règle retenue pour ce projet : ${rule} ✓`, rule);
            });
            ruleBox = el("div", { className: "rule-box" },
                el("label", { className: "check-line" }, exact, " cette commande exacte"),
                el("label", { className: "check-line" }, prefixRadio, " toutes celles qui commencent par ", prefix),
                el("p", { className: "muted small" },
                    "Une règle ne couvre jamais une suite avec ; & | > $ (enchaînement) : l'agent te redemandera."),
                confirmRule);
            box.insertBefore(ruleBox, buttons.nextSibling);
        });
        buttons.append(always);
    }
    const deny = el("button", { className: "ghost danger" }, "Refuser");
    deny.addEventListener("click", () => decide("deny", "Refusé ✗"));
    buttons.append(deny);
    box.append(el("div", { className: "approval-title" }, title), diffNode(ev.preview, ev.kind), buttons);
    return box;
}

async function undoChange(backupId, button) {
    if (!confirm("Remettre le fichier dans l'état d'avant cette modification ?")) return;
    button.disabled = true;
    try {
        await api("/api/agent/undo", {
            method: "POST",
            body: JSON.stringify({ conversation_id: chat.current.id, backup_id: backupId }),
        });
        await openConversation(chat.current.id);
    } catch (err) {
        button.disabled = false;
        showChatError(err.message);
    }
}

async function undoTask(taskId, button) {
    if (chat.controller) return;
    const cid = chat.current.id;
    const query = `conversation_id=${cid}&task_id=${taskId}`;
    try {
        const preview = await api(`/api/agent/undo-task?${query}`);
        if (!preview.files.length) {
            showChatError("Rien à annuler : les modifications de cette tâche sont déjà annulées.");
            return;
        }
        const ok = preview.files.filter((f) => !f.conflict);
        const conflicts = preview.files.filter((f) => f.conflict);
        const line = (f) => `  • ${f.path} (${f.action === "supprimer" ? "créé par l'agent : supprimé" : "restauré"})`;
        let text = "Annuler toute la tâche ?\n\n";
        if (ok.length) text += `${ok.length} fichier(s) remis dans leur état d'avant :\n${ok.map(line).join("\n")}\n\n`;
        if (conflicts.length) {
            text += `${conflicts.length} fichier(s) retouché(s) depuis la tâche, laissé(s) tel(s) quel(s) :\n`
                + conflicts.map((f) => `  • ${f.path}`).join("\n") + "\n\n";
        }
        if (preview.commands.length) {
            text += `${preview.commands.length} commande(s) lancée(s) : leurs effets ne s'annulent pas.\n`
                + preview.commands.slice(0, 5).map((c) => `  • ${c.slice(0, 80)}`).join("\n") + "\n\n";
        }
        text += "Les versions actuelles partent à la corbeille.";
        if (!confirm(text)) return;
        const force = conflicts.length > 0
            && confirm(`Restaurer aussi les ${conflicts.length} fichier(s) retouché(s) depuis la tâche ?\n`
                + "Tes changements faits après la tâche partiront à la corbeille.");
        if (!ok.length && !force) return;
        button.disabled = true;
        await api("/api/agent/undo-task", {
            method: "POST",
            body: JSON.stringify({ conversation_id: cid, task_id: taskId, force }),
        });
        await openConversation(cid);
    } catch (err) {
        button.disabled = false;
        showChatError(err.message);
    }
}

function taskFooterNode(taskId, undone) {
    // Sous la dernière étape d'une tâche qui a modifié des fichiers : annuler le tout, ou le bilan de l'annulation
    if (undone) {
        const parts = [];
        if (undone.restored.length) parts.push(`${undone.restored.length} fichier(s) restauré(s)`);
        if (undone.deleted.length) parts.push(`${undone.deleted.length} supprimé(s)`);
        if (undone.skipped.length) parts.push(`${undone.skipped.length} retouché(s) depuis, laissé(s) tel(s) quel(s)`);
        if (undone.errors.length) parts.push(`${undone.errors.length} erreur(s) : ${undone.errors.join(" ; ")}`);
        return el("div", { className: "summary-divider" }, `↩ Tâche annulée : ${parts.join(", ") || "rien à restaurer"}`);
    }
    const button = el("button", { className: "ghost" }, "↩ Annuler toute la tâche");
    button.addEventListener("click", () => undoTask(taskId, button));
    return el("div", { className: "task-actions" }, button);
}

function renderMessages() {
    const box = $("#messages");
    const messages = chat.current?.messages || [];
    renderPlanCard({ reset: true });
    if (!messages.length) {
        box.replaceChildren(el("p", { className: "hint muted" }, "Pose ta première question 💬"));
        return;
    }
    const nodes = [];
    const summary = chat.current?.summary;
    // Le dernier plan proposé attend ta décision tant que le serveur le garde en attente et que tu n'as rien répondu
    const lastUser = messages.map((m) => m.role).lastIndexOf("user");
    const lastPlan = messages.map((m) => m.name).lastIndexOf("propose_plan");
    const plan = chat.current?.plan;
    const locked = plan && ["required", "pending"].includes(plan.status) && plan.task_id === chat.current?.task_id;
    const planPending = locked && plan.status === "pending" && lastPlan > lastUser;
    // Plan exigé, mais écrit en texte au lieu d'appeler propose_plan : sa réponse finale tient lieu de plan
    const last = messages.at(-1);
    const textPlan = locked && !planPending && messages.length - 1 > lastUser && last?.role === "assistant" && last.content;
    const question = pendingQuestionIndex(messages);
    // Tâches qui ont modifié des fichiers (directement ou par un sous-agent) : leur dernier message
    const taskEnd = {};
    const taskWrites = new Set();
    messages.forEach((m, index) => {
        if (!m.task_id) return;
        taskEnd[m.task_id] = index;
        if (m.backup_id || m.name === "delegate") taskWrites.add(m.task_id);
    });
    const undoneTasks = chat.current?.undone_tasks || {};
    messages.forEach((m, index) => {
        if (summary && index === summary.upto) nodes.push(summaryNode(summary, summary.upto));
        if (m.role === "tool") {
            nodes.push(toolNode(m, planPending && index === lastPlan, index === question));
        } else if (m.role === "assistant" && !m.content && !m.reasoning) {
            // simple appel d'outil : la carte de l'outil suffit
        } else {
            nodes.push(messageNode(m));
        }
        if (m.task_id && taskEnd[m.task_id] === index && (taskWrites.has(m.task_id) || undoneTasks[m.task_id])) {
            nodes.push(taskFooterNode(m.task_id, undoneTasks[m.task_id]));
        }
    });
    if (textPlan) {
        nodes.push(el("div", { className: "plan-card" },
            el("div", { className: "approval-title" }, "🗺️ Plan ci-dessus : en attente de ta décision"), planActions()));
    }
    box.replaceChildren(...nodes);
    box.scrollTop = box.scrollHeight;
}

$("#messages").addEventListener("click", async (event) => {
    const button = event.target.closest(".copy");
    if (!button) return;
    const code = button.closest(".code").querySelector("code").textContent;
    try {
        await navigator.clipboard.writeText(code);
        button.textContent = "Copié ✓";
    } catch {
        button.textContent = "Échec";
    }
    setTimeout(() => (button.textContent = "Copier"), 1500);
});

const CONTINUE_PROMPT = "Continue la tâche là où tu t'es arrêté.";

function showChatError(message, continuable = false) {
    const box = $("#chat-error");
    box.textContent = message;
    if (continuable) {
        // Limite d'appels atteinte ou réponse coupée : on relance sans retaper la demande
        const button = el("button", { type: "button", className: "primary continue" }, "▶ Continuer");
        button.addEventListener("click", () => send(CONTINUE_PROMPT, undefined, true));
        box.append(" ", button);
    }
    box.hidden = false;
}

// --- Envoi et streaming -----------------------------------------------------

function setBusy(busy) {
    const card = $("#plan-card");
    card.classList.toggle("running", busy);
    if (busy) card.open = true;
    else if (!card.hidden && currentPlan()?.every((i) => i.status === "completed")) card.open = false;
    $("#btn-send").disabled = busy;
    $("#btn-regen").disabled = busy;
    $("#btn-compact").disabled = busy;
    if (busy) $("#compact-pop").hidden = true;
    $("#btn-stop").hidden = !busy;
}

async function readEvents(resp, onEvent) {
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
            const raw = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);
            for (const line of raw.split("\n")) {
                if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
            }
        }
    }
}

async function send(content, answer, continueTask = false, pictures = [], planFirst = false) {
    // answer : réponse à la question en attente de l'agent (content vaut alors null)
    // continueTask : bouton « Continuer », le message prolonge la tâche en cours
    // pictures : pièces jointes du nouveau message ({kind: "image", src} ou {kind: "file", name, data})
    if (chat.controller) return;
    $("#chat-error").hidden = true;
    if (state.status?.state !== "ready") {
        showChatError("Aucun modèle prêt : choisis un modèle dans le sélecteur du haut.");
        return false;
    }
    if (!chat.current) {
        const pending = chat.pendingAgent;
        try {
            chat.current = await api("/api/conversations", {
                method: "POST",
                body: JSON.stringify({
                    preset_id: $("#preset-select").value,
                    persona_id: $("#persona-select").value || undefined,
                    prive: Boolean(chat.pendingPrivate),
                    agent: { enabled: Boolean(pending.enabled), workspace: pending.enabled ? pending.workspace : null,
                        auto_web: Boolean(pending.auto_web) },
                }),
            });
        } catch (err) {
            showChatError(err.message);
            return false;
        }
        $("#btn-rename").hidden = false;
        syncAgentBar();
    }

    if (answer !== undefined) {
        const question = chat.current.messages[pendingQuestionIndex(chat.current.messages)];
        if (question) Object.assign(question, { status: "ok", answer: answer.trim() });
    } else if (content === null) {
        while (["assistant", "tool"].includes(chat.current.messages.at(-1)?.role)) chat.current.messages.pop();
    } else {
        chat.current.messages.push({ role: "user", content, localImages: imagesOf(pictures), localFiles: filesOf(pictures) });
        if (!continueTask) delete chat.current.todo;
    }
    renderMessages();
    const box = $("#messages");

    // Réponse en cours : un nouveau bloc à chaque étape de l'agent
    let live = null;
    let liveNode = null;
    let pending = false;
    const toolCards = {};
    const atBottom = () => box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
    const append = (node) => {
        const stick = atBottom();
        box.append(node);
        if (stick) box.scrollTop = box.scrollHeight;
    };
    const ensureLive = () => {
        if (live) return;
        live = { role: "assistant", content: "", reasoning: "" };
        liveNode = messageNode(live);
        liveNode.querySelector(".reasoning").open = true;
        append(liveNode);
    };
    const scheduleRender = () => {
        if (pending) return;
        pending = true;
        const node = liveNode;
        const msg = live;
        requestAnimationFrame(() => {
            pending = false;
            const stick = atBottom();
            updateMessageNode(node, msg);
            if (stick) box.scrollTop = box.scrollHeight;
        });
    };
    const closeLive = () => {
        if (!live) return;
        if (!live.content && !live.reasoning) liveNode.remove();
        else updateMessageNode(liveNode, live);
        live = null;
        liveNode = null;
    };
    ensureLive();

    setBusy(true);
    chat.controller = new AbortController();
    let aborted = false;
    let ok = true;
    let errorShown = null;  // réaffichée après le rechargement final de la conversation
    let waiting = false;  // l'agent t'a demandé quelque chose : la notification « terminée » n'aurait pas de sens
    try {
        const resp = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                conversation_id: chat.current.id, content, answer, continue_task: continueTask, plan_first: planFirst,
                preset_id: $("#preset-select").value, images: imagesOf(pictures), files: filesOf(pictures),
            }),
            signal: chat.controller.signal,
        });
        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.detail || `Erreur HTTP ${resp.status}`);
        }
        await readEvents(resp, (ev) => {
            if (ev.type === "delta") {
                ensureLive();
                if (ev.content && !live.content) liveNode.querySelector(".reasoning").open = false;
                live.content += ev.content;
                live.reasoning += ev.reasoning;
                scheduleRender();
            } else if (ev.type === "step_end") {
                closeLive();
            } else if (ev.type === "tool_call") {
                closeLive();
                const data = { tool_call_id: ev.call_id, name: ev.name, args: ev.args, status: "running" };
                const node = toolNode(data);
                toolCards[ev.call_id] = { data, node };
                append(node);
                if (ev.name === "ask_user") {
                    waiting = true;
                    notify("waiting", "Aria te pose une question", ev.args?.question || chat.current.title);
                } else if (ev.name === "propose_plan") {
                    waiting = true;
                    notify("waiting", "Un plan attend ta validation", chat.current.title);
                }
            } else if (ev.type === "approval") {
                // Une demande d'un sous-agent s'affiche dans la carte de sa délégation
                const card = toolCards[ev.call_id] || toolCards[ev.parent_id];
                card.node.open = true;
                card.node.append(approvalNode(ev));
                card.node.scrollIntoView({ block: "nearest" });
                notify("waiting", "Aria attend ton accord", chat.current.title);
            } else if (ev.type === "hook") {
                append(hookNote(ev));
            } else if (ev.type === "hooks_untrusted") {
                refreshHooksBanner();
            } else if (ev.type === "sub_tool_call") {
                const card = toolCards[ev.parent_id];
                card.data.subagent = card.data.subagent || { steps: [] };
                const step = { name: ev.name, args: ev.args, status: "running" };
                card.data.subagent.steps.push(step);
                card.node.open = true;
                const line = subStepNode(step);
                card.subLines = { ...(card.subLines || {}), [ev.call_id]: { step, line } };
                card.node.querySelector(".sub-steps").append(line);
            } else if (ev.type === "sub_tool_result") {
                const entry = toolCards[ev.parent_id]?.subLines?.[ev.call_id];
                if (entry) {
                    entry.step.status = ev.status;
                    const fresh = subStepNode(entry.step);
                    entry.line.replaceWith(fresh);
                    entry.line = fresh;
                }
            } else if (ev.type === "memory") {
                chat.memoryLoaded = true;
                syncAgentBar();
            } else if (ev.type === "context") {
                renderGauge(ev);
                if ((sidePref("aria-side-tab") || side.tab) === "context") refreshContext();
                if (ev.saturated) showChatError("Le contexte est presque plein et il n'y a plus d'anciens échanges à résumer.");
            } else if (ev.type === "compacted") {
                renderGauge(ev);
                closeLive();
                const what = ev.messages ? `${ev.messages} anciens messages résumés` : "résultats d'outils raccourcis";
                append(el("div", { className: "summary-divider" },
                    `🗜 ${what} pour libérer de la place : `
                    + `${ev.before.toLocaleString("fr-FR")} → ${ev.used.toLocaleString("fr-FR")} tokens`));
                if (ev.saturated) showChatError("Le contexte reste trop petit pour cette tâche : recharge le modèle avec un contexte plus grand.");
            } else if (ev.type === "tool_result") {
                if (ev.todo) {
                    renderTodo(ev.todo);
                    chat.current.todo = { task_id: null, items: ev.todo };  // tâche en cours : affiché sans condition
                    renderPlanCard();
                }
                if (ev.backup_id) refreshChangesSoon();
                const card = toolCards[ev.call_id];
                // Commande lancée ou arrêtée en arrière-plan : l'onglet Arrière-plan suit
                if ((card?.data.name === "run_command" && card.data.args?.background) || card?.data.name === "stop_command") {
                    refreshBackground();
                }
                Object.assign(card.data, { status: ev.status, content: ev.output, backup_id: ev.backup_id, syntax: ev.syntax, rule: ev.rule,
                    souvenir: ev.souvenir });
                const fresh = toolNode(card.data);
                card.node.replaceWith(fresh);
                card.node = fresh;
                ensureLive();
            } else if (ev.type === "done" && ev.message && live) {
                Object.assign(live, ev.message);
            } else if (ev.type === "error") {
                errorShown = { message: ev.message, continuable: ev.continuable };
                showChatError(ev.message, ev.continuable);
            }
        });
    } catch (err) {
        if (err.name === "AbortError") {
            aborted = true;
        } else {
            ok = false;
            showChatError(err.message);
        }
    } finally {
        chat.controller = null;
        setBusy(false);
    }

    const id = chat.current.id;
    if (!aborted && !waiting) {
        if (errorShown || !ok) notify("task", "Tâche arrêtée", errorShown?.message || chat.current.title);
        else notify("task", agentSettings().enabled ? "Tâche terminée" : "Réponse prête", chat.current.title);
    }
    if (aborted) {
        // Le serveur enregistre la réponse partielle de son côté : on l'affiche tout de suite
        if (live) {
            live.stats = { ...(live.stats || {}), stopped: true };
            closeLive();
        }
        setTimeout(() => openConversation(id), 600);
    } else {
        await openConversation(id);
        if (errorShown) showChatError(errorShown.message, errorShown.continuable);
    }
    return ok;
}

$("#chat-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = $("#chat-input");
    const content = input.value;
    const pictures = [...chat.attachments];
    if ((!content.trim() && !pictures.length) || chat.controller) return;
    // « /compact » ou « /compact garde les décisions… » : compactage à la demande, rien n'est envoyé au modèle
    const compact = content.trim().match(/^\/compact(?:\s+([\s\S]*))?$/);
    if (compact) {
        if (!chat.current) {
            showChatError("Rien à compacter : la conversation n'a pas encore commencé.");
            return;
        }
        input.value = "";
        await compactNow((compact[1] || "").trim());
        return;
    }
    // « /plan ta demande » : comme la puce « 🗺 Plan d'abord », pour ce message
    const planCommand = content.trim().match(/^\/plan(?:\s+([\s\S]*))?$/);
    const planFirst = chat.planFirst || Boolean(planCommand);
    const request = planCommand ? (planCommand[1] || "").trim() : content;
    if (planCommand && !request && !pictures.length) {
        showChatError("Écris ta demande après /plan, par exemple « /plan migre le site vers Python 3.12 ».");
        return;
    }
    // Une question de l'agent attend : ton message est ta réponse
    const asked = chat.current && pendingQuestionIndex(chat.current.messages) >= 0;
    if (asked && pictures.length) {
        showChatError("Réponds d'abord à la question de l'agent, puis envoie ta pièce jointe.");
        return;
    }
    if (asked && planCommand) {
        showChatError("Réponds d'abord à la question de l'agent, puis demande ton plan.");
        return;
    }
    input.value = "";
    setAttachments([]);
    const ok = asked ? await send(null, content) : await send(request, undefined, false, pictures, planFirst);
    if (ok === false) {
        if (!input.value) input.value = content;
        if (!chat.attachments.length) setAttachments(pictures);
    } else if (!asked) {
        setPlanFirst(false);  // la puce ne vaut que pour un message
    }
});

// --- 🗺 Plan d'abord : pour le prochain message, un plan avant toute modification ---------------

chat.planFirst = false;

function setPlanFirst(on) {
    chat.planFirst = on;
    const chip = $("#btn-plan-first");
    chip.classList.toggle("chip-on", on);
    chip.setAttribute("aria-pressed", String(on));
    chip.textContent = on ? "🗺 Plan d'abord ✓" : "🗺 Plan d'abord";
}

$("#btn-plan-first").addEventListener("click", () => {
    setPlanFirst(!chat.planFirst);
    $("#chat-input").focus();
});

// --- Pièces jointes : 📎, coller (Ctrl+V) ou glisser-déposer -------------------------------
// Images (si le modèle voit), textes, code et PDF (lus par Aria et donnés au modèle comme du texte)

const MAX_ATTACHMENTS = 5;
const MAX_FILE_BYTES = 10 * 1024 * 1024;
const TEXT_EXTENSIONS = new Set(("txt md markdown log csv tsv json jsonl yaml yml toml ini cfg conf xml html htm css js mjs ts "
    + "tsx jsx py ps1 psm1 psd1 bat cmd sh sql java kt c h cpp hpp cs go rs rb php lua r env gitignore dockerfile "
    + "properties reg srt vtt tex rst pdf").split(" "));
chat.attachments = [];

function imagesOf(list) {
    return (list || []).filter((a) => a.kind === "image").map((a) => a.src);
}

function filesOf(list) {
    return (list || []).filter((a) => a.kind === "file").map(({ name, data }) => ({ name, data }));
}

function fileExtension(name) {
    const base = name.toLowerCase().split(/[\\/]/).pop();
    return base.includes(".") ? base.split(".").pop() : base;
}

function setAttachments(list) {
    chat.attachments = list;
    const box = $("#attach-list");
    box.hidden = !list.length;
    box.replaceChildren(...list.map((a, i) => {
        const remove = el("button", { type: "button", className: "ghost", title: "Retirer" }, "✕");
        remove.addEventListener("click", () => setAttachments(chat.attachments.filter((_, j) => j !== i)));
        const body = a.kind === "image" ? el("img", { src: a.src, alt: "Image à envoyer" })
            : el("span", { className: "file-chip" }, `📄 ${a.name}`);
        return el("span", { className: "attach-thumb" }, body, remove);
    }));
}

function visionModels() {
    return (state.models || []).filter((m) => m.mmproj && !/mmproj/i.test(m.file || "")).map((m) => m.name);
}

function readAsDataUrl(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
    });
}

async function addFiles(files) {
    const all = [...files];
    if (!all.length) return;
    const problems = [];
    const pictures = all.filter((f) => f.type.startsWith("image/"));
    const documents = all.filter((f) => !f.type.startsWith("image/"));
    if (pictures.length && !state.status?.vision) {
        const seeing = visionModels();
        problems.push(`Le modèle chargé ne voit pas les images${seeing.length ? ` : charge plutôt ${seeing.join(" ou ")}` : " (il lui faut son projecteur vision)"}.`);
    }
    const usable = state.status?.vision ? [...pictures] : [];
    for (const f of documents) {
        if (!TEXT_EXTENSIONS.has(fileExtension(f.name))) problems.push(`« ${f.name} » : format non pris en charge (images, textes, code et PDF).`);
        else if (f.size > MAX_FILE_BYTES) problems.push(`« ${f.name} » : trop gros (10 Mo au plus).`);
        else usable.push(f);
    }
    const room = MAX_ATTACHMENTS - chat.attachments.length;
    if (usable.length > room) problems.push(`${MAX_ATTACHMENTS} pièces jointes au plus par message.`);
    const added = await Promise.all(usable.slice(0, Math.max(room, 0)).map(async (f) => f.type.startsWith("image/")
        ? { kind: "image", src: await readAsDataUrl(f) }
        : { kind: "file", name: f.name, data: await readAsDataUrl(f) }));
    if (added.length) setAttachments([...chat.attachments, ...added]);
    if (problems.length) showChatError(problems.join(" "));
}

$("#btn-attach").addEventListener("click", () => $("#attach-input").click());
$("#attach-input").addEventListener("change", (event) => {
    addFiles(event.target.files);
    event.target.value = "";
});
$("#chat-input").addEventListener("paste", (event) => {
    const files = [...(event.clipboardData?.files || [])];
    if (files.length) {  // un fichier collé (pas du texte) : pièce jointe
        event.preventDefault();
        addFiles(files);
    }
});
$("#chat-form").addEventListener("dragover", (event) => {
    if ([...event.dataTransfer.items].some((i) => i.kind === "file")) event.preventDefault();
});
$("#chat-form").addEventListener("drop", (event) => {
    if (!event.dataTransfer.files.length) return;
    event.preventDefault();
    addFiles(event.dataTransfer.files);
});

$("#chat-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        $("#chat-form").requestSubmit();
    }
});

$("#btn-stop").addEventListener("click", () => chat.controller?.abort());

$("#btn-regen").addEventListener("click", () => {
    if (chat.current?.messages.length) send(null);
});

// --- Démarrage ---------------------------------------------------------------

setInterval(() => {
    const s = state.status;
    $("#btn-attach").title = s?.state === "ready" && !s.vision
        ? "Joindre un fichier texte, du code ou un PDF (ou glisse-le ici). Les images demandent un modèle avec vision."
        : "Joindre une image, un fichier texte, du code ou un PDF (ou colle-le, ou glisse-le ici)";
    $("#chat-model").textContent = s?.state === "ready"
        ? `Modèle : ${s.model.name}`
        : s?.state === "loading" ? "Chargement du modèle…" : "Aucun modèle chargé (sélecteur en haut)";
    $("#chat-model").title = $("#chat-model").textContent;  // nom complet au survol quand il est tronqué
}, 1000);

(async () => {
    await loadPresets();
    await loadPersonas();
    refreshProfileBanner();
    await loadConversations();
    if (chat.conversations.length) {
        await openConversation(chat.conversations[0].id);
    } else {
        while (!state.config) await new Promise((resolve) => setTimeout(resolve, 50));  // lue par app.js
        resetPendingAgent();
        renderMessages();
    }
})();
