// Souvenirs : la carte de Réglages › Général, la puce « 🧠 Retenu » sous une réponse et le mode privé 🕶 du chat.
// S'appuie sur $, api, el, state (app.js), chat, showChatError, refreshContext (chat.js) et flashSaved (settings.js).

const souvenirsUi = { items: [], categories: {}, personas: {} };

// --- Carte « Souvenirs » (Réglages › Général) ------------------------------------------------------

async function loadSouvenirsCard() {
    let data;
    try {
        data = await api("/api/souvenirs");
    } catch {
        return;
    }
    souvenirsUi.items = data.souvenirs;
    souvenirsUi.categories = data.categories;
    try {
        const personas = await api("/api/personas");
        souvenirsUi.personas = Object.fromEntries(personas.personas.map((p) => [p.id, p.name]));
    } catch {
        souvenirsUi.personas = {};
    }
    const select = $("#souvenir-new-cat");
    if (!select.options.length) {
        select.append(...Object.entries(data.categories).map(([id, label]) => el("option", { value: id }, label)));
    }
    renderSouvenirs();
}

function renderSouvenirs() {
    const wanted = $("#souvenir-filter").value.trim().toLowerCase();
    const items = souvenirsUi.items.filter((s) => !wanted || s.texte.toLowerCase().includes(wanted));
    const total = souvenirsUi.items.length;
    $("#souvenirs-count").textContent = total ? `${total} souvenir${total > 1 ? "s" : ""}` : "";
    $("#btn-souvenirs-clear").hidden = !total;
    const list = $("#souvenirs-list");
    if (!items.length) {
        list.replaceChildren(el("p", { className: "muted small" }, total
            ? "Aucun souvenir ne contient ce texte."
            : "Aucun souvenir pour l'instant : ton IA retiendra ce qui compte au fil des conversations, ou ajoute-en un ici."));
        return;
    }
    // Rangés par catégorie, les plus récents d'abord (l'ordre de l'API)
    const groups = Object.keys(souvenirsUi.categories).map((cat) => [cat, items.filter((s) => s.categorie === cat)])
        .filter(([, found]) => found.length);
    list.replaceChildren(...groups.map(([cat, found]) => el("div", { className: "souvenir-group" },
        el("div", { className: "souvenir-cat" }, `${souvenirsUi.categories[cat]} (${found.length})`),
        el("ul", { className: "rules-list" }, ...found.map(souvenirRow)))));
}

function souvenirRow(s) {
    const text = el("input", { value: s.texte, maxLength: 300, title: souvenirTitle(s) });
    text.addEventListener("change", () => editSouvenir(s, { texte: text.value }, text));
    const cat = el("select", { className: "souvenir-cat-select" },
        ...Object.entries(souvenirsUi.categories).map(([id, label]) => el("option", { value: id, selected: id === s.categorie }, label)));
    cat.addEventListener("change", () => editSouvenir(s, { categorie: cat.value }, cat));
    const del = el("button", { className: "ghost", title: "Oublier ce souvenir" }, "✕");
    del.addEventListener("click", async () => {
        try {
            await api(`/api/souvenirs/${s.id}`, { method: "DELETE" });
            flashSaved();
            loadSouvenirsCard();
        } catch (err) {
            alert(err.message);
        }
    });
    const parts = [text, cat, del];
    if (s.intime) {
        const who = souvenirsUi.personas[s.persona] || "persona supprimé";
        parts.unshift(el("span", { className: "souvenir-badge", title: `Souvenir intime : ne revient qu'avec ${who}, en registre naturel` }, `🔒 ${who}`));
    }
    return el("li", {}, ...parts);
}

function souvenirTitle(s) {
    const when = new Date(s.modifie * 1000).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
    return `n° ${s.id} · ${s.conversation ? "retenu dans une conversation" : "ajouté à la main"} · modifié le ${when}`;
}

