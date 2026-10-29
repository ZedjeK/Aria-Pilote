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
    $("#family-sections").replaceChildren();  // autre persona : on repart de « ouvert s'il a des traits »
    renderPersonality();
    renderRules();
    renderRegisters();
    renderPersonaPreview();
}

// Enregistrement : tout le persona d'un coup, un court instant après la dernière modification
function savePersona() {
    personaUi.version = (personaUi.version || 0) + 1;
    clearTimeout(personaUi.saveTimer);
    personaUi.saveTimer = setTimeout(async () => {
        const p = personaUi.current;
        const version = personaUi.version;
        try {
            const saved = await api(`/api/personas/${p.id}`, { method: "PUT", body: JSON.stringify(p) });
            // Le même objet est mis à jour, pas remplacé : les boutons déjà affichés le modifient (avant, leur clic
            // suivant partait sur l'ancien objet et se perdait). Une modif faite pendant l'envoi n'est pas écrasée.
            if (personaUi.current === p && version === personaUi.version) Object.assign(p, saved, { rules: p.rules });
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
        if (field.dataset.persona === "intimate") renderFamilies();  // le bloc Séduction & intimité dit s'il compte
        savePersona();
    });
}

$("#persona-pick").addEventListener("change", () => loadPersonaSection($("#persona-pick").value));

// Personnalité : un bloc par famille (tempérament, relation, humour…), puis la carte des goûts.
// Chaque trait a une intensité par registre : trois crans (léger, marqué, très marqué).
const LEVEL_LABELS = ["absent", "léger", "marqué", "très marqué"];
const INTIMATE_FAMILY = "intimite";
// Intensité propre à chaque thème coché, et ce qu'elle veut dire pour le modèle
const THEME_LEVELS = [[1, "♡ Aime bien"], [2, "♥ Adore"], [3, "🔥 Passion"]];
const TASTE_LISTS = [
    { key: "favorites", label: "❤ Coups de cœur", hint: "Ex. Mirror's Edge et Faith, Blade Runner 1982, Kraftwerk" },
    { key: "dislikes", label: "💢 N'aime pas", hint: "Ex. le Comic Sans, admin/admin, le câblage spaghetti" },
    { key: "opinions", label: "⚡ Avis tranchés", hint: "Ex. quatre espaces pour l'indentation, pas deux" },
];

function traitFamily(traitId) {
    return personaUi.catalogue.traits.find((t) => t.id === traitId)?.family;
}

function renderPersonality() {
    renderFamilies();
    renderTastes();
}

function dots(traitId, register) {
    // Séduction & intimité : jamais hors du registre naturel (le serveur l'impose aussi)
    if (traitFamily(traitId) === INTIMATE_FAMILY && register !== "naturel") {
        return el("span", { className: "dots locked", title: "Jamais dans ce registre" }, "—");
    }
    const level = personaUi.current.traits[traitId]?.[register] || 0;
    const box = el("span", { className: "dots", title: LEVEL_LABELS[level] });
    for (let k = 1; k <= 3; k++) {
        const dot = el("button", { type: "button", className: `dot${k <= level ? " on" : ""}`, title: LEVEL_LABELS[k] });
        dot.addEventListener("click", () => {
            // Clic sur le cran déjà atteint : on redescend d'un cran (jusqu'à absent)
            personaUi.current.traits[traitId] = { ...personaUi.current.traits[traitId], [register]: level === k ? k - 1 : k };
            renderPersonality();
            savePersona();
        });
        box.append(dot);
    }
    return box;
}

function traitGrid(ids) {
    const byId = Object.fromEntries(personaUi.catalogue.traits.map((t) => [t.id, t]));
    const head = [el("span"), ...personaUi.catalogue.registers.map((r) => el("span", { className: "grid-head" }, r.label)), el("span")];
    const rows = ids.flatMap((id) => {
        const remove = el("button", { type: "button", className: "ghost remove", title: "Retirer ce trait" }, "✕");
        remove.addEventListener("click", () => {
            delete personaUi.current.traits[id];
            renderPersonality();
            savePersona();
        });
        return [el("span", { className: "trait-name", title: byId[id].definition }, byId[id].label),
            ...REGISTER_IDS.map((r) => dots(id, r)), remove];
    });
    return el("div", { className: "trait-grid" }, ...head, ...rows);
}

function familyMenu(family, chosen, menu) {
    const items = personaUi.catalogue.traits.filter((t) => t.family === family.id && !chosen.has(t.id));
    menu.replaceChildren(...(items.length ? items.map((t) => {
        const item = el("button", { type: "button", className: "trait-option" },
            el("span", {}, t.label), el("span", { className: "muted small" }, t.definition));
        item.addEventListener("click", () => {
            personaUi.current.traits[t.id] = { ...t.suggested };  // intensités suggérées, modifiables
            renderPersonality();
            savePersona();
        });
        return item;
    }) : [el("p", { className: "muted small" }, "Tous les traits de cette famille sont déjà choisis.")]));
}

function renderFamilies() {
    const box = $("#family-sections");
    const open = new Set([...box.querySelectorAll("details[open]")].map((d) => d.dataset.family));
    const first = !box.children.length;
    const traits = personaUi.current.traits;
    const chosen = new Set(Object.keys(traits));
    box.replaceChildren(...personaUi.catalogue.families.filter((f) => f.id !== "gouts").map((family) => {
        const ids = Object.keys(traits).filter((id) => traitFamily(id) === family.id);
        const section = el("details", { className: "family-section" });
        section.dataset.family = family.id;
        section.open = first ? ids.length > 0 : open.has(family.id);
        section.append(el("summary", {}, `${family.icon || ""} ${family.label}`,
            el("span", { className: "muted small" }, ids.length ? ` · ${ids.length}` : " · aucun")));
        if (family.id === INTIMATE_FAMILY) {
            section.append(el("p", { className: "muted small" }, personaUi.current.intimate
                ? "Seulement en registre naturel : jamais dans une compétence sobre ou professionnelle."
                : "Ne compte pas tant que « Contenu intime autorisé » n'est pas coché (plus bas). Seulement en registre naturel."));
        }
        if (ids.length) section.append(traitGrid(ids));
        const menu = el("div", { className: "trait-menu inline" });
        menu.hidden = true;
        const add = el("button", { type: "button", className: "ghost" }, "+ Ajouter");
        add.addEventListener("click", () => {
            if (menu.hidden) familyMenu(family, chosen, menu);
            menu.hidden = !menu.hidden;
        });
        section.append(el("div", { className: "trait-add" }, add, menu));
        return section;
    }));
}

function renderTastes() {
    const p = personaUi.current;
    // Intensité des goûts, par registre : c'est elle qui dose à quel point la personnalité en parle
    const row = $("#tastes-level");
    if (p.traits.gouts) {
        row.replaceChildren(traitGrid(["gouts"]));
    } else {
        const enable = el("button", { type: "button", className: "ghost" }, "Activer les goûts");
        enable.addEventListener("click", () => {
            p.traits.gouts = { ...personaUi.catalogue.traits.find((t) => t.id === "gouts").suggested };
            renderPersonality();
            savePersona();
        });
        row.replaceChildren(el("span", { className: "muted small" }, "Pas encore de goûts : elle n'en parlera pas. "), enable);
    }
    const chosen = new Set(p.themes || []);
    $("#themes-list").replaceChildren(...personaUi.catalogue.theme_categories.map((category) => {
        const themes = personaUi.catalogue.themes.filter((t) => t.category === category.id);
        const count = themes.filter((t) => chosen.has(t.id)).length;
        const box = el("details", { className: "theme-category" });
        box.open = count > 0;
        box.append(el("summary", {}, category.label, el("span", { className: "muted small" }, count ? ` · ${count}` : "")),
            el("div", { className: "theme-chips" }, ...themes.map((theme) => {
                const chip = el("button", { type: "button", className: `chip${chosen.has(theme.id) ? " on" : ""}` }, theme.label);
                chip.addEventListener("click", () => {
                    const set = new Set(p.themes || []);
                    p.theme_details ||= {};
                    if (set.has(theme.id)) {
                        set.delete(theme.id);
                        delete p.theme_details[theme.id];
                    } else {
                        set.add(theme.id);
                        p.theme_details[theme.id] = { level: 2, note: "" };  // un nouveau thème démarre à « adore »
                    }
                    // L'ordre de tes clics est gardé : le dernier thème coché arrive en bas de ta liste
                    p.themes = [...(p.themes || []).filter((id) => set.has(id)), ...[...set].filter((id) => !(p.themes || []).includes(id))];
                    if (set.size && !p.traits.gouts) p.traits.gouts = { ...personaUi.catalogue.traits.find((t) => t.id === "gouts").suggested };
                    renderTastes();
                    savePersona();
                });
                return chip;
            })));
        return box;
    }));
    renderThemeRows();
    $("#taste-lists").replaceChildren(...TASTE_LISTS.map(({ key, label, hint }) => {
        const items = p[key] || [];
        const list = el("ul", { className: "rules-list" }, ...items.map((text, i) => {
            const input = el("input", { value: text, maxLength: 150 });
            input.addEventListener("change", () => { p[key][i] = input.value; savePersona(); });
            const remove = el("button", { type: "button", className: "ghost danger-text", title: "Retirer" }, "✕");
            remove.addEventListener("click", () => { p[key].splice(i, 1); renderTastes(); savePersona(); });
            return el("li", {}, input, remove);
        }));
        const input = el("input", { placeholder: hint, maxLength: 150 });
        const add = el("button", { type: "button", className: "ghost" }, "+ Ajouter");
        const commit = () => {
            const text = input.value.trim();
            if (!text) return;
            p[key] = [...items, text];
            if (!p.traits.gouts) p.traits.gouts = { ...personaUi.catalogue.traits.find((t) => t.id === "gouts").suggested };
            renderTastes();
            savePersona();
        };
        add.addEventListener("click", commit);
        input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } });
        return el("div", { className: "taste-list" }, el("div", { className: "set-label" }, label), list,
            el("div", { className: "taste-add" }, input, add));
    }));
}

