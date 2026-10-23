// Page Réglages : navigation à gauche, une adresse par section (#/reglages/<section>), enregistrement automatique.
// S'appuie sur $, api, el, state, loadModels, loadEngines, renderHardware (app.js) et SKILL_SOURCES (chat.js).

const SECTIONS = ["general", "modele-defaut", "mes-modeles", "decouvrir", "moteurs", "materiel",
    "comportement", "autorisations", "competences", "hooks", "web", "donnees"];

// --- Navigation ------------------------------------------------------------------

function route() {
    const match = location.hash.match(/^#\/reglages(?:\/([\w-]+))?/);
    const inSettings = Boolean(match);
    $("#view-chat").hidden = inSettings;
    $("#view-settings").hidden = !inSettings;
    $("#btn-settings").classList.toggle("active", inSettings);
    if (!inSettings) return;
    let section = match[1];
    if (!SECTIONS.includes(section)) {
        // « Réglages » tout court : la dernière section ouverte
        let last = null;
        try {
            last = localStorage.getItem("aria-settings-section");
        } catch {
            // stockage indisponible
        }
        history.replaceState(null, "", `#/reglages/${SECTIONS.includes(last) ? last : "general"}`);
        route();
        return;
    }
    try {
        localStorage.setItem("aria-settings-section", section);
    } catch {
        // idem
    }
    for (const box of document.querySelectorAll(".settings-section")) box.hidden = box.dataset.section !== section;
    for (const link of document.querySelectorAll(".settings-nav a[data-section]")) {
        link.classList.toggle("active", link.dataset.section === section);
    }
    const current = document.querySelector(`.settings-section[data-section="${section}"]`);
    $("#settings-title").textContent = current.dataset.title;
    $(".settings-body").scrollTop = 0;
    LOADERS[section]?.();
}

window.addEventListener("hashchange", route);

// --- Enregistrement automatique ----------------------------------------------------

let savedTimer = null;

function flashSaved() {
    const flag = $("#saved-flag");
    flag.hidden = false;
    clearTimeout(savedTimer);
    savedTimer = setTimeout(() => { flag.hidden = true; }, 1800);
}

async function saveConfig(patch) {
    state.config = await api("/api/config", { method: "PUT", body: JSON.stringify(patch) });
    flashSaved();
    renderPicker();
    if (patch.new_conversation) resetPendingAgent();  // une conversation pas encore créée prend les nouveaux réglages
}

// data-config="load_defaults.ctx_size" → {load_defaults: {ctx_size: valeur}}
function configValue(path) {
    return path.split(".").reduce((obj, key) => obj?.[key], state.config);
}

function fillConfigFields(scope) {
    for (const field of scope.querySelectorAll("[data-config]")) {
        const value = configValue(field.dataset.config);
        if (field.type === "checkbox") field.checked = Boolean(value);
        else field.value = value ?? "";
        field.classList.remove("invalid");
    }
}

for (const field of document.querySelectorAll("#view-settings [data-config]")) {
    field.addEventListener("change", async () => {
        let value = field.value;
        if (field.type === "checkbox") value = field.checked;
        else if (field.hasAttribute("data-number")) value = Number(field.value);
        else if (field.type === "number") {
            // Une valeur hors limites n'est pas enregistrée : le champ passe en rouge
            if (field.value === "" ? field.name !== "threads" : !field.checkValidity()) {
                field.classList.add("invalid");
                return;
            }
            value = Number(field.value || 0);
        }
        field.classList.remove("invalid");
        const keys = field.dataset.config.split(".");
        const patch = keys.reduceRight((inner, key) => ({ [key]: inner }), value);
        try {
            await saveConfig(patch);
            if (field.type === "text") fillConfigFields(field.closest(".settings-section"));  // chemin normalisé par le serveur
        } catch (err) {
            // Refusé par le serveur (dossier introuvable, agent sans dossier…) : on remet la valeur enregistrée
            alert(err.message);
            fillConfigFields(field.closest(".settings-section"));
        }
    });
}

// --- Sections ------------------------------------------------------------------------

function loadGeneral() {
    $("#set-version").textContent = state.status?.version ? `v${state.status.version}` : "?";
    fillConfigFields($('.settings-section[data-section="general"]'));
    renderNotifications();
    loadUpdate(false);
}

// --- Mises à jour ------------------------------------------------------------------------

function since(seconds) {
    const minutes = Math.round((Date.now() / 1000 - seconds) / 60);
    if (minutes < 1) return "à l'instant";
    if (minutes < 60) return `il y a ${minutes} min`;
    const hours = Math.round(minutes / 60);
    return hours < 24 ? `il y a ${hours} h` : `il y a ${Math.round(hours / 24)} j`;
}

function renderUpdateBadge(u) {
    const badge = $("#update-badge");
    badge.hidden = !u?.newer;
    if (u?.newer) badge.textContent = `v${u.latest} disponible`;
}

function renderUpdate(u) {
    const box = $("#update-info");
    let line;
    if (u.error) line = el("div", { className: "error small" }, `⚠ ${u.error}`);
    else if (!u.checked_at) line = el("div", { className: "muted small" }, "Pas encore vérifié : clique sur « Vérifier maintenant ».");
    else if (u.newer) line = el("div", { className: "update-new" }, `🎉 La version ${u.latest} est disponible (tu as la ${u.current}).`);
    else if (u.dev) line = el("div", { className: "small" }, `🧪 Version de développement : plus récente que la dernière publiée (${u.latest}).`);
    else if (!u.latest) line = el("div", { className: "muted small" }, "Aucune version publiée sur GitHub pour l'instant.");
    else line = el("div", { className: "ok-text small" }, `✓ À jour : la ${u.current} est la dernière version publiée.`);
    const when = u.checked_at ? el("div", { className: "muted small" }, `Dernière vérification ${since(u.checked_at)}.`) : "";
    box.replaceChildren(line, when);
    $("#update-howto").hidden = !u.newer;
    if (u.changes_url) $("#update-changes").href = u.changes_url;
    renderUpdateBadge(u);
}

async function loadUpdate(force) {
    const button = $("#btn-update-check");
    button.disabled = true;
    try {
        renderUpdate(await api(force ? "/api/update/check" : "/api/update", force ? { method: "POST" } : {}));
    } catch (err) {
        $("#update-info").replaceChildren(el("div", { className: "error small" }, err.message));
    } finally {
        button.disabled = false;
    }
}

$("#btn-update-check").addEventListener("click", () => loadUpdate(true));

// --- Notifications -----------------------------------------------------------------------

function renderNotifications() {
    const supported = "Notification" in window;
    const permission = supported ? Notification.permission : "denied";
    const enabled = Boolean(state.config.notifications?.enabled);
    $("#set-notif").checked = enabled && permission === "granted";
    $("#set-notif").disabled = !supported;
    $("#notif-perm").textContent = !supported ? "Ce navigateur ne gère pas les notifications."
        : permission === "denied" ? "Refusées par le navigateur : clique sur le cadenas à gauche de l'adresse, autorise les notifications, puis réactive ici."
            : permission === "granted" ? "Autorisées par le navigateur. Un clic sur une notification ramène l'onglet d'Aria."
                : "Le navigateur te demandera l'autorisation quand tu les activeras.";
    for (const box of document.querySelectorAll('[data-config^="notifications."]')) box.disabled = !$("#set-notif").checked;
    $("#btn-notif-test").disabled = !$("#set-notif").checked;
}

$("#set-notif").addEventListener("change", async () => {
    const box = $("#set-notif");
    if (box.checked && Notification.permission !== "granted") {
        // La demande d'autorisation du navigateur n'arrive qu'ici, sur ton clic
        const permission = await Notification.requestPermission();
        if (permission !== "granted") {
            box.checked = false;
            renderNotifications();
            return;
        }
    }
    try {
        await saveConfig({ notifications: { enabled: box.checked } });
    } catch (err) {
        alert(err.message);
    }
    renderNotifications();
});

$("#btn-notif-test").addEventListener("click", () => {
    showNotification("Aria Pilote", "Les notifications fonctionnent 👋 Clique ici pour revenir à Aria.", "aria-test");
});

function loadDefaultModel() {
    const pick = $("#set-default-model");
    const usable = state.models.filter((m) => !m.error && !/mmproj/i.test(m.file));
    pick.replaceChildren(el("option", { value: "" }, "Aucun"), ...usable.map((m) => el("option", { value: m.id }, m.name)));
    fillConfigFields($('.settings-section[data-section="modele-defaut"]'));
    // Un modèle par défaut disparu (supprimé, dossier retiré) : on l'affiche tel quel plutôt que « Aucun »
    if (state.config.default_model && pick.value !== state.config.default_model) {
        pick.append(el("option", { value: state.config.default_model }, "Modèle introuvable"));
        pick.value = state.config.default_model;
    }
}

function loadEnginesSection() {
    loadEngines();
    fillConfigFields($("#defaults-form"));
    const box = $("#logs");
    box.scrollTop = box.scrollHeight;
}

function loadHardwareSection() {
    renderHardware();
    $("#set-model-dirs").value = state.config.model_dirs.join("\n");
}

$("#set-model-dirs").addEventListener("change", async () => {
    const dirs = $("#set-model-dirs").value.split("\n").map((d) => d.trim()).filter(Boolean);
    try {
        await saveConfig({ model_dirs: dirs });
        await loadModels();
    } catch (err) {
        alert(err.message);
    }
});

async function loadRules() {
    const box = $("#rules-all");
    box.replaceChildren(el("p", { className: "muted" }, "Chargement…"));
    const projects = await api("/api/rules");
    // Même forme que les clés du serveur : chemin Windows en minuscules, sans barre finale
    const focus = state.rulesFocus?.replace(/\//g, "\\").replace(/\\+$/, "").toLowerCase();
    state.rulesFocus = null;
    box.replaceChildren(...projects.map(({ root, rules }) => {
        const list = el("ul", { className: "skills-list" }, ...rules.map((rule) => {
            const del = el("button", { className: "ghost danger-text" }, "Retirer");
            del.addEventListener("click", async () => {
                await api("/api/rules", { method: "DELETE", body: JSON.stringify({ root, rule }) });
                flashSaved();
                loadRules();
            });
            return el("li", {}, el("code", { className: "skill-text" }, rule), del);
        }));
        const card = el("div", { className: "set-card" }, el("div", { className: "set-card-title mono" }, root), list);
        if (focus && root === focus) {
            card.classList.add("focus");
            setTimeout(() => card.scrollIntoView({ block: "center" }), 0);
        }
        return card;
    }));
    if (!projects.length) {
        box.append(el("p", { className: "muted" },
            "Aucune règle. Sur une demande d'accord de commande, « Toujours autoriser dans ce projet » en ajoute une."));
    }
}

async function loadSkills() {
    $("#skill-form").hidden = true;
    const list = $("#skills-manage");
    list.hidden = false;
    list.replaceChildren(el("li", { className: "muted" }, "Chargement…"));
    const [items, agents] = await Promise.all([api("/api/skills"), api("/api/subagents")]);
    list.replaceChildren(...items.map((s) => {
        const item = el("li", { className: "static" },
            el("div", { className: "skill-text" },
                el("div", { className: "skill-name" }, `/${s.name}`),
                el("div", { className: "skill-desc" }, s.description)),
            el("span", { className: "badge" }, SKILL_SOURCES[s.source] || s.source));
        if (s.editable) {
            const edit = el("button", { className: "ghost" }, "Modifier");
            edit.addEventListener("click", () => editSkill(s.name));
            const del = el("button", { className: "ghost danger-text" }, "Supprimer");
            del.addEventListener("click", async () => {
                if (!confirm(`Envoyer la compétence « ${s.name} » à la corbeille ?`)) return;
                await api(`/api/skills/${s.name}`, { method: "DELETE" });
                loadSkills();
            });
            item.append(edit, del);
        }
        return item;
    }));
    if (!items.length) list.append(el("li", { className: "muted" }, "Aucune compétence."));
    $("#subagents-list").replaceChildren(...agents.map((a) => el("li", { className: "static" },
        el("div", { className: "skill-text" },
            el("div", { className: "skill-name" }, a.name),
            el("div", { className: "skill-desc" }, a.description)),
        el("span", { className: "badge", title: a.tools.join(", ") }, `${a.tools.length} outils`))));
    if (!agents.length) $("#subagents-list").append(el("li", { className: "muted" }, "Aucun sous-agent."));
}

async function editSkill(name) {
    const form = $("#skill-form");
    form.reset();
    $("#skill-error").hidden = true;
    form.name.readOnly = Boolean(name);
    if (name) {
        const s = await api(`/api/skills/${name}`);
        form.name.value = s.name;
        form.description.value = s.description;
        form.body.value = s.body;
    } else {
        form.body.value = "1. …\n2. …\n";
    }
    $("#skills-manage").hidden = true;
    form.hidden = false;
    form.name.focus();
}

$("#btn-skill-new").addEventListener("click", () => editSkill(null));
$("#btn-skill-cancel").addEventListener("click", loadSkills);

$("#skill-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
        await api("/api/skills", {
            method: "POST",
            body: JSON.stringify({ name: form.name.value.trim(), description: form.description.value, body: form.body.value }),
        });
        flashSaved();
        loadSkills();
    } catch (err) {
        $("#skill-error").textContent = err.message;
        $("#skill-error").hidden = false;
    }
});

