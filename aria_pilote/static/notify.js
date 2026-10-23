// Notifications du navigateur (centre de notifications de Windows) : fin de tâche, agent qui t'attend,
// modèle chargé, téléchargement terminé. Réglées dans Réglages › Général ; un clic ramène l'onglet d'Aria.
// S'appuie sur state (app.js).

function notificationsAllowed() {
    return "Notification" in window && Notification.permission === "granted";
}

function showNotification(title, body, tag) {
    const note = new Notification(title, { body, icon: "favicon.png", tag });
    note.onclick = () => {
        window.focus();
        note.close();
    };
    return note;
}

function notify(kind, title, body = "") {
    const n = state.config?.notifications;
    if (!n?.enabled || !n[kind] || !notificationsAllowed()) return;
    // « Seulement quand Aria n'est pas au premier plan » : inutile de te prévenir de ce que tu regardes
    if (n.only_background && document.visibilityState === "visible" && document.hasFocus()) return;
    showNotification(title, body, `aria-${kind}`);
}
