# -*- coding: utf-8 -*-
"""
Quick Translator for LibreOffice (Writer, Calc, Impress)
=========================================================
Universal translation extension built with modern UNO architecture.
Features:
 - Single 'Translate' button in Add-ons toolbar
 - Automatic selection capture from active document (Writer, Calc, Impress)
 - Native AWT Dialog pop-up ("Quick Translator", compact ~280x220 layout)
 - Source Language selection (AUTO + European, Nordic, Asian languages)
 - Target Language selection (default: IT - Italian)
 - Smart Swap button ("⇄") with special rule: AUTO -> IT / DA
 - Online translation engine with UTF-8 & URL encoding and error handling
 - Licensed under GNU GPL v3.0

Compatible with LibreOffice 5.x, 6.x, 7.x, 24.x+ on Linux, Windows, macOS.
"""

import uno
import unohelper
import json
import os
import threading
import urllib.request
import urllib.parse
import urllib.error
import ssl
import socket
import html
import traceback

from com.sun.star.task import XJobExecutor
from com.sun.star.awt import XActionListener, XItemListener, XWindowListener, PosSize
from com.sun.star.ui import XUIElementFactory, XUIElement, XToolPanel, XSidebarPanel, UIElementType, LayoutSize
from com.sun.star.lang import XComponent, EventObject
from com.sun.star.view import XSelectionChangeListener
from com.sun.star.datatransfer import XTransferable, DataFlavor

# Supported language definitions in Alphabetical Order (A-Z)
LANGUAGES = [
    ("AUTO", "AUTO - Automatic Detection"),
    ("AR", "AR - Arabic"),
    ("ZH", "ZH - Chinese (Simplified)"),
    ("CS", "CS - Czech"),
    ("DA", "DA - Danish"),
    ("NL", "NL - Dutch"),
    ("EN", "EN - English"),
    ("FI", "FI - Finnish"),
    ("FR", "FR - French"),
    ("DE", "DE - German"),
    ("EL", "EL - Greek"),
    ("HI", "HI - Hindi"),
    ("HU", "HU - Hungarian"),
    ("IS", "IS - Icelandic"),
    ("ID", "ID - Indonesian"),
    ("IT", "IT - Italian"),
    ("JA", "JA - Japanese"),
    ("KO", "KO - Korean"),
    ("NO", "NO - Norwegian"),
    ("PL", "PL - Polish"),
    ("PT", "PT - Portuguese"),
    ("RO", "RO - Romanian"),
    ("ES", "ES - Spanish"),
    ("SV", "SV - Swedish"),
    ("TH", "TH - Thai"),
    ("TR", "TR - Turkish"),
    ("VI", "VI - Vietnamese")
]

def get_prefs_file_path():
    """Returns the config file path for saving user preferences across sessions."""
    try:
        home_dir = os.path.expanduser("~")
        return os.path.join(home_dir, ".libreoffice_quick_translator_prefs.json")
    except Exception:
        return None

def load_saved_preferences():
    """Loads previously saved language selections (Target Language & Source Language)."""
    p = get_prefs_file_path()
    if p and os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {
                    "source_lang": data.get("source_lang", "AUTO"),
                    "target_lang": data.get("target_lang", "IT")
                }
        except Exception as e:
            print("[QuickTranslator] Failed to load preferences:", e)
    return {"source_lang": "AUTO", "target_lang": "IT"}

def save_preferences(source_lang, target_lang):
    """Saves user language selection so it persists across sessions."""
    p = get_prefs_file_path()
    if p:
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"source_lang": source_lang, "target_lang": target_lang}, f, indent=2)
        except Exception as e:
            print("[QuickTranslator] Failed to save preferences:", e)