async function loadHooks() {
    $("#hooks-error").hidden = true;
    $("#set-hooks").value = (await api("/api/hooks")).global;
}

// Les hooks s'enregistrent au bouton : un JSON à moitié tapé ne doit jamais remplacer le bon
$("#btn-hooks-save").addEventListener("click", async () => {
    $("#hooks-error").hidden = true;
    try {
        await api("/api/hooks/global", { method: "PUT", body: JSON.stringify({ content: $("#set-hooks").value }) });
        flashSaved();
    } catch (err) {
        $("#hooks-error").textContent = err.message;
        $("#hooks-error").hidden = false;
    }
});

// --- Web et SearXNG ---------------------------------------------------------------------

async function loadSearx() {
    $("#sx-error").hidden = true;
    const s = await api("/api/searxng");
    const button = $("#btn-sx-toggle");
    $("#sx-state").textContent = !s.installed ? "non installé" : s.running ? "en marche" : "à la demande";
    $("#sx-state").className = `badge${s.running ? " loaded" : ""}`;
    $("#sx-detail").textContent = s.installed
        ? `${s.url} · démarré tout seul à la première recherche`
        : "Lance tools/install_searxng.py pour l'installer.";
    button.hidden = !s.installed;
    button.textContent = s.running ? "⏹ Arrêter" : "▶ Démarrer";
    button.dataset.running = s.running ? "1" : "";
}

