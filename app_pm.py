"""
MRB PM Report – Fixed Form Filler
===================================
Uses PDF form widget API (set_field_value / on_state) so text actually
goes INTO the form fields and photos render inside the template image boxes.

Run:  streamlit run app_pm.py
Needs:  template.pdf  in the same folder
"""

import base64, io, json, math
from pathlib import Path
from typing import Any, Dict

import fitz          # PyMuPDF ≥ 1.23
import streamlit as st
from PIL import Image, ImageOps

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
TEMPLATE    = BASE_DIR / "template.pdf"
DRAFT_DIR   = BASE_DIR / "drafts"
DRAFT_DIR.mkdir(exist_ok=True)

st.set_page_config(page_title="MRB PM Report", layout="wide")

# ── Image helpers ─────────────────────────────────────────────────────────────

def _normalise_image(uploaded_file) -> bytes:
    """EXIF-transpose + resize + JPEG encode.

    Fixes PNG / WhatsApp / screenshot images with transparency (RGBA)
    by placing them on a white background before saving as JPEG.
    """
    img = Image.open(uploaded_file)
    img = ImageOps.exif_transpose(img)
    img.thumbnail((1600, 1600))

    # JPEG cannot save RGBA / LA / P with transparency.
    # Convert anything transparent to RGB on white background.
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

# ── Widget helpers ────────────────────────────────────────────────────────────

def _set_text(page: fitz.Page, field_name: str, value: str):
    """Fill a text widget by name on a page."""
    value = (value or "").strip()
    if not value:
        return
    for w in page.widgets():
        if w.field_name == field_name:
            w.field_value = value
            w.update()
            return

def _set_checkbox(page: fitz.Page, field_name: str, checked: bool):
    """Check or uncheck a checkbox widget by name."""
    for w in page.widgets():
        if w.field_name == field_name:
            w.field_value = w.on_state() if checked else "Off"
            w.update()
            return


# ── Plain visible Unit Work/Data renderer ─────────────────────────────────────
# This does NOT depend on PDF form field appearance. It writes normal visible
# PDF text directly on top of the template boxes and keeps the boxes visible.

def _draw_plain_text(page: fitz.Page, x: float, y: float, value: str, size: int = 8):
    value = (value or "").strip()
    if not value:
        return
    page.insert_text(
        fitz.Point(x, y),
        value,
        fontsize=size,
        fontname="helv",
        color=(0, 0, 0),
        overlay=True,
    )

def _draw_plain_box(page: fitz.Page, rect_tuple, value: str, size: int = 7):
    value = (value or "").strip()
    if not value:
        return
    rect = fitz.Rect(rect_tuple)
    page.insert_textbox(
        rect,
        value,
        fontsize=size,
        fontname="helv",
        color=(0, 0, 0),
        overlay=True,
        align=0,
    )

def _draw_unit_work_page_plain(page: fitz.Page, unit: Dict[str, Any]):
    """Draw every Unit Work/Data field into the visible blue boxes.

    IMPORTANT: Do not delete widgets here. Deleting widgets removes the blue
    visual boxes from the template. We only draw visible text on top.
    """

    # Top identification field boxes
    _draw_plain_box(page, (80, 50, 218, 65), unit.get("unit_id", ""), 8)
    _draw_plain_box(page, (318, 50, 567, 65), unit.get("serial", ""), 8)

    _draw_plain_box(page, (87, 69, 226, 84), unit.get("manufacturer", ""), 8)
    _draw_plain_box(page, (318, 69, 457, 84), unit.get("model", ""), 8)
    _draw_plain_box(page, (492, 69, 560, 84), unit.get("age", ""), 8)

    # PM Scope checkmarks — mobile-friendly small marks
    # Use a small filled square instead of a large X so it stays inside the box
    # on laptop, tablet, and phone PDF viewers.
    scope = unit.get("scope", []) or []
    scope_boxes = {
        "Filters": (29, 159, 37, 167),
        "Belts": (103, 159, 111, 167),
        "Evap Coil": (171, 159, 179, 167),
        "Cond Coil": (273, 159, 281, 167),
        "Drain": (29, 186, 37, 194),
        "Electrical (visual)": (103, 186, 111, 194),
        "Safeties": (273, 186, 281, 194),
        "General cleaning": (370, 186, 378, 194),
    }
    for label, rect in scope_boxes.items():
        if label in scope:
            page.draw_rect(
                fitz.Rect(rect),
                color=(0, 0, 0),
                fill=(0, 0, 0),
                overlay=True,
            )

    # Operational reading boxes
    _draw_plain_box(page, (30, 204, 139, 219), unit.get("supply_temp", ""), 8)
    _draw_plain_box(page, (170, 204, 279, 219), unit.get("return_temp", ""), 8)
    _draw_plain_box(page, (310, 204, 419, 219), unit.get("delta_t", ""), 8)

    _draw_plain_box(page, (30, 229, 249, 244), unit.get("compressor_amps", ""), 8)
    _draw_plain_box(page, (280, 229, 389, 244), unit.get("blower_amps", ""), 8)
    _draw_plain_box(page, (420, 229, 529, 244), unit.get("condenser_fan_amps", ""), 8)

    _draw_plain_box(page, (30, 254, 209, 269), unit.get("voltage", ""), 8)
    _draw_plain_box(page, (280, 254, 529, 269), unit.get("refrigerant", ""), 8)

    # Large text areas
    _draw_plain_box(page, (31, 342, 580, 446), unit.get("observations", ""), 7)
    _draw_plain_box(page, (31, 536, 580, 580), unit.get("priority_a", ""), 7)
    _draw_plain_box(page, (31, 626, 580, 670), unit.get("priority_b", ""), 7)
    _draw_plain_box(page, (31, 714, 580, 758), unit.get("priority_c", ""), 7)


