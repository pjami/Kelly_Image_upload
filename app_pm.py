"""
MRB PM Report – Streamlit App
==============================
Correctly fills PDF form fields using field_value + widget.update() + doc.bake().
Black dot fix: unchecked checkboxes are DELETED (not set to False) to prevent
the template's Zapf Dingbats black square from appearing on mobile/PDF viewers.

Run:  streamlit run app_pm.py
Needs: template.pdf in the same folder
"""

import base64, io, json
from pathlib import Path
from typing import Any, Dict

import fitz          # PyMuPDF >= 1.23
import streamlit as st
from PIL import Image, ImageOps

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent
TEMPLATE  = BASE_DIR / "template.pdf"
DRAFT_DIR = BASE_DIR / "drafts"
DRAFT_DIR.mkdir(exist_ok=True)

st.set_page_config(page_title="MRB PM Report", layout="wide")

# ── Image helpers ─────────────────────────────────────────────────────────────

def _normalise_image(uploaded_file) -> bytes:
    img = Image.open(uploaded_file)
    img = ImageOps.exif_transpose(img)
    img.thumbnail((1600, 1600))
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        bg.paste(img, mask=img.getchannel("A"))
        img = bg
    else:
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return buf.getvalue()

def _b64_to_bytes(b64: str) -> bytes:
    return base64.b64decode(b64.encode())

# ── PDF field constants ────────────────────────────────────────────────────────

CHECK_BASES = {
    "Filters":             "u1_filters",
    "Belts":               "u1_belts",
    "Evap Coil":           "u1_evap",
    "Cond Coil":           "u1_cond",
    "Drain":               "u1_drain",
    "Electrical (visual)": "u1_elec",
    "Safeties":            "u1_safe",
    "General cleaning":    "u1_clean",
}

PHOTO_WIDGET_NAMES = [
    "Image3_af_image",
    "Image4_af_image",
    "Image6_af_image",
    "Image5_af_image",
    "Image7_af_image",
    "Image8_af_image",
]
CAPTION_WIDGET_NAMES = [
    "u1_p1_cap",
    "u1_p2_cap",
    "u1_p3_cap",
    "u1_p4_cap",
    "u1_p5_cap",
    "u1_p6_cap",
]

PROJECT_FIELDS = [
    ("proj_customer", "customer"),
    ("proj_site",     "site"),
    ("proj_date",     "date"),
    ("proj_techs",    "techs"),
    ("proj_wo",       "work_order"),
    ("proj_sign",     "signature"),
    ("proj_signdate", "signature_date"),
]

# ── Core PDF helpers ──────────────────────────────────────────────────────────

def _set_field(page: fitz.Page, field_name: str, value: str):
    """Set a text widget value using the proper widget API."""
    value = (value or "").strip()
    for w in (page.widgets() or []):
        if w.field_name == field_name:
            w.field_value = value
            w.text_fontsize = 8
            w.update()
            return True
    return False

def _set_checkbox(page: fitz.Page, field_name: str, checked: bool):
    """
    Set checkbox on or off.
    FIX: Unchecked boxes are DELETED rather than set to False.
    The template's unchecked appearance uses Zapf Dingbats char 4 (filled black
    square) which renders as a black dot/square when bake() flattens the PDF.
    Deleting the widget leaves a clean empty blue box (the box background is
    drawn in the page content stream, not by the widget).
    """
    for w in list(page.widgets() or []):
        if w.field_name == field_name:
            if checked:
                w.field_value = True
                w.update()
            else:
                page.delete_widget(w)
            return True
    return False

def _insert_photo(page: fitz.Page, widget_name: str, img_bytes: bytes):
    """Replace an image widget with a JPEG, keeping proportions."""
    target_rect = None
    to_del = []
    for w in page.widgets():
        if w.field_name == widget_name:
            target_rect = fitz.Rect(w.rect)
            to_del.append(w)
    if target_rect is None:
        return
    for w in to_del:
        page.delete_widget(w)
    page.draw_rect(target_rect, color=None, fill=(1, 1, 1), overlay=True)
    page.insert_image(target_rect, stream=img_bytes, keep_proportion=True, overlay=True)

# ── Extra photo pages ─────────────────────────────────────────────────────────

