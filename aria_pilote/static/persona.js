// Fiche « Toi » (Réglages › Général) et page Persona (Réglages › Assistant › Persona).
// S'appuie sur $, api, el, state (app.js), flashSaved, saveConfig (settings.js) et loadPersonas (chat.js).

const personaUi = { catalogue: null, list: [], defaultId: null, current: null, skills: [], saveTimer: null };
const REGISTER_IDS = ["naturel", "sobre", "professionnel"];
const tokensOf = (chars) => `≈ ${Math.round(chars / 4).toLocaleString("fr-FR")} tokens`;  // estimation, sans modèle

// --- Fiche « Toi » ------------------------------------------------------------------------------

async function loadProfileCard() {
    const profile = await api("/api/profile");
    for (const field of document.querySelectorAll("[data-profile]")) {
        const key = field.dataset.profile;
        if (field.type === "checkbox") field.checked = Boolean(profile[key]);
        else field.value = profile[key] ?? "";
    }
    renderProfilePreview(profile.preview);
}

function renderProfilePreview(text) {
    $("#profile-preview").textContent = text || "Fiche vide : le modèle ne lit rien sur toi pour l'instant.";
    $("#profile-tokens").textContent = text ? tokensOf(text.length) : "";
}

for (const field of document.querySelectorAll("[data-profile]")) {
    field.addEventListener("change", async () => {
        const data = {};
        for (const f of document.querySelectorAll("[data-profile]")) {
            data[f.dataset.profile] = f.type === "checkbox" ? f.checked : f.value;
        }
        try {
            const saved = await api("/api/profile", { method: "PUT", body: JSON.stringify(data) });
            renderProfilePreview(saved.preview);
            flashSaved();
            refreshProfileBanner();
        } catch (err) {
            alert(err.message);
        }
    });
}

// --- Page Persona -----------------------------------------------------------------------------------

async function loadPersonaSection(selectId) {
    if (!personaUi.catalogue) personaUi.catalogue = await api("/api/persona-catalogue");
    const [{ personas, default: defaultId }, skills] = await Promise.all([api("/api/personas"), api("/api/skills")]);
    personaUi.list = personas;
    personaUi.defaultId = defaultId;
    personaUi.skills = skills.map((s) => ({ name: s.name, registre: s.registre || "sobre" }));
    const wanted = selectId || personaUi.current?.id || defaultId;
    personaUi.current = structuredClone(personas.find((p) => p.id === wanted) || personas[0]);
    renderPersonaPage();
}

function renderPersonaPage() {
    const p = personaUi.current;
    const pick = $("#persona-pick");
    pick.replaceChildren(...personaUi.list.map((x) => el("option", { value: x.id },
        `${x.avatar ? x.avatar + " " : ""}${x.name}${x.id === personaUi.defaultId ? " ⭐" : ""}`)));
    pick.value = p.id;
    $("#persona-default-flag").hidden = p.id !== personaUi.defaultId;
    $("#btn-persona-default").disabled = p.id === personaUi.defaultId;
    $("#btn-persona-delete").disabled = personaUi.list.length <= 1;
    $("#btn-persona-export").href = `/api/personas/${p.id}/export`;
    $("#persona-error").hidden = true;
    for (const field of document.querySelectorAll("[data-persona]")) {
        const key = field.dataset.persona;
        if (field.type === "checkbox") field.checked = Boolean(p[key]);
        else field.value = p[key] ?? "";
    }
    renderTraitGrid();
    renderThemes();
    renderRules();
    renderRegisters();
    renderPersonaPreview();
}

// Enregistrement : tout le persona d'un coup, un court instant après la dernière modification
function savePersona() {
    clearTimeout(personaUi.saveTimer);
    personaUi.saveTimer = setTimeout(async () => {
        const p = personaUi.current;
        try {
            const saved = await api(`/api/personas/${p.id}`, { method: "PUT", body: JSON.stringify(p) });
            personaUi.current = { ...saved, rules: p.rules };  // les règles en cours de saisie restent affichées
            const index = personaUi.list.findIndex((x) => x.id === saved.id);
            personaUi.list[index] = saved;
            flashSaved();
            $("#persona-error").hidden = true;
            const pick = $("#persona-pick");
            pick.options[pick.selectedIndex].textContent =
                `${saved.avatar ? saved.avatar + " " : ""}${saved.name}${saved.id === personaUi.defaultId ? " ⭐" : ""}`;
            renderPersonaPreview();
            loadPersonas();  // la puce 🎭 du chat suit
        } catch (err) {
            $("#persona-error").textContent = err.message;
            $("#persona-error").hidden = false;
        }
    }, 300);
}

for (const field of document.querySelectorAll("[data-persona]")) {
    field.addEventListener("change", () => {
        personaUi.current[field.dataset.persona] = field.type === "checkbox" ? field.checked : field.value;
        savePersona();
    });
}

