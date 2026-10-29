"""Écran du PC (chantier 4) : fenêtres ouvertes, captures et éléments d'interface, en lecture seule.

Les éléments viennent de l'accessibilité de Windows (UI Automation) : chacun reçoit un numéro, dessiné aussi sur la
capture, pour que le modèle désigne « le 12 » plutôt que des coordonnées. Les actions (clic, frappe) arrivent à
l'étape 4C, l'activation et les garde-fous à l'étape 4D.
"""

import ctypes
import sys
import time

from PIL import Image, ImageDraw, ImageFont, ImageGrab

MAX_ELEMENTS = 120
MAX_TEXTS = 40
WALK_BUDGET_S = 4  # un navigateur peut exposer des milliers d'éléments : on s'arrête à temps
VALUE_CHARS = 300
TYPES = {  # éléments numérotés (on peut agir dessus), avec leur nom en français
    "ButtonControl": "bouton", "SplitButtonControl": "bouton", "EditControl": "champ", "DocumentControl": "document",
    "CheckBoxControl": "case", "RadioButtonControl": "option", "ComboBoxControl": "liste déroulante",
    "ListItemControl": "élément de liste", "MenuItemControl": "menu", "TabItemControl": "onglet",
    "HyperlinkControl": "lien", "TreeItemControl": "élément d'arbre", "SliderControl": "curseur",
    "SpinnerControl": "compteur", "DataItemControl": "cellule",
}
MARK_COLORS = [(255, 0, 170), (0, 170, 255), (255, 140, 0), (0, 190, 90), (150, 80, 255)]


class ScreenError(Exception):
    pass


if sys.platform == "win32":
    from ctypes import wintypes as wt

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    kernel32 = ctypes.windll.kernel32
    dwmapi = ctypes.windll.dwmapi
    # Handles 64 bits : sans restype, ctypes les tronquerait en int 32 bits
    for fn, res in ((user32.GetWindowDC, wt.HDC), (gdi32.CreateCompatibleDC, wt.HDC),
                    (gdi32.CreateCompatibleBitmap, wt.HBITMAP), (gdi32.SelectObject, wt.HGDIOBJ),
                    (kernel32.OpenProcess, wt.HANDLE), (user32.GetWindow, wt.HWND),
                    (user32.GetWindowDpiAwarenessContext, ctypes.c_void_p), (user32.MonitorFromWindow, ctypes.c_void_p)):
        fn.restype = res
    user32.GetWindowDC.argtypes = [wt.HWND]
    user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
    user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
    gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
    gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wt.HDC]
    gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    try:
        # Coordonnées en pixels réels sur chaque écran, comme les captures (sinon décalées à 125 % ou 150 %)
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]


def _require_windows():
    if sys.platform != "win32":
        raise ScreenError("Le contrôle de l'écran n'existe que sous Windows.")


# --- Fenêtres ------------------------------------------------------------------------

def _title(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _cloaked(hwnd):
    """Fenêtre « masquée » par Windows (applis du Store en veille, autres bureaux virtuels)."""
    value = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), 14, ctypes.byref(value), ctypes.sizeof(value))
    return bool(value.value)