_EXTRA_PHOTO_RECTS = [
    ((15, 72, 286, 285),   (17, 291, 284, 307)),
    ((294, 72, 563, 285),  (294, 291, 563, 307)),
    ((15, 317, 286, 521),  (17, 526, 284, 542)),
    ((294, 317, 563, 521), (294, 526, 563, 542)),
    ((15, 548, 286, 754),  (17, 759, 284, 775)),
    ((294, 548, 563, 754), (294, 759, 563, 775)),
]

def _make_extra_page(doc: fitz.Document, unit_num: int) -> fitz.Page:
    pg = doc.new_page(width=612, height=792)
    pg.insert_text((29, 40), f"UNIT {unit_num} - EXTRA PHOTOS", fontsize=14, fontname="helv")
    for img_rect, cap_rect in _EXTRA_PHOTO_RECTS:
        pg.draw_rect(fitz.Rect(img_rect), color=(0.7, 0.7, 0.7), width=0.8)
        pg.draw_rect(fitz.Rect(cap_rect), color=(0, 0, 0), fill=(0.82, 0.86, 1), width=0.8)
    return pg

# ── Main PDF fill ─────────────────────────────────────────────────────────────

def fill_pdf(data: Dict[str, Any]) -> bytes:
    if not TEMPLATE.exists():
        st.error(f"template.pdf not found at {TEMPLATE}")
        return b""

    doc = fitz.open(str(TEMPLATE))

    # ── Page 0: Project overview ───────────────────────────────────────────
    p0   = doc[0]
    proj = data.get("project", {})
    for field_name, key in PROJECT_FIELDS:
        _set_field(p0, field_name, proj.get(key, ""))
    if proj.get("site_map_b64"):
        _insert_photo(p0, "Image9_af_image", _b64_to_bytes(proj["site_map_b64"]))

    # ── Units 1-15 ────────────────────────────────────────────────────────
    for n in range(1, 16):
        unit      = data.get("units", {}).get(str(n), {})
        suf       = str(n)
        work_idx  = 1 + (n - 1) * 2   # 1, 3, 5, ... 29
        photo_idx = work_idx + 1       # 2, 4, 6, ... 30

        # ── Work / data page ──────────────────────────────────────────
        if work_idx < len(doc):
            wp = doc[work_idx]

            def sf(base, key, _wp=wp, _suf=suf, _unit=unit):
                _set_field(_wp, f"{base} {_suf}", _unit.get(key, ""))

            sf("u1_id",      "unit_id")
            sf("u1_loc",     "serial")
            sf("u1_mfg",     "manufacturer")
            sf("u1_model",   "model")
            sf("u1_age",     "age")
            sf("u1_supply",  "supply_temp")
            sf("u1_return",  "return_temp")
            sf("u1_delta",   "delta_t")
            sf("u1_amps",    "compressor_amps")
            sf("u1_blower",  "blower_amps")
            sf("u1_fan",     "condenser_fan_amps")
            sf("u1_voltage", "voltage")
            sf("u1_refrig",  "refrigerant")
            sf("u1_obs",     "observations")
            sf("u1_recA",    "priority_a")
            sf("u1_recB",    "priority_b")
            sf("u1_recC",    "priority_c")

            # Checkboxes — delete unchecked to avoid black dot bug
            scope = unit.get("scope", []) or []
            for label, base in CHECK_BASES.items():
                checked = label in scope
                if not _set_checkbox(wp, f"{base} {suf}", checked):
                    _set_checkbox(wp, f"{base}# {suf}", checked)

        # ── Photo page ────────────────────────────────────────────────
        if photo_idx < len(doc):
            pp       = doc[photo_idx]
            photos   = unit.get("photos",   [])
            captions = unit.get("captions", [])

            for slot in range(min(6, len(photos))):
                _insert_photo(pp, f"{PHOTO_WIDGET_NAMES[slot]} {suf}",
                              _b64_to_bytes(photos[slot]["b64"]))
                cap = captions[slot] if slot < len(captions) else ""
                _set_field(pp, f"{CAPTION_WIDGET_NAMES[slot]} {suf}", cap)

            # Extra photos beyond 6 -> appended pages
            if len(photos) > 6:
                for chunk_start in range(0, len(photos) - 6, 6):
                    chunk    = photos[6 + chunk_start: 6 + chunk_start + 6]
                    new_page = _make_extra_page(doc, n)
                    for slot, ph in enumerate(chunk):
                        img_rect, cap_rect = _EXTRA_PHOTO_RECTS[slot]
                        new_page.draw_rect(fitz.Rect(img_rect), color=None, fill=(1,1,1), overlay=True)
                        new_page.insert_image(fitz.Rect(img_rect),
                                              stream=_b64_to_bytes(ph["b64"]),
                                              keep_proportion=True, overlay=True)
                        cap_idx = 6 + chunk_start + slot
                        cap = captions[cap_idx] if cap_idx < len(captions) else ""
                        if cap:
                            new_page.insert_textbox(
                                fitz.Rect(cap_rect), cap,
                                fontsize=7, fontname="helv",
                                color=(0, 0, 0), overlay=True)

    # ── Bake widgets into page content (flattens, no interactive fields) ──
    doc.bake(annots=True, widgets=True)

    out = io.BytesIO()
    doc.save(out, garbage=4, deflate=True, clean=True)
    doc.close()
    out.seek(0)
    return out.read()