def get_active_document_selection(ctx, frame=None):
    """
    Extracts highlighted/selected text from the active document (Writer, Calc, or Impress).
    Returns an empty string if no text is currently selected.
    """
    try:
        model = None
        if frame is not None:
            controller = frame.getController()
            if controller is not None:
                model = controller.getModel()
        if model is None:
            smgr = ctx.ServiceManager
            desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
            model = desktop.getCurrentComponent()

        if not model:
            return ""

        selection = model.getCurrentSelection()
        if not selection:
            return ""

        # 1. Writer Document Selection Handling
        if hasattr(selection, "getCount") and selection.getCount() > 0:
            first_item = selection.getByIndex(0)
            if hasattr(first_item, "getString"):
                text = first_item.getString()
                if text:
                    return text.strip()

        # 2. Calc Spreadsheet Cell Selection Handling
        if hasattr(selection, "getString"):
            cell_text = selection.getString()
            if cell_text:
                return cell_text.strip()

        # 3. Impress / Draw Selection Handling
        if hasattr(selection, "getCount"):
            for i in range(selection.getCount()):
                item = selection.getByIndex(i)
                if hasattr(item, "getString") and item.getString():
                    return item.getString().strip()

    except Exception as e:
        print("[QuickTranslator] Warning during selection capture:", e)

    return ""


BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

def make_http_get_request(url, timeout=4):
    """
    Executes an HTTP/HTTPS GET request engineered for enterprise, school, and home networks:
    1. Uses standard Chrome browser headers to prevent rejection by school WAF/bot filters.
    2. Automatically uses system-configured HTTP/HTTPS proxies (KDE, GNOME, Windows).
    3. Handles school/corporate SSL inspection (MITM): if custom root certificates fail
       Python's default verification, automatically retries with unverified SSL context.
    """
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "close"
    }
    req = urllib.request.Request(url, headers=headers)
    
    # Attempt 1: Standard verified SSL request (with system proxy if configured)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                return response.read().decode("utf-8", errors="replace")
            raise urllib.error.HTTPError(url, response.status, f"HTTP Error {response.status}", response.headers, None)
    except urllib.error.URLError as url_err:
        err_msg = str(url_err).lower()
        reason_msg = str(getattr(url_err, "reason", "")).lower()
        # Detect SSL verification failure from school firewall/proxy certificate
        is_ssl = "certificate_verify_failed" in err_msg or "certificate_verify_failed" in reason_msg or "ssl" in reason_msg
        if is_ssl:
            print("[QuickTranslator] SSL inspection detected; retrying with unverified context...")
            try:
                unverified_ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(req, timeout=timeout, context=unverified_ctx) as response:
                    if response.status == 200:
                        return response.read().decode("utf-8", errors="replace")
            except Exception as ssl_e:
                raise ssl_e
        raise url_err
    except Exception as general_err:
        if "certificate_verify_failed" in str(general_err).lower():
            try:
                unverified_ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(req, timeout=timeout, context=unverified_ctx) as response:
                    if response.status == 200:
                        return response.read().decode("utf-8", errors="replace")
            except Exception as ssl_e:
                raise ssl_e
        raise general_err