$("#btn-sx-toggle").addEventListener("click", async () => {
    const button = $("#btn-sx-toggle");
    button.disabled = true;
    $("#sx-error").hidden = true;
    if (!button.dataset.running) button.textContent = "Démarrage…";
    try {
        await api(`/api/searxng/${button.dataset.running ? "stop" : "start"}`, { method: "POST" });
    } catch (err) {
        $("#sx-error").textContent = err.message;
        $("#sx-error").hidden = false;
    } finally {
        button.disabled = false;
        loadSearx();
    }
});

// --- Données ---------------------------------------------------------------------------

function humanSize(bytes) {
    if (bytes < 1024) return `${bytes} o`;
    const units = ["Ko", "Mo", "Go"];
    let value = bytes / 1024;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
    }
    return `${value.toLocaleString("fr-FR", { maximumFractionDigits: 1 })} ${units[unit]}`;
}

function showDataError(message) {
    $("#data-error").textContent = message;
    $("#data-error").hidden = !message;
}

function renderData(d) {
    const n = d.conversations.count;
    $("#data-conv").textContent = `${n} conversation${n > 1 ? "s" : ""} · ${humanSize(d.conversations.bytes)}`;
    $("#data-backups").textContent = `${humanSize(d.backups.bytes)} de sauvegardes`;
    $("#data-app").textContent = d.app;
    $("#data-logs").replaceChildren(...d.logs.map((log) => {
        const open = el("button", { className: "ghost", disabled: !log.exists }, "Ouvrir");
        open.addEventListener("click", () => openPlace(log.name));
        return el("li", { className: "static" },
            el("div", { className: "skill-text" }, el("div", { className: "skill-name" }, log.name),
                el("div", { className: "skill-desc" }, log.exists ? humanSize(log.bytes) : "pas encore créé")),
            open);
    }));
    $("#btn-conv-trash").disabled = !n;
}