def _insert_photo(page: fitz.Page, widget_name: str, img_bytes: bytes):
    """
    Insert a JPEG into the exact rect of a Button/image widget.
    Strategy:
      1. Find the widget, grab its rect.
      2. Delete the widget (so it doesn't cover the image).
      3. White-fill the rect (clean background).
      4. Insert the image keeping proportions.
    """
    target_rect = None
    widgets_to_del = []
    for w in page.widgets():
        if w.field_name == widget_name:
            target_rect = fitz.Rect(w.rect)
            widgets_to_del.append(w)
    if target_rect is None:
        return
    for w in widgets_to_del:
        page.delete_widget(w)
    # White background so nothing bleeds through
    page.draw_rect(target_rect, color=None, fill=(1, 1, 1), overlay=True)
    # Insert image – keep_proportion centres and letterboxes inside rect
    page.insert_image(target_rect, stream=img_bytes, keep_proportion=True, overlay=True)

def _insert_site_map(page: fitz.Page, img_bytes: bytes):
    """Special handler for the site-map image widget on page 1."""
    _insert_photo(page, "Image9_af_image", img_bytes)

# ── Photo slot ordering on photo pages ────────────────────────────────────────
# Widget names for the 6 photo slots and 6 caption slots, in order
PHOTO_WIDGET_NAMES   = [
    "Image3_af_image",   # top-left
    "Image4_af_image",   # top-right
    "Image6_af_image",   # mid-left   (note: 5 & 6 are swapped in template)
    "Image5_af_image",   # mid-right
    "Image7_af_image",   # bottom-left
    "Image8_af_image",   # bottom-right
]
CAPTION_WIDGET_NAMES = [
    "u1_p1_cap",   # top-left caption
    "u1_p2_cap",   # top-right caption
    "u1_p3_cap",   # mid-left caption
    "u1_p4_cap",   # mid-right caption
    "u1_p5_cap",   # bottom-left caption
    "u1_p6_cap",   # bottom-right caption
]

# ── Field-name suffix per page-pair ──────────────────────────────────────────
# The template reuses the same base field names on every unit page but
# appends a unique suffix: "u1_id 1", "u1_id 2", … "u1_id 15"
# Page pair for unit N:  work page = page index 1+(N-1)*2,
#                        photo page = page index 2+(N-1)*2
# Suffix = str(N)  (1-based, space-separated from base name)

def _work_suffix(unit_num: int) -> str:
    return str(unit_num)

# Checkbox field bases → Streamlit scope key
CHECK_BASES = {
    "Filters":            "u1_filters",
    "Belts":              "u1_belts",
    "Evap Coil":          "u1_evap",
    "Cond Coil":          "u1_cond",
    "Drain":              "u1_drain",
    "Electrical (visual)":"u1_elec",
    "Safeties":           "u1_safe",
    "General cleaning":   "u1_clean",
}

# ── PDF fill ──────────────────────────────────────────────────────────────────