def diagnose_network_failure(errors):
    """
    Produces a clear, professional English diagnostic report identifying whether
    the translation failure is caused by a school firewall, SSL interception,
    proxy authentication, content filtering, rate limiting, or captive portal.
    """
    combined = " | ".join([str(e) for e in errors])
    combined_lower = combined.lower()

    if "certificate_verify_failed" in combined_lower or "self-signed" in combined_lower or "sslcipher" in combined_lower:
        issue = "School / Corporate SSL Inspection Firewall"
        detail = "The school network firewall is intercepting secure HTTPS traffic with a custom security certificate not recognized by LibreOffice's Python runtime."
        advice = "Ask your school IT administrator if an institutional root certificate is required, or connect via an unrestricted mobile hotspot."
    elif "407" in combined_lower or "proxy authentication" in combined_lower:
        issue = "Proxy Authentication Required"
        detail = "The school or enterprise network requires authentication credentials to route web traffic through its proxy server."
        advice = "Configure your proxy credentials in LibreOffice under: Tools > Options > Internet > Proxy."
    elif "getaddrinfo failed" in combined_lower or "name or service not known" in combined_lower or "gaierror" in combined_lower or "nodename" in combined_lower:
        issue = "DNS / Content Filter Block"
        detail = "The translation domain servers (api.mymemory.translated.net / translate.googleapis.com) could not be resolved by DNS."
        advice = "The school's DNS server or firewall content filter is likely restricting translation domains."
    elif "403" in combined_lower or "forbidden" in combined_lower:
        issue = "Network Access Forbidden (HTTP 403)"
        detail = "Outbound translation requests were rejected by the network gateway or captive portal."
        advice = "Open a web browser to verify if you must log into the school Wi-Fi (Captive Portal) before accessing the internet."
    elif "timed out" in combined_lower or "timeout" in combined_lower:
        issue = "Network Timeout / Bandwidth Throttling"
        detail = "The translation service did not respond within the 12-second window."
        advice = "The school Wi-Fi may be saturated or heavily throttling outbound API traffic. Please retry in a few moments."
    elif "429" in combined_lower:
        issue = "Rate Limit Exceeded (HTTP 429)"
        detail = "Too many translation requests have been sent from this shared school IP address."
        advice = "Please wait a couple of minutes before attempting another translation."
    elif "connection refused" in combined_lower or "errno 111" in combined_lower:
        issue = "Connection Actively Refused by Firewall"
        detail = "The network router or firewall actively refused outbound connections on port 443."
        advice = "Check if external HTTPS connections to translation services are restricted by school policy."
    else:
        issue = "Internet / Network Connectivity Error"
        detail = "Could not establish a connection with any online translation service."
        advice = "Check your Wi-Fi or Ethernet connection, ensure captive portal login is complete, or test on another network."

    report = (
        "[Translation Diagnostic Report]\n"
        f"Diagnosed Cause: {issue}\n\n"
        f"Details: {detail}\n\n"
        "Suggested Solutions:\n"
        f"1. {advice}\n"
        "2. Test if your web browser can open: https://translate.google.com\n"
        "3. If on school Wi-Fi, verify that proxy/captive portal login has not expired.\n\n"
        f"Technical Log: {combined}"
    )
    return report


def perform_online_translation(text, source_lang="AUTO", target_lang="IT"):
    """
    Translates text using online translation APIs with proper UTF-8 and URL encoding.
    Prioritizes low-latency endpoints (Google GTX -> Google Client5 -> MyMemory)
    with adaptive 4-second timeouts, browser emulation headers, and school SSL resilience.
    """
    if not text or not text.strip():
        return ""

    clean_text = text.strip()
    src_param = "autodetect" if source_lang == "AUTO" else source_lang.lower()
    tgt_param = target_lang.lower()
    sl_google = "auto" if source_lang == "AUTO" else source_lang.lower()
    encoded_query = urllib.parse.quote(clean_text.encode("utf-8"))

    errors = []

    # 1. Primary Service: Google Translate public GTX endpoint (Fastest & high availability)
    try:
        gt_url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={sl_google}&tl={tgt_param}&dt=t&q={encoded_query}"
        raw_data = make_http_get_request(gt_url, timeout=4)
        gt_json = json.loads(raw_data)
        if isinstance(gt_json, list) and len(gt_json) > 0 and isinstance(gt_json[0], list):
            translated_chunks = [chunk[0] for chunk in gt_json[0] if chunk and len(chunk) > 0 and chunk[0]]
            if translated_chunks:
                return "".join(translated_chunks)
    except Exception as e1:
        print("[QuickTranslator] Google GTX attempt failed:", e1)
        errors.append(f"Google GTX: {e1}")

    # 2. Secondary Service: Google Translate Client5 endpoint (dict-chrome-ex)
    try:
        alt_url = f"https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl={sl_google}&tl={tgt_param}&q={encoded_query}"
        raw_data = make_http_get_request(alt_url, timeout=4)
        alt_json = json.loads(raw_data)
        if isinstance(alt_json, list) and len(alt_json) > 0:
            if isinstance(alt_json[0], str):
                return alt_json[0]
            elif isinstance(alt_json[0], list) and len(alt_json[0]) > 0 and isinstance(alt_json[0][0], str):
                return alt_json[0][0]
    except Exception as e2:
        print("[QuickTranslator] Google Client5 attempt failed:", e2)
        errors.append(f"Google Client5: {e2}")

    # 3. Tertiary Service: MyMemory API (Free tier fallback)
    try:
        lang_pair = f"{src_param}|{tgt_param}"
        url = f"https://api.mymemory.translated.net/get?q={encoded_query}&langpair={lang_pair}"
        raw_data = make_http_get_request(url, timeout=4)
        data = json.loads(raw_data)
        if "responseData" in data and "translatedText" in data["responseData"]:
            result = data["responseData"]["translatedText"]
            if not result.startswith("MYMEMORY WARNING:"):
                return html.unescape(result)
            else:
                errors.append(f"MyMemory quota warning: {result[:50]}")
    except Exception as e3:
        print("[QuickTranslator] MyMemory API attempt failed:", e3)
        errors.append(f"MyMemory: {e3}")

    # If all endpoints failed, build diagnostic report
    report = diagnose_network_failure(errors)
    raise RuntimeError(report)


