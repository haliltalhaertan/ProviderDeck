"""Native Windows notification icon, backed only by the local cached snapshot."""
from __future__ import annotations

import argparse
import ctypes as ct
from ctypes import wintypes as w
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import http.client
import json
import math
from pathlib import Path
from tempfile import NamedTemporaryFile
import threading
from urllib.parse import urlsplit
import uuid

from launch import safe_runtime_url

APP_GUID = uuid.UUID('5af98201-8bbc-4d53-b374-053fccbcff84')
FALLBACK_TIP = 'ProviderDeck · yerel panele ulaşılamadı'
ICON_ID = 1
MAX_STATE_BYTES = 12_000_000


def _number(value):
    return (isinstance(value, (float, int)) and not isinstance(value, bool)
            and -1e18 <= value <= 1e18 and math.isfinite(value))


_SUBSCRIPTION_NAMES = {
    'anthropic': ('Claude', 'Claude'),
    'openai-codex': ('Codex', 'Codex'),
    'copilot': ('GitHub Copilot', 'Copilot'),
    'opencode-go': ('OpenCode Go', 'Go'),
    'antigravity': ('Google Antigravity', 'Google'),
    'muse-code': ('Muse Code', 'Muse'),
}


@dataclass(frozen=True)
class SubscriptionLine:
    provider_id: str
    name: str
    detail: str
    compact: str
    compact_short: str = ''


class TrayPreferences:
    """Display-only preferences, owned by the tray's UI thread."""

    def __init__(self, path):
        self.path = path
        self.hidden = set()
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            hidden = data.get('hidden_subscriptions') if isinstance(data, dict) else None
            if isinstance(hidden, list):
                self.hidden = {value for value in hidden if isinstance(value, str)}
        except (OSError, ValueError):
            pass

    def visible(self, lines):
        return [line for line in lines if line.provider_id not in self.hidden]

    def toggle(self, provider_id):
        hidden = self.hidden ^ {provider_id}
        pending = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                    prefix='tray-preferences-', suffix='.tmp', delete=False) as file:
                pending = Path(file.name)
                json.dump({'hidden_subscriptions': sorted(hidden)}, file)
            pending.replace(self.path)
        except OSError:
            if pending is not None:
                try:
                    pending.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        self.hidden = hidden


def _label(value, fallback):
    if not isinstance(value, str):
        return fallback
    clean = ''.join(c if c.isprintable() else ' ' for c in value)
    return ' '.join(clean.split())[:56] or fallback


def _fmt_pct(value):
    return '%' + f'{value:.1f}'.removesuffix('.0').replace('.', ',')


def _is_five_hour(window):
    label = _label(window.get('label'), '').lower()
    return '5 saat' in label or label == '5s' or 'kayan pencere' in label


def _pick_five_hour(known, chosen):
    if chosen is None or _is_five_hour(chosen):
        return None
    candidates = [w for w in known if w is not chosen and _is_five_hour(w)]
    if not candidates:
        return None
    if chosen.get('scope') == 'model':
        prefix = _label(chosen.get('label'), '').lower().split(' ', 1)[0]
        if prefix:
            same_group = next((w for w in candidates if _label(w.get('label'), '').lower().startswith(prefix)), None)
            if same_group is not None:
                return same_group
    return next((w for w in candidates if w.get('scope') != 'model'), None) or candidates[0]