def fill_pdf(data: Dict[str, Any]) -> bytes:
    if not TEMPLATE.exists():
        st.error(f"template.pdf not found at {TEMPLATE}. Place your template PDF next to app_pm.py.")
        return b""

    doc = fitz.open(str(TEMPLATE))

    # ── Page 1 – project overview ─────────────────────────────────────────
    p0 = doc[0]
    proj = data.get("project", {})
    _set_text(p0, "proj_customer",   proj.get("customer", ""))
    _set_text(p0, "proj_site",       proj.get("site", ""))
    _set_text(p0, "proj_date",       proj.get("date", ""))
    _set_text(p0, "proj_techs",      proj.get("techs", ""))
    _set_text(p0, "proj_wo",         proj.get("work_order", ""))
    _set_text(p0, "proj_sign",       proj.get("signature", ""))
    _set_text(p0, "proj_signdate",   proj.get("signature_date", ""))
    if proj.get("site_map_b64"):
        _insert_site_map(p0, _b64_to_bytes(proj["site_map_b64"]))

    # ── Units 1-15 ────────────────────────────────────────────────────────
    for n in range(1, 16):
        unit       = data.get("units", {}).get(str(n), {})
        suf        = _work_suffix(n)
        work_idx   = 1 + (n - 1) * 2    # 1, 3, 5, … 29
        photo_idx  = work_idx + 1        # 2, 4, 6, … 30

        # ── Work page ──────────────────────────────────────────────────
        if work_idx < len(doc):
            wp = doc[work_idx]

            # Identity fields
            _set_text(wp, f"u1_id {suf}",    unit.get("unit_id", ""))
            _set_text(wp, f"u1_loc {suf}",   unit.get("serial", ""))
            _set_text(wp, f"u1_mfg {suf}",   unit.get("manufacturer", ""))
            _set_text(wp, f"u1_model {suf}",  unit.get("model", ""))
            _set_text(wp, f"u1_age {suf}",   unit.get("age", ""))

            # Readings
            _set_text(wp, f"u1_supply {suf}", unit.get("supply_temp", ""))
            _set_text(wp, f"u1_return {suf}", unit.get("return_temp", ""))
            _set_text(wp, f"u1_delta {suf}",  unit.get("delta_t", ""))
            _set_text(wp, f"u1_amps {suf}",   unit.get("compressor_amps", ""))
            _set_text(wp, f"u1_blower {suf}", unit.get("blower_amps", ""))
            _set_text(wp, f"u1_fan {suf}",    unit.get("condenser_fan_amps", ""))
            _set_text(wp, f"u1_voltage {suf}",unit.get("voltage", ""))
            _set_text(wp, f"u1_refrig {suf}", unit.get("refrigerant", ""))

            # Text areas
            _set_text(wp, f"u1_obs {suf}",  unit.get("observations", ""))
            _set_text(wp, f"u1_recA {suf}", unit.get("priority_a", ""))
            _set_text(wp, f"u1_recB {suf}", unit.get("priority_b", ""))
            _set_text(wp, f"u1_recC {suf}", unit.get("priority_c", ""))

            # Checkboxes
            scope = unit.get("scope", [])
            for label, base in CHECK_BASES.items():
                # Handle the "#" variant on unit 7
                for candidate in [f"{base} {suf}", f"{base}# {suf}"]:
                    _set_checkbox(wp, candidate, label in scope)

            # Final guaranteed visible overlay for all Unit Work/Data fields.
            # This is the key fix: it does not rely on PDF form field rendering.
            _draw_unit_work_page_plain(wp, unit)

        # ── Photo page ──────────────────────────────────────────────────
        if photo_idx < len(doc):
            pp      = doc[photo_idx]
            photos  = unit.get("photos", [])
            captions= unit.get("captions", [])

            for slot in range(min(6, len(photos))):
                photo_widget   = f"{PHOTO_WIDGET_NAMES[slot]} {suf}"
                caption_widget = f"{CAPTION_WIDGET_NAMES[slot]} {suf}"
                _insert_photo(pp, photo_widget, _b64_to_bytes(photos[slot]["b64"]))
                cap = captions[slot] if slot < len(captions) else ""
                _set_text(pp, caption_widget, cap)

            # Extra photos: append new pages (>6 only)
            if len(photos) > 6:
                extra = photos[6:]
                for chunk_start in range(0, len(extra), 6):
                    chunk    = extra[chunk_start:chunk_start + 6]
                    new_page = _make_extra_page(doc, n)
                    for slot, photo_obj in enumerate(chunk):
                        img_rect, cap_rect = _EXTRA_PHOTO_RECTS[slot]
                        new_page.draw_rect(fitz.Rect(img_rect), color=None, fill=(1,1,1), overlay=True)
                        new_page.insert_image(fitz.Rect(img_rect), stream=_b64_to_bytes(photo_obj["b64"]),
                                              keep_proportion=True, overlay=True)
                        cap_idx = 6 + chunk_start + slot
                        cap = captions[cap_idx] if cap_idx < len(captions) else ""
                        if cap:
                            new_page.insert_textbox(fitz.Rect(cap_rect), cap, fontsize=7,
                                                     fontname="helv", color=(0,0,0), overlay=True)

    # ── FORCE PDF FORM FIELDS TO RENDER ─────────────────────────

    # update all widgets so values appear visibly in PDF viewers
    for page in doc:
        widgets = list(page.widgets() or [])
        for widget in widgets:
            widget.update()

    # flatten appearance streams
    try:
        doc.need_appearances(False)
    except:
        pass

    # save final PDF
    out = io.BytesIO()

    doc.save(
        out,
        garbage=4,
        deflate=True,
        clean=True
    )

    doc.close()

    out.seek(0)

    return out.read()


