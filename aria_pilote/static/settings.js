// Page Réglages : navigation à gauche, une adresse par section (#/reglages/<section>), enregistrement automatique.
// S'appuie sur $, api, el, state, loadModels, loadEngines, renderHardware (app.js) et SKILL_SOURCES (chat.js).

const SECTIONS = ["general", "persona", "modele-defaut", "mes-modeles", "presets", "decouvrir", "moteurs", "materiel",
    "comportement", "autorisations", "competences", "hooks", "mcp", "web", "donnees"];

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
    loadProfileCard();
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
    const box = $("#logs");
    box.scrollTop = box.scrollHeight;
}

function loadHardwareSection() {
    renderHardware();
}

function loadModelsSection() {
    loadModels();
    $("#set-model-dirs").value = state.config.model_dirs.join("\n");
}

$("#btn-hw-rescan").addEventListener("click", renderHardware);
$("#link-defaults").addEventListener("click", () => { state.selectedId = DEFAULTS_ID; });

// Boutons « Parcourir » : la fenêtre de Windows remplit le champ, qui s'enregistre comme une saisie
async function browseInto(field, title, append = false) {
    let path;
    try {
        path = await pickFolder(append ? "" : field.value.trim(), title);
    } catch (err) {
        alert(err.message);
        return;
    }
    if (!path) return;
    field.value = append ? [field.value.trim(), path].filter(Boolean).join("\n") : path;
    field.dispatchEvent(new Event("change"));
}

$("#btn-nc-browse").addEventListener("click", () => browseInto($("#set-nc-workspace"), "Dossier proposé aux nouvelles conversations"));
$("#btn-model-dir-add").addEventListener("click", () => browseInto($("#set-model-dirs"), "Dossier de modèles à ajouter", true));

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

// Compétences : liste, puis un formulaire qui suit le modèle (ou le fichier brut en mode texte)
const skillUi = { name: null, fields: null, editable: true, previewTimer: null };
const SKILL_LISTS = {
    eviter: "Ex. Relire tout un code pour en lister les défauts : revue-code.",
    pratiques: "Ex. Des noms explicites, des fonctions courtes.",
    toujours: "Ex. Un code complet et exécutable.",
    jamais: "Ex. Inventer une option ou une bibliothèque.",
    verifier: "Ex. Le code se lance tel quel.",
};

function emptySkillFields() {
    return {
        name: "", description: "", registre: "sobre", titre: "", objectif: "", eviter: [""],
        methode: [{ cas: "", etapes: [""] }], pratiques: [], format: "",
        regles: { toujours: [""], jamais: [""] }, verifier: [""], exemple: { demande: "", reponse: "" },
    };
}

