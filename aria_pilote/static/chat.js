// Onglet Chat : conversations, presets, streaming des réponses et mode Agent.
// S'appuie sur $, api, el et state définis dans app.js, et renderMarkdown dans markdown.js.

const chat = {
    conversations: [],
    current: null,
    presets: [],
    controller: null,
    pendingAgent: { enabled: false, workspace: "" },  // réglages d'une conversation pas encore créée
};

// --- Presets ---------------------------------------------------------------

async function loadPresets() {
    chat.presets = await api("/api/presets");
    const select = $("#preset-select");
    const wanted = chat.current?.preset_id || select.value || "default";
    select.replaceChildren(...chat.presets.map((p) => el("option", { value: p.id }, p.name)));
    select.value = chat.presets.some((p) => p.id === wanted) ? wanted : chat.presets[0]?.id;
}

function currentPreset() {
    return chat.presets.find((p) => p.id === $("#preset-select").value);
}

$("#preset-select").addEventListener("change", async () => {
    if (!chat.current) return;
    chat.current = await api(`/api/conversations/${chat.current.id}`, {
        method: "PATCH",
        body: JSON.stringify({ preset_id: $("#preset-select").value }),
    });
});

$("#btn-edit-preset").addEventListener("click", () => {
    const preset = currentPreset();
    if (!preset) return;
    const form = $("#preset-form");
    for (const field of ["name", "system_prompt", "temperature", "top_p", "top_k", "min_p", "presence_penalty", "max_tokens"]) {
        form[field].value = preset[field] ?? "";
    }
    form.thinking.value = preset.thinking === null || preset.thinking === undefined ? "" : String(preset.thinking);
    $("#preset-dialog").showModal();
});

function readPresetForm() {
    const form = $("#preset-form");
    const num = (name) => (form[name].value === "" ? null : Number(form[name].value));
    return {
        name: form.name.value.trim(),
        system_prompt: form.system_prompt.value,
        temperature: num("temperature"),
        top_p: num("top_p"),
        top_k: num("top_k"),
        min_p: num("min_p"),
        presence_penalty: num("presence_penalty"),
        max_tokens: num("max_tokens"),
        thinking: form.thinking.value === "" ? null : form.thinking.value === "true",
    };
}

$("#preset-dialog").addEventListener("close", async () => {
    const action = $("#preset-dialog").returnValue;
    const preset = currentPreset();
    try {
        if (action === "save") {
            await api(`/api/presets/${preset.id}`, { method: "PUT", body: JSON.stringify(readPresetForm()) });
        } else if (action === "new") {
            const created = await api("/api/presets", { method: "POST", body: JSON.stringify(readPresetForm()) });
            $("#preset-select").value = created.id;
        } else if (action === "delete") {
            if (!confirm(`Supprimer le preset « ${preset.name} » ?`)) return;
            await api(`/api/presets/${preset.id}`, { method: "DELETE" });
        } else {
            return;
        }
        await loadPresets();
        $("#preset-select").dispatchEvent(new Event("change"));
    } catch (err) {
        showChatError(err.message);
    }
});

// --- Mode Agent ---------------------------------------------------------------

function agentSettings() {
    return chat.current ? chat.current.agent || { enabled: false, workspace: "" } : chat.pendingAgent;
}

function syncAgentBar() {
    const a = agentSettings();
    $("#agent-toggle").checked = Boolean(a.enabled);
    $("#agent-root").value = a.workspace || "";
    $(".agent-bar").classList.toggle("on", Boolean(a.enabled));
    const info = [];
    if (a.enabled) {
        info.push(a.auto_write ? "modifs autorisées" : "modifs demandées");
        info.push(a.auto_web ? "web autorisé" : "web demandé");
        info.push("commandes demandées");
        if (chat.memoryLoaded) info.push("ARIA.md chargé");
    }
    $("#agent-info").textContent = info.join(" · ");
    renderTodo(a.enabled ? a.todo : null);
}

const TODO_MARKS = { pending: "☐", in_progress: "▶", completed: "☑" };

function renderTodo(items) {
    const list = $("#agent-todo");
    list.hidden = !items?.length;
    list.replaceChildren(...(items || []).map((it) => el("li", { className: it.status }, `${TODO_MARKS[it.status] || "☐"} ${it.content}`)));
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
}

$("#agent-toggle").addEventListener("change", () => {
    saveAgent({ enabled: $("#agent-toggle").checked, workspace: $("#agent-root").value.trim() });
});

$("#btn-agent-root").addEventListener("click", () => {
    saveAgent({ workspace: $("#agent-root").value.trim() });
});

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
            el("span", { className: "conv-title" }, c.title),
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
    if (!id) chat.pendingAgent = { enabled: false, workspace: "" };
    $("#conv-title").textContent = chat.current?.title || "Nouvelle conversation";
    $("#btn-rename").hidden = !chat.current;
    if (chat.current?.preset_id && chat.presets.some((p) => p.id === chat.current.preset_id)) {
        $("#preset-select").value = chat.current.preset_id;
    }
    $("#chat-error").hidden = true;
    syncAgentBar();
    renderMessages();
    loadConversations();
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
        node.textContent = m.content;
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

const TOOL_STATUS = { running: "en cours…", ok: "ok", error: "erreur", denied: "refusé" };

function shortArgs(args) {
    const main = args?.path ?? args?.pattern ?? args?.command ?? args?.query ?? args?.url ?? "";
    const text = String(main);
    return text.length > 70 ? text.slice(0, 69) + "…" : text;
}