# Extra page photo/caption rects (same layout used when >6 photos)
_EXTRA_PHOTO_RECTS = [
    ((15, 72, 286, 285),  (17, 291, 284, 307)),
    ((294, 72, 563, 285), (294, 291, 563, 307)),
    ((15, 317, 286, 521), (17, 526, 284, 542)),
    ((294, 317, 563, 521),(294, 526, 563, 542)),
    ((15, 548, 286, 754), (17, 759, 284, 775)),
    ((294, 548, 563, 754),(294, 759, 563, 775)),
]

def _make_extra_page(doc: fitz.Document, unit_num: int) -> fitz.Page:
    pg = doc.new_page(width=612, height=792)
    pg.insert_text((29, 29), f"UNIT {unit_num} – EXTRA PHOTOS", fontsize=14, fontname="helv")
    for img_rect, cap_rect in _EXTRA_PHOTO_RECTS:
        pg.draw_rect(fitz.Rect(img_rect), color=(0.7,0.7,0.7), width=0.8)
        pg.draw_rect(fitz.Rect(cap_rect), color=(0,0,0), fill=(0.82,0.86,1), width=0.8)
    return pg


# ── Session state ─────────────────────────────────────────────────────────────

def _empty() -> Dict:
    return {"project": {}, "units": {str(i): {"photos": [], "captions": []} for i in range(1, 16)}}

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
    dname = st.text_input("Draft name",
                           value=st.session_state.data.get("project",{}).get("customer","PM_Draft"))
    if st.button("Save draft"):
        safe = "".join(c for c in dname if c.isalnum() or c in "-_ ").strip().replace(" ","_") or "draft"
        (DRAFT_DIR / f"{safe}.json").write_text(json.dumps(st.session_state.data))
        st.success("Saved")

    st.divider()
    if not TEMPLATE.exists():
        st.error("template.pdf missing – place it next to app_pm.py")
    else:
        st.success("template.pdf found ✓")


# ── Main UI ───────────────────────────────────────────────────────────────────

st.title("MRB PM Report")
st.caption("Fills the actual PDF form fields — text and photos appear correctly in the output.")

proj = st.session_state.data.setdefault("project", {})

st.subheader("Project Overview")
c1, c2 = st.columns(2)
with c1:
    proj["customer"]       = st.text_input("Customer Name",               value=proj.get("customer",""))
    proj["site"]           = st.text_input("Site Address",                value=proj.get("site",""))
    proj["work_order"]     = st.text_input("Work Order #",                value=proj.get("work_order",""))
with c2:
    proj["date"]           = st.text_input("Date",                        value=proj.get("date",""))
    proj["techs"]          = st.text_input("Technician(s)",               value=proj.get("techs",""))
    proj["signature_date"] = st.text_input("Signature Date",              value=proj.get("signature_date",""))
proj["signature"]          = st.text_input("Lead Technician Signature",   value=proj.get("signature",""))

