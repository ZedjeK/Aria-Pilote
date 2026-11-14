// Bac à sable Linux : la puce de la zone de saisie (les commandes de l'agent dans une copie du projet, sur un Linux
// jetable) et la section des Réglages qui le crée et le supprime. Chargé après chat.js, avant settings.js.

state.sandbox = null;  // dernier état lu de /api/bac-a-sable ; null : pas encore lu, ou illisible
let sandboxPoll = null;

async function loadSandboxState() {
    try {
        state.sandbox = await api("/api/bac-a-sable");
    } catch {
        state.sandbox = null;
    }
    sandboxSync();
}

// --- Puce de la conversation -------------------------------------------------------------------------------

function sandboxSync() {
    const chip = $("#btn-sandbox");
    const a = chat.current ? chat.current.agent || {} : chat.pendingAgent || {};
    const attached = Boolean(a.enabled && a.workspace);
    // Sans WSL, le bac à sable n'existe pas pour ce PC : la puce ne s'affiche pas. Il faut aussi une conversation
    // (l'option se range dans elle), comme pour la puce MCP.
    chip.hidden = !(attached && chat.current && state.sandbox?.wsl);
    if (chip.hidden) return;
    const ready = Boolean(state.sandbox.cree);
    const on = ready && Boolean(chat.current.sandbox);  // un bac supprimé depuis ne laisse pas la puce allumée
    chip.classList.toggle("chip-on", on);
    chip.setAttribute("aria-pressed", String(on));
    chip.textContent = on ? "🧪 Bac à sable ✓" : "🧪 Bac à sable";
    chip.title = !ready
        ? "Le bac à sable n'est pas créé : clique pour le créer (Réglages)."
        : on ? "L'agent lance ses commandes dans une copie de ton projet, sur un Linux jetable sans réseau, et "
            + "te propose de rapporter ses changements.\nClique pour couper."
            : "Laisser l'agent lancer ses commandes dans une copie de ton projet, sur un Linux jetable sans réseau : "
            + "rien n'y touche ton PC.";
}

$("#btn-sandbox").addEventListener("click", async () => {
    if (!chat.current) return;
    if (!state.sandbox?.cree) {
        location.hash = "#/reglages/bac-a-sable";
        return;
    }
    try {
        chat.current = await api(`/api/conversations/${chat.current.id}`, {
            method: "PATCH", body: JSON.stringify({ sandbox: !chat.current.sandbox }),
        });
        sandboxSync();
        refreshContext();
    } catch (err) {
        showChatError(err.message);
    }
});

// --- Section des Réglages ----------------------------------------------------------------------------------

const SANDBOX_STEPS = {
    telechargement: "Téléchargement de l'image",
    import: "Import de la distribution dans WSL…",
    preparation: "Préparation : utilisateur de l'agent, réseau coupé…",
};

function sandboxSectionOpen() {
    return !$('.settings-section[data-section="bac-a-sable"]').hidden && !$("#view-settings").hidden;
}

function showSandboxError(message) {
    $("#bas-error").textContent = message || "";
    $("#bas-error").hidden = !message;
}

function megabytes(bytes) {
    return Math.round(bytes / 1048576);
}

async function loadBacASable() {
    clearTimeout(sandboxPoll);
    await loadSandboxState();
    renderBacASable();
}

function renderBacASable() {
    const s = state.sandbox;
    if (!s) {
        $("#bas-state").textContent = "illisible";
        $("#bas-state").className = "badge";
        $("#bas-detail").textContent = "L'état du bac à sable n'a pas pu être lu.";
        return;
    }
    const creating = s.creation.etat === "en cours";
    const badge = $("#bas-state");
    badge.className = `badge${s.cree ? " loaded" : ""}`;
    badge.textContent = !s.wsl ? "WSL absent" : creating ? "création…" : s.cree ? "prêt" : "pas créé";
    $("#bas-detail").textContent = !s.wsl ? "WSL 2 n'est pas installé sur ce PC."
        : s.cree ? (s.image ? `${s.image.nom} · créé le ${s.image.cree_le}` : "Distribution WSL aria-bac-a-sable")
            : "Aucune distribution : crée-la pour que l'agent puisse y lancer ses commandes.";
    $("#bas-wsl-help").hidden = s.wsl;

    // Choix de l'image : seulement avant la création
    const select = $("#bas-image");
    const chosen = select.value || s.image_par_defaut;
    select.replaceChildren(...Object.entries(s.images).map(([key, name]) => {
        const label = key === s.image_par_defaut ? `${name} (conseillée)` : name;
        return el("option", { value: key, selected: key === chosen }, label);
    }));
    $("#bas-image-row").hidden = s.cree;
    select.disabled = creating || !s.wsl;

    $("#btn-bas-create").hidden = s.cree;
    $("#btn-bas-create").disabled = !s.wsl || creating;
    $("#btn-bas-delete").hidden = !s.cree;
    $("#btn-bas-delete").disabled = creating;

    const progress = $("#bas-progress");
    if (creating) {
        const c = s.creation;
        let text = SANDBOX_STEPS[c.etape] || "Création…";
        if (c.etape === "telechargement" && c.octets) {
            text += c.total ? ` : ${megabytes(c.octets)} / ${megabytes(c.total)} Mo` : ` : ${megabytes(c.octets)} Mo`;
        }
        progress.textContent = text;
        sandboxPoll = setTimeout(() => { if (sandboxSectionOpen()) loadBacASable(); }, 1000);
    } else {
        progress.textContent = "";
        // Une création qui a échoué garde son message jusqu'à la prochaine : on le relit à chaque affichage
        if (s.creation.etat === "erreur") showSandboxError(s.creation.message);
    }
    $("#set-bas-public").checked = Boolean(s.reseau_public);
}

$("#btn-bas-create").addEventListener("click", async () => {
    showSandboxError("");
    try {
        await api("/api/bac-a-sable/creer", { method: "POST", body: JSON.stringify({ image: $("#bas-image").value }) });
    } catch (err) {
        showSandboxError(err.message);
    }
    loadBacASable();
});

$("#btn-bas-delete").addEventListener("click", async () => {
    if (!confirm("Supprimer le bac à sable ?\n\nLes copies de tes projets qu'il contient disparaissent, et ce que "
        + "l'agent y avait changé sans le rapporter est perdu. Tes vrais projets ne sont pas touchés. Cette "
        + "suppression est sans retour.")) return;
    showSandboxError("");
    $("#btn-bas-delete").disabled = true;
    try {
        await api("/api/bac-a-sable/supprimer", { method: "POST", body: JSON.stringify({ confirmer: true }) });
    } catch (err) {
        showSandboxError(err.message);
    }
    loadBacASable();
});

// Internet public : demande confirmation en l'activant (pas en le coupant), car ça ouvre une sortie pour ce que contient
// la copie du projet
$("#set-bas-public").addEventListener("change", async (event) => {
    const box = event.target;
    if (box.checked && !confirm("Activer l'Internet public pour l'agent ?\n\nIl pourra installer des paquets (pip, apt), "
        + "mais aussi envoyer n'importe où le contenu de la copie de ton projet : rien ne distingue un "
        + "téléchargement d'un envoi. Les adresses de ton réseau local et de Windows restent bloquées.")) {
        box.checked = false;
        return;
    }
    showSandboxError("");
    try {
        await saveConfig({ bac_a_sable: { reseau_public: box.checked } });
    } catch (err) {
        box.checked = !box.checked;
        showSandboxError(err.message);
    }
    loadSandboxState();
});

loadSandboxState();