def _process(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wt.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value.rsplit("\\", 1)[-1]
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _rect(hwnd):
    """Cadre visible de la fenêtre (sans l'ombre), en pixels d'écran."""
    rect = wt.RECT()
    if dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def windows():
    """Fenêtres principales visibles, dans l'ordre d'empilement (la plus en avant d'abord)."""
    _require_windows()
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def collect(hwnd, _):
        # Boîtes de dialogue comprises (Enregistrer sous, Ouvrir…, fenêtres « possédées ») ; pas les WS_EX_TOOLWINDOW
        if user32.IsWindowVisible(hwnd) and _title(hwnd) and not _cloaked(hwnd) \
                and not user32.GetWindowLongW(hwnd, -20) & 0x80:
            found.append(hwnd)
        return True

    user32.EnumWindows(collect, 0)
    return [{"id": int(h), "titre": _title(h), "processus": _process(h), "rect": _rect(h),
             "reduite": bool(user32.IsIconic(wt.HWND(h))),
             "proprietaire": int(user32.GetWindow(wt.HWND(h), 4) or 0) or None} for h in found]


def _pid(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
    return pid.value


def _class(hwnd):
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(wt.HWND(hwnd), buffer, 256)
    return buffer.value


MAX_POPUPS = 6
SHELL_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"}  # barre des tâches, bureau


def popups(window):
    """Ce qui s'est ouvert par-dessus la fenêtre, dans la même appli : menus, listes déroulantes, boîtes de
    dialogue. Ils font partie de ce que l'agent observe et peut toucher (un menu est une fenêtre à part)."""
    _require_windows()
    pid = _pid(window["id"])
    if not pid:
        return []
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def collect(hwnd, _):
        h = int(hwnd or 0)
        if h == window["id"] or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or _cloaked(h):
            return True
        if _pid(h) != pid:
            return True
        cls = _class(h)
        if cls in SHELL_CLASSES:
            return True
        owner = int(user32.GetWindow(hwnd, 4) or 0)
        style = user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF
        if cls == "#32768" or owner == window["id"] or (style & 0x80000000 and not _title(h)):  # menu, possédée, WS_POPUP
            rect = _rect(h)
            if rect[2] - rect[0] > 4 and rect[3] - rect[1] > 4:
                found.append({"id": h, "titre": _title(h), "classe": cls, "rect": rect})
        return True

    user32.EnumWindows(collect, 0)
    return found[:MAX_POPUPS]


def zone(window, extra=()):
    """Cadre qui englobe la fenêtre et ce qui s'est ouvert par-dessus."""
    rects = [window["rect"], *(p["rect"] for p in extra)]
    return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))


def restore(hwnd):
    """Rouvre une fenêtre réduite dans la barre des tâches et la met au premier plan."""
    _require_windows()
    user32.ShowWindow(wt.HWND(hwnd), 9)  # SW_RESTORE
    time.sleep(0.3)
    return bring_to_front(hwnd)


def capture_region(rect):
    """Ce qui est affiché dans ce rectangle de l'écran, en pleine résolution (zoom, menus ouverts)."""
    _require_windows()
    return ImageGrab.grab(bbox=tuple(int(v) for v in rect), all_screens=True)


def find(spec, listed=None):
    """Fenêtre désignée par son id, un morceau de son titre ou son programme (ex. « Bloc-notes », « notepad »)."""
    listed = windows() if listed is None else listed
    text = str(spec or "").strip()
    if not text:
        raise ScreenError("Quelle fenêtre ? Donne son id, un morceau de son titre ou son programme.")
    if text.isdigit():
        match = [w for w in listed if w["id"] == int(text)]
    else:
        low = text.lower()
        match = [w for w in listed if low in w["titre"].lower()] or \
                [w for w in listed if low in w["processus"].lower()]
    if not match:
        names = " ; ".join(f"{w['titre']} ({w['id']})" for w in listed[:15]) or "aucune"
        raise ScreenError(f"Aucune fenêtre ne correspond à « {text} ». Fenêtres ouvertes : {names}.")
    return match[0]


def describe_windows(listed):
    lines = [f"[{w['id']}] {w['titre']} — {w['processus'] or '?'}" + (" (réduite)" if w["reduite"] else "")
             for w in listed]
    return "Fenêtres ouvertes (de la plus en avant à la plus en arrière) :\n" + "\n".join(lines)


# --- Captures ----------------------------------------------------------------------

