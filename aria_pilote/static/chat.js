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
    for (const field of ["name", "system_prompt", "temperature", "top_p", "top_k", "min_p", "presence_penalty", "max_tokens", "thinking_budget"]) {
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
        thinking_budget: num("thinking_budget") || null,
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
    $("#plan-toggle").checked = Boolean(a.plan_mode);
    $("#plan-toggle").disabled = !a.enabled;
    $("#agent-root").value = a.workspace || "";
    $(".agent-bar").classList.toggle("on", Boolean(a.enabled));
    const info = [];
    if (a.enabled) {
        if (a.plan_mode) info.push("mode Plan : lecture seule, plan à valider");
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
                body: JSON.stringify({ agent: { enabled: next.enabled, workspace: next.workspace || null, plan_mode: Boolean(next.plan_mode) } }),
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

$("#agent-toggle").addEventListener("change", () => {
    saveAgent({ enabled: $("#agent-toggle").checked, workspace: $("#agent-root").value.trim() });
});

$("#plan-toggle").addEventListener("change", () => {
    saveAgent({ plan_mode: $("#plan-toggle").checked });
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
    renderGauge(chat.current?.context);
    renderMessages();
    loadConversations();
    refreshHooksBanner();
    refreshBackground();
}

// --- Compétences -------------------------------------------------------------------

const SKILL_SOURCES = { "intégrée": "intégrée", perso: "perso", projet: "projet" };

function skillsQuery() {
    return chat.current ? `?conversation_id=${chat.current.id}` : "";
}

async function openSkills() {
    $("#skill-form").hidden = true;
    $("#skills-list").hidden = false;
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
        if (s.editable) {
            const edit = el("button", { className: "ghost" }, "Modifier");
            edit.addEventListener("click", (event) => { event.stopPropagation(); editSkill(s.name); });
            const del = el("button", { className: "ghost danger-text" }, "Supprimer");
            del.addEventListener("click", async (event) => {
                event.stopPropagation();
                if (!confirm(`Envoyer la compétence « ${s.name} » à la corbeille ?`)) return;
                await api(`/api/skills/${s.name}`, { method: "DELETE" });
                openSkills();
            });
            item.append(edit, del);
        }
        list.append(item);
    }
    if (!items.length) list.append(el("li", { className: "muted" }, "Aucune compétence."));
}

async function editSkill(name) {
    const form = $("#skill-form");
    form.reset();
    $("#skill-error").hidden = true;
    form.name.readOnly = Boolean(name);
    if (name) {
        const s = await api(`/api/skills/${name}${skillsQuery()}`);
        form.name.value = s.name;
        form.description.value = s.description;
        form.body.value = s.body;
    } else {
        form.body.value = "1. …\n2. …\n";
    }
    $("#skills-list").hidden = true;
    form.hidden = false;
    form.name.focus();
}

$("#btn-skills").addEventListener("click", openSkills);
$("#btn-skills-close").addEventListener("click", () => $("#skills-dialog").close());
$("#btn-skill-new").addEventListener("click", () => editSkill(null));
$("#btn-skill-cancel").addEventListener("click", openSkills);

$("#skill-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
        await api("/api/skills", {
            method: "POST",
            body: JSON.stringify({ name: form.name.value.trim(), description: form.description.value, body: form.body.value }),
        });
        openSkills();
    } catch (err) {
        $("#skill-error").textContent = err.message;
        $("#skill-error").hidden = false;
    }
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
    // Commandes de l'agent qui tournent encore (gardées à ta demande) : chacune avec son bouton Arrêter
    const box = $("#bg-commands");
    const list = chat.current ? await api(`/api/agent/background?conversation_id=${chat.current.id}`) : [];
    box.hidden = !list.length;
    box.replaceChildren(...list.map((c) => {
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
        return el("div", { className: "bg-command" },
            el("span", {}, `⚙ ${c.id} · `), el("code", {}, c.command.slice(0, 80)), el("span", { className: "muted small" }, ` · ${c.state}`), stop);
    }));
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

function renderGauge(ctx) {
    const gauge = $("#ctx-gauge");
    gauge.hidden = !ctx?.max;
    if (!ctx?.max) return;
    const pct = Math.min(100, (100 * ctx.used) / ctx.max);
    gauge.classList.toggle("high", pct >= 60 && pct < 85);
    gauge.classList.toggle("full", pct >= 85);
    gauge.querySelector(".ctx-bar span").style.width = `${pct}%`;
    gauge.querySelector(".ctx-text").textContent =
        `${ctx.used.toLocaleString("fr-FR")} / ${ctx.max.toLocaleString("fr-FR")} tokens`;
}

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

const TOOL_STATUS = {
    running: "en cours…", ok: "ok", error: "erreur", denied: "refusé",
    pending: "en attente de ta réponse", skipped: "après ta réponse", unanswered: "sans réponse",
};

function shortArgs(args) {
    const main = args?.path ?? args?.pattern ?? args?.command ?? args?.query ?? args?.url ?? args?.name ?? args?.id ?? "";
    const text = String(main);
    return text.length > 70 ? text.slice(0, 69) + "…" : text;
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
        await saveAgent({ plan_mode: false });
        send("Le plan est approuvé : exécute-le étape par étape, en tenant ton plan à jour.");
    });
    const change = el("button", { className: "ghost" }, "✏️ Demander des changements");
    change.addEventListener("click", () => {
        const input = $("#chat-input");
        input.value = "Modifie le plan : ";
        input.focus();
    });
    return el("div", { className: "row" }, approve, change);
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
    const node = el("details", { className: `tool ${m.status}` });
    node.dataset.callId = m.tool_call_id || "";
    const bg = m.args?.background ? (m.args.keep ? "⚙ arrière-plan, gardée · " : "⚙ arrière-plan · ") : "";
    node.append(el("summary", {}, `🔧 ${m.name}  ${shortArgs(m.args)} · ${bg}${TOOL_STATUS[m.status] || m.status}`));
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
    if (!messages.length) {
        box.replaceChildren(el("p", { className: "hint muted" }, "Pose ta première question 💬"));
        return;
    }
    const nodes = [];
    const summary = chat.current?.summary;
    // Le dernier plan proposé attend ta décision tant que le mode Plan est actif et que tu n'as rien répondu
    const lastUser = messages.map((m) => m.role).lastIndexOf("user");
    const lastPlan = messages.map((m) => m.name).lastIndexOf("propose_plan");
    const planMode = Boolean(chat.current?.agent?.plan_mode);
    const planPending = planMode && lastPlan > lastUser;
    // Un modèle qui écrit son plan en texte au lieu d'appeler propose_plan : sa réponse finale tient lieu de plan
    const last = messages.at(-1);
    const textPlan = planMode && !planPending && messages.length - 1 > lastUser && last?.role === "assistant" && last.content;
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

async function send(content, answer, continueTask = false) {
    // answer : réponse à la question en attente de l'agent (content vaut alors null)
    // continueTask : bouton « Continuer », le message prolonge la tâche en cours
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

    if (answer !== undefined) {
        const question = chat.current.messages[pendingQuestionIndex(chat.current.messages)];
        if (question) Object.assign(question, { status: "ok", answer: answer.trim() });
    } else if (content === null) {
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
    let errorShown = null;  // réaffichée après le rechargement final de la conversation
    try {
        const resp = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                conversation_id: chat.current.id, content, answer, continue_task: continueTask,
                preset_id: $("#preset-select").value,
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
            } else if (ev.type === "approval") {
                // Une demande d'un sous-agent s'affiche dans la carte de sa délégation
                const card = toolCards[ev.call_id] || toolCards[ev.parent_id];
                card.node.open = true;
                card.node.append(approvalNode(ev));
                card.node.scrollIntoView({ block: "nearest" });
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
    if (!content.trim() || chat.controller) return;
    input.value = "";
    // Une question de l'agent attend : ton message est ta réponse
    const asked = chat.current && agentSettings().enabled && pendingQuestionIndex(chat.current.messages) >= 0;
    const ok = asked ? await send(null, content) : await send(content);
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