$("#persona-pick").addEventListener("change", () => loadPersonaSection($("#persona-pick").value));

// Grille des traits : une ligne par trait, trois registres, trois crans chacun
function dots(traitId, register) {
    const level = personaUi.current.traits[traitId][register] || 0;
    const box = el("span", { className: "dots", title: `${["absent", "léger", "marqué", "très marqué"][level]}` });
    for (let k = 1; k <= 3; k++) {
        const dot = el("button", { type: "button", className: `dot${k <= level ? " on" : ""}`, title: ["léger", "marqué", "très marqué"][k - 1] });
        dot.addEventListener("click", () => {
            // Clic sur le cran déjà atteint : on redescend d'un cran (jusqu'à absent)
            personaUi.current.traits[traitId][register] = level === k ? k - 1 : k;
            renderTraitGrid();
            renderThemes();
            savePersona();
        });
        box.append(dot);
    }
    return box;
}

function renderTraitGrid() {
    const grid = $("#trait-grid");
    const traits = personaUi.current.traits;
    const byId = Object.fromEntries(personaUi.catalogue.traits.map((t) => [t.id, t]));
    const head = [el("span"), ...personaUi.catalogue.registers.map((r) => el("span", { className: "grid-head" }, r.label)), el("span")];
    const rows = Object.keys(traits).filter((id) => byId[id]).flatMap((id) => {
        const remove = el("button", { type: "button", className: "ghost remove", title: "Retirer ce trait" }, "✕");
        remove.addEventListener("click", () => {
            delete personaUi.current.traits[id];
            renderTraitGrid();
            renderThemes();
            savePersona();
        });
        const label = el("span", { className: "trait-name", title: byId[id].definition }, id === "gouts" ? "🎨 Goûts" : byId[id].label);
        return [label, ...REGISTER_IDS.map((r) => dots(id, r)), remove];
    });
    grid.replaceChildren(...head, ...rows);
    grid.hidden = !rows.length;  // sans trait, seule reste « + Ajouter un trait »
}

// Menu « + Ajouter un trait » : le catalogue par famille, sans les traits déjà choisis
$("#btn-trait-add").addEventListener("click", () => {
    const menu = $("#trait-menu");
    if (!menu.hidden) {
        menu.hidden = true;
        return;
    }
    const chosen = new Set(Object.keys(personaUi.current.traits));
    const groups = personaUi.catalogue.families.map((family) => {
        const items = personaUi.catalogue.traits.filter((t) => t.family === family.id && !chosen.has(t.id));
        if (!items.length) return null;
        return el("div", { className: "trait-family" }, el("div", { className: "set-label" }, family.label),
            ...items.map((t) => {
                const item = el("button", { type: "button", className: "trait-option" },
                    el("span", {}, t.id === "gouts" ? "🎨 Goûts" : t.label), el("span", { className: "muted small" }, t.definition));
                item.addEventListener("click", () => {
                    personaUi.current.traits[t.id] = { ...t.suggested };  // intensités suggérées, modifiables
                    menu.hidden = true;
                    renderTraitGrid();
                    renderThemes();
                    savePersona();
                });
                return item;
            }));
    }).filter(Boolean);
    menu.replaceChildren(...(groups.length ? groups : [el("p", { className: "muted small" }, "Tous les traits sont déjà choisis.")]));
    menu.hidden = false;
});

function renderThemes() {
    const box = $("#themes-box");
    box.hidden = !personaUi.current.traits.gouts;
    if (box.hidden) return;
    const chosen = new Set(personaUi.current.themes || []);
    $("#themes-list").replaceChildren(...personaUi.catalogue.themes.map((theme) => {
        const check = el("input", { type: "checkbox", checked: chosen.has(theme.id) });
        check.addEventListener("change", () => {
            const themes = new Set(personaUi.current.themes || []);
            if (check.checked) themes.add(theme.id);
            else themes.delete(theme.id);
            personaUi.current.themes = personaUi.catalogue.themes.map((t) => t.id).filter((id) => themes.has(id));
            savePersona();
        });
        return el("label", { className: "theme" }, check, theme.label);
    }));
}

// Règles d'or : une par ligne, dans l'ordre de priorité
function renderRules() {
    const rules = personaUi.current.rules || [];
    $("#rules-list").replaceChildren(...rules.map((rule, i) => {
        const input = el("input", { value: rule, maxLength: 300, placeholder: "Ex. Ne jamais inventer un fait : dire « je ne sais pas » et vérifier." });
        input.addEventListener("change", () => {
            personaUi.current.rules[i] = input.value;
            savePersona();
        });
        const move = (delta, label, title) => {
            const b = el("button", { type: "button", className: "ghost", title, disabled: i + delta < 0 || i + delta >= rules.length }, label);
            b.addEventListener("click", () => {
                const list = personaUi.current.rules;
                [list[i], list[i + delta]] = [list[i + delta], list[i]];
                renderRules();
                savePersona();
            });
            return b;
        };
        const remove = el("button", { type: "button", className: "ghost danger-text", title: "Retirer" }, "✕");
        remove.addEventListener("click", () => {
            personaUi.current.rules.splice(i, 1);
            renderRules();
            savePersona();
        });
        return el("li", {}, el("span", { className: "rule-rank" }, `${i + 1}.`), input,
            move(-1, "↑", "Plus prioritaire"), move(1, "↓", "Moins prioritaire"), remove);
    }));
    $("#btn-rule-add").disabled = rules.length >= 20;
}

