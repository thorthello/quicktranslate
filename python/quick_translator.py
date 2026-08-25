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
import urllib.request
import urllib.parse
import html
import traceback

from com.sun.star.task import XJobExecutor
from com.sun.star.awt import XActionListener

# Supported language definitions (European, Nordic, Asian focus)
LANGUAGES = [
    ("AUTO", "AUTO - Automatic Detection"),
    ("IT", "IT - Italian"),
    ("DA", "DA - Danish"),
    ("EN", "EN - English"),
    ("DE", "DE - German"),
    ("FR", "FR - French"),
    ("ES", "ES - Spanish"),
    ("NL", "NL - Dutch"),
    ("PT", "PT - Portuguese"),
    ("PL", "PL - Polish"),
    ("EL", "EL - Greek"),
    ("CS", "CS - Czech"),
    ("HU", "HU - Hungarian"),
    ("RO", "RO - Romanian"),
    ("SV", "SV - Swedish"),
    ("NO", "NO - Norwegian"),
    ("FI", "FI - Finnish"),
    ("IS", "IS - Icelandic"),
    ("ZH", "ZH - Chinese (Simplified)"),
    ("JA", "JA - Japanese"),
    ("KO", "KO - Korean"),
    ("HI", "HI - Hindi"),
    ("AR", "AR - Arabic"),
    ("TR", "TR - Turkish"),
    ("VI", "VI - Vietnamese"),
    ("TH", "TH - Thai"),
    ("ID", "ID - Indonesian")
]

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