async function loadData() {
    showDataError("");
    fillConfigFields($('.settings-section[data-section="donnees"]'));
    renderData(await api("/api/data"));
}

async function openPlace(target) {
    showDataError("");
    try {
        await api("/api/data/open", { method: "POST", body: JSON.stringify({ target }) });
    } catch (err) {
        showDataError(err.message);
    }
}

for (const button of document.querySelectorAll("[data-open]")) {
    button.addEventListener("click", () => openPlace(button.dataset.open));
}

$("#btn-conv-trash").addEventListener("click", async () => {
    const n = chat.conversations.length;
    if (!confirm(`Envoyer tes ${n} conversation${n > 1 ? "s" : ""} (et leurs sauvegardes) à la corbeille ?\n`
        + "Tu pourras les récupérer depuis la corbeille de Windows.")) return;
    showDataError("");
    try {
        const result = await api("/api/data/conversations/trash", { method: "POST" });
        renderData(result);
        await openConversation(null);
        await loadConversations();
        flashSaved();
    } catch (err) {
        showDataError(err.message);
    }
});

$("#btn-backups-clean").addEventListener("click", async () => {
    showDataError("");
    try {
        const result = await api("/api/data/backups/cleanup", { method: "POST" });
        renderData(result);
        showDataError("");
        $("#data-backups").textContent += result.trashed
            ? ` · ${result.trashed} dossier${result.trashed > 1 ? "s" : ""} envoyé${result.trashed > 1 ? "s" : ""} à la corbeille`
            : " · rien à ranger";
    } catch (err) {
        showDataError(err.message);
    }
});

const LOADERS = {
    general: loadGeneral,
    "modele-defaut": loadDefaultModel,
    "mes-modeles": () => loadModels(),
    moteurs: loadEnginesSection,
    materiel: loadHardwareSection,
    autorisations: loadRules,
    competences: loadSkills,
    hooks: loadHooks,
    comportement: () => fillConfigFields($('.settings-section[data-section="comportement"]')),
    web: () => {
        fillConfigFields($('.settings-section[data-section="web"]'));
        loadSearx();
    },
    donnees: loadData,
};

// app.js lit la config au démarrage : on attend qu'elle soit là avant d'afficher une section
(async function startRouting() {
    while (!state.config) await new Promise((resolve) => setTimeout(resolve, 50));
    route();
    // La pastille « nouvelle version » de la barre du haut (GitHub n'est interrogé que si c'est activé)
    if (state.config.update_check) {
        try {
            renderUpdateBadge(await api("/api/update"));
        } catch {
            // hors ligne : pas de pastille
        }
    }
})();