def _dpi_factor(hwnd):
    """Agrandissement que Windows applique à la fenêtre : 1 pour une appli qui gère elle-même l'affichage à 125 %,
    1,25 pour une ancienne appli qu'il dessine en 100 % puis étire."""
    try:
        awareness = user32.GetAwarenessFromDpiAwarenessContext(
            ctypes.c_void_p(user32.GetWindowDpiAwarenessContext(wt.HWND(hwnd))))
        monitor = user32.MonitorFromWindow(wt.HWND(hwnd), 2)  # MONITOR_DEFAULTTONEAREST
        dpi_x, dpi_y = wt.UINT(), wt.UINT()
        ctypes.windll.shcore.GetDpiForMonitor(ctypes.c_void_p(monitor), 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
    except (AttributeError, OSError):
        return 1.0
    rendered = {0: 96, 1: user32.GetDpiForSystem()}.get(awareness, dpi_x.value)  # 2 : l'appli suit chaque écran
    return dpi_x.value / rendered if rendered else 1.0


def _print_window(hwnd):
    """Contenu de la fenêtre, même recouverte par une autre (PrintWindow) ; None si l'appli ne se laisse pas faire."""
    outer = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(outer))
    w, h = outer.right - outer.left, outer.bottom - outer.top
    if w <= 0 or h <= 0:
        return None
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bitmap)
    try:
        if not user32.PrintWindow(hwnd, mem, 2):  # PW_RENDERFULLCONTENT : applis modernes comprises
            return None
        header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h, biPlanes=1,
                                  biBitCount=32, biCompression=0)
        buffer = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mem, bitmap, 0, h, buffer, ctypes.byref(header), 0)
        image = Image.frombuffer("RGB", (w, h), buffer, "raw", "BGRX", 0, 1)
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)
    if not image.getbbox():  # tout noir : rendu que PrintWindow ne voit pas (certains jeux, vidéos)
        return None
    factor = _dpi_factor(hwnd)
    if factor > 1.01:
        # Appli qui ignore l'agrandissement d'affichage : PrintWindow la rend à sa taille d'origine, dans le coin ;
        # on la remet à la taille vue à l'écran, sinon les repères (en pixels réels) tomberaient à côté
        image = image.crop((0, 0, round(w / factor), round(h / factor))).resize((w, h), Image.LANCZOS)
    left, top, right, bottom = _rect(hwnd)  # on retire l'ombre de la fenêtre
    return image.crop((left - outer.left, top - outer.top, right - outer.left, bottom - outer.top))


def capture_window(window):
    """Image de la fenêtre et sa position à l'écran (l'origine sert à placer les repères)."""
    _require_windows()
    if window["reduite"]:
        raise ScreenError(f"La fenêtre « {window['titre']} » est réduite dans la barre des tâches : "
                          "elle ne se voit pas tant qu'elle n'est pas rouverte.")
    image = _print_window(window["id"])
    if image is None:
        # Plan B : ce qui est affiché à cet endroit de l'écran (juste si la fenêtre n'est pas recouverte)
        image = ImageGrab.grab(bbox=window["rect"], all_screens=True)
    return image, window["rect"][:2]


def capture_screen():
    """Tous les écrans côte à côte, et l'origine du bureau virtuel."""
    _require_windows()
    left, top = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)  # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN
    return ImageGrab.grab(all_screens=True), (left, top)


# --- Éléments (UI Automation) ---------------------------------------------------------------

def _value(ctrl, kind):
    try:
        if kind in ("EditControl", "ComboBoxControl"):
            if getattr(ctrl, "IsPassword", False):
                return None
            return ctrl.GetValuePattern().Value
        if kind == "DocumentControl":
            return ctrl.GetTextPattern().DocumentRange.GetText(VALUE_CHARS + 1)
        if kind in ("CheckBoxControl", "RadioButtonControl"):
            pattern = ctrl.GetTogglePattern() if kind == "CheckBoxControl" else ctrl.GetSelectionItemPattern()
            state = pattern.ToggleState if kind == "CheckBoxControl" else pattern.IsSelected
            return "cochée" if state in (1, True) else "décochée"
    except Exception:  # motif absent ou appli qui répond mal : la valeur est facultative
        return None
    return None


