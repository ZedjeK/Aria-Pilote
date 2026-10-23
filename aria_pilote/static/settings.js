// Page Réglages : navigation à gauche, une adresse par section (#/reglages/<section>), enregistrement automatique.
// S'appuie sur $, api, el, state, loadModels, loadEngines, renderHardware (app.js) et SKILL_SOURCES (chat.js).

const SECTIONS = ["general", "modele-defaut", "mes-modeles", "decouvrir", "moteurs", "materiel",
    "autorisations", "competences", "hooks"];

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
        } catch (err) {
            field.classList.add("invalid");
            alert(err.message);
        }
    });
}

// --- Sections ------------------------------------------------------------------------

function loadGeneral() {
    $("#set-version").textContent = state.status?.version ? `v${state.status.version}` : "?";
}

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

const LOADERS = {
    general: loadGeneral,
    "modele-defaut": loadDefaultModel,
    "mes-modeles": () => loadModels(),
    moteurs: loadEnginesSection,
    materiel: loadHardwareSection,
    autorisations: loadRules,
    competences: loadSkills,
    hooks: loadHooks,
};

// app.js lit la config au démarrage : on attend qu'elle soit là avant d'afficher une section
(async function startRouting() {
    while (!state.config) await new Promise((resolve) => setTimeout(resolve, 50));
    route();
})();