// Les listes « à faire toujours » et « à ne jamais faire » vivent sous regles
function skillList(key) {
    const f = skillUi.fields;
    if (key === "toujours" || key === "jamais") return f.regles[key];
    return f[key];
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
                el("div", { className: "skill-desc" }, s.description),
                el("div", { className: "skill-reg" }, `Registre suggéré : ${s.registre}`)),
            el("span", { className: "badge" }, SKILL_SOURCES[s.source] || s.source));
        const open = el("button", { className: "ghost" }, s.editable ? "Modifier" : "Voir");
        open.addEventListener("click", () => editSkill(s.name));
        item.append(open);
        if (s.editable) {
            const del = el("button", { className: "ghost danger-text" }, "Supprimer");
            del.addEventListener("click", async () => {
                if (!confirm(`Envoyer la compétence « ${s.name} » à la corbeille ?`)) return;
                await api(`/api/skills/${s.name}`, { method: "DELETE" });
                loadSkills();
            });
            item.append(del);
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

// Une liste de lignes (↑ ↓ ✕ et + Ajouter), sur le modèle des règles d'or du persona
function renderLineList(ul, items, placeholder, numbered = false) {
    const rows = items.map((value, i) => {
        const input = el("input", { value, placeholder: i === 0 ? placeholder : "", disabled: !skillUi.editable });
        input.addEventListener("input", () => { items[i] = input.value; scheduleSkillPreview(); });
        const parts = numbered ? [el("span", { className: "rule-rank" }, `${i + 1}.`), input] : [input];
        if (skillUi.editable) {
            const move = (delta, label) => {
                const b = el("button", { type: "button", className: "ghost", disabled: i + delta < 0 || i + delta >= items.length }, label);
                b.addEventListener("click", () => {
                    [items[i], items[i + delta]] = [items[i + delta], items[i]];
                    renderLineList(ul, items, placeholder, numbered);
                    scheduleSkillPreview();
                });
                return b;
            };
            const remove = el("button", { type: "button", className: "ghost danger-text", title: "Retirer" }, "✕");
            remove.addEventListener("click", () => {
                items.splice(i, 1);
                renderLineList(ul, items, placeholder, numbered);
                scheduleSkillPreview();
            });
            parts.push(move(-1, "↑"), move(1, "↓"), remove);
        }
        return el("li", {}, ...parts);
    });
    if (skillUi.editable) {
        const add = el("button", { type: "button", className: "ghost" }, numbered ? "+ Ajouter une étape" : "+ Ajouter");
        add.addEventListener("click", () => {
            items.push("");
            renderLineList(ul, items, placeholder, numbered);
            ul.querySelectorAll("input")[items.length - 1].focus();
        });
        rows.push(el("li", {}, add));
    }
    ul.replaceChildren(...rows);
}

function renderSkillCases() {
    const cases = skillUi.fields.methode;
    $("#skill-cases").replaceChildren(...cases.map((c, i) => {
        const title = el("input", {
            value: c.cas, disabled: !skillUi.editable,
            placeholder: cases.length > 1 ? "Nom du cas (ex. Corriger un bug)" : "Nom du cas (facultatif s'il n'y en a qu'un)",
        });
        title.addEventListener("input", () => { c.cas = title.value; scheduleSkillPreview(); });
        const head = el("div", { className: "skill-case-head" }, title);
        if (skillUi.editable && cases.length > 1) {
            const remove = el("button", { type: "button", className: "ghost danger-text", title: "Retirer ce cas" }, "✕");
            remove.addEventListener("click", () => { cases.splice(i, 1); renderSkillCases(); scheduleSkillPreview(); });
            head.append(remove);
        }
        const steps = el("ol", { className: "rules-list" });
        renderLineList(steps, c.etapes, "Ex. Rassemble le contexte : message complet, version, système.", true);
        return el("div", { className: "skill-case" }, head, steps);
    }));
    $("#btn-case-add").hidden = !skillUi.editable;
}

function renderSkillForm() {
    const form = $("#skill-form");
    const f = skillUi.fields;
    form.name.value = f.name;
    form.registre.value = f.registre || "sobre";
    form.description.value = f.description;
    form.objectif.value = f.objectif;
    form.format.value = f.format;
    form.exemple_demande.value = f.exemple?.demande || "";
    form.exemple_reponse.value = f.exemple?.reponse || "";
    for (const ul of form.querySelectorAll("[data-list]")) {
        const key = ul.getAttribute("data-list");
        renderLineList(ul, skillList(key), SKILL_LISTS[key]);
    }
    renderSkillCases();
    for (const field of [form.registre, form.description, form.objectif, form.format, form.exemple_demande,
        form.exemple_reponse]) {
        field.disabled = !skillUi.editable;
    }
    form.markdown.readOnly = !skillUi.editable;
}

// Les cases simples du formulaire -> skillUi.fields (les listes s'y écrivent au fil de la frappe)
function readSkillForm() {
    const form = $("#skill-form");
    Object.assign(skillUi.fields, {
        name: form.name.value.trim(), registre: form.registre.value, description: form.description.value,
        objectif: form.objectif.value, format: form.format.value,
        exemple: { demande: form.exemple_demande.value, reponse: form.exemple_reponse.value },
    });
    return skillUi.fields;
}

function showSkillTokens(text) {
    $("#skill-tokens").textContent = `≈ ${Math.round(text.length / 4)} tokens une fois chargée`;
}

function scheduleSkillPreview() {
    clearTimeout(skillUi.previewTimer);
    skillUi.previewTimer = setTimeout(async () => {
        if ($("#skill-text-mode").checked) {
            showSkillTokens($("#skill-form").markdown.value);
            return;
        }
        const { markdown } = await api("/api/skills/convert", { method: "POST", body: JSON.stringify({ fields: readSkillForm() }) });
        $("#skill-preview pre").textContent = markdown;
        showSkillTokens(markdown);
    }, 250);
}

function setSkillTextMode(on) {
    $("#skill-text-mode").checked = on;
    $("#skill-fields").hidden = on;
    $("#skill-preview").hidden = on;
    $("#skill-text-box").hidden = !on;
    $("#skill-form").registre.closest("label").hidden = on;  // en mode texte, l'en-tête du fichier fait foi
}

$("#skill-text-mode").addEventListener("change", async (event) => {
    const form = $("#skill-form");
    $("#skill-error").hidden = true;
    if (event.target.checked) {
        const { markdown } = await api("/api/skills/convert", { method: "POST", body: JSON.stringify({ fields: readSkillForm() }) });
        form.markdown.value = markdown;
        setSkillTextMode(true);
    } else {
        const { fields } = await api("/api/skills/convert", { method: "POST", body: JSON.stringify({ markdown: form.markdown.value }) });
        if (!fields) {
            event.target.checked = true;
            $("#skill-error").textContent = "Ce texte sort du modèle (section inconnue ou paragraphe libre) : il reste en mode texte.";
            $("#skill-error").hidden = false;
            return;
        }
        skillUi.fields = { ...fields, name: form.name.value.trim() || fields.name };
        renderSkillForm();
        setSkillTextMode(false);
    }
    scheduleSkillPreview();
});

$("#skill-form").markdown.addEventListener("input", scheduleSkillPreview);
for (const name of ["registre", "description", "objectif", "format", "exemple_demande", "exemple_reponse"]) {
    $("#skill-form")[name].addEventListener("input", scheduleSkillPreview);
}

async function editSkill(name) {
    const form = $("#skill-form");
    $("#skill-error").hidden = true;
    $("#skill-preview").open = false;
    form.name.readOnly = Boolean(name);
    let source = "perso";
    if (name) {
        const s = await api(`/api/skills/${name}`);
        source = s.source;
        skillUi.editable = s.editable;
        skillUi.fields = s.fields || { ...emptySkillFields(), name: s.name, description: s.description, registre: s.registre };
        form.markdown.value = s.markdown;
        // Une compétence en texte libre (hors modèle) s'édite en mode texte
        setSkillTextMode(!s.fields);
    } else {
        skillUi.editable = true;
        skillUi.fields = emptySkillFields();
        form.markdown.value = "";
        setSkillTextMode(false);
    }
    skillUi.name = name;
    renderSkillForm();
    $("#skill-form-title").textContent = name ? `🧩 /${name}` : "🧩 Nouvelle compétence";
    const note = $("#skill-readonly");
    note.hidden = skillUi.editable;
    note.textContent = source === "intégrée"
        ? "Compétence intégrée, en lecture seule. « Personnaliser » en fait ta copie modifiable, qui la remplace."
        : `Compétence du projet : elle se modifie dans .aria/skills/${name}/SKILL.md.`;
    $("#btn-skill-customize").hidden = skillUi.editable || source !== "intégrée";
    $("#btn-skill-save").hidden = !skillUi.editable;
    $("#btn-skill-cancel").textContent = skillUi.editable ? "Annuler" : "Fermer";
    $("#skills-manage").hidden = true;
    form.hidden = false;
    scheduleSkillPreview();
    if (!name) form.name.focus();
}

$("#btn-skill-new").addEventListener("click", () => editSkill(null));
$("#btn-skill-cancel").addEventListener("click", loadSkills);
$("#btn-case-add").addEventListener("click", () => {
    skillUi.fields.methode.push({ cas: "", etapes: [""] });
    renderSkillCases();
    scheduleSkillPreview();
});

$("#btn-skill-customize").addEventListener("click", async () => {
    try {
        await api(`/api/skills/${skillUi.name}/customize`, { method: "POST" });
        flashSaved();
        editSkill(skillUi.name);
    } catch (err) {
        $("#skill-error").textContent = err.message;
        $("#skill-error").hidden = false;
    }
});

$("#skill-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const name = form.name.value.trim();
    const body = $("#skill-text-mode").checked
        ? { name, description: "", body: form.markdown.value }
        : { fields: readSkillForm() };
    try {
        await api("/api/skills", { method: "POST", body: JSON.stringify(body) });
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

// --- Serveurs MCP ---------------------------------------------------------------------

const MCP_STATES = { pret: "🟢 prêt", demarrage: "🟡 démarrage…", erreur: "🔴 erreur", arrete: "⚪ arrêté" };
const MCP_MODES = [["ask", "Demander"], ["allow", "Toujours autoriser"], ["off", "Désactivé"]];
let mcpPoll = null;

function mcpSectionOpen() {
    return !$('.settings-section[data-section="mcp"]').hidden && !$("#view-settings").hidden;
}

async function loadMcp() {
    clearTimeout(mcpPoll);
    let data;
    try {
        data = await api("/api/mcp");
    } catch (err) {
        $("#mcp-config-error").textContent = err.message;
        $("#mcp-config-error").hidden = false;
        return;
    }
    renderMcp(data);
    // Serveurs qui démarrent encore (au lancement d'Aria) : on repasse voir
    if (data.servers.some((s) => s.state === "demarrage")) {
        mcpPoll = setTimeout(() => { if (mcpSectionOpen()) loadMcp(); }, 1500);
    }
}

function renderMcp(data) {
    state.mcp = data;
    syncMcpChip();
    $("#mcp-config-error").textContent = data.config_error ? `mcp.json : ${data.config_error}` : "";
    $("#mcp-config-error").hidden = !data.config_error;
    const textarea = $("#set-mcp");
    if (document.activeElement !== textarea) textarea.value = data.content;
    $("#mcp-path").textContent = data.config_path;
    const box = $("#mcp-servers");
    box.replaceChildren(...data.servers.map(mcpServerCard));
    if (!data.servers.length) {
        box.append(el("p", { className: "muted" }, "Aucun serveur pour l'instant : ajoute le premier ci-dessous."));
    }
}

async function mcpPatch(name, patch) {
    const data = await api(`/api/mcp/${encodeURIComponent(name)}`, { method: "PATCH", body: JSON.stringify(patch) });
    flashSaved();
    renderMcp({ ...state.mcp, ...data });
}

function mcpServerCard(s) {
    const card = el("div", { className: "set-card mcp-server" });
    const restart = el("button", { className: "ghost" }, "↻ Relancer");
    restart.addEventListener("click", async () => {
        restart.disabled = true;
        restart.textContent = "Démarrage…";
        try {
            renderMcp({ ...state.mcp, ...(await api(`/api/mcp/${encodeURIComponent(s.name)}/restart`, { method: "POST" })) });
        } catch (err) {
            alert(err.message);
            restart.disabled = false;
            restart.textContent = "↻ Relancer";
        }
    });
    const logButton = el("button", { className: "ghost" }, "Journal");
    const log = el("pre", { className: "mcp-log", hidden: true });
    logButton.addEventListener("click", async () => {
        if (!log.hidden) { log.hidden = true; return; }
        const r = await api(`/api/mcp/${encodeURIComponent(s.name)}/log`);
        log.textContent = r.text || "(rien d'écrit par le serveur)";
        log.hidden = false;
    });
    const remove = el("button", { className: "ghost danger-text" }, "Retirer");
    remove.addEventListener("click", async () => {
        if (!confirm(`Retirer le serveur « ${s.name} » de mcp.json ?`)) return;
        try {
            const config = JSON.parse(state.mcp.content);
            delete config.mcpServers[s.name];
            await saveMcpConfig(config);
        } catch (err) {
            alert(err.message);
        }
    });
    const badge = el("span", { className: `badge${s.state === "erreur" ? " err" : s.state === "pret" ? " loaded" : ""}` },
        MCP_STATES[s.state] || s.state);
    const count = s.state === "pret" ? `${s.tools.length} outil${s.tools.length > 1 ? "s" : ""} · ≈ ${s.tokens} tokens` : "";
    card.append(el("div", { className: "set-card-head" },
        el("div", {}, el("div", { className: "set-card-title" }, `${s.name}  `, badge), el("div", { className: "mcp-meta" }, s.target)),
        el("span", { className: "muted small nowrap", title: "Place prise dans le contexte par la description de ses outils" }, count)));
    const info = [s.type === "http" ? "distant" : "local"];
    if (s.server_info?.name) info.push(`${s.server_info.name}${s.server_info.version ? ` ${s.server_info.version}` : ""}`);
    if (s.protocol) info.push(`protocole ${s.protocol}`);
    card.append(el("div", { className: "mcp-meta" }, info.join(" · ")));

    const enabled = el("input", { type: "checkbox", checked: s.enabled });
    enabled.addEventListener("change", () => mcpPatch(s.name, { enabled: enabled.checked }).catch((e) => alert(e.message)));
    const byDefault = el("input", { type: "checkbox", checked: s.default_active, disabled: !s.enabled });
    byDefault.addEventListener("change", () => mcpPatch(s.name, { default_active: byDefault.checked }).catch((e) => alert(e.message)));
    card.append(el("div", { className: "mcp-toggles" },
        el("label", { className: "check-line" }, enabled, " Activé (démarré avec Aria)"),
        el("label", { className: "check-line" }, byDefault,
            " Actif d'office dans les conversations (sinon, à cocher avec 🔌 dans la conversation)")));
    if (s.error) card.append(el("pre", { className: "mcp-error" }, s.error));

    if (s.tools.length) {
        card.append(el("ul", { className: "skills-list" }, ...s.tools.map((t) => {
            const mode = el("select", { title: "Ce que fait Aria quand l'agent appelle cet outil" },
                ...MCP_MODES.map(([value, label]) => el("option", { value, selected: t.mode === value }, label)));
            mode.addEventListener("change", () => mcpPatch(s.name, { tools: { [t.name]: mode.value } }).catch((e) => alert(e.message)));
            const hints = [];
            if (t.read_only) hints.push(el("span", { className: "badge", title: "Indication du serveur : elle ne vaut pas accord" }, "lecture seule"));
            if (t.destructive) hints.push(el("span", { className: "badge err" }, "destructeur"));
            return el("li", { className: "static" },
                el("div", { className: "skill-text" },
                    el("div", { className: "skill-name" }, t.name, " ", ...hints),
                    el("div", { className: "skill-desc" }, t.description)),
                mode);
        })));
    }
    card.append(el("div", { className: "row" }, restart, logButton, remove), log);
    return card;
}

async function saveMcpConfig(config) {
    await api("/api/mcp/config", { method: "PUT", body: JSON.stringify({ content: JSON.stringify(config, null, 4) }) });
    flashSaved();
    await loadMcp();
}

function mcpLines(text) {
    return text.split("\n").map((l) => l.trim()).filter(Boolean);
}

function mcpPairs(text, separator, what) {
    const out = {};
    for (const line of mcpLines(text)) {
        const at = line.indexOf(separator);
        if (at < 1) throw new Error(`${what} : « ${line} » n'a pas la forme Nom${separator}valeur.`);
        out[line.slice(0, at).trim()] = line.slice(at + 1).trim();
    }
    return out;
}

$("#mcp-new-type").addEventListener("change", () => {
    const type = $("#mcp-new-type").value;
    for (const label of document.querySelectorAll(".mcp-form [data-mcp]")) label.hidden = label.dataset.mcp !== type;
});

$("#btn-mcp-add").addEventListener("click", async () => {
    const error = $("#mcp-new-error");
    error.hidden = true;
    const button = $("#btn-mcp-add");
    try {
        const name = $("#mcp-new-name").value.trim();
        if (!/^[A-Za-z0-9_-]{1,32}$/.test(name)) throw new Error("Nom : lettres, chiffres, - et _ (32 au plus).");
        const config = JSON.parse(state.mcp?.content || '{"mcpServers": {}}');
        config.mcpServers ||= {};
        if (config.mcpServers[name]) throw new Error(`Le serveur « ${name} » existe déjà.`);
        if ($("#mcp-new-type").value === "http") {
            const spec = { url: $("#mcp-new-url").value.trim() };
            const headers = mcpPairs($("#mcp-new-headers").value, ":", "En-têtes");
            if (Object.keys(headers).length) spec.headers = headers;
            config.mcpServers[name] = spec;
        } else {
            const spec = { command: $("#mcp-new-command").value.trim(), args: mcpLines($("#mcp-new-args").value) };
            const env = mcpPairs($("#mcp-new-env").value, "=", "Variables");
            if (Object.keys(env).length) spec.env = env;
            config.mcpServers[name] = spec;
        }
        button.disabled = true;
        button.textContent = "Démarrage…";
        await saveMcpConfig(config);
        for (const id of ["#mcp-new-name", "#mcp-new-command", "#mcp-new-args", "#mcp-new-env", "#mcp-new-url", "#mcp-new-headers"]) {
            $(id).value = "";
        }
    } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
    } finally {
        button.disabled = false;
        button.textContent = "Ajouter et démarrer";
    }
});

// mcp.json s'enregistre au bouton, comme les hooks : un JSON à moitié collé ne remplace jamais le bon
$("#btn-mcp-save").addEventListener("click", async () => {
    const error = $("#mcp-json-error");
    error.hidden = true;
    const button = $("#btn-mcp-save");
    button.disabled = true;
    try {
        await api("/api/mcp/config", { method: "PUT", body: JSON.stringify({ content: $("#set-mcp").value }) });
        flashSaved();
        $("#set-mcp").blur();
        await loadMcp();
    } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
    } finally {
        button.disabled = false;
    }
});

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

// --- Presets --------------------------------------------------------------------------------

const presetUi = { selected: null, focused: null };

// Encadré « Comprendre ce réglage » : il suit le champ survolé ou en cours de modification
const PRESET_HELP = {
    overview: {
        title: "Comprendre les réglages",
        text: ["Un preset décide de la façon dont le modèle répond, quel que soit le modèle chargé.",
            "Le prompt système et la réflexion changent ce que le modèle lit. Les autres réglages ne sont pas lus par "
            + "le modèle : ils décident comment on pioche chaque mot parmi ceux qu'il propose, avec leurs probabilités.",
            "Survole ou modifie un réglage pour en voir le détail ici."],
    },
    name: {
        title: "Nom",
        text: ["Le nom affiché dans la puce 🎛 du chat et dans cette liste. Choisis-le d'après l'usage "
            + "(« Code précis », « Brainstorming ») plutôt que d'après un modèle : un preset vaut pour tous."],
    },
    thinking: {
        title: "Réflexion",
        text: ["Les modèles qui savent raisonner peuvent écrire une réflexion avant leur réponse (repliable dans le chat).",
            "Forcée : meilleure sur la logique, les maths, le code et les plans, mais plus lente et plus gourmande en tokens.",
            "Coupée : réponse directe, idéale pour discuter, reformuler ou répondre à une question simple.",
            "Le modèle décide : il réfléchit quand il le juge utile. Sur un modèle qui ne sait pas raisonner, ce réglage est sans effet."],
    },
    thinking_budget: {
        title: "Budget de réflexion",
        text: ["Le nombre maximal de tokens de réflexion. Au-delà, le moteur referme la réflexion et le modèle doit répondre.",
            "Trop bas, le raisonnement est coupé avant d'aboutir. Vide : sans limite, mais en tâche d'agent, Aria plafonne "
            + "quand même la réflexion à 2 048 tokens pour qu'il agisse au lieu de réfléchir sans fin."],
        values: [["Vide", "sans limite"], ["1 024 à 4 096", "le plus courant"], ["8 192 et plus", "problèmes difficiles"]],
    },
    temperature: {
        title: "Température",
        text: ["Elle aplatit ou accentue l'écart entre les mots probables et les autres. Basse, le modèle prend presque "
            + "toujours le mot le plus probable : précis mais répétitif. Haute, il ose : varié, puis incohérent.",
            "Elle agit avec Top K, Top P et Min P, qui écartent d'abord les mots trop improbables."],
        values: [["0 à 0,3", "code, faits, extraction"], ["0,6 à 0,8", "discussion"], ["0,9 à 1,2", "création, brainstorming"],
            ["Au-delà de 1,3", "souvent incohérent"]],
    },
    presence_penalty: {
        title: "Presence penalty",
        text: ["Pénalise tout mot déjà apparu dans les derniers mots générés (une seule apparition suffit) et pousse vers des mots et des idées neufs.",
            "Efficace contre les répétitions et les boucles, fréquentes sur les modèles fortement quantifiés. Trop haute, "
            + "elle gêne les textes qui doivent répéter un terme : un nom de variable dans du code, un mot technique."],
        values: [["0", "neutre"], ["0,5 à 1,5", "contre les répétitions"], ["Négative", "encourage la répétition (rarement utile)"]],
    },
    top_k: {
        title: "Top K",
        text: ["Ne garde que les K mots les plus probables avant le tirage. Plus K est petit, plus le modèle reste prudent."],
        values: [["0", "désactivé"], ["20 à 40", "le plus courant"], ["1", "toujours le mot favori"]],
    },
    top_p: {
        title: "Top P",
        text: ["Garde le plus petit groupe de mots dont les probabilités additionnées atteignent P, puis tire dedans. "
            + "Le groupe s'élargit quand le modèle hésite et se resserre quand il est sûr de lui."],
        values: [["1", "désactivé"], ["0,8 à 0,95", "le plus courant"]],
    },
    min_p: {
        title: "Min P",
        text: ["Écarte les mots dont la probabilité est inférieure à cette fraction de celle du mot favori. "
            + "À 0,05 et avec un favori à 60 %, tout mot sous 3 % est écarté.",
            "Il s'adapte à la confiance du modèle, ce qui le rend souvent plus fiable que Top P à température élevée."],
        values: [["0", "désactivé"], ["0,05", "bon compromis"], ["0,1 et plus", "plus prudent"]],
    },
    max_tokens: {
        title: "Max tokens",
        text: ["La longueur maximale d'une réponse, réflexion comprise. Atteinte, la réponse est coupée : le chat te "
            + "propose alors de laisser le modèle continuer.",
            "En tâche d'agent, Aria la remonte à 8 192 au minimum. Elle ne réserve rien : c'est un plafond, pas une place dans le contexte."],
        values: [["2 048 à 4 096", "discussion"], ["8 192 à 16 384", "réflexion, longs textes, code"]],
    },
};

function renderPresetHelp(key) {
    const help = PRESET_HELP[key] || PRESET_HELP.overview;
    $("#preset-help-title").textContent = help.title;
    const body = help.text.map((t) => el("p", {}, t));
    if (help.values) {
        body.push(el("div", { className: "help-values" }, ...help.values.flatMap(([value, use]) =>
            [el("span", { className: "mono" }, value), el("span", {}, use)])));
    }
    $("#preset-help-body").replaceChildren(...body);
}

function presetFieldKey(target) {
    const field = target.closest("label")?.querySelector("input, select, textarea");
    return field?.name || null;
}

$("#preset-form").addEventListener("mouseover", (event) => renderPresetHelp(presetFieldKey(event.target) || presetUi.focused));
$("#preset-form").addEventListener("mouseleave", () => renderPresetHelp(presetUi.focused));
$("#preset-form").addEventListener("focusin", (event) => {
    presetUi.focused = presetFieldKey(event.target);
    renderPresetHelp(presetUi.focused);
});
const PRESET_NUMBERS = ["temperature", "top_p", "top_k", "min_p", "presence_penalty", "max_tokens", "thinking_budget"];

function presetBadges(p) {
    const badges = el("div", { className: "badges" });
    if (p.id === state.config.default_preset) badges.append(el("span", { className: "badge loaded" }, "⭐ par défaut"));
    if (p.thinking === true) badges.append(el("span", { className: "badge" }, "réflexion forcée"));
    if (p.thinking === false) badges.append(el("span", { className: "badge" }, "réflexion coupée"));
    if (p.temperature !== null && p.temperature !== undefined) badges.append(el("span", { className: "badge" }, `t° ${p.temperature}`));
    return badges;
}

async function loadPresetsSection(selectId) {
    await loadPresets();  // la puce du chat suit
    if (selectId) presetUi.selected = selectId;
    if (!chat.presets.some((p) => p.id === presetUi.selected)) {
        presetUi.selected = state.config.default_preset || chat.presets[0]?.id;
    }
    $("#preset-list").replaceChildren(...chat.presets.map((p) => {
        const item = el("li", { className: p.id === presetUi.selected ? "selected" : "" },
            el("div", { className: "title" }, p.name), presetBadges(p));
        item.addEventListener("click", () => loadPresetsSection(p.id));
        return item;
    }));
    fillPresetForm();
    renderPresetHelp(presetUi.focused);
}

function fillPresetForm() {
    const p = chat.presets.find((x) => x.id === presetUi.selected);
    const form = $("#preset-form");
    if (!p) return;
    form.name.value = p.name;
    for (const key of PRESET_NUMBERS) form[key].value = p[key] ?? "";
    form.thinking.value = p.thinking === null || p.thinking === undefined ? "" : String(p.thinking);
    for (const field of form.elements) field.classList.remove("invalid");
    $("#preset-error").hidden = true;
    $("#btn-preset-default").disabled = p.id === state.config.default_preset;
    $("#btn-preset-delete").disabled = chat.presets.length <= 1;
}

function readPreset() {
    const form = $("#preset-form");
    const data = { name: form.name.value.trim(),
        thinking: form.thinking.value === "" ? null : form.thinking.value === "true" };
    for (const key of PRESET_NUMBERS) data[key] = form[key].value === "" ? null : Number(form[key].value);
    return data;
}

// Enregistrement automatique : un champ hors limites passe en rouge et rien n'est envoyé
$("#preset-form").addEventListener("change", async (event) => {
    const field = event.target;
    const valid = field.checkValidity() && (field.name !== "name" || field.value.trim());
    field.classList.toggle("invalid", !valid);
    if (!valid) return;
    $("#preset-error").hidden = true;
    try {
        await api(`/api/presets/${presetUi.selected}`, { method: "PUT", body: JSON.stringify(readPreset()) });
        flashSaved();
        await loadPresetsSection();
    } catch (err) {
        $("#preset-error").textContent = err.message;
        $("#preset-error").hidden = false;
    }
});

async function createPreset(data) {
    const created = await api("/api/presets", { method: "POST", body: JSON.stringify(data) });
    flashSaved();
    await loadPresetsSection(created.id);
    $("#preset-form").name.select();
}

$("#btn-preset-new").addEventListener("click", () => {
    // Un nouveau preset part des réglages du preset par défaut
    const base = chat.presets.find((p) => p.id === state.config.default_preset) || chat.presets[0] || {};
    const { id, ...rest } = base;
    createPreset({ ...rest, name: "Nouveau preset" });
});

$("#btn-preset-duplicate").addEventListener("click", () => {
    const { id, ...rest } = chat.presets.find((p) => p.id === presetUi.selected);
    createPreset({ ...rest, name: `${rest.name} (copie)`.slice(0, 60) });
});

$("#btn-preset-default").addEventListener("click", async () => {
    await saveConfig({ default_preset: presetUi.selected });
    await loadPresetsSection();
});

$("#btn-preset-delete").addEventListener("click", async () => {
    const p = chat.presets.find((x) => x.id === presetUi.selected);
    if (!confirm(`Supprimer le preset « ${p.name} » ? Les conversations qui l'utilisent passeront sur le preset par défaut.`)) return;
    try {
        await api(`/api/presets/${p.id}`, { method: "DELETE" });
        state.config = await api("/api/config");  // le preset par défaut a pu changer
        flashSaved();
        await loadPresetsSection(state.config.default_preset);
    } catch (err) {
        alert(err.message);
    }
});

const LOADERS = {
    general: loadGeneral,
    "modele-defaut": loadDefaultModel,
    "mes-modeles": loadModelsSection,
    persona: () => loadPersonaSection(),
    presets: () => loadPresetsSection(),
    moteurs: loadEnginesSection,
    materiel: loadHardwareSection,
    autorisations: loadRules,
    competences: loadSkills,
    hooks: loadHooks,
    mcp: loadMcp,
    comportement: () => fillConfigFields($('.settings-section[data-section="comportement"]')),
    web: () => {
        fillConfigFields($('.settings-section[data-section="web"]'));
        loadSearx();
    },
    donnees: loadData,
};

// app.js lit la config au démarrage : on attend qu'elle soit là avant d'afficher une section
(async function startRouting() {
    // Tous les scripts de la page chargés (persona.js vient après celui-ci), et la config lue par app.js
    if (document.readyState === "loading") await new Promise((resolve) => document.addEventListener("DOMContentLoaded", resolve));
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