def read_elements(window, limit=MAX_ELEMENTS, extra=()):
    """Éléments utilisables (numérotés) et textes visibles de la fenêtre, lus par UI Automation ; `extra` : les menus
    et boîtes de dialogue ouverts par-dessus (popups), lus ensuite, à la suite des numéros."""
    _require_windows()
    import uiautomation as auto  # chargé à la demande : quelques secondes au premier appel

    elements, texts, truncated = [], [], False
    with auto.UIAutomationInitializerInThread():
        started = time.monotonic()
        # Ce qui est ouvert par-dessus d'abord : un menu ou une boîte de dialogue est ce qui attend une action
        for source in [*extra, window]:
            root = auto.ControlFromHandle(source["id"])
            if root is None:
                if source is window:
                    raise ScreenError(f"La fenêtre « {window['titre']} » ne répond pas à l'accessibilité de Windows.")
                continue
            if _walk(auto, root, source, elements, texts, limit, started):
                truncated = True
                break
    return elements, texts, truncated


def _walk(auto, root, source, elements, texts, limit, started):
    """Parcourt une fenêtre ; True si la liste est coupée (trop d'éléments ou trop long)."""
    left, top, right, bottom = source["rect"]
    for ctrl, _depth in auto.WalkControl(root, includeTop=False, maxDepth=30):
        if time.monotonic() - started > WALK_BUDGET_S or len(elements) >= limit:
            return True
        try:
            kind = ctrl.ControlTypeName
            if kind not in TYPES and kind != "TextControl":
                continue
            if ctrl.IsOffscreen:
                continue
            r = ctrl.BoundingRectangle
            box = (max(r.left, left), max(r.top, top), min(r.right, right), min(r.bottom, bottom))
            if box[2] - box[0] < 2 or box[3] - box[1] < 2:
                continue  # hors de la fenêtre ou invisible
            name = " ".join((ctrl.Name or "").split())[:80]
            if kind == "TextControl":
                if name and len(texts) < MAX_TEXTS and name not in texts:
                    texts.append(name)
                continue
            value = _value(ctrl, kind)
            elements.append({
                "n": len(elements) + 1, "type": kind, "nom": name, "rect": box,
                "valeur": " ".join(value.split())[:VALUE_CHARS] if isinstance(value, str) else None,
                "mot_de_passe": bool(getattr(ctrl, "IsPassword", False)),
                "actif": bool(ctrl.IsEnabled),
            })
        except Exception:  # élément disparu pendant la lecture (menu refermé…)
            continue
    return False


def describe_elements(window, elements, texts, truncated, extra=()):
    w = window["rect"][2] - window["rect"][0]
    h = window["rect"][3] - window["rect"][1]
    lines = [f"Fenêtre « {window['titre']} » ({window['processus'] or '?'}), {w}x{h} à l'écran, id {window['id']}"]
    for p in extra:
        what = "menu" if p["classe"] == "#32768" or not p["titre"] else f"boîte de dialogue « {p['titre']} »"
        lines.append(f"Ouvert par-dessus : {what} (ses éléments sont en tête de liste)")
    if elements:
        lines.append("Éléments (désigne-les par leur numéro) :")
        for e in elements:
            line = f"[{e['n']}] {TYPES[e['type']]} « {e['nom'] or 'sans nom'} »"
            if e["mot_de_passe"]:
                line += " (champ mot de passe : c'est à l'utilisateur de le remplir)"
            elif e["valeur"]:
                line += f" = « {e['valeur']} »"
            if not e["actif"]:
                line += " (désactivé)"
            lines.append(line)
    else:
        lines.append("Aucun élément exposé par l'accessibilité de Windows : fie-toi à la capture (voir_ecran).")
    if truncated:
        lines.append(f"(liste coupée à {len(elements)} éléments : la fenêtre en expose davantage)")
    if texts:
        lines.append("Textes affichés : " + " · ".join(f"« {t} »" for t in texts))
    return "\n".join(lines)


# --- Repères sur la capture -------------------------------------------------------------