def subscription_lines(state):
    rows = (state or {}).get('providers', [])
    details = {}
    for p in rows:
        if not isinstance(p, dict):
            continue
        pid = p.get('id')
        if not isinstance(pid, str) or pid in details or (p.get('kind') != 'subscription' and pid != 'muse-code'):
            continue
        default = _label(p.get('name'), _label(pid, 'Abonelik'))
        name, short = _SUBSCRIPTION_NAMES.get(pid, (default, default))
        windows = p.get('windows')
        known = [w for w in windows if isinstance(w, dict) and _number(w.get('remaining_percent'))
                 and 0 <= w['remaining_percent'] <= 100] if isinstance(windows, list) else []
        preferred = ('Premium etkileşim', 'Haftalık') if pid == 'copilot' else ('Haftalık',)
        chosen = next((w for label in preferred for w in known if w.get('label') == label and w.get('scope') != 'model'), None)
        chosen = chosen or next((w for w in known if w.get('scope') != 'model'), None) or (known[0] if known else None)
        five_hour = _pick_five_hour(known, chosen)
        old = bool(p.get('stale'))
        if chosen:
            label = _label(chosen.get('label'), 'kota').lower()
            scope = ' (model)' if chosen.get('scope') == 'model' else ''
            value = _fmt_pct(chosen['remaining_percent'])
            compact_short = f'{short}: {value}'
            if five_hour is not None:
                fh_val = _fmt_pct(five_hour['remaining_percent'])
                detail = f'{name} — {label}{scope} {value} · 5s {fh_val}'
                compact = f'{short}: {value} · 5s {fh_val}'
            else:
                detail = f'{name} — {label}{scope} {value}'
                compact = compact_short
        else:
            detail, compact, compact_short = f'{name} — kota bilinmiyor', f'{short}: bilinmiyor', f'{short}: bilinmiyor'
        suffix = '*' if old else ''
        details[pid] = SubscriptionLine(pid, name, detail + (' (eski)' if old else ''), compact + suffix, compact_short + suffix)
    return [details.pop(pid) for pid in _SUBSCRIPTION_NAMES if pid in details] + list(details.values())


def _tooltip_length(value):
    # NOTIFYICONDATA.szTip has 128 UTF-16 code units, including its terminator.
    return len(value.encode('utf-16-le')) // 2


def _snapshot_time(state):
    value = state.get('generated_at')
    try:
        timestamp = datetime.fromisoformat(value) if isinstance(value, str) else None
        return timestamp.astimezone().strftime('%H:%M') if timestamp and timestamp.tzinfo else None
    except (ValueError, OverflowError, OSError):
        return None


def tooltip(state, lines=None):
    if state is None:
        return FALLBACK_TIP
    rows = [p for p in state.get('providers', []) if isinstance(p, dict)]
    balances = [p['balance_usd'] for p in rows if _number(p.get('balance_usd'))]
    total = sum((Decimal(str(v)) for v in balances if v > 0), Decimal(0))
    amount = '$' + f'{total:.2f}'.replace('.', ',') if balances else None
    balance = 'Pozitif USD: ' + amount if amount is not None else 'USD bakiye bilinmiyor'
    stamp = _snapshot_time(state)
    title = f'ProviderDeck · Son veri {stamp}' if stamp else 'ProviderDeck · Son okuma'
    lines = subscription_lines(state) if lines is None else lines
    newline = chr(10)
    detailed = newline.join([title, balance] + [line.detail.replace(' — ', ' ', 1) for line in lines])
    if _tooltip_length(detailed) <= 127:
        return detailed
    short_title = 'ProviderDeck · ' + (amount or 'USD bilinmiyor')
    headings = [short_title + f' · Son veri {stamp}', short_title + f' · {stamp}'] if stamp else []
    for compact in ([line.compact for line in lines], [line.compact_short or line.compact for line in lines]):
        for heading in headings + [short_title]:
            summary = newline.join([heading] + compact)
            if _tooltip_length(summary) <= 127:
                return summary
    return newline.join([title, balance, f'{len(lines)} abonelik · liste için sağ tık'])


def read_state(runtime):
    """No proxies, redirects, auth, or upstream provider requests."""
    try:
        record = json.loads(runtime.read_text(encoding='utf-8'))
        url = record['url']
        if record.get('app') != 'providerdeck' or not safe_runtime_url(url):
            return None, None
        conn = http.client.HTTPConnection('127.0.0.1', urlsplit(url).port, timeout=2)
        try:
            conn.request('GET', '/api/state', headers={'Accept': 'application/json'})
            with conn.getresponse() as response:
                if response.status != 200:
                    return None, None
                data = response.read(MAX_STATE_BYTES + 1)
                if len(data) > MAX_STATE_BYTES:
                    return None, None
            state = json.loads(data)
        finally:
            conn.close()
        if not isinstance(state, dict) or not isinstance(state.get('providers'), list):
            return None, None
        return url.rstrip('/'), state
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        return None, None


class Guid(ct.Structure):
    _fields_ = [('Data1', w.DWORD), ('Data2', w.WORD), ('Data3', w.WORD), ('Data4', ct.c_ubyte * 8)]