site_map = st.file_uploader("Site Map / Roof Plan (optional)", type=["png","jpg","jpeg"])
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
            unit["unit_id"]         = st.text_input("Unit ID / Tag",        value=unit.get("unit_id",""),         key=f"id_{n}")
            unit["manufacturer"]    = st.text_input("Manufacturer",          value=unit.get("manufacturer",""),    key=f"mfg_{n}")
            unit["supply_temp"]     = st.text_input("Supply Temp (°F)",      value=unit.get("supply_temp",""),     key=f"sup_{n}")
            unit["compressor_amps"] = st.text_input("Compressor Amps",       value=unit.get("compressor_amps",""), key=f"comp_{n}")
            unit["voltage"]         = st.text_input("Voltage",               value=unit.get("voltage",""),         key=f"volt_{n}")
        with c2:
            unit["serial"]          = st.text_input("Serial #",              value=unit.get("serial",""),          key=f"ser_{n}")
            unit["model"]           = st.text_input("Model",                 value=unit.get("model",""),           key=f"mod_{n}")
            unit["return_temp"]     = st.text_input("Return Temp (°F)",      value=unit.get("return_temp",""),     key=f"ret_{n}")
            unit["blower_amps"]     = st.text_input("Blower Amps",           value=unit.get("blower_amps",""),     key=f"blw_{n}")
            unit["refrigerant"]     = st.text_input("Refrigerant Type",      value=unit.get("refrigerant",""),     key=f"ref_{n}")
        with c3:
            unit["age"]             = st.text_input("Age",                   value=unit.get("age",""),             key=f"age_{n}")
            unit["delta_t"]         = st.text_input("Delta-T (°F)",          value=unit.get("delta_t",""),         key=f"dlt_{n}")
            unit["condenser_fan_amps"]=st.text_input("Condenser Fan Amps",   value=unit.get("condenser_fan_amps",""),key=f"cfan_{n}")

        unit["scope"] = st.multiselect(
            "PM Scope (checked items)",
            list(CHECK_BASES.keys()),
            default=unit.get("scope", []),
            key=f"scope_{n}",
        )

        unit["observations"] = st.text_area("General Observations",              value=unit.get("observations",""), key=f"obs_{n}")
        unit["priority_a"]   = st.text_area("Priority A – Monitor",              value=unit.get("priority_a",""),   key=f"pa_{n}")
        unit["priority_b"]   = st.text_area("Priority B – Attention Recommended", value=unit.get("priority_b",""),  key=f"pb_{n}")
        unit["priority_c"]   = st.text_area("Priority C – Action Recommended",   value=unit.get("priority_c",""),   key=f"pc_{n}")

        # ── Photos ────────────────────────────────────────────────────────
        st.markdown("#### Photos")
        st.caption("First 6 photos go into the template photo boxes. Additional photos create extra pages.")

        new_photos = st.file_uploader(
            f"Add photos for Unit {n}",
            type=["png","jpg","jpeg"],
            accept_multiple_files=True,
            key=f"up_{n}",
        )
        if new_photos:
            existing_keys = {
                (p.get("name"), p.get("size"))
                for p in unit.setdefault("photos", [])
            }
            added_count = 0
            for f in new_photos:
                key = (f.name, getattr(f, "size", None))
                if key not in existing_keys:
                    unit.setdefault("photos", []).append({
                        "name": f.name,
                        "size": getattr(f, "size", None),
                        "b64": base64.b64encode(_normalise_image(f)).decode(),
                    })
                    unit.setdefault("captions", []).append("")
                    existing_keys.add(key)
                    added_count += 1
            if added_count:
                st.success(f"Added {added_count} photo(s) to Unit {n}")

        photos   = unit.setdefault("photos",   [])
        captions = unit.setdefault("captions", [])

        if photos:
            st.write(f"{len(photos)} photo(s) saved for Unit {n}")
            for i, ph in enumerate(list(photos)):
                slot_label = f"Slot {i+1}" if i < 6 else f"Extra {i-5}"
                pc1, pc2, pc3 = st.columns([1, 2, 1])
                with pc1:
                    st.image(_b64_to_bytes(ph["b64"]), caption=slot_label, width=140)
                with pc2:
                    while len(captions) <= i:
                        captions.append("")
                    captions[i] = st.text_input(f"Caption – {slot_label}", value=captions[i], key=f"cap_{n}_{i}")
                with pc3:
                    if st.button("Delete", key=f"del_{n}_{i}"):
                        photos.pop(i)
                        if i < len(captions):
                            captions.pop(i)
                        st.rerun()

# ── Generate ──────────────────────────────────────────────────────────────────

st.divider()
if st.button("Generate PM Report PDF", type="primary", use_container_width=True):
    with st.spinner("Building PDF…"):
        pdf_bytes = fill_pdf(st.session_state.data)
    if pdf_bytes:
        cname = st.session_state.data.get("project",{}).get("customer","PM") or "PM"
        fname = cname.replace(" ","_").replace("/","-") + "_PM_Report.pdf"
        st.download_button(
            "⬇️ Download PM Report PDF",
            data=pdf_bytes,
            file_name=fname,
            mime="application/pdf",
        )
