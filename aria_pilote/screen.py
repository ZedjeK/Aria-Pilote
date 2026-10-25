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
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4) and _title(hwnd) and not _cloaked(hwnd) \
                and not user32.GetWindowLongW(hwnd, -20) & 0x80:  # ni fenêtre possédée, ni WS_EX_TOOLWINDOW
            found.append(hwnd)
        return True

    user32.EnumWindows(collect, 0)
    return [{"id": int(h), "titre": _title(h), "processus": _process(h), "rect": _rect(h),
             "reduite": bool(user32.IsIconic(wt.HWND(h)))} for h in found]


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


def read_elements(window, limit=MAX_ELEMENTS):
    """Éléments utilisables (numérotés) et textes visibles de la fenêtre, lus par UI Automation."""
    _require_windows()
    import uiautomation as auto  # chargé à la demande : quelques secondes au premier appel

    left, top, right, bottom = window["rect"]
    elements, texts, truncated = [], [], False
    with auto.UIAutomationInitializerInThread():
        root = auto.ControlFromHandle(window["id"])
        if root is None:
            raise ScreenError(f"La fenêtre « {window['titre']} » ne répond pas à l'accessibilité de Windows.")
        started = time.monotonic()
        for ctrl, _depth in auto.WalkControl(root, includeTop=False, maxDepth=30):
            if time.monotonic() - started > WALK_BUDGET_S or len(elements) >= limit:
                truncated = True
                break
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
    return elements, texts, truncated


def describe_elements(window, elements, texts, truncated):
    w = window["rect"][2] - window["rect"][0]
    h = window["rect"][3] - window["rect"][1]
    lines = [f"Fenêtre « {window['titre']} » ({window['processus'] or '?'}), {w}x{h} à l'écran, id {window['id']}"]
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
