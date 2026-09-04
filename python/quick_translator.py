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
import urllib.request
import urllib.parse
import urllib.error
import ssl
import socket
import html
import traceback

from com.sun.star.task import XJobExecutor
from com.sun.star.awt import XActionListener, XItemListener

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

def get_active_document_selection(ctx):
    """
    Extracts highlighted/selected text from the active document (Writer, Calc, or Impress).
    Returns an empty string if no text is currently selected.
    """
    try:
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

def make_http_get_request(url, timeout=12):
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
    Uses a multi-tier fallback chain (MyMemory -> Google GTX -> Google Client5),
    with browser headers and school network SSL resilience.
    """
    if not text or not text.strip():
        return ""

    clean_text = text.strip()
    src_param = "autodetect" if source_lang == "AUTO" else source_lang.lower()
    tgt_param = target_lang.lower()
    sl_google = "auto" if source_lang == "AUTO" else source_lang.lower()
    encoded_query = urllib.parse.quote(clean_text.encode("utf-8"))

    errors = []

    # 1. Primary Service: MyMemory API (Free tier, no API key required)
    try:
        lang_pair = f"{src_param}|{tgt_param}"
        url = f"https://api.mymemory.translated.net/get?q={encoded_query}&langpair={lang_pair}"
        raw_data = make_http_get_request(url, timeout=12)
        data = json.loads(raw_data)
        if "responseData" in data and "translatedText" in data["responseData"]:
            result = data["responseData"]["translatedText"]
            if not result.startswith("MYMEMORY WARNING:"):
                return html.unescape(result)
            else:
                errors.append(f"MyMemory quota warning: {result[:50]}")
    except Exception as e1:
        print("[QuickTranslator] MyMemory API attempt failed:", e1)
        errors.append(f"MyMemory: {e1}")

    # 2. Secondary Service: Google Translate public GTX endpoint
    try:
        gt_url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={sl_google}&tl={tgt_param}&dt=t&q={encoded_query}"
        raw_data = make_http_get_request(gt_url, timeout=12)
        gt_json = json.loads(raw_data)
        if isinstance(gt_json, list) and len(gt_json) > 0 and isinstance(gt_json[0], list):
            translated_chunks = [chunk[0] for chunk in gt_json[0] if chunk and len(chunk) > 0 and chunk[0]]
            if translated_chunks:
                return "".join(translated_chunks)
    except Exception as e2:
        print("[QuickTranslator] Google GTX attempt failed:", e2)
        errors.append(f"Google GTX: {e2}")

    # 3. Tertiary Service: Google Translate Client5 endpoint (dict-chrome-ex)
    try:
        alt_url = f"https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl={sl_google}&tl={tgt_param}&q={encoded_query}"
        raw_data = make_http_get_request(alt_url, timeout=12)
        alt_json = json.loads(raw_data)
        if isinstance(alt_json, list) and len(alt_json) > 0:
            if isinstance(alt_json[0], str):
                return alt_json[0]
            elif isinstance(alt_json[0], list) and len(alt_json[0]) > 0 and isinstance(alt_json[0][0], str):
                return alt_json[0][0]
    except Exception as e3:
        print("[QuickTranslator] Google Client5 attempt failed:", e3)
        errors.append(f"Google Client5: {e3}")

    # If all endpoints failed, build diagnostic report
    report = diagnose_network_failure(errors)
    raise RuntimeError(report)


class QuickTranslatorDialog(unohelper.Base):
    """
    Native UNO AWT Dialog implementation for Quick Translator.
    Creates a compact ~280x220 dialog dynamically with top row controls,
    center source text box, and bottom translated text box.
    """
    def __init__(self, ctx):
        self.ctx = ctx
        self.dialog = None
        self.source_list = None
        self.target_list = None
        self.source_box = None
        self.target_box = None
        self.status_label = None

    def show(self, initial_text=""):
        smgr = self.ctx.ServiceManager
        
        # 1. Create Dialog Model (Movable, Closeable, Desktop-independent Floating Window)
        dialog_model = smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialogModel", self.ctx)
        dialog_model.PositionX = 150
        dialog_model.PositionY = 100
        dialog_model.Width = 264
        dialog_model.Height = 275
        dialog_model.Title = "Quick Translator"
        dialog_model.DesktopAsParent = True
        dialog_model.Moveable = True
        dialog_model.Closeable = True
        dialog_model.Decoration = True

        # Load saved user language preferences (Default: AUTO -> IT)
        prefs = load_saved_preferences()
        saved_src = prefs.get("source_lang", "AUTO")
        saved_tgt = prefs.get("target_lang", "IT")

        # 2. Controls - Row 1: Source Language Dropdown (Lengthened by 20%: Width 88, LineCount=14)
        src_model = dialog_model.createInstance("com.sun.star.awt.UnoControlListBoxModel")
        src_model.PositionX = 6
        src_model.PositionY = 8
        src_model.Width = 88
        src_model.Height = 14
        src_model.Dropdown = True
        src_model.LineCount = 14
        src_model.Name = "SourceLangList"
        src_items = [label for code, label in LANGUAGES]
        src_model.StringItemList = tuple(src_items)
        src_idx = 0
        for i, item in enumerate(src_items):
            if item.startswith(saved_src + " -") or item == saved_src:
                src_idx = i
                break
        src_model.SelectedItems = (src_idx,)
        dialog_model.insertByName("SourceLangList", src_model)

        # 3. Controls - Row 1: Swap Button ("⇄")
        swap_model = dialog_model.createInstance("com.sun.star.awt.UnoControlButtonModel")
        swap_model.PositionX = 98
        swap_model.PositionY = 8
        swap_model.Width = 16
        swap_model.Height = 14
        swap_model.Label = "⇄"
        swap_model.Name = "SwapBtn"
        swap_model.HelpText = "Swap Languages (Special: AUTO -> IT / DA)"
        dialog_model.insertByName("SwapBtn", swap_model)

        # 4. Controls - Row 1: Target Language Dropdown (Lengthened by 20%: Width 86, LineCount=14)
        tgt_model = dialog_model.createInstance("com.sun.star.awt.UnoControlListBoxModel")
        tgt_model.PositionX = 118
        tgt_model.PositionY = 8
        tgt_model.Width = 86
        tgt_model.Height = 14
        tgt_model.Dropdown = True
        tgt_model.LineCount = 14
        tgt_model.Name = "TargetLangList"
        tgt_items = [label for code, label in LANGUAGES if code != "AUTO"]
        tgt_model.StringItemList = tuple(tgt_items)
        tgt_idx = 0
        for i, item in enumerate(tgt_items):
            if item.startswith(saved_tgt + " -") or item == saved_tgt:
                tgt_idx = i
                break
        tgt_model.SelectedItems = (tgt_idx,)
        dialog_model.insertByName("TargetLangList", tgt_model)

        # 5. Controls - Row 1: Translate Button ("Translate")
        trans_model = dialog_model.createInstance("com.sun.star.awt.UnoControlButtonModel")
        trans_model.PositionX = 208
        trans_model.PositionY = 8
        trans_model.Width = 50
        trans_model.Height = 14
        trans_model.Label = "Translate"
        trans_model.Name = "TranslateBtn"
        dialog_model.insertByName("TranslateBtn", trans_model)

        # 6. Controls - Center: Source Text Section (Bold Title + Multiline VScroll)
        lbl_src = dialog_model.createInstance("com.sun.star.awt.UnoControlFixedTextModel")
        lbl_src.PositionX = 6
        lbl_src.PositionY = 26
        lbl_src.Width = 252
        lbl_src.Height = 8
        lbl_src.Label = "Source Text:"
        lbl_src.FontWeight = 150.0  # Bold font weight
        dialog_model.insertByName("LblSource", lbl_src)

        src_text_model = dialog_model.createInstance("com.sun.star.awt.UnoControlEditModel")
        src_text_model.PositionX = 6
        src_text_model.PositionY = 36
        src_text_model.Width = 252
        src_text_model.Height = 98
        src_text_model.MultiLine = True
        src_text_model.VScroll = True
        src_text_model.Text = initial_text
        src_text_model.Name = "SourceTextBox"
        dialog_model.insertByName("SourceTextBox", src_text_model)

        # 7. Controls - Lower: Translated Text Section (Bold Title + Multiline VScroll)
        lbl_tgt = dialog_model.createInstance("com.sun.star.awt.UnoControlFixedTextModel")
        lbl_tgt.PositionX = 6
        lbl_tgt.PositionY = 138
        lbl_tgt.Width = 252
        lbl_tgt.Height = 8
        lbl_tgt.Label = "Translated Text:"
        lbl_tgt.FontWeight = 150.0  # Bold font weight
        dialog_model.insertByName("LblTarget", lbl_tgt)

        tgt_text_model = dialog_model.createInstance("com.sun.star.awt.UnoControlEditModel")
        tgt_text_model.PositionX = 6
        tgt_text_model.PositionY = 148
        tgt_text_model.Width = 252
        tgt_text_model.Height = 98
        tgt_text_model.MultiLine = True
        tgt_text_model.VScroll = True
        tgt_text_model.Text = ""
        tgt_text_model.Name = "TranslatedTextBox"
        dialog_model.insertByName("TranslatedTextBox", tgt_text_model)

        # 8. Controls - Bottom Status / Actions
        status_model = dialog_model.createInstance("com.sun.star.awt.UnoControlFixedTextModel")
        status_model.PositionX = 6
        status_model.PositionY = 252
        status_model.Width = 252
        status_model.Height = 16
        status_model.Label = "Ready to translate."
        status_model.Name = "StatusLabel"
        dialog_model.insertByName("StatusLabel", status_model)

        # 9. Instantiate Dialog
        self.dialog = smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialog", self.ctx)
        self.dialog.setModel(dialog_model)

        # Get control references
        self.source_list = self.dialog.getControl("SourceLangList")
        self.target_list = self.dialog.getControl("TargetLangList")
        self.source_box = self.dialog.getControl("SourceTextBox")
        self.target_box = self.dialog.getControl("TranslatedTextBox")
        self.status_label = self.dialog.getControl("StatusLabel")

        # 10. Bind Action Listeners & Item Listeners
        class SwapListener(unohelper.Base, XActionListener):
            def __init__(self, parent):
                self.parent = parent

            def actionPerformed(self, actionEvent):
                self.parent.on_swap_clicked()

            def disposing(self, source):
                pass

        class TranslateListener(unohelper.Base, XActionListener):
            def __init__(self, parent):
                self.parent = parent

            def actionPerformed(self, actionEvent):
                self.parent.on_translate_clicked()

            def disposing(self, source):
                pass

        class ItemChangeListener(unohelper.Base, XItemListener):
            def __init__(self, parent):
                self.parent = parent

            def itemStateChanged(self, itemEvent):
                self.parent.on_lang_changed()

            def disposing(self, source):
                pass

        self.dialog.getControl("SwapBtn").addActionListener(SwapListener(self))
        self.dialog.getControl("TranslateBtn").addActionListener(TranslateListener(self))
        self.target_list.addItemListener(ItemChangeListener(self))
        self.source_list.addItemListener(ItemChangeListener(self))

        # Create window peer and execute dialog
        toolkit = smgr.createInstanceWithContext("com.sun.star.awt.Toolkit", self.ctx)
        self.dialog.createPeer(toolkit, None)

        # Automatic Translation on Popup Open:
        # If source text is already captured from the document or provided, translate immediately
        if initial_text and initial_text.strip():
            try:
                self.on_translate_clicked()
            except Exception as e:
                print("[QuickTranslator] Auto-translate on open error:", e)

        self.dialog.execute()
        self.dialog.dispose()

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
            # Special requirement: AUTO -> Source = IT, Target = DA
            self.set_selected_by_code(self.source_list, "IT")
            self.set_selected_by_code(self.target_list, "DA")
            self.status_label.setText("Languages swapped: IT (Source) ⇄ DA (Target)")
            save_preferences("IT", "DA")
        else:
            # Standard swap
            self.set_selected_by_code(self.source_list, tgt_code)
            self.set_selected_by_code(self.target_list, src_code)
            self.status_label.setText(f"Languages swapped: {tgt_code} ⇄ {src_code}")
            save_preferences(tgt_code, src_code)

        # Automatically translate if source text is present
        if self.source_box and self.source_box.getText().strip():
            self.on_translate_clicked()

    def on_translate_clicked(self):
        """
        Translates text in SourceTextBox and writes result to TranslatedTextBox.
        Handles UTF-8 and network errors gracefully.
        """
        source_text = self.source_box.getText()
        if not source_text or not source_text.strip():
            self.status_label.setText("Please enter or select some text to translate.")
            return

        src_code = self.get_selected_code(self.source_list, is_source=True)
        tgt_code = self.get_selected_code(self.target_list, is_source=False)

        # Save user preferences
        save_preferences(src_code, tgt_code)

        self.status_label.setText(f"Translating from {src_code} to {tgt_code}...")

        try:
            translated = perform_online_translation(source_text, src_code, tgt_code)
            self.target_box.setText(translated)
            self.status_label.setText("Translation completed successfully!")
        except Exception as e:
            raw_err = str(e)
            if raw_err.startswith("RuntimeError: "):
                raw_err = raw_err[len("RuntimeError: "):]
            self.target_box.setText(raw_err)
            self.status_label.setText("Network error: see diagnostic report in output box.")


# --- Modern LibreOffice UNO Component Implementation ---
class TranslatorJobService(unohelper.Base, XJobExecutor):
    """
    Modern UNO Component implementing XJobExecutor.
    Triggered when the user clicks 'Translate' on the Addons toolbar.
    """
    def __init__(self, ctx):
        self.ctx = ctx

    def trigger(self, args):
        try:
            # 1. Intercept selected text from active document
            initial_text = get_active_document_selection(self.ctx)
            
            # 2. Open native Quick Translator Dialog
            dialog = QuickTranslatorDialog(self.ctx)
            dialog.show(initial_text=initial_text)
        except Exception as e:
            traceback.print_exc()


# UNO Registration Helper
g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    TranslatorJobService,
    "org.libreoffice.quicktranslator.TranslatorService",
    ("com.sun.star.task.JobExecutor", "com.sun.star.task.XJobExecutor"),
)

# Standalone script entry point for direct script execution
def open_dialog(*args):
    ctx = uno.getComponentContext()
    service = TranslatorJobService(ctx)
    service.trigger("open")