// Le détail d'un thème, lu au moment du clic : l'enregistrement remplace les objets internes du persona
function currentDetail(themeId) {
    const details = personaUi.current.theme_details ||= {};
    return details[themeId] ||= { level: 3, note: "" };
}

// Les thèmes cochés, un par ligne : leur intensité (aime bien, adore, passion) et ce qu'elle en aime précisément
function renderThemeRows() {
    const p = personaUi.current;
    const box = $("#theme-rows");
    const themes = (p.themes || []).map((id) => personaUi.catalogue.themes.find((t) => t.id === id)).filter(Boolean);
    if (!themes.length) {
        box.replaceChildren(el("p", { className: "muted small" }, "Coche des thèmes ci-dessus : chacun aura ici son intensité et tes précisions."));
        return;
    }
    p.theme_details ||= {};
    const categories = Object.fromEntries(personaUi.catalogue.theme_categories.map((c) => [c.id, c.label]));
    box.replaceChildren(el("div", { className: "set-label" }, `Ses thèmes · ${themes.length}`), ...themes.map((theme) => {
        const detail = p.theme_details[theme.id] ||= { level: 3, note: "" };  // thème coché avant : il restait « passion »
        const levels = el("div", { className: "theme-levels", role: "group", ariaLabel: `Intensité pour ${theme.label}` },
            ...THEME_LEVELS.map(([value, label]) => {
                const b = el("button", { type: "button", className: `chip${detail.level === value ? " on" : ""}` }, label);
                b.addEventListener("click", () => {
                    currentDetail(theme.id).level = value;
                    renderThemeRows();
                    savePersona();
                });
                return b;
            }));
        const note = el("input", { value: detail.note || "", maxLength: 150,
            placeholder: `Précise : ${theme.example || "lesquels, ce qu'elle en aime"}` });
        note.addEventListener("change", () => {
            currentDetail(theme.id).note = note.value.trim();
            savePersona();
        });
        const remove = el("button", { type: "button", className: "ghost danger-text", title: "Retirer ce thème" }, "✕");
        remove.addEventListener("click", () => {
            p.themes = p.themes.filter((id) => id !== theme.id);
            delete p.theme_details[theme.id];
            renderTastes();
            savePersona();
        });
        return el("div", { className: "theme-row" },
            el("div", {}, el("div", {}, theme.label), el("div", { className: "muted small" }, categories[theme.category] || "")),
            levels, note, remove);
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

// Clic ailleurs : les menus « + Ajouter » des familles se ferment
document.addEventListener("click", (event) => {
    if (event.target.closest(".trait-add")) return;
    for (const menu of document.querySelectorAll(".family-section .trait-menu")) menu.hidden = true;
});