function toolNode(m) {
    const node = el("details", { className: `tool ${m.status}` });
    node.dataset.callId = m.tool_call_id || "";
    node.append(el("summary", {}, `🔧 ${m.name}  ${shortArgs(m.args)} · ${TOOL_STATUS[m.status] || m.status}`));
    node.append(el("pre", {}, JSON.stringify(m.args, null, 2)));
    if (m.content) node.append(el("pre", {}, m.content));
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
    if (kind === "command") {
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
    }[ev.kind];
    const buttons = el("div", { className: "row" });
    const decide = async (decision, label) => {
        for (const b of buttons.children) b.disabled = true;
        try {
            await api("/api/agent/approve", {
                method: "POST",
                body: JSON.stringify({ approval_id: ev.approval_id, decision }),
            });
            buttons.replaceChildren(el("span", { className: "muted small" }, label));
        } catch (err) {
            showChatError(err.message);
        }
    };
    const allow = el("button", { className: "primary" }, "Autoriser");
    allow.addEventListener("click", () => decide("allow", "Autorisé ✓"));
    buttons.append(allow);
    if (ev.kind === "write" || ev.kind === "web") {
        const label = ev.kind === "write" ? "Autoriser les modifs pour cette conversation" : "Autoriser le web pour cette conversation";
        const session = el("button", { className: "ghost" }, label);
        session.addEventListener("click", () => decide("allow_session", "Autorisé pour cette conversation ✓"));
        buttons.append(session);
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

function renderMessages() {
    const box = $("#messages");
    const messages = chat.current?.messages || [];
    if (!messages.length) {
        box.replaceChildren(el("p", { className: "hint muted" }, "Pose ta première question 💬"));
        return;
    }
    const nodes = [];
    for (const m of messages) {
        if (m.role === "tool") {
            nodes.push(toolNode(m));
        } else if (m.role === "assistant" && !m.content && !m.reasoning) {
            continue;  // simple appel d'outil : la carte de l'outil suffit
        } else {
            nodes.push(messageNode(m));
        }
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

function showChatError(message) {
    $("#chat-error").textContent = message;
    $("#chat-error").hidden = false;
}

// --- Envoi et streaming -----------------------------------------------------

function setBusy(busy) {
    $("#btn-send").disabled = busy;
    $("#btn-regen").disabled = busy;
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

async function send(content) {
    if (chat.controller) return;
    $("#chat-error").hidden = true;
    if (state.status?.state !== "ready") {
        showChatError("Aucun modèle prêt : charge un modèle dans l'onglet Modèles.");
        return false;
    }
    if (!chat.current) {
        const pending = chat.pendingAgent;
        chat.current = await api("/api/conversations", {
            method: "POST",
            body: JSON.stringify({ preset_id: $("#preset-select").value }),
        });
        if (pending.enabled || pending.workspace) {
            try {
                chat.current = await api(`/api/conversations/${chat.current.id}`, {
                    method: "PATCH",
                    body: JSON.stringify({ agent: { enabled: pending.enabled, workspace: pending.workspace || null } }),
                });
            } catch (err) {
                showChatError(err.message);
                return false;
            }
        }
        $("#btn-rename").hidden = false;
        syncAgentBar();
    }

    if (content === null) {
        while (["assistant", "tool"].includes(chat.current.messages.at(-1)?.role)) chat.current.messages.pop();
    } else {
        chat.current.messages.push({ role: "user", content });
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
    try {
        const resp = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ conversation_id: chat.current.id, content, preset_id: $("#preset-select").value }),
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
            } else if (ev.type === "approval") {
                const card = toolCards[ev.call_id];
                card.node.open = true;
                card.node.append(approvalNode(ev));
                card.node.scrollIntoView({ block: "nearest" });
            } else if (ev.type === "memory") {
                chat.memoryLoaded = true;
                syncAgentBar();
            } else if (ev.type === "tool_result") {
                if (ev.todo) renderTodo(ev.todo);
                const card = toolCards[ev.call_id];
                Object.assign(card.data, { status: ev.status, content: ev.output, backup_id: ev.backup_id });
                const fresh = toolNode(card.data);
                card.node.replaceWith(fresh);
                card.node = fresh;
                ensureLive();
            } else if (ev.type === "done" && ev.message && live) {
                Object.assign(live, ev.message);
            } else if (ev.type === "error") {
                showChatError(ev.message);
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
    if (aborted) {
        // Le serveur enregistre la réponse partielle de son côté : on l'affiche tout de suite
        if (live) {
            live.stats = { ...(live.stats || {}), stopped: true };
            closeLive();
        }
        setTimeout(() => openConversation(id), 600);
    } else {
        await openConversation(id);
    }
    return ok;
}

$("#chat-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = $("#chat-input");
    const content = input.value;
    if (!content.trim() || chat.controller) return;
    input.value = "";
    const ok = await send(content);
    if (ok === false && !input.value) input.value = content;
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
    $("#chat-model").textContent = s?.state === "ready"
        ? `Modèle : ${s.model.name}`
        : s?.state === "loading" ? "Chargement du modèle…" : "Aucun modèle chargé (onglet Modèles)";
}, 1000);

(async () => {
    await loadPresets();
    await loadConversations();
    if (chat.conversations.length) {
        await openConversation(chat.conversations[0].id);
    } else {
        syncAgentBar();
        renderMessages();
    }
})();