_ACTIVE_PANELS = []


class TextTransferable(unohelper.Base, XTransferable):
    """Clipboard transferable helper for plain UTF-16 text."""
    def __init__(self, text):
        self.text = text

    def getTransferData(self, flavor):
        if flavor.MimeType.startswith("text/plain"):
            return self.text
        return None

    def getTransferDataFlavors(self):
        flavor = DataFlavor()
        flavor.MimeType = "text/plain;charset=utf-16"
        flavor.HumanPresentableName = "Unicode Text"
        flavor.DataType = uno.getTypeByName("string")
        return (flavor,)

    def isDataFlavorSupported(self, flavor):
        return flavor.MimeType.startswith("text/plain")


class QuickTranslatorPanel(unohelper.Base, XUIElement, XToolPanel, XSidebarPanel, XComponent):
    """
    Native UNO Sidebar Panel implementation for Quick Translator.
    Embeds directly into LibreOffice's right-hand Sidebar (no popup dialog),
    resizes responsively, and automatically captures selected text from the document.
    """
    def __init__(self, ctx, frame, parent_window, resource_url):
        self.ctx = ctx
        self.Frame = frame
        self.ResourceURL = resource_url
        self.Type = UIElementType.TOOLPANEL
        self._parent_window = parent_window
        self._disposed = False
        self._event_listeners = []
        self._last_captured_text = ""
        self._selection_timer = None
        self._selection_listener = None
        self._window_listener = None

        smgr = self.ctx.ServiceManager
        toolkit = parent_window.getToolkit()
        if toolkit is None:
            toolkit = smgr.createInstanceWithContext("com.sun.star.awt.Toolkit", self.ctx)

        # Create embedded AWT container inside the Sidebar ParentWindow
        self.container = smgr.createInstanceWithContext("com.sun.star.awt.UnoControlContainer", self.ctx)
        container_model = smgr.createInstanceWithContext("com.sun.star.awt.UnoControlContainerModel", self.ctx)
        self.container.setModel(container_model)
        self.container.createPeer(toolkit, parent_window)
        self.Window = self.container

        prefs = load_saved_preferences()
        saved_src = prefs.get("source_lang", "AUTO")
        saved_tgt = prefs.get("target_lang", "IT")

        def _make_control(ctrl_service, model_service, props):
            ctrl = smgr.createInstanceWithContext(ctrl_service, self.ctx)
            model = smgr.createInstanceWithContext(model_service, self.ctx)
            for k, v in props.items():
                if k == "Name":
                    continue
                setattr(model, k, v)
            ctrl.setModel(model)
            return ctrl

        # Row 1: Language selectors & Swap
        src_items = [label for code, label in LANGUAGES]
        src_idx = 0
        for i, item in enumerate(src_items):
            if item.startswith(saved_src + " -") or item == saved_src:
                src_idx = i
                break

        self.source_list = _make_control(
            "com.sun.star.awt.UnoControlListBox",
            "com.sun.star.awt.UnoControlListBoxModel",
            {
                "Name": "SourceLangList",
                "Dropdown": True,
                "LineCount": 14,
                "StringItemList": tuple(src_items),
                "SelectedItems": (src_idx,),
            },
        )
        self.container.addControl("SourceLangList", self.source_list)

        self.swap_btn = _make_control(
            "com.sun.star.awt.UnoControlButton",
            "com.sun.star.awt.UnoControlButtonModel",
            {
                "Name": "SwapBtn",
                "Label": "⇄",
                "HelpText": "Swap Languages (AUTO -> IT / DA)",
            },
        )
        self.container.addControl("SwapBtn", self.swap_btn)

        tgt_items = [label for code, label in LANGUAGES if code != "AUTO"]
        tgt_idx = 0
        for i, item in enumerate(tgt_items):
            if item.startswith(saved_tgt + " -") or item == saved_tgt:
                tgt_idx = i
                break

        self.target_list = _make_control(
            "com.sun.star.awt.UnoControlListBox",
            "com.sun.star.awt.UnoControlListBoxModel",
            {
                "Name": "TargetLangList",
                "Dropdown": True,
                "LineCount": 14,
                "StringItemList": tuple(tgt_items),
                "SelectedItems": (tgt_idx,),
            },
        )
        self.container.addControl("TargetLangList", self.target_list)

        # Row 2: Action buttons
        self.translate_btn = _make_control(
            "com.sun.star.awt.UnoControlButton",
            "com.sun.star.awt.UnoControlButtonModel",
            {
                "Name": "TranslateBtn",
                "Label": "Translate ALT+Q",
                "HelpText": "Translate text (Shortcut: ALT+Q)",
            },
        )
        self.container.addControl("TranslateBtn", self.translate_btn)

        self.copy_btn = _make_control(
            "com.sun.star.awt.UnoControlButton",
            "com.sun.star.awt.UnoControlButtonModel",
            {
                "Name": "CopyBtn",
                "Label": "Copy",
                "HelpText": "Copy translated text to clipboard",
            },
        )
        self.container.addControl("CopyBtn", self.copy_btn)

        # Source & Target text areas
        self.lbl_source = _make_control(
            "com.sun.star.awt.UnoControlFixedText",
            "com.sun.star.awt.UnoControlFixedTextModel",
            {
                "Name": "LblSource",
                "Label": "Source Text:",
                "FontWeight": 150.0,
            },
        )
        self.container.addControl("LblSource", self.lbl_source)

        self.source_box = _make_control(
            "com.sun.star.awt.UnoControlEdit",
            "com.sun.star.awt.UnoControlEditModel",
            {
                "Name": "SourceTextBox",
                "MultiLine": True,
                "VScroll": True,
                "Text": "",
            },
        )
        self.container.addControl("SourceTextBox", self.source_box)

        self.lbl_target = _make_control(
            "com.sun.star.awt.UnoControlFixedText",
            "com.sun.star.awt.UnoControlFixedTextModel",
            {
                "Name": "LblTarget",
                "Label": "Translated Text:",
                "FontWeight": 150.0,
            },
        )
        self.container.addControl("LblTarget", self.lbl_target)

        self.target_box = _make_control(
            "com.sun.star.awt.UnoControlEdit",
            "com.sun.star.awt.UnoControlEditModel",
            {
                "Name": "TranslatedTextBox",
                "MultiLine": True,
                "VScroll": True,
                "Text": "",
            },
        )
        self.container.addControl("TranslatedTextBox", self.target_box)

        self.status_label = _make_control(
            "com.sun.star.awt.UnoControlFixedText",
            "com.sun.star.awt.UnoControlFixedTextModel",
            {
                "Name": "StatusLabel",
                "Label": "Select text in the document to translate.",
                "MultiLine": True,
            },
        )
        self.container.addControl("StatusLabel", self.status_label)

        # Bind UI listeners
        panel_ref = self

        class _ActionListener(unohelper.Base, XActionListener):
            def __init__(self, callback):
                self.callback = callback
            def actionPerformed(self, event):
                self.callback()
            def disposing(self, source):
                pass

        class _ItemListener(unohelper.Base, XItemListener):
            def itemStateChanged(self, event):
                panel_ref.on_lang_changed()
            def disposing(self, source):
                pass

        class _ResizeListener(unohelper.Base, XWindowListener):
            def windowResized(self, event):
                panel_ref._layout_controls(event.Width, event.Height)
            def windowMoved(self, event):
                pass
            def windowShown(self, event):
                ps = panel_ref._parent_window.getPosSize()
                panel_ref._layout_controls(ps.Width, ps.Height)
            def windowHidden(self, event):
                pass
            def disposing(self, source):
                pass

        class _DocSelectionListener(unohelper.Base, XSelectionChangeListener):
            def selectionChanged(self, event):
                panel_ref.on_document_selection_changed()
            def disposing(self, source):
                pass

        self.swap_btn.addActionListener(_ActionListener(self.on_swap_clicked))
        self.translate_btn.addActionListener(_ActionListener(self.on_translate_clicked))
        self.copy_btn.addActionListener(_ActionListener(self.on_copy_clicked))
        self.source_list.addItemListener(_ItemListener())
        self.target_list.addItemListener(_ItemListener())

        self._window_listener = _ResizeListener()
        parent_window.addWindowListener(self._window_listener)

        # Attach live selection listener to the document controller
        try:
            controller = frame.getController() if frame is not None else None
            if controller is not None and hasattr(controller, "addSelectionChangeListener"):
                self._selection_listener = _DocSelectionListener()
                controller.addSelectionChangeListener(self._selection_listener)
        except Exception as e:
            print("[QuickTranslator] Could not attach selection listener:", e)

        # Initial layout
        ps = parent_window.getPosSize()
        self._layout_controls(ps.Width, ps.Height)
        self.container.setVisible(True)

        _ACTIVE_PANELS.append(self)

        # Capture any text already selected when the panel opens
        self.capture_and_translate(force=True)

    # --- XUIElement / XToolPanel / XSidebarPanel / XComponent ---
    def getRealInterface(self):
        return self

    def getWindow(self):
        return self.container

    def createAccessible(self, parent_accessible):
        try:
            return self.container.getAccessibleContext()
        except Exception:
            return None

    def getHeightForWidth(self, width):
        return LayoutSize(360, -1, 460)

    def getMinimalWidth(self):
        return 370

    def addEventListener(self, listener):
        if listener not in self._event_listeners:
            self._event_listeners.append(listener)

    def removeEventListener(self, listener):
        if listener in self._event_listeners:
            self._event_listeners.remove(listener)

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        if self._selection_timer is not None:
            try:
                self._selection_timer.cancel()
            except Exception:
                pass
            self._selection_timer = None
        if self._selection_listener is not None and self.Frame is not None:
            try:
                controller = self.Frame.getController()
                if controller is not None and hasattr(controller, "removeSelectionChangeListener"):
                    controller.removeSelectionChangeListener(self._selection_listener)
            except Exception:
                pass
            self._selection_listener = None
        if self._window_listener is not None and self._parent_window is not None:
            try:
                self._parent_window.removeWindowListener(self._window_listener)
            except Exception:
                pass
            self._window_listener = None
        if self in _ACTIVE_PANELS:
            _ACTIVE_PANELS.remove(self)
        evt = EventObject(self)
        for listener in list(self._event_listeners):
            try:
                listener.disposing(evt)
            except Exception:
                pass
        self._event_listeners.clear()
        try:
            self.container.dispose()
        except Exception:
            pass

    # --- Responsive Pixel Layout ---
    def _layout_controls(self, width, height):
        if self._disposed:
            return
        w = max(int(width), 220)
        h = max(int(height), 340)
        self.container.setPosSize(0, 0, w, h, PosSize.POSSIZE)

        m = 8
        g = 6
        usable_w = w - 2 * m

        # Row 1: Source dropdown | Swap | Target dropdown
        row_h = 26
        y = m
        swap_w = 30
        lang_w = max(60, (usable_w - swap_w - 2 * g) // 2)
        self.source_list.setPosSize(m, y, lang_w, row_h, PosSize.POSSIZE)
        self.swap_btn.setPosSize(m + lang_w + g, y, swap_w, row_h, PosSize.POSSIZE)
        self.target_list.setPosSize(m + lang_w + g + swap_w + g, y, usable_w - lang_w - swap_w - 2 * g, row_h, PosSize.POSSIZE)

        # Row 2: Translate button | Copy button
        y += row_h + g
        copy_w = 72
        trans_w = max(80, usable_w - copy_w - g)
        self.translate_btn.setPosSize(m, y, trans_w, row_h, PosSize.POSSIZE)
        self.copy_btn.setPosSize(m + trans_w + g, y, copy_w, row_h, PosSize.POSSIZE)

        # Footer StatusLabel
        status_h = 30
        status_y = h - m - status_h

        # Remaining space for Source & Target sections
        y += row_h + 10
        lbl_h = 18
        avail_for_boxes = status_y - y - (2 * lbl_h) - 14
        box_h = max(75, avail_for_boxes // 2)

        self.lbl_source.setPosSize(m, y, usable_w, lbl_h, PosSize.POSSIZE)
        y += lbl_h + 2
        self.source_box.setPosSize(m, y, usable_w, box_h, PosSize.POSSIZE)
        y += box_h + 8

        self.lbl_target.setPosSize(m, y, usable_w, lbl_h, PosSize.POSSIZE)
        y += lbl_h + 2
        tgt_box_h = max(75, status_y - y - 6)
        self.target_box.setPosSize(m, y, usable_w, tgt_box_h, PosSize.POSSIZE)

        self.status_label.setPosSize(m, status_y, usable_w, status_h, PosSize.POSSIZE)

    # --- Selection & Actions ---
    def capture_and_translate(self, force=False):
        if self._disposed:
            return
        text = get_active_document_selection(self.ctx, frame=self.Frame)
        if text and (force or text != self._last_captured_text):
            self._last_captured_text = text
            self.source_box.setText(text)
            self.on_translate_clicked()

    def on_document_selection_changed(self):
        if self._disposed:
            return
        text = get_active_document_selection(self.ctx, frame=self.Frame)
        if not text or text == self._last_captured_text:
            return
        self._last_captured_text = text
        self.source_box.setText(text)

        if self._selection_timer is not None:
            try:
                self._selection_timer.cancel()
            except Exception:
                pass

        def _debounced_translate():
            if not self._disposed:
                self.on_translate_clicked()

        self._selection_timer = threading.Timer(0.45, _debounced_translate)
        self._selection_timer.daemon = True
        self._selection_timer.start()

    def get_selected_code(self, list_control, is_source=True):
        """Helper to extract language code from list selection."""
        item = list_control.getSelectedItem()
        if not item:
            return "AUTO" if is_source else "IT"
        return item.split(" - ")[0].strip()

    def set_selected_by_code(self, list_control, target_code):
        """Helper to set selected item in list control by language code."""
        items = list_control.getItems()
        for idx, item in enumerate(items):
            if item.startswith(target_code + " -") or item == target_code:
                list_control.selectItemPos(idx, True)
                return

    def on_lang_changed(self):
        """Persists chosen language immediately when user changes dropdown selection."""
        try:
            src_code = self.get_selected_code(self.source_list, is_source=True)
            tgt_code = self.get_selected_code(self.target_list, is_source=False)
            save_preferences(src_code, tgt_code)
        except Exception as e:
            print("[QuickTranslator] on_lang_changed save error:", e)

    def on_swap_clicked(self):
        """
        Swap logic implementation:
        - If Source Language is 'AUTO', sets Source to 'IT' and Target to 'DA'.
        - In other cases, swaps Source and Target selections.
        """
        src_code = self.get_selected_code(self.source_list, is_source=True)
        tgt_code = self.get_selected_code(self.target_list, is_source=False)

        if src_code == "AUTO":
            self.set_selected_by_code(self.source_list, "IT")
            self.set_selected_by_code(self.target_list, "DA")
            self.status_label.setText("Languages swapped: IT (Source) ⇄ DA (Target)")
            save_preferences("IT", "DA")
        else:
            self.set_selected_by_code(self.source_list, tgt_code)
            self.set_selected_by_code(self.target_list, src_code)
            self.status_label.setText(f"Languages swapped: {tgt_code} ⇄ {src_code}")
            save_preferences(tgt_code, src_code)

        if self.source_box and self.source_box.getText().strip():
            self.on_translate_clicked()

    def on_copy_clicked(self):
        """Copies translated text to the system clipboard."""
        translated = self.target_box.getText()
        if not translated or not translated.strip():
            self.status_label.setText("Nothing to copy.")
            return
        try:
            smgr = self.ctx.ServiceManager
            clipboard = smgr.createInstanceWithContext("com.sun.star.datatransfer.clipboard.SystemClipboard", self.ctx)
            clipboard.setContents(TextTransferable(translated), None)
            self.status_label.setText("Translated text copied to clipboard!")
        except Exception as e:
            self.status_label.setText(f"Copy failed: {e}")

    def on_translate_clicked(self):
        """
        Translates text in SourceTextBox asynchronously in a background thread.
        """
        if self._disposed:
            return
        source_text = self.source_box.getText()
        if not source_text or not source_text.strip():
            self.status_label.setText("Please enter or select some text to translate.")
            return

        src_code = self.get_selected_code(self.source_list, is_source=True)
        tgt_code = self.get_selected_code(self.target_list, is_source=False)
        save_preferences(src_code, tgt_code)

        self.status_label.setText(f"Translating {src_code} -> {tgt_code}...")
        self.target_box.setText("Translating in progress, please wait...")

        def _worker_thread():
            try:
                translated = perform_online_translation(source_text, src_code, tgt_code)
                if not self._disposed:
                    self.target_box.setText(translated)
                    self.status_label.setText("Translation completed successfully!")
            except Exception as e:
                if not self._disposed:
                    raw_err = str(e)
                    if raw_err.startswith("RuntimeError: "):
                        raw_err = raw_err[len("RuntimeError: "):]
                    self.target_box.setText(raw_err)
                    self.status_label.setText("Network error: see diagnostic report.")

        t = threading.Thread(target=_worker_thread, daemon=True)
        t.start()


class QuickTranslatorSidebarFactory(unohelper.Base, XUIElementFactory):
    """UNO UIElementFactory that creates QuickTranslatorPanel inside the Sidebar."""
    def __init__(self, ctx):
        self.ctx = ctx

    def createUIElement(self, resource_url, args):
        frame = None
        parent_window = None
        for prop in args:
            if prop.Name == "Frame":
                frame = prop.Value
            elif prop.Name == "ParentWindow":
                parent_window = prop.Value

        if parent_window is None:
            raise uno.RuntimeException("ParentWindow is required for QuickTranslatorPanel", self)

        return QuickTranslatorPanel(self.ctx, frame, parent_window, resource_url)


# --- Modern LibreOffice UNO Component Implementation ---
class TranslatorJobService(unohelper.Base, XJobExecutor):
    """
    Modern UNO Component implementing XJobExecutor.
    Triggered when the user clicks 'Translate' on the Addons toolbar or menu.
    Opens and focuses the QuickTranslatorDeck in LibreOffice's right-hand Sidebar.
    """
    def __init__(self, ctx):
        self.ctx = ctx

    def trigger(self, args):
        try:
            smgr = self.ctx.ServiceManager
            desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", self.ctx)
            frame = desktop.getCurrentFrame()
            if frame is None:
                return

            controller = frame.getController()
            if controller is not None and hasattr(controller, "getSidebar"):
                sidebar = controller.getSidebar()
                if sidebar is not None:
                    was_visible = sidebar.isVisible()
                    if not was_visible:
                        sidebar.setVisible(True)

                    def _open_deck():
                        try:
                            decks = sidebar.getDecks()
                            if decks is not None and decks.hasByName("QuickTranslatorDeck"):
                                deck = decks.getByName("QuickTranslatorDeck")
                                deck.activate(True)
                            for panel in list(_ACTIVE_PANELS):
                                if panel.Frame == frame:
                                    panel.capture_and_translate(force=True)
                                    break
                        except Exception:
                            traceback.print_exc()

                    if not was_visible:
                        t = threading.Timer(0.1, _open_deck)
                        t.daemon = True
                        t.start()
                    else:
                        _open_deck()
                    return

            # Fallback if panel is already open
            for panel in list(_ACTIVE_PANELS):
                if panel.Frame == frame:
                    panel.capture_and_translate(force=True)
                    break
        except Exception:
            traceback.print_exc()


# UNO Registration Helper
g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    TranslatorJobService,
    "org.libreoffice.quicktranslator.TranslatorService",
    ("com.sun.star.task.JobExecutor", "com.sun.star.task.XJobExecutor"),
)
g_ImplementationHelper.addImplementation(
    QuickTranslatorSidebarFactory,
    "org.libreoffice.quicktranslator.SidebarFactory",
    ("com.sun.star.ui.UIElementFactory",),
)


# Standalone script entry point for direct script execution
def open_dialog(*args):
    ctx = uno.getComponentContext()
    service = TranslatorJobService(ctx)
    service.trigger("open")