$("#btn-rule-add").addEventListener("click", () => {
    personaUi.current.rules = [...(personaUi.current.rules || []), ""];
    renderRules();
    $("#rules-list").lastElementChild.querySelector("input").focus();
});

// Registre selon le contexte : la discussion, puis chaque compétence existante
function renderRegisters() {
    const registers = personaUi.current.registers || {};
    // Sans réglage dans le persona, une compétence prend le registre qu'elle suggère
    const contexts = [{ name: "discussion", registre: "naturel" }, ...personaUi.skills];
    const options = personaUi.catalogue.registers;
    $("#register-table").replaceChildren(...contexts.flatMap(({ name: context, registre }) => {
        const select = el("select", {}, ...options.map((r) => el("option", { value: r.id }, r.label)));
        select.value = registers[context] || registre;
        select.addEventListener("change", () => {
            personaUi.current.registers = { ...personaUi.current.registers, [context]: select.value };
            savePersona();
        });
        return [el("span", {}, context === "discussion" ? "💬 Discussion" : `🧩 ${context}`), select];
    }));
}

async function renderPersonaPreview() {
    const register = $("#persona-preview-register").value;
    try {
        const preview = await api(`/api/personas/${personaUi.current.id}/preview?register=${register}`);
        $("#persona-preview").textContent = [preview.persona, preview.profile].filter(Boolean).join("\n\n");
        $("#persona-tokens").textContent = `${tokensOf(preview.chars)} à chaque message (persona et fiche Toi), en registre ${register}`;
    } catch {
        // persona supprimé entre-temps : la page se recharge
    }
}

$("#persona-preview-register").addEventListener("change", renderPersonaPreview);

// --- Barre de gestion ----------------------------------------------------------------------------------

async function createPersonaFrom(data) {
    try {
        const created = await api("/api/personas", { method: "POST", body: JSON.stringify(data) });
        flashSaved();
        await loadPersonaSection(created.id);
        loadPersonas();
        return created;
    } catch (err) {
        alert(err.message);
        return null;
    }
}

$("#btn-persona-new").addEventListener("click", async () => {
    await createPersonaFrom({ name: "Nouveau persona", gender: "feminin", traits: {}, rules: [], registers: { discussion: "naturel" } });
    document.querySelector('[data-persona="name"]').select();
});

$("#btn-persona-duplicate").addEventListener("click", () => {
    const { id, ...copy } = personaUi.current;
    createPersonaFrom({ ...copy, name: `${copy.name} (copie)`.slice(0, 40) });
});

$("#btn-persona-default").addEventListener("click", async () => {
    await saveConfig({ default_persona: personaUi.current.id });
    await loadPersonaSection(personaUi.current.id);
    loadPersonas();
});

$("#btn-persona-delete").addEventListener("click", async () => {
    const p = personaUi.current;
    if (!confirm(`Supprimer le persona « ${p.name} » ? Ses conversations passeront sur le persona par défaut.`)) return;
    try {
        const result = await api(`/api/personas/${p.id}`, { method: "DELETE" });
        state.config = await api("/api/config");
        flashSaved();
        personaUi.current = null;
        await loadPersonaSection(result.default);
        loadPersonas();
    } catch (err) {
        alert(err.message);
    }
});

$("#btn-persona-import").addEventListener("click", () => $("#persona-import-file").click());

$("#persona-import-file").addEventListener("change", async () => {
    const file = $("#persona-import-file").files[0];
    $("#persona-import-file").value = "";
    if (!file) return;
    let data;
    try {
        data = JSON.parse(await file.text());
    } catch {
        alert("Ce fichier n'est pas un persona lisible (JSON attendu).");
        return;
    }
    try {
        const result = await api("/api/personas/import", { method: "POST", body: JSON.stringify(data) });
        flashSaved();
        await loadPersonaSection(result.persona.id);
        loadPersonas();
        if (result.warnings.length) alert(`Persona importé, avec des éléments ignorés :\n${result.warnings.join("\n")}`);
    } catch (err) {
        alert(err.message);
    }
});

// Clic ailleurs : le menu des traits se ferme
document.addEventListener("click", (event) => {
    const menu = $("#trait-menu");
    if (!menu.hidden && !event.target.closest(".trait-add")) menu.hidden = true;
});
