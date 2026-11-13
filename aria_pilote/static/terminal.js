// Onglet Terminal du panneau latéral : un vrai PowerShell dans le dossier de travail de la conversation.
// xterm.js affiche, un WebSocket relie au pseudo-terminal du serveur (voir terminal.py pour le contrôle d'accès).
// S'appuie sur $, api, el (app.js), et sur chat, sideOpen, sidePref, side (chat.js).

const term = {
    xterm: null,     // l'écran (xterm.js), créé au premier affichage de l'onglet
    fit: null,
    socket: null,
    cid: null,       // conversation dont le terminal est affiché
    opening: false,
};

function terminalVisible() {
    return sideOpen() && (sidePref("aria-side-tab") || side.tab) === "terminal" && !$("#view-chat").hidden;
}

function terminalNote(text, action) {
    const note = $("#terminal-note");
    note.replaceChildren(text);
    if (action) note.append(" ", action);
    note.hidden = !text;
}

function terminalScreen() {
    if (term.xterm) return term.xterm;
    const css = getComputedStyle(document.documentElement);
    const color = (name) => css.getPropertyValue(name).trim();
    term.xterm = new Terminal({
        cursorBlink: true,
        fontFamily: css.getPropertyValue("--mono").trim() || "Consolas, monospace",
        fontSize: 13,
        scrollback: 5000,
        theme: { background: color("--deep"), foreground: color("--text"), cursor: color("--accent"),
            selectionBackground: color("--border") },
    });
    term.fit = new FitAddon.FitAddon();
    term.xterm.loadAddon(term.fit);
    term.xterm.open($("#terminal-host"));
    term.xterm.onData((data) => terminalSend({ t: "in", d: data }));
    term.xterm.onResize(({ rows, cols }) => terminalSend({ t: "size", rows, cols }));
    new ResizeObserver(() => { if (terminalVisible()) terminalFit(); }).observe($("#terminal-host"));
    return term.xterm;
}

function terminalFit() {
    try {
        term.fit.fit();
    } catch {
        // onglet pas encore mesurable : le prochain redimensionnement s'en charge
    }
}

function terminalSend(message) {
    if (term.socket?.readyState === WebSocket.OPEN) term.socket.send(JSON.stringify(message));
}

function terminalDisconnect() {
    const socket = term.socket;
    term.socket = null;
    if (socket) {
        socket.onclose = null;
        socket.close();
    }
}

async function terminalConnect() {
    const cid = chat.current?.id;
    if (!cid || term.opening) return;
    term.opening = true;
    terminalDisconnect();
    const screen = terminalScreen();
    screen.reset();
    term.cid = cid;
    terminalFit();
    try {
        const { token } = await api("/api/terminal/token", { method: "POST", body: JSON.stringify({ conversation_id: cid }) });
        // Onglet pas encore mesuré (quelques colonnes) : la taille par défaut du serveur, corrigée au premier ajustement
        const size = screen.cols >= 20 ? `&rows=${screen.rows}&cols=${screen.cols}` : "";
        const query = `token=${encodeURIComponent(token)}${size}`;
        const socket = new WebSocket(`ws://${location.host}/api/terminal/ws?${query}`);
        term.socket = socket;
        socket.onopen = () => {
            terminalNote("");
            screen.focus();
        };
        socket.onmessage = (event) => {
            const message = JSON.parse(event.data);
            if (message.t === "out") screen.write(message.d);
            else if (message.t === "exit") terminalEnded("Le terminal s'est arrêté.");
            else if (message.t === "error") terminalEnded(message.d);
        };
        socket.onclose = () => {
            if (term.socket === socket) terminalEnded("Connexion au terminal perdue.");
        };
    } catch (err) {
        terminalEnded(err.message, false);
    } finally {
        term.opening = false;
    }
}

function terminalEnded(text, restartable = true) {
    terminalDisconnect();
    if (!restartable) {
        terminalNote(text);
        return;
    }
    const button = el("button", { type: "button", className: "ghost" }, "↻ Relancer");
    button.addEventListener("click", terminalConnect);
    terminalNote(text, button);
}

// Appelé à chaque changement du panneau ou de conversation : affiche le terminal de la conversation en cours
function terminalSync() {
    if (!terminalVisible()) return;
    const conv = chat.current;
    const folder = conv?.agent?.workspace;
    $("#terminal-bar").hidden = !folder;
    $("#terminal-host").hidden = !folder;
    if (!folder) {
        terminalDisconnect();
        term.cid = null;
        terminalNote("Attache un dossier à la conversation : le terminal s'ouvre dedans.");
        return;
    }
    $("#terminal-cwd").textContent = folder;
    $("#terminal-cwd").title = folder;
    if (term.cid === conv.id && term.socket) {
        terminalFit();
        return;
    }
    terminalConnect();
}

$("#btn-terminal-restart").addEventListener("click", async () => {
    if (!chat.current?.id) return;
    if (!confirm("Redémarrer le terminal ? Ce qui y tourne sera arrêté.")) return;
    terminalDisconnect();
    try {
        await api("/api/terminal/close", { method: "POST", body: JSON.stringify({ conversation_id: chat.current.id }) });
    } catch (err) {
        terminalNote(err.message);
        return;
    }
    terminalConnect();
});