def _font(size):
    for name in ("segoeuib.ttf", "arialbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def fit(image, max_side):
    """Capture réduite pour le modèle, et l'échelle appliquée (les repères et l'étape 4C en ont besoin)."""
    scale = min(1.0, max_side / max(image.size))
    if scale < 1:
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    return image, scale


def draw_marks(image, elements, origin, scale):
    """Encadre chaque élément et écrit son numéro, aux mêmes numéros que la liste envoyée au modèle."""
    image = image.convert("RGB")
    draw = ImageDraw.Draw(image)
    font = _font(13)
    for e in elements:
        color = MARK_COLORS[(e["n"] - 1) % len(MARK_COLORS)]
        x0, y0, x1, y1 = [round((v - origin[i % 2]) * scale) for i, v in enumerate(e["rect"])]
        draw.rectangle((x0, y0, x1, y1), outline=color, width=2)
        label = str(e["n"])
        tw, th = draw.textbbox((0, 0), label, font=font)[2:]
        lx, ly = x0, (y0 - th - 4 if y0 - th - 4 >= 0 else y0)  # au-dessus du cadre, sinon dedans
        draw.rectangle((lx, ly, lx + tw + 6, ly + th + 4), fill=color)
        draw.text((lx + 3, ly + 1), label, fill=(255, 255, 255), font=font)
    return image


# --- Actions (étape 4C) -------------------------------------------------------------------
# Chaque action part de ce que le modèle a observé (numéro d'élément ou point de la capture) et passe par
# SendInput, comme une vraie souris et un vrai clavier. Nos entrées portent une marque (dwExtraInfo) : l'étape 4D
# s'en servira pour reconnaître les tiennes et mettre l'agent en pause dès que tu reprends la main.

ARIA_INPUT = 0xA51A
SETTLE_S = 0.6  # le temps que la fenêtre réagisse avant la nouvelle observation
MAX_TEXT = 2000
TYPE_DELAY_S = 0.02  # entre deux lettres tapées (voir type_text)
MAX_WAIT_S = 10
OWN_WINDOW = "aria pilote"  # l'agent n'agit jamais sur la fenêtre qui affiche ses propres accords
# Applis jamais observables ni actionnables, quoi que l'utilisateur autorise : réglages et sécurité du système,
# et les terminaux (l'agent a déjà run_command et ses propres accords pour ça)
DENIED_PROCESSES = frozenset({
    "systemsettings.exe", "regedit.exe", "taskmgr.exe", "mmc.exe", "useraccountcontrolsettings.exe",
    "lsass.exe", "consent.exe", "sechealthui.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
    "windowsterminal.exe", "conhost.exe", "openconsole.exe",
})
DENIED_TITLES = ("paramètres", "panneau de configuration", "sécurité windows", "windows security",
                 "contrôle de compte d'utilisateur", "user account control", "gestionnaire des tâches")
KEYS = {
    "ctrl": 0x11, "control": 0x11, "maj": 0x10, "shift": 0x10, "alt": 0x12, "altgr": 0xA5, "win": 0x5B,
    "entree": 0x0D, "enter": 0x0D, "echap": 0x1B, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "espace": 0x20,
    "space": 0x20, "retour": 0x08, "backspace": 0x08, "suppr": 0x2E, "delete": 0x2E, "del": 0x2E, "inser": 0x2D,
    "insert": 0x2D, "haut": 0x26, "up": 0x26, "bas": 0x28, "down": 0x28, "gauche": 0x25, "left": 0x25,
    "droite": 0x27, "right": 0x27, "debut": 0x24, "home": 0x24, "fin": 0x23, "end": 0x23, "pagehaut": 0x21,
    "pageup": 0x21, "pagebas": 0x22, "pagedown": 0x22, "menu": 0x5D, "apps": 0x5D,
}
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5D, 0xA5}
MODIFIERS = {0x10, 0x11, 0x12, 0x5B, 0xA5}

if sys.platform == "win32":
    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                    ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]

    user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.WindowFromPoint.argtypes = [wt.POINT]
    user32.WindowFromPoint.restype = wt.HWND
    user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    user32.GetAncestor.restype = wt.HWND
    user32.GetForegroundWindow.restype = wt.HWND