async function editSouvenir(s, patch, field) {
    try {
        Object.assign(s, await api(`/api/souvenirs/${s.id}`, { method: "PUT", body: JSON.stringify(patch) }));
        field.classList.remove("invalid");
        flashSaved();
        renderSouvenirs();
    } catch (err) {
        field.classList.add("invalid");
        alert(err.message);
    }
}

async function addSouvenir() {
    const input = $("#souvenir-new");
    if (!input.value.trim()) return;
    try {
        await api("/api/souvenirs", { method: "POST",
            body: JSON.stringify({ texte: input.value, categorie: $("#souvenir-new-cat").value }) });
        input.value = "";
        flashSaved();
        loadSouvenirsCard();
    } catch (err) {
        alert(err.message);
    }
}

$("#btn-souvenir-add").addEventListener("click", addSouvenir);
$("#souvenir-new").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
        e.preventDefault();
        addSouvenir();
    }
});
$("#souvenir-filter").addEventListener("input", renderSouvenirs);
$("#btn-souvenirs-clear").addEventListener("click", async () => {
    const n = souvenirsUi.items.length;
    if (!confirm(`Oublier les ${n} souvenirs ? C'est définitif.`)) return;
    try {
        await api("/api/souvenirs?confirm=true", { method: "DELETE" });
        flashSaved();
        loadSouvenirsCard();
    } catch (err) {
        alert(err.message);
    }
});

// --- Puce « 🧠 Retenu » sous une réponse ----------------------------------------------------------

const SOUVENIR_ACTIONS = { "ajouté": "🧠 Retenu", "mis à jour": "🧠 Mis à jour", "oublié": "🧠 Oublié" };

function souvenirNode(m) {
    const s = m.souvenir;
    const node = el("div", { className: `souvenir-chip${s.annule ? " undone" : ""}` });
    node.dataset.callId = m.tool_call_id || "";
    node.append(el("span", {}, `${SOUVENIR_ACTIONS[s.action] || "🧠"} : « ${s.texte} »`));
    if (s.action === "mis à jour" && s.ancien && s.ancien !== s.texte) {
        node.title = `Avant : « ${s.ancien} »`;
    }
    if (s.annule) {
        node.append(el("span", { className: "muted small" }, "annulé ↩"));
    } else {
        const undo = el("button", { className: "ghost", title: "Défaire : comme si rien n'avait été retenu" }, "Annuler");
        undo.addEventListener("click", async () => {
            undo.disabled = true;
            try {
                await api("/api/souvenirs/annuler", { method: "POST",
                    body: JSON.stringify({ conversation_id: chat.current.id, tool_call_id: m.tool_call_id }) });
                s.annule = true;
                node.replaceWith(souvenirNode(m));
                refreshContext();
            } catch (err) {
                undo.disabled = false;
                showChatError(err.message);
            }
        });
        node.append(undo);
    }
    return node;
}

// --- 🕶 Conversation privée ----------------------------------------------------------------------

function privateOn() {
    return chat.current ? Boolean(chat.current.prive) : Boolean(chat.pendingPrivate);
}

function syncPrivateChip() {
    const chip = $("#btn-private");
    chip.hidden = state.config?.memory?.enabled === false;
    const on = privateOn();
    chip.classList.toggle("chip-on", on);
    chip.setAttribute("aria-pressed", String(on));
    chip.textContent = on ? "🕶 Privée ✓" : "🕶 Privée";
    chip.title = on
        ? "Conversation privée : rien n'y est retenu et aucun souvenir n'y sert. Clique pour revenir à la normale."
        : "Conversation privée : rien n'y sera retenu, et aucun souvenir n'y servira";
}

$("#btn-private").addEventListener("click", async () => {
    if (!chat.current) {
        chat.pendingPrivate = !chat.pendingPrivate;  // envoyé à la création de la conversation
        syncPrivateChip();
        return;
    }
    try {
        chat.current = await api(`/api/conversations/${chat.current.id}`, {
            method: "PATCH", body: JSON.stringify({ prive: !chat.current.prive }),
        });
        syncPrivateChip();
        loadConversations();
        refreshContext();
    } catch (err) {
        showChatError(err.message);
    }
});
