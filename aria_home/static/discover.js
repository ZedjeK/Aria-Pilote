// Onglet Découvrir : recherche sur Hugging Face et file de téléchargements.
// S'appuie sur $, api, el, formatSize et loadModels définis dans app.js.

const hubState = {
    repo: null,
    files: [],
    mmprojKey: null,
    jobStatus: {},
};

const FIT_LABELS = {
    gpu: "tient sur le GPU",
    partial: "GPU + RAM (plus lent)",
    too_big: "trop gros",
    unknown: "?",
};

const ROLE_LABELS = { vision: "vision", draft: "brouillon" };

function formatCount(n) {
    if (n >= 1e6) return `${(n / 1e6).toFixed(1)} M`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(1)} k`;
    return String(n);
}

function formatDuration(s) {
    if (s == null) return "";
    if (s < 60) return `${s} s`;
    if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} s`;
    return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

// --- Recherche --------------------------------------------------------------

$("#hub-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const list = $("#hub-results");
    $("#hub-error").hidden = true;
    list.replaceChildren(el("li", { className: "muted" }, "Recherche…"));
    try {
        const results = await api(`/api/hub/search?q=${encodeURIComponent($("#hub-query").value.trim())}`);
        list.replaceChildren();
        if (!results.length) {
            list.append(el("li", { className: "muted" }, "Aucun dépôt GGUF trouvé."));
            return;
        }
        for (const r of results) {
            const badges = el(
                "div",
                { className: "badges" },
                el("span", { className: "badge" }, `⬇ ${formatCount(r.downloads)}`),
                el("span", { className: "badge" }, `♥ ${formatCount(r.likes)}`)
            );
            if (r.updated_at) badges.append(el("span", { className: "badge" }, new Date(r.updated_at).toLocaleDateString("fr-FR")));
            if (r.gated) badges.append(el("span", { className: "badge err" }, "accès restreint"));
            const item = el("li", {}, el("div", { className: "title" }, r.repo), badges);
            item.addEventListener("click", () => {
                for (const li of list.children) li.classList.remove("selected");
                item.classList.add("selected");
                openRepo(r.repo);
            });
            list.append(item);
        }
    } catch (err) {
        list.replaceChildren();
        $("#hub-error").textContent = err.message;
        $("#hub-error").hidden = false;
    }
});

// --- Fichiers d'un dépôt ------------------------------------------------------

async function openRepo(repo) {
    hubState.repo = repo;
    $("#hub-empty").hidden = true;
    $("#hub-detail").hidden = false;
    $("#hub-repo").textContent = repo;
    $("#hub-dest").textContent = "Chargement de la liste des fichiers…";
    $("#hub-files").replaceChildren();
    try {
        const data = await api(`/api/hub/files?repo=${encodeURIComponent(repo)}`);
        if (hubState.repo !== repo) return;
        hubState.files = data.files;
        hubState.mmprojKey = data.recommended_mmproj;
        $("#hub-dest").textContent = `Destination : ${data.download_root}\\${repo.replace("/", "\\")}`;
        renderFiles();
    } catch (err) {
        $("#hub-dest").textContent = "";
        $("#hub-files").replaceChildren(el("tr", {}, el("td", { className: "error", colSpan: 6 }, err.message)));
    }
}

function renderFiles() {
    const mmproj = hubState.files.find((f) => f.key === hubState.mmprojKey);
    $("#hub-vision-wrap").hidden = !mmproj;
    $("#hub-mmproj").textContent = mmproj ? `${mmproj.name}, ${formatSize(mmproj.size)}` : "";

    const body = $("#hub-files");
    body.replaceChildren();
    if (!hubState.files.length) {
        body.append(el("tr", {}, el("td", { className: "muted", colSpan: 6 }, "Aucun fichier GGUF dans ce dépôt.")));
        return;
    }
    for (const f of hubState.files) {
        const name = el("td", { className: "name" }, f.name);
        if (ROLE_LABELS[f.role]) name.append(" ", el("span", { className: "badge" }, ROLE_LABELS[f.role]));

        const engine = el("td");
        if (f.role === "model") {
            engine.append(f.engine === "prism" ? el("span", { className: "badge prism" }, "PrismML") : "llama.cpp");
        }

        const fit = el("td", { className: f.fit ? `fit-${f.fit}` : "" }, f.fit ? FIT_LABELS[f.fit] : "");

        const action = el("td");
        if (f.downloaded) {
            action.append(el("span", { className: "badge loaded" }, "déjà là"));
        } else {
            const button = el("button", { className: "ghost" }, "Télécharger");
            button.addEventListener("click", () => startDownload(f, button));
            action.append(button);
        }

        body.append(el("tr", {}, name, el("td", {}, f.quant || ""), el("td", {}, formatSize(f.size)), engine, fit, action));
    }
}