def _send(*inputs):
    array = (INPUT * len(inputs))(*inputs)
    if user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT)) != len(inputs):
        raise ScreenError("Windows a bloqué l'action (fenêtre d'un programme lancé en administrateur ?).")


def _mouse(flags, data=0):
    return INPUT(type=0, u=_INPUTUNION(mi=MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, ARIA_INPUT)))


def _key(vk, up=False):
    flags = (0x2 if up else 0) | (0x1 if vk in EXTENDED else 0)
    return INPUT(type=1, u=_INPUTUNION(ki=KEYBDINPUT(vk, 0, flags, 0, ARIA_INPUT)))


def _char(ch, up=False):
    return INPUT(type=1, u=_INPUTUNION(ki=KEYBDINPUT(0, ord(ch), 0x4 | (0x2 if up else 0), 0, ARIA_INPUT)))


def parse_keys(spec):
    """« ctrl+s », « Maj+Tab », « entrée », « F5 » -> codes de touches (modificateurs d'abord)."""
    import unicodedata
    plain = unicodedata.normalize("NFKD", spec or "").encode("ascii", "ignore").decode().lower()
    parts = [p.strip() for p in plain.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ScreenError("Quelles touches ? Par exemple « ctrl+s », « entree » ou « alt+tab ».")
    codes = []
    for part in parts:
        if part in KEYS:
            codes.append(KEYS[part])
        elif len(part) == 1 and part.isalnum():
            codes.append(ord(part.upper()))
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            codes.append(0x6F + int(part[1:]))
        else:
            raise ScreenError(f"Touche inconnue : « {part} ».")
    return codes


def press(spec):
    codes = parse_keys(spec)
    _send(*[_key(c) for c in codes], *[_key(c, up=True) for c in reversed(codes)])


def type_text(text):
    """Texte tapé caractère par caractère (Unicode : accents et symboles compris), Entrée pour les retours à la ligne."""
    if len(text) > MAX_TEXT:
        raise ScreenError(f"Texte trop long pour être tapé : {MAX_TEXT} caractères au plus.")
    for ch in text.replace("\r\n", "\n"):
        if ch == "\n":
            _send(_key(0x0D), _key(0x0D, up=True))
        else:
            _send(_char(ch), _char(ch, up=True))
        # Le Bloc-notes de Windows 11 perd ou mélange des lettres à 4 ms (« Essai rria DD » pour « Essai Aria 5D »),
        # pas à 15 ms (mesuré) : 20 ms, avec de la marge
        time.sleep(TYPE_DELAY_S)


def _move(x, y):
    if not user32.SetCursorPos(int(x), int(y)):
        raise ScreenError("Impossible de placer la souris à cet endroit.")


def click(x, y, button="gauche", double=False):
    down, up = {"gauche": (0x2, 0x4), "droit": (0x8, 0x10)}[button]
    _move(x, y)
    time.sleep(0.05)
    for _ in range(2 if double else 1):
        _send(_mouse(down), _mouse(up))
        time.sleep(0.05)


def scroll(x, y, direction, amount):
    _move(x, y)
    sign = 1 if direction in ("haut", "droite") else -1
    flag = 0x01000 if direction in ("gauche", "droite") else 0x0800  # MOUSEEVENTF_HWHEEL / WHEEL
    for _ in range(amount):
        _send(_mouse(flag, sign * 120))
        time.sleep(0.03)


def drag(x0, y0, x1, y1):
    _move(x0, y0)
    _send(_mouse(0x2))
    try:
        for step in range(1, 11):
            _move(x0 + (x1 - x0) * step / 10, y0 + (y1 - y0) * step / 10)
            time.sleep(0.02)
    finally:
        _send(_mouse(0x4))


def is_own_window(window):
    return OWN_WINDOW in window["titre"].lower() or window["processus"].lower().startswith("aria-pilote")


def is_denied(window):
    """Appli interdite d'office (réglages, sécurité, terminal) : jamais observable ni actionnable."""
    titre = window["titre"].lower()
    return (window["processus"].lower() in DENIED_PROCESSES or is_own_window(window)
            or any(t in titre for t in DENIED_TITLES))


def allowed_windows(listed, allow):
    """Fenêtres que l'agent a le droit de voir : jamais les interdites ; si `allow` est une liste (même vide), seuls
    ces programmes (la liste que l'utilisateur a cochée dans « Prendre la main ») ; `allow` à None = toutes les non
    interdites."""
    usable = [w for w in listed if not is_denied(w)]
    if allow is None:
        return usable
    allow = {p.lower() for p in allow}
    return [w for w in usable if w["processus"].lower() in allow]


def ensure_allowed(window, allow):
    """Lève une erreur claire si la fenêtre visée est interdite ou hors de la liste autorisée."""
    if is_denied(window):
        raise ScreenError(f"La fenêtre « {window['titre']} » fait partie des applis que je ne touche jamais "
                          "(réglages système, sécurité ou terminal).")
    if allow is not None and window["processus"].lower() not in {p.lower() for p in allow}:
        raise ScreenError(f"La fenêtre « {window['titre']} » n'est pas dans celles que tu m'as autorisées. "
                          "Ajoute-la avec « Prendre la main » si tu veux que j'y agisse.")


def foreground():
    return int(user32.GetForegroundWindow() or 0)


def info(hwnd):
    """Titre, programme et cadre d'une fenêtre quelconque (barre des tâches, bureau, menu Démarrer compris)."""
    _require_windows()
    hwnd = int(hwnd or 0)
    return {"id": hwnd, "titre": _title(hwnd) or _class(hwnd), "processus": _process(hwnd), "rect": _rect(hwnd),
            "reduite": bool(user32.IsIconic(wt.HWND(hwnd))), "proprietaire": None} if hwnd else None


def window_at(x, y):
    """La fenêtre (principale) sous ce point de l'écran, décrite comme les autres."""
    return info(owner_at(x, y))


def taskbar():
    """La barre des tâches principale (bouton Démarrer, applis épinglées, zone de notification)."""
    _require_windows()
    hwnd = user32.FindWindowW("Shell_TrayWnd", None)
    found = info(hwnd)
    if found:
        found["titre"] = "Barre des tâches"
    return found


def desktop_rect():
    """Le bureau virtuel : tous les écrans côte à côte."""
    _require_windows()
    left, top = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    return left, top, left + user32.GetSystemMetrics(78), top + user32.GetSystemMetrics(79)


def open_app(name):
    """Ouvre une appli par le menu Démarrer : Windows, son nom, Entrée (comme toi au clavier)."""
    press("win")
    time.sleep(0.9)
    type_text(name)
    time.sleep(1.2)
    press("entree")


def owner_at(x, y):
    """Fenêtre principale sous ce point de l'écran."""
    hwnd = user32.WindowFromPoint(wt.POINT(int(x), int(y)))
    return int(user32.GetAncestor(hwnd, 2) or 0) if hwnd else 0  # GA_ROOT


def bring_to_front(hwnd):
    """Met la fenêtre au premier plan avant d'y cliquer ou d'y taper (Windows le refuse souvent à un programme en
    arrière-plan : on emprunte la file d'entrée de la fenêtre active le temps de la demande)."""
    if int(user32.GetForegroundWindow() or 0) == hwnd:
        return True
    current = kernel32.GetCurrentThreadId()
    target = user32.GetWindowThreadProcessId(wt.HWND(user32.GetForegroundWindow()), None)
    attached = target and target != current and user32.AttachThreadInput(current, target, True)
    try:
        user32.BringWindowToTop(wt.HWND(hwnd))
        user32.SetForegroundWindow(wt.HWND(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(current, target, False)
    for _ in range(20):
        if int(user32.GetForegroundWindow() or 0) == hwnd:
            return True
        time.sleep(0.025)
    return False


def focused_is_password():
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread():
        try:
            return bool(auto.GetFocusedControl().IsPassword)
        except Exception:
            return False
