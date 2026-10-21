// Rendu Markdown minimal et sûr : tout le texte est échappé avant d'ajouter nos balises.

function escapeHtml(text) {
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function renderInline(text) {
    const codes = [];
    let s = escapeHtml(text).replace(/`([^`]+)`/g, (_, code) => {
        codes.push(code);
        return `\u0000${codes.length - 1}\u0000`;
    });
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
    s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
}

const FENCE_RE = /^\s*(```|~~~)\s*([\w+#.-]*)/;
const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const HR_RE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const QUOTE_RE = /^\s*>\s?(.*)$/;
const LIST_RE = /^\s*([-*+]|\d+[.)])\s+(.*)$/;
const TABLE_SEP_RE = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;

function isTableStart(lines, i) {
    return lines[i].includes("|") && i + 1 < lines.length && TABLE_SEP_RE.test(lines[i + 1]);
}

function isBlockStart(lines, i) {
    const line = lines[i];
    return FENCE_RE.test(line) || HEADING_RE.test(line) || HR_RE.test(line)
        || QUOTE_RE.test(line) || LIST_RE.test(line) || isTableStart(lines, i);
}

function tableCells(row) {
    return row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}

function codeBlock(code, lang) {
    return `<div class="code"><div class="code-head"><span>${escapeHtml(lang || "code")}</span>`
        + `<button type="button" class="copy">Copier</button></div>`
        + `<pre><code>${escapeHtml(code)}</code></pre></div>`;
}

function renderMarkdown(src) {
    const lines = (src || "").replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
        const line = lines[i];

        const fence = line.match(FENCE_RE);
        if (fence) {
            // Un bloc non refermé (réponse en cours de streaming) va jusqu'à la fin
            const body = [];
            i++;
            while (i < lines.length && !lines[i].trim().startsWith(fence[1])) {
                body.push(lines[i]);
                i++;
            }
            i++;
            out.push(codeBlock(body.join("\n"), fence[2]));
            continue;
        }

        if (!line.trim()) {
            i++;
            continue;
        }

        const heading = line.match(HEADING_RE);
        if (heading) {
            const level = heading[1].length;
            out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
            i++;
            continue;
        }

        if (HR_RE.test(line)) {
            out.push("<hr>");
            i++;
            continue;
        }

        if (QUOTE_RE.test(line)) {
            const body = [];
            while (i < lines.length && QUOTE_RE.test(lines[i])) {
                body.push(lines[i].match(QUOTE_RE)[1]);
                i++;
            }
            out.push(`<blockquote>${renderMarkdown(body.join("\n"))}</blockquote>`);
            continue;
        }

        if (isTableStart(lines, i)) {
            const head = tableCells(lines[i]).map((c) => `<th>${renderInline(c)}</th>`).join("");
            i += 2;
            const rows = [];
            while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
                rows.push(`<tr>${tableCells(lines[i]).map((c) => `<td>${renderInline(c)}</td>`).join("")}</tr>`);
                i++;
            }
            out.push(`<div class="table"><table><thead><tr>${head}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`);
            continue;
        }

        const first = line.match(LIST_RE);
        if (first) {
            const ordered = /\d/.test(first[1]);
            const items = [];
            while (i < lines.length) {
                const m = lines[i].match(LIST_RE);
                if (m && /\d/.test(m[1]) === ordered) {
                    items.push(m[2]);
                } else if (items.length && lines[i].trim() && /^\s{2,}/.test(lines[i])) {
                    items[items.length - 1] += " " + lines[i].trim();
                } else {
                    break;
                }
                i++;
            }
            const tag = ordered ? "ol" : "ul";
            const start = ordered && parseInt(first[1], 10) !== 1 ? ` start="${parseInt(first[1], 10)}"` : "";
            out.push(`<${tag}${start}>${items.map((it) => `<li>${renderInline(it)}</li>`).join("")}</${tag}>`);
            continue;
        }

        const para = [];
        while (i < lines.length && lines[i].trim() && (para.length === 0 || !isBlockStart(lines, i))) {
            para.push(lines[i]);
            i++;
        }
        out.push(`<p>${para.map(renderInline).join("<br>")}</p>`);
    }
    return out.join("");
}