# ── Session state ─────────────────────────────────────────────────────────────

def _empty() -> Dict:
    return {
        "project": {},
        "units": {str(i): {"photos": [], "captions": []} for i in range(1, 16)}
    }

if "data" not in st.session_state:
    st.session_state.data = _empty()

# ── Sidebar – drafts ──────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Drafts")
    files = sorted(DRAFT_DIR.glob("*.json"))
    sel   = st.selectbox("Open saved draft", ["-- none --"] + [f.name for f in files])
    if st.button("Load") and sel != "-- none --":
        st.session_state.data = json.loads((DRAFT_DIR / sel).read_text())
        st.success("Loaded")
        st.rerun()
    dname = st.text_input(
        "Draft name",
        value=st.session_state.data.get("project", {}).get("customer", "PM_Draft")
    )
    if st.button("Save draft"):
        safe = "".join(c for c in dname if c.isalnum() or c in "-_ ").strip().replace(" ", "_") or "draft"
        (DRAFT_DIR / f"{safe}.json").write_text(json.dumps(st.session_state.data))
        st.success("Saved")
    st.divider()
    if not TEMPLATE.exists():
        st.error("template.pdf missing - place it next to app_pm.py")
    else:
        st.success("template.pdf found")

# ── Main UI ───────────────────────────────────────────────────────────────────

st.title("MRB PM Report")
st.caption("Fill in the form below and click Generate to produce the filled PDF.")

proj = st.session_state.data.setdefault("project", {})

st.subheader("Project Overview")
c1, c2 = st.columns(2)
with c1:
    proj["customer"]       = st.text_input("Customer Name",             value=proj.get("customer", ""))
    proj["site"]           = st.text_input("Site Address",              value=proj.get("site", ""))
    proj["work_order"]     = st.text_input("Work Order #",              value=proj.get("work_order", ""))
with c2:
    proj["date"]           = st.text_input("Date",                      value=proj.get("date", ""))
    proj["techs"]          = st.text_input("Technician(s)",             value=proj.get("techs", ""))
    proj["signature_date"] = st.text_input("Signature Date",            value=proj.get("signature_date", ""))
proj["signature"]          = st.text_input("Lead Technician Signature", value=proj.get("signature", ""))

site_map = st.file_uploader("Site Map / Roof Plan (optional)", type=["png", "jpg", "jpeg"])
if site_map:
    proj["site_map_b64"] = base64.b64encode(_normalise_image(site_map)).decode()
    st.image(site_map, caption="Site map preview", width=320)

st.divider()

# ── Units ─────────────────────────────────────────────────────────────────────

units = st.session_state.data.setdefault("units", {})