async function startDownload(file, button) {
    button.disabled = true;
    const paths = [...file.paths];
    const mmproj = hubState.files.find((f) => f.key === hubState.mmprojKey);
    if (file.role === "model" && mmproj && !mmproj.downloaded && $("#hub-vision").checked) {
        paths.push(...mmproj.paths);
    }
    try {
        await api("/api/downloads", {
            method: "POST",
            body: JSON.stringify({ repo: hubState.repo, paths, label: `${hubState.repo} · ${file.name}` }),
        });
        button.textContent = "En file ✓";
        refreshDownloads();
    } catch (err) {
        button.disabled = false;
        $("#hub-error").textContent = err.message;
        $("#hub-error").hidden = false;
    }
}

// --- Téléchargements ------------------------------------------------------------

const STATUS_LABELS = {
    queued: "en attente",
    downloading: "téléchargement",
    verifying: "vérification SHA-256…",
    done: "terminé ✓",
    error: "erreur",
    cancelled: "annulé",
};

function jobButton(label, handler) {
    const button = el("button", { className: "ghost" }, label);
    button.addEventListener("click", async () => {
        button.disabled = true;
        try {
            await handler();
        } catch (err) {
            $("#hub-error").textContent = err.message;
            $("#hub-error").hidden = false;
        }
        refreshDownloads();
    });
    return button;
}

async function refreshDownloads() {
    let jobs;
    try {
        jobs = await api("/api/downloads");
    } catch {
        return;
    }
    const list = $("#dl-list");
    list.replaceChildren();
    if (!jobs.length) {
        list.append(el("li", { className: "muted" }, "Aucun téléchargement."));
    }
    let finished = false;
    for (const job of jobs) {
        if (job.status === "done" && hubState.jobStatus[job.id] && hubState.jobStatus[job.id] !== "done") finished = true;
        hubState.jobStatus[job.id] = job.status;

        const head = el("div", { className: "dl-head" }, el("span", { className: "dl-label" }, job.label));
        if (["queued", "downloading"].includes(job.status)) {
            head.append(jobButton("Annuler", () => api(`/api/downloads/${job.id}/cancel`, { method: "POST" })));
        }
        if (["cancelled", "error"].includes(job.status)) {
            head.append(jobButton("Reprendre", () => api(`/api/downloads/${job.id}/resume`, { method: "POST" })));
        }
        if (!["downloading", "verifying"].includes(job.status)) {
            const label = job.status === "done" ? "Retirer" : "Abandonner";
            head.append(jobButton(label, () => api(`/api/downloads/${job.id}`, { method: "DELETE" })));
        }

        const pct = job.total_bytes ? (100 * job.done_bytes) / job.total_bytes : 0;
        const info = [STATUS_LABELS[job.status], `${formatSize(job.done_bytes)} / ${formatSize(job.total_bytes)}`, `${pct.toFixed(1)} %`];
        if (job.status === "downloading" && job.speed) info.push(`${(job.speed / 1024 ** 2).toFixed(1)} Mo/s`);
        if (job.eta_s != null) info.push(`reste ${formatDuration(job.eta_s)}`);
        if (job.current && job.status !== "done") info.push(job.current);

        const item = el(
            "li",
            {},
            head,
            el("div", { className: "dl-bar" }, el("span", { style: `width: ${pct}%` })),
            el("div", { className: "dl-info" }, info.join(" · "))
        );
        if (job.error) item.append(el("div", { className: "error small" }, job.error));
        list.append(item);
    }
    if (finished) {
        // Le backend a déjà rescanné : on rafraîchit l'onglet Modèles et les badges « déjà là »
        loadModels();
        if (hubState.repo) openRepo(hubState.repo);
    }
}

refreshDownloads();
setInterval(refreshDownloads, 1000);