def perform_online_translation(text, source_lang="AUTO", target_lang="IT"):
    """
    Translates text using online translation APIs with proper UTF-8 and URL encoding.
    Fallback chain ensures maximum reliability.
    """
    if not text or not text.strip():
        return ""

    clean_text = text.strip()
    src_param = "autodetect" if source_lang == "AUTO" else source_lang.lower()
    tgt_param = target_lang.lower()

    # 1. Primary Service: MyMemory API (Free tier, no API key required)
    try:
        lang_pair = f"{src_param}|{tgt_param}"
        encoded_query = urllib.parse.quote(clean_text.encode("utf-8"))
        url = f"https://api.mymemory.translated.net/get?q={encoded_query}&langpair={lang_pair}"
        
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "LibreOffice-QuickTranslator/1.0"}
        )
        
        with urllib.request.urlopen(req, timeout=8) as response:
            if response.status == 200:
                raw_data = response.read().decode("utf-8")
                data = json.loads(raw_data)
                if "responseData" in data and "translatedText" in data["responseData"]:
                    result = data["responseData"]["translatedText"]
                    if not result.startswith("MYMEMORY WARNING:"):
                        return html.unescape(result)
    except Exception as e1:
        print("[QuickTranslator] MyMemory API error:", e1)

    # 2. Secondary Service: Google Translate public endpoint fallback
    try:
        sl = "auto" if source_lang == "AUTO" else source_lang.lower()
        encoded_q = urllib.parse.quote(clean_text.encode("utf-8"))
        gt_url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={sl}&tl={tgt_param}&dt=t&q={encoded_q}"
        
        req2 = urllib.request.Request(
            gt_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req2, timeout=8) as response:
            if response.status == 200:
                raw_data = response.read().decode("utf-8")
                gt_json = json.loads(raw_data)
                if isinstance(gt_json, list) and len(gt_json) > 0 and isinstance(gt_json[0], list):
                    translated_chunks = [chunk[0] for chunk in gt_json[0] if chunk and len(chunk) > 0 and chunk[0]]
                    return "".join(translated_chunks)
    except Exception as e2:
        print("[QuickTranslator] Fallback service error:", e2)

    raise RuntimeError("Unable to reach translation server. Please check your internet connection.")


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
        # Dimensions: 20% narrower (224) and 25% taller (275)
        dialog_model = smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialogModel", self.ctx)
        dialog_model.PositionX = 150
        dialog_model.PositionY = 100
        dialog_model.Width = 224
        dialog_model.Height = 275
        dialog_model.Title = "Quick Translator"
        dialog_model.DesktopAsParent = True
        dialog_model.Moveable = True
        dialog_model.Closeable = True
        dialog_model.Decoration = True

        # 2. Controls - Row 1: Source Language Dropdown
        src_model = dialog_model.createInstance("com.sun.star.awt.UnoControlListBoxModel")
        src_model.PositionX = 6
        src_model.PositionY = 8
        src_model.Width = 72
        src_model.Height = 14
        src_model.Dropdown = True
        src_model.Name = "SourceLangList"
        src_items = [label for code, label in LANGUAGES]
        src_model.StringItemList = tuple(src_items)
        src_model.SelectedItems = (0,) # AUTO is default
        dialog_model.insertByName("SourceLangList", src_model)

        # 3. Controls - Row 1: Swap Button ("⇄")
        swap_model = dialog_model.createInstance("com.sun.star.awt.UnoControlButtonModel")
        swap_model.PositionX = 81
        swap_model.PositionY = 8
        swap_model.Width = 16
        swap_model.Height = 14
        swap_model.Label = "⇄"
        swap_model.Name = "SwapBtn"
        swap_model.HelpText = "Swap Languages (Special: AUTO -> IT / DA)"
        dialog_model.insertByName("SwapBtn", swap_model)

        # 4. Controls - Row 1: Target Language Dropdown
        tgt_model = dialog_model.createInstance("com.sun.star.awt.UnoControlListBoxModel")
        tgt_model.PositionX = 100
        tgt_model.PositionY = 8
        tgt_model.Width = 68
        tgt_model.Height = 14
        tgt_model.Dropdown = True
        tgt_model.Name = "TargetLangList"
        tgt_items = [label for code, label in LANGUAGES if code != "AUTO"]
        tgt_model.StringItemList = tuple(tgt_items)
        it_idx = 0
        for i, item in enumerate(tgt_items):
            if item.startswith("IT -"):
                it_idx = i
                break
        tgt_model.SelectedItems = (it_idx,)
        dialog_model.insertByName("TargetLangList", tgt_model)

        # 5. Controls - Row 1: Translate Button ("Translate")
        trans_model = dialog_model.createInstance("com.sun.star.awt.UnoControlButtonModel")
        trans_model.PositionX = 171
        trans_model.PositionY = 8
        trans_model.Width = 47
        trans_model.Height = 14
        trans_model.Label = "Translate"
        trans_model.Name = "TranslateBtn"
        dialog_model.insertByName("TranslateBtn", trans_model)

        # 6. Controls - Center: Source Text Section (Bold Title + Multiline VScroll)
        lbl_src = dialog_model.createInstance("com.sun.star.awt.UnoControlFixedTextModel")
        lbl_src.PositionX = 6
        lbl_src.PositionY = 26
        lbl_src.Width = 212
        lbl_src.Height = 8
        lbl_src.Label = "Source Text:"
        lbl_src.FontWeight = 150.0  # Bold font weight
        dialog_model.insertByName("LblSource", lbl_src)

        src_text_model = dialog_model.createInstance("com.sun.star.awt.UnoControlEditModel")
        src_text_model.PositionX = 6
        src_text_model.PositionY = 36
        src_text_model.Width = 212
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
        lbl_tgt.Width = 212
        lbl_tgt.Height = 8
        lbl_tgt.Label = "Translated Text:"
        lbl_tgt.FontWeight = 150.0  # Bold font weight
        dialog_model.insertByName("LblTarget", lbl_tgt)

        tgt_text_model = dialog_model.createInstance("com.sun.star.awt.UnoControlEditModel")
        tgt_text_model.PositionX = 6
        tgt_text_model.PositionY = 148
        tgt_text_model.Width = 212
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
        status_model.Width = 212
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

        # 10. Bind Action Listeners
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

        self.dialog.getControl("SwapBtn").addActionListener(SwapListener(self))
        self.dialog.getControl("TranslateBtn").addActionListener(TranslateListener(self))

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
        else:
            # Standard swap
            self.set_selected_by_code(self.source_list, tgt_code)
            self.set_selected_by_code(self.target_list, src_code)
            self.status_label.setText(f"Languages swapped: {tgt_code} ⇄ {src_code}")

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

        self.status_label.setText(f"Translating from {src_code} to {tgt_code}...")

        try:
            translated = perform_online_translation(source_text, src_code, tgt_code)
            self.target_box.setText(translated)
            self.status_label.setText("Translation completed successfully!")
        except Exception as e:
            error_msg = f"Translation error: {str(e)}"
            self.target_box.setText(error_msg)
            self.status_label.setText("Error during translation. See output box.")


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