for n in range(1, 16):
    unit = units.setdefault(str(n), {"photos": [], "captions": []})
    with st.expander(f"Unit {n}", expanded=(n == 1)):

        c1, c2, c3 = st.columns(3)
        with c1:
            unit["unit_id"]            = st.text_input("Unit ID / Tag",      value=unit.get("unit_id", ""),           key=f"id_{n}")
            unit["manufacturer"]       = st.text_input("Manufacturer",        value=unit.get("manufacturer", ""),      key=f"mfg_{n}")
            unit["supply_temp"]        = st.text_input("Supply Temp (F)",     value=unit.get("supply_temp", ""),       key=f"sup_{n}")
            unit["compressor_amps"]    = st.text_input("Compressor Amps",     value=unit.get("compressor_amps", ""),   key=f"comp_{n}")
            unit["voltage"]            = st.text_input("Voltage",             value=unit.get("voltage", ""),           key=f"volt_{n}")
        with c2:
            unit["serial"]             = st.text_input("Serial #",            value=unit.get("serial", ""),            key=f"ser_{n}")
            unit["model"]              = st.text_input("Model",               value=unit.get("model", ""),             key=f"mod_{n}")
            unit["return_temp"]        = st.text_input("Return Temp (F)",     value=unit.get("return_temp", ""),       key=f"ret_{n}")
            unit["blower_amps"]        = st.text_input("Blower Amps",         value=unit.get("blower_amps", ""),       key=f"blw_{n}")
            unit["refrigerant"]        = st.text_input("Refrigerant Type",    value=unit.get("refrigerant", ""),       key=f"ref_{n}")
        with c3:
            unit["age"]                = st.text_input("Age",                 value=unit.get("age", ""),               key=f"age_{n}")
            unit["delta_t"]            = st.text_input("Delta-T (F)",         value=unit.get("delta_t", ""),           key=f"dlt_{n}")
            unit["condenser_fan_amps"] = st.text_input("Condenser Fan Amps",  value=unit.get("condenser_fan_amps", ""),key=f"cfan_{n}")

        unit["scope"] = st.multiselect(
            "PM Scope (checked items)",
            list(CHECK_BASES.keys()),
            default=unit.get("scope", []),
            key=f"scope_{n}",
        )

        unit["observations"] = st.text_area("General Observations",               value=unit.get("observations", ""), key=f"obs_{n}")
        unit["priority_a"]   = st.text_area("Priority A - Monitor",               value=unit.get("priority_a", ""),   key=f"pa_{n}")
        unit["priority_b"]   = st.text_area("Priority B - Attention Recommended",  value=unit.get("priority_b", ""),   key=f"pb_{n}")
        unit["priority_c"]   = st.text_area("Priority C - Action Recommended",    value=unit.get("priority_c", ""),   key=f"pc_{n}")

        st.markdown("#### Photos")
        st.caption("First 6 photos go into template boxes. Extra photos create additional pages.")

        new_photos = st.file_uploader(
            f"Add photos for Unit {n}",
            type=["png", "jpg", "jpeg"],
            accept_multiple_files=True,
            key=f"up_{n}",
        )
        if new_photos:
            existing_keys = {(p.get("name"), p.get("size")) for p in unit.setdefault("photos", [])}
            added = 0
            for f in new_photos:
                k = (f.name, getattr(f, "size", None))
                if k not in existing_keys:
                    unit["photos"].append({
                        "name": f.name,
                        "size": getattr(f, "size", None),
                        "b64":  base64.b64encode(_normalise_image(f)).decode(),
                    })
                    unit.setdefault("captions", []).append("")
                    existing_keys.add(k)
                    added += 1
            if added:
                st.success(f"Added {added} photo(s) to Unit {n}")

        photos   = unit.setdefault("photos",   [])
        captions = unit.setdefault("captions", [])

        if photos:
            st.write(f"{len(photos)} photo(s) for Unit {n}")
            for i, ph in enumerate(list(photos)):
                label = f"Slot {i+1}" if i < 6 else f"Extra {i-5}"
                pc1, pc2, pc3 = st.columns([1, 2, 1])
                with pc1:
                    st.image(_b64_to_bytes(ph["b64"]), caption=label, width=140)
                with pc2:
                    while len(captions) <= i:
                        captions.append("")
                    captions[i] = st.text_input(f"Caption - {label}", value=captions[i], key=f"cap_{n}_{i}")
                with pc3:
                    if st.button("Delete", key=f"del_{n}_{i}"):
                        photos.pop(i)
                        if i < len(captions):
                            captions.pop(i)
                        st.rerun()

# ── Generate ──────────────────────────────────────────────────────────────────

st.divider()
if st.button("Generate PM Report PDF", type="primary", use_container_width=True):
    with st.spinner("Building PDF..."):
        pdf_bytes = fill_pdf(st.session_state.data)
    if pdf_bytes:
        cname = st.session_state.data.get("project", {}).get("customer", "PM") or "PM"
        fname = cname.replace(" ", "_").replace("/", "-") + "_PM_Report.pdf"
        st.download_button(
            "Download PM Report PDF",
            data=pdf_bytes,
            file_name=fname,
            mime="application/pdf",
        )
