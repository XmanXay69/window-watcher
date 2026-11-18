"""
hotkeys.py: a keyboard shortcut that works even while another app is in front.

Normal shortcuts (like the ones in the menu bar) only work while Window
Watcher is the active app. This file uses Apple's "hot key" API
(RegisterEventHotKey, part of the Carbon framework), which is what many Mac
apps use for system-wide shortcuts. It needs no special permission.

Carbon is a C library, so we call it through Python's built-in `ctypes`
module, which can call C functions directly.

An app that reserves a key combination for itself, or software that blocks
outside shortcuts on purpose, still gets its way: we don't try to get around
that.
"""

import ctypes

carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")

# Key code for the C key and the modifier flags Carbon expects.
KEY_C = 8
COMMAND = 1 << 8
SHIFT = 1 << 9
OPTION = 1 << 11
CONTROL = 1 << 12

# Mac key codes for the keys you can use in a shortcut (US keyboard layout).
# Qt names keys by character; Carbon wants these numbers.
MAC_KEY_CODES = {
    "A": 0, "S": 1, "D": 2, "F": 3, "H": 4, "G": 5, "Z": 6, "X": 7, "C": 8, "V": 9,
    "B": 11, "Q": 12, "W": 13, "E": 14, "R": 15, "Y": 16, "T": 17, "1": 18, "2": 19,
    "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28,
    "0": 29, "]": 30, "O": 31, "U": 32, "[": 33, "I": 34, "P": 35, "L": 37, "J": 38,
    "'": 39, "K": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "N": 45, "M": 46, ".": 47,
    "`": 50, "Space": 49, "Return": 36, "Tab": 48,
    "F1": 122, "F2": 120, "F3": 99, "F4": 118, "F5": 96, "F6": 97, "F7": 98,
    "F8": 100, "F9": 101, "F10": 109, "F11": 103, "F12": 111,
}


def carbon_combo(portable_text):
    """Turn a Qt shortcut like "Ctrl+Shift+C" (Qt's "Ctrl" is the Command key
    on a Mac, and "Meta" is the Control key) into Carbon's (key code, modifiers).
    Returns None for keys we don't know."""
    *modifier_names, key = portable_text.split("+") if portable_text != "+" else ["+"]
    if key not in MAC_KEY_CODES:
        return None
    flags = {"Ctrl": COMMAND, "Shift": SHIFT, "Alt": OPTION, "Meta": CONTROL}
    modifiers = 0
    for name in modifier_names:
        modifiers |= flags.get(name, 0)
    return MAC_KEY_CODES[key], modifiers


def _four_letters(text):
    """Carbon names many things with 4-letter codes like 'keyb'."""
    return int.from_bytes(text.encode("ascii"), "big")


class _HotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class _EventType(ctypes.Structure):
    _fields_ = [("event_class", ctypes.c_uint32), ("event_kind", ctypes.c_uint32)]


# The shape of the function Carbon calls when a hot key is pressed.
_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

# Tell ctypes the argument types of the Carbon functions we use.
carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
carbon.InstallEventHandler.argtypes = [ctypes.c_void_p, _HANDLER, ctypes.c_ulong,
                                       ctypes.POINTER(_EventType), ctypes.c_void_p,
                                       ctypes.POINTER(ctypes.c_void_p)]
carbon.InstallEventHandler.restype = ctypes.c_int32
carbon.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, _HotKeyID, ctypes.c_void_p,
                                       ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
carbon.RegisterEventHotKey.restype = ctypes.c_int32
carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
carbon.UnregisterEventHotKey.restype = ctypes.c_int32


class GlobalHotKey:
    """
    One system-wide shortcut. Example:
        hotkey = GlobalHotKey(KEY_C, COMMAND | SHIFT, my_function)
        hotkey.turn_on()    # my_function() now runs whenever ⌘⇧C is pressed
        hotkey.turn_off()
    The function runs on the main thread, so it may update the window.
    """

    def __init__(self, key_code, modifiers, callback):
        self.key_code = key_code
        self.modifiers = modifiers
        self.callback = callback
        self.hotkey_ref = None
        # Keep a reference to the C callback, or Python would garbage-collect it.
        self._handler = _HANDLER(self._on_event)
        pressed = _EventType(_four_letters("keyb"), 5)  # 5 = "hot key pressed"
        status = carbon.InstallEventHandler(carbon.GetApplicationEventTarget(), self._handler,
                                            1, ctypes.byref(pressed), None, None)
        if status != 0:
            raise OSError(f"Couldn't listen for shortcuts (error {status}).")

    def _on_event(self, call_ref, event_ref, user_data):
        try:
            self.callback()
        except Exception as error:  # never let an error escape into Carbon
            print("Shortcut error:", error)
        return 0

    def turn_on(self):
        """Start listening. Returns False if another app already uses this shortcut."""
        if self.hotkey_ref:
            return True
        ref = ctypes.c_void_p()
        status = carbon.RegisterEventHotKey(self.key_code, self.modifiers,
                                            _HotKeyID(_four_letters("WWtc"), 1),
                                            carbon.GetApplicationEventTarget(), 0, ctypes.byref(ref))
        if status != 0:
            return False
        self.hotkey_ref = ref
        return True

    def turn_off(self):
        if self.hotkey_ref:
            carbon.UnregisterEventHotKey(self.hotkey_ref)
            self.hotkey_ref = None

    def change(self, key_code, modifiers):
        """Switch to a different key combination. Returns False if another
        app already uses the new one everywhere."""
        was_on = self.hotkey_ref is not None
        self.turn_off()
        self.key_code, self.modifiers = key_code, modifiers
        return self.turn_on() if was_on else True