class NotifyData(ct.Structure):
    _fields_ = [('cbSize', w.DWORD), ('hWnd', w.HWND), ('uID', w.UINT), ('uFlags', w.UINT),
                ('uCallbackMessage', w.UINT), ('hIcon', w.HICON), ('szTip', ct.c_wchar * 128),
                ('dwState', w.DWORD), ('dwStateMask', w.DWORD), ('szInfo', ct.c_wchar * 256),
                ('uVersion', w.UINT), ('szInfoTitle', ct.c_wchar * 64), ('dwInfoFlags', w.DWORD),
                ('guidItem', Guid), ('hBalloonIcon', w.HICON)]


class NotifyIdentifier(ct.Structure):
    _fields_ = [('cbSize', w.DWORD), ('hWnd', w.HWND), ('uID', w.UINT), ('guidItem', Guid)]


def icon_bounds(icon_guid=APP_GUID):
    identifier = NotifyIdentifier(ct.sizeof(NotifyIdentifier), None, 0, Guid.from_buffer_copy(icon_guid.bytes_le))
    rect = w.RECT()
    locate = ct.windll.shell32.Shell_NotifyIconGetRect
    locate.argtypes = [ct.POINTER(NotifyIdentifier), ct.POINTER(w.RECT)]
    locate.restype = ct.c_long
    if locate(ct.byref(identifier), ct.byref(rect)) < 0:
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def run_tray(runtime, icon_guid=APP_GUID):
    import webbrowser
    import win32api as api
    import win32con as wc
    import win32event as event
    import win32gui as gui

    suffix = icon_guid.hex
    mutex = event.CreateMutex(None, False, 'Local\\ProviderDeck.Tray.' + suffix)
    duplicate = api.GetLastError() == 183
    if duplicate or not runtime.is_file():
        mutex.Close()
        return 0

    wm_tray, wm_update = wc.WM_APP + 6, wc.WM_APP + 7
    taskbar_created = gui.RegisterWindowMessage('TaskbarCreated')
    hinst = api.GetModuleHandle(None)
    image = str(Path(__file__).with_name('tray.ico'))
    hicon = gui.LoadImage(0, image, wc.IMAGE_ICON, 0, 0, wc.LR_LOADFROMFILE | wc.LR_DEFAULTSIZE)
    notify = ct.windll.shell32.Shell_NotifyIconW
    notify.argtypes = [w.DWORD, ct.POINTER(NotifyData)]
    notify.restype = w.BOOL
    notify_data = NotifyData(cbSize=ct.sizeof(NotifyData), uID=ICON_ID,
                             uFlags=0x01 | 0x02 | 0x04 | 0x20 | 0x80,
                             uCallbackMessage=wm_tray, hIcon=hicon, szTip='ProviderDeck · son veri okunuyor',
                             guidItem=Guid.from_buffer_copy(icon_guid.bytes_le))
    stopped = threading.Event()
    state_lock = threading.Lock()
    latest = (None, None, ())
    preferences = TrayPreferences(runtime.with_name('tray-preferences.json'))
    registered = False

    def add_icon():
        nonlocal registered
        registered = bool(notify(0, ct.byref(notify_data)))
        if registered:
            notify_data.uVersion = 4
            notify(4, ct.byref(notify_data))
        return registered

    def open_panel():
        with state_lock:
            url = latest[0]
        if url:
            threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

    def update_tip():
        with state_lock:
            _, state, subscriptions = latest
        tip = tooltip(state, preferences.visible(subscriptions))
        if tip != notify_data.szTip:
            notify_data.szTip = tip
            if registered:
                notify(1, ct.byref(notify_data))

    def menu(hwnd):
        handle = gui.CreatePopupMenu()
        try:
            gui.AppendMenu(handle, wc.MF_STRING, 1, 'ProviderDeck’i aç')
            with state_lock:
                subscriptions = latest[2]
            visible = preferences.visible(subscriptions)
            if visible:
                gui.AppendMenu(handle, wc.MF_SEPARATOR, 0, '')
                for line in visible:
                    gui.AppendMenu(handle, wc.MF_STRING | wc.MF_GRAYED, 0, line.detail.replace('&', '&&'))
            gui.AppendMenu(handle, wc.MF_SEPARATOR, 0, '')
            commands = {}
            if subscriptions:
                submenu = gui.CreatePopupMenu()
                try:
                    gui.AppendMenu(handle, wc.MF_POPUP, submenu, 'Gösterilen abonelikler')
                except gui.error:
                    gui.DestroyMenu(submenu)
                    raise
                for index, line in enumerate(subscriptions, 100):
                    checked = wc.MF_UNCHECKED if line.provider_id in preferences.hidden else wc.MF_CHECKED
                    gui.AppendMenu(submenu, wc.MF_STRING | checked, index, line.name.replace('&', '&&'))
                    commands[index] = line.provider_id
            else:
                gui.AppendMenu(handle, wc.MF_STRING | wc.MF_GRAYED, 0, 'Gösterilen abonelikler (veri yok)')
            gui.AppendMenu(handle, wc.MF_STRING, 2, 'Simgeyi kapat')
            rect = icon_bounds(icon_guid)
            x, y = (rect[0], rect[1]) if rect else gui.GetCursorPos()
            try:
                gui.SetForegroundWindow(hwnd)
            except gui.error:
                pass
            command = gui.TrackPopupMenu(handle, wc.TPM_RETURNCMD | wc.TPM_RIGHTBUTTON | wc.TPM_BOTTOMALIGN,
                                         x, y, 0, hwnd, None)
            if command == 1:
                open_panel()
            elif command == 2:
                gui.DestroyWindow(hwnd)
            if gui.IsWindow(hwnd):
                gui.PostMessage(hwnd, wc.WM_NULL, 0, 0)
        finally:
            gui.DestroyMenu(handle)
        if command in commands:
            try:
                preferences.toggle(commands[command])
            except OSError:
                gui.MessageBox(hwnd, 'Görünüm ayarı kaydedilemedi.', 'ProviderDeck', wc.MB_OK | wc.MB_ICONWARNING)
            else:
                update_tip()

    def wndproc(hwnd, message, wparam, lparam):
        if message == wm_tray:
            code = lparam & 0xffff
            if code in (wc.WM_USER, wc.WM_USER + 1, wc.WM_LBUTTONUP):
                open_panel()
            elif code in (wc.WM_CONTEXTMENU, wc.WM_RBUTTONUP):
                menu(hwnd)
            return 0
        if message == taskbar_created:
            add_icon()
            return 0
        if message == wm_update:
            update_tip()
            return 0
        if message == wc.WM_DESTROY:
            stopped.set()
            if registered:
                notify(2, ct.byref(notify_data))
            gui.PostQuitMessage(0)
            return 0
        return gui.DefWindowProc(hwnd, message, wparam, lparam)

    def poll(hwnd):
        nonlocal latest
        remaining = 0
        while not stopped.is_set():
            try:
                if not runtime.is_file():
                    gui.PostMessage(hwnd, wc.WM_CLOSE, 0, 0)
                    break
                if remaining <= 0:
                    url, state = read_state(runtime)
                    lines = subscription_lines(state)
                    with state_lock:
                        latest = (url, state, lines)
                    gui.PostMessage(hwnd, wm_update, 0, 0)
                    remaining = 30
            except gui.error:
                break
            stopped.wait(1)
            remaining -= 1

    wnd_class = gui.WNDCLASS()
    wnd_class.hInstance = hinst
    wnd_class.lpszClassName = 'ProviderDeckTray_' + suffix
    wnd_class.lpfnWndProc = wndproc
    atom = gui.RegisterClass(wnd_class)
    hwnd = gui.CreateWindow(atom, 'ProviderDeck', wc.WS_POPUP, 0, 0, 0, 0, 0, 0, hinst, None)
    notify_data.hWnd = hwnd
    try:
        if not add_icon():
            return 1
        threading.Thread(target=poll, args=(hwnd,), daemon=True).start()
        gui.PumpMessages()
        return 0
    finally:
        if gui.IsWindow(hwnd):
            gui.DestroyWindow(hwnd)
        gui.DestroyIcon(hicon)
        mutex.Close()


def main():
    parser = argparse.ArgumentParser(description='ProviderDeck Windows bildirim alanı')
    parser.add_argument('--runtime', required=True, type=Path)
    args = parser.parse_args()
    return run_tray(args.runtime)


if __name__ == '__main__':
    raise SystemExit(main())
