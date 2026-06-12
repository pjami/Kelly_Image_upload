"""
MRB PM Report – Field App v5
==============================
Upgrades in this version:
- Progress bar per unit (fields filled %)
- Unit status tags: Empty / In Progress / Complete
- Select All / Clear All scope checkboxes per unit
- Copy unit (duplicate Unit N into Unit N+1)
- Auto-collapse completed units
- Validation before PDF (blocks blank customer/site)
- Better draft list (shows site, unit count, date)
- Last saved timestamp visible in header
- Mobile-friendly stacked layout improvements
"""

import base64, copy, io, json, smtplib, uuid
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import fitz
import gspread
import streamlit as st
from google.oauth2.service_account import Credentials
from PIL import Image, ImageOps

BASE_DIR = Path(__file__).parent
TEMPLATE  = BASE_DIR / "template.pdf"

st.set_page_config(page_title="MRB PM Reports", page_icon="🔧",
                   layout="wide", initial_sidebar_state="expanded")

MRB_NAVY = "#1B2A4A"
MRB_RED  = "#C0272D"

st.markdown(f"""
<style>
  .stApp {{ background: #F4F6F9; }}

  .mrb-header {{
    background: {MRB_NAVY};
    padding: 12px 24px;
    border-radius: 10px;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 6px;
  }}
  .mrb-header h2 {{ margin:0; color:white; font-size:1.3rem; }}
  .mrb-header p  {{ margin:0; color:rgba(255,255,255,0.65); font-size:0.8rem; }}
  .mrb-saved-ts  {{ color:rgba(255,255,255,0.55); font-size:0.75rem; text-align:right; }}

  .stButton>button[kind="primary"] {{
    background: {MRB_NAVY}; border:none; border-radius:8px; font-weight:600;
  }}
  .stButton>button[kind="primary"]:hover {{ background:{MRB_RED}; }}

  .unit-tag {{
    display:inline-block; font-size:11px; font-weight:600;
    padding:2px 10px; border-radius:20px; margin-left:8px;
  }}
  .tag-empty    {{ background:#F1EFE8; color:#5F5E5A; }}
  .tag-progress {{ background:#FAEEDA; color:#854F0B; }}
  .tag-complete {{ background:#EAF3DE; color:#3B6D11; }}

  .progress-wrap {{ margin: 4px 0 10px 0; }}
  .progress-bar-bg {{
    background:#E2E8F0; border-radius:6px; height:6px; overflow:hidden;
  }}
  .progress-bar-fill {{
    height:6px; border-radius:6px; transition:width 0.3s;
  }}

  div[data-testid="stNumberInput"] input {{ text-align:center; }}

  @media(max-width:768px){{
    .mrb-header h2 {{ font-size:1rem; }}
    .mrb-saved-ts  {{ font-size:0.7rem; }}
  }}
</style>
""", unsafe_allow_html=True)

# ── Google Sheets ──────────────────────────────────────────────────────────────
SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/drive"]

@st.cache_resource
def get_sheet():
    try:
        creds  = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"], scopes=SCOPES)
        client = gspread.authorize(creds)
        return client.open_by_key(st.secrets["SHEET_ID"]).sheet1
    except Exception as e:
        st.sidebar.error(f"Sheets error: {e}")
        return None

def sheet_save(draft_id, name, data, status="Draft"):
    sheet = get_sheet()
    if not sheet: return
    try:
        # Ensure header exists so saved drafts show up correctly
        vals = sheet.get_all_values()
        if not vals:
            sheet.append_row(["id", "name", "data", "updated_at", "status"])

        updated = datetime.now().strftime("%Y-%m-%d %H:%M")
        payload = json.dumps(data)
        cell = sheet.find(draft_id, in_column=1)
        if cell:
            sheet.update([[draft_id, name, payload, updated, status]],
                         f"A{cell.row}:E{cell.row}")
        else:
            sheet.append_row([draft_id, name, payload, updated, status])
        try:
            sheet_load_all.clear()
        except Exception:
            pass
    except Exception as e:
        st.warning(f"⚠️ Save failed: {e}")

@st.cache_data(ttl=60, show_spinner=False)
def sheet_load_all():
    sheet = get_sheet()
    if not sheet: return []
    try:
        rows = sheet.get_all_values()
        if len(rows) < 2: return []
        result = []
        for row in rows[1:]:
            if len(row) >= 5 and row[0]:
                # Try to pull extra info from stored JSON without full parse
                extra = {}
                try:
                    d = json.loads(row[2])
                    p = d.get("project", {})
                    extra["site"]       = p.get("site", "")
                    extra["units_count"]= d.get("num_units", 1)
                except: pass
                result.append({
                    "id":         row[0],
                    "name":       row[1],
                    "updated_at": row[3],
                    "status":     row[4],
                    **extra
                })
        return result
    except Exception as e:
        st.warning(f"⚠️ Load failed: {e}")
        return []

def sheet_load_one(draft_id):
    sheet = get_sheet()
    if not sheet: return {}
    try:
        cell = sheet.find(draft_id, in_column=1)
        if cell:
            row = sheet.row_values(cell.row)
            return json.loads(row[2]) if len(row) > 2 else {}
    except: pass
    return {}

def sheet_delete(draft_id):
    sheet = get_sheet()
    if not sheet: return
    try:
        cell = sheet.find(draft_id, in_column=1)
        if cell:
            sheet.delete_rows(cell.row)
            try:
                sheet_load_all.clear()
            except Exception:
                pass
    except Exception as e:
        st.warning(f"⚠️ Delete failed: {e}")

# ── Image helpers ──────────────────────────────────────────────────────────────
def _normalise_image(f) -> bytes:
    img = Image.open(f)
    img = ImageOps.exif_transpose(img)
    img.thumbnail((1600, 1600))
    if img.mode in ("RGBA","LA") or (img.mode=="P" and "transparency" in img.info):
        bg = Image.new("RGB", img.size, (255,255,255))
        img = img.convert("RGBA")
        bg.paste(img, mask=img.getchannel("A"))
        img = bg
    else:
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return buf.getvalue()

def _b64(b): return base64.b64encode(b).decode()
def _unb64(s): return base64.b64decode(s.encode())

# ── PDF constants ──────────────────────────────────────────────────────────────
CHECK_BASES = {
    "Filters":"u1_filters","Belts":"u1_belts",
    "Evap Coil":"u1_evap","Cond Coil":"u1_cond",
    "Drain":"u1_drain","Electrical (visual)":"u1_elec",
    "Safeties":"u1_safe","General cleaning":"u1_clean",
}
PHOTO_WIDGETS   = [f"Image{i}_af_image" for i in range(3,9)]
CAPTION_WIDGETS = [f"u1_p{i}_cap"        for i in range(1,7)]
PROJECT_FIELDS  = [
    ("proj_customer","customer"), ("proj_site","site"),
    ("proj_date","date"),         ("proj_techs","techs"),
    ("proj_wo","work_order"),     ("proj_sign","signature"),
    ("proj_signdate","signature_date"),
]
_EXTRA_RECTS = [
    ((15,72,286,285),(17,291,284,307)),  ((294,72,563,285),(294,291,563,307)),
    ((15,317,286,521),(17,526,284,542)), ((294,317,563,521),(294,526,563,542)),
    ((15,548,286,754),(17,759,284,775)), ((294,548,563,754),(294,759,563,775)),
]

SCOPE_ITEMS = ["Filters","Belts","Evap Coil","Cond Coil",
               "Drain","Electrical (visual)","Safeties","General cleaning"]

def _set_field(page, name, value):
    value = (value or "").strip()
    for w in list(page.widgets() or []):
        if w.field_name == name:
            w.field_value = value; w.fill_color=(1,1,1); w.border_color=(0.5,0.5,0.5)
            if not w.text_fontsize: w.text_fontsize=9
            w.update(); return True
    return False

def _set_checkbox(page, name, checked):
    for w in list(page.widgets() or []):
        if w.field_name == name:
            if checked: w.field_value=True; w.fill_color=(1,1,1); w.update()
            else: page.delete_widget(w)
            return True
    return False

def _insert_photo(page, name, img_bytes):
    rect, dels = None, []
    for w in list(page.widgets() or []):
        if w.field_name == name: rect=fitz.Rect(w.rect); dels.append(w)
    if not rect: return
    for w in dels: page.delete_widget(w)
    page.draw_rect(rect, color=None, fill=(1,1,1), overlay=True)
    page.insert_image(rect, stream=img_bytes, keep_proportion=True, overlay=True)

def _hide_widget(page, name):
    for w in list(page.widgets() or []):
        if w.field_name == name:
            w.border_color=(1,1,1); w.fill_color=(1,1,1); w.update(); return

def _make_extra_page(doc, n):
    pg = doc.new_page(width=612, height=792)
    pg.insert_text((29,40), f"UNIT {n} - EXTRA PHOTOS", fontsize=14, fontname="helv")
    for ir, cr in _EXTRA_RECTS:
        pg.draw_rect(fitz.Rect(ir), color=(0.7,0.7,0.7), width=0.8)
        pg.draw_rect(fitz.Rect(cr), color=(0,0,0), fill=(0.82,0.86,1), width=0.8)
    return pg

def fill_pdf(data: Dict[str, Any]) -> bytes:
    if not TEMPLATE.exists():
        st.error("template.pdf not found"); return b""
    doc = fitz.open(str(TEMPLATE))
    p0  = doc[0]; proj = data.get("project", {})
    for fn, k in PROJECT_FIELDS: _set_field(p0, fn, proj.get(k,""))
    if proj.get("site_map_b64"): _insert_photo(p0,"Image9_af_image",_unb64(proj["site_map_b64"]))
    else: _hide_widget(p0,"Image9_af_image")

    for n in range(1, 16):
        unit = data.get("units",{}).get(str(n),{})
        suf  = str(n); wi=1+(n-1)*2; pi=wi+1
        if wi < len(doc):
            wp = doc[wi]
            def sf(b,k,_w=wp,_s=suf,_u=unit): _set_field(_w,f"{b} {_s}",_u.get(k,""))
            sf("u1_id","unit_id"); sf("u1_loc","serial"); sf("u1_mfg","manufacturer")
            sf("u1_model","model"); sf("u1_age","age"); sf("u1_supply","supply_temp")
            sf("u1_return","return_temp"); sf("u1_delta","delta_t")
            sf("u1_amps","compressor_amps"); sf("u1_blower","blower_amps")
            sf("u1_fan","condenser_fan_amps"); sf("u1_voltage","voltage")
            sf("u1_refrig","refrigerant"); sf("u1_obs","observations")
            sf("u1_recA","priority_a"); sf("u1_recB","priority_b"); sf("u1_recC","priority_c")
            for lbl, base in CHECK_BASES.items():
                _set_checkbox(wp, f"{base} {suf}", lbl in (unit.get("scope") or []))
        if pi < len(doc):
            pp=doc[pi]; photos=unit.get("photos",[]); caps=unit.get("captions",[])
            for s in range(min(6,len(photos))):
                _insert_photo(pp,f"{PHOTO_WIDGETS[s]} {suf}",_unb64(photos[s]["b64"]))
                _set_field(pp,f"{CAPTION_WIDGETS[s]} {suf}",caps[s] if s<len(caps) else "")
            for s in range(len(photos),6): _hide_widget(pp,f"{PHOTO_WIDGETS[s]} {suf}")
            if len(photos) > 6:
                for cs in range(0,len(photos)-6,6):
                    chunk=photos[6+cs:6+cs+6]; np2=_make_extra_page(doc,n)
                    for s,ph in enumerate(chunk):
                        ir,cr=_EXTRA_RECTS[s]
                        np2.draw_rect(fitz.Rect(ir),color=None,fill=(1,1,1),overlay=True)
                        np2.insert_image(fitz.Rect(ir),stream=_unb64(ph["b64"]),keep_proportion=True,overlay=True)
                        ci=6+cs+s; cap=caps[ci] if ci<len(caps) else ""
                        if cap: np2.insert_textbox(fitz.Rect(cr),cap,fontsize=7,fontname="helv",color=(0,0,0),overlay=True)

    out=io.BytesIO(); doc.save(out,garbage=4,deflate=True,clean=True); doc.close()
    out.seek(0); return out.read()

# ── Unit helpers ───────────────────────────────────────────────────────────────
UNIT_TRACK_FIELDS = ["unit_id","serial","manufacturer","model","age",
                     "supply_temp","return_temp","delta_t","compressor_amps",
                     "blower_amps","condenser_fan_amps","voltage","refrigerant","observations"]

def _unit_progress(unit: dict) -> int:
    """Return 0-100 % of key fields filled for a unit."""
    filled = sum(1 for f in UNIT_TRACK_FIELDS if unit.get(f,"").strip())
    scope_ok = 1 if unit.get("scope") else 0
    total = len(UNIT_TRACK_FIELDS) + 1
    return int((filled + scope_ok) / total * 100)

def _unit_status(unit: dict) -> tuple:
    """Return (label, css_class) for a unit."""
    pct = _unit_progress(unit)
    if pct == 0:   return "Empty",       "tag-empty"
    if pct == 100: return "Complete ✓",  "tag-complete"
    return f"In Progress {pct}%",        "tag-progress"

def _progress_color(pct: int) -> str:
    if pct == 100: return "#639922"
    if pct >= 50:  return "#BA7517"
    return "#E24B4A"

# ── Data helpers ───────────────────────────────────────────────────────────────
def _empty():
    return {
        "project": {
            "customer":"","site":"","work_order":"",
            "date": datetime.now().strftime("%m/%d/%Y"),
            "techs":"","signature_date":"","signature":""
        },
        "units": {str(i): {"photos":[],"captions":[],"scope":[]} for i in range(1,16)},
        "num_units": 1
    }

def _sync_state_to_widgets(data, num_units):
    """Delete all widget keys then set from data — forces Streamlit to re-render fresh."""
    ALL_PROJ_KEYS = ["proj_customer","proj_site","proj_wo","proj_date",
                     "proj_techs","proj_signdate","proj_sign","num_units_input","site_map_upload"]
    for k in ALL_PROJ_KEYS:
        st.session_state.pop(k, None)
    for n in range(1, 16):
        for prefix in ["u_id_","u_ser_","u_age_","u_mfg_","u_mod_",
                       "u_sup_","u_ret_","u_dlt_","u_ca_","u_ba_",
                       "u_cfa_","u_vol_","u_ref_","u_obs_","u_pa_","u_pb_","u_pc_","u_up_"]:
            st.session_state.pop(f"{prefix}{n}", None)
        for si in range(8):
            st.session_state.pop(f"u_sc_{n}_{si}", None)
        for pi in range(20):
            st.session_state.pop(f"u_cap_{n}_{pi}", None)

    proj = data.get("project", {})
    st.session_state["proj_customer"]   = proj.get("customer","")
    st.session_state["proj_site"]       = proj.get("site","")
    st.session_state["proj_wo"]         = proj.get("work_order","")
    st.session_state["proj_date"]       = proj.get("date", datetime.now().strftime("%m/%d/%Y"))
    st.session_state["proj_techs"]      = proj.get("techs","")
    st.session_state["proj_signdate"]   = proj.get("signature_date","")
    st.session_state["proj_sign"]       = proj.get("signature","")
    st.session_state["num_units_input"] = num_units

    units = data.get("units", {})
    for n in range(1, 16):
        u = units.get(str(n), {})
        scope = u.get("scope", [])
        st.session_state[f"u_id_{n}"]  = u.get("unit_id","")
        st.session_state[f"u_ser_{n}"] = u.get("serial","")
        st.session_state[f"u_age_{n}"] = u.get("age","")
        st.session_state[f"u_mfg_{n}"] = u.get("manufacturer","")
        st.session_state[f"u_mod_{n}"] = u.get("model","")
        st.session_state[f"u_sup_{n}"] = u.get("supply_temp","")
        st.session_state[f"u_ret_{n}"] = u.get("return_temp","")
        st.session_state[f"u_dlt_{n}"] = u.get("delta_t","")
        st.session_state[f"u_ca_{n}"]  = u.get("compressor_amps","")
        st.session_state[f"u_ba_{n}"]  = u.get("blower_amps","")
        st.session_state[f"u_cfa_{n}"] = u.get("condenser_fan_amps","")
        st.session_state[f"u_vol_{n}"] = u.get("voltage","")
        st.session_state[f"u_ref_{n}"] = u.get("refrigerant","")
        st.session_state[f"u_obs_{n}"] = u.get("observations","")
        st.session_state[f"u_pa_{n}"]  = u.get("priority_a","")
        st.session_state[f"u_pb_{n}"]  = u.get("priority_b","")
        st.session_state[f"u_pc_{n}"]  = u.get("priority_c","")
        for si, item in enumerate(SCOPE_ITEMS):
            st.session_state[f"u_sc_{n}_{si}"] = (item in scope)

# ── Session state init ─────────────────────────────────────────────────────────
if "data" not in st.session_state:
    st.session_state.data           = _empty()
    st.session_state.draft_id       = None
    st.session_state.last_snapshot  = ""
    st.session_state.pdf_ready      = None
    st.session_state.num_units      = 1
    st.session_state.show_drafts    = False
    st.session_state.last_saved_ts  = ""
    st.session_state.active_unit    = 1
    st.session_state.confirm_new    = False
    st.session_state.pdf_preview    = b""
    _sync_state_to_widgets(st.session_state.data, 1)

# ── Keep data dict in sync with visible Streamlit widgets ─────────────────────
def _collect_widgets_to_data():
    """Collect latest widget values before Save/Generate so buttons never save stale data."""
    data = st.session_state.setdefault("data", _empty())
    proj = data.setdefault("project", {})
    proj["customer"] = st.session_state.get("proj_customer", proj.get("customer", ""))
    proj["site"] = st.session_state.get("proj_site", proj.get("site", ""))
    proj["work_order"] = st.session_state.get("proj_wo", proj.get("work_order", ""))
    proj["date"] = st.session_state.get("proj_date", proj.get("date", ""))
    proj["techs"] = st.session_state.get("proj_techs", proj.get("techs", ""))
    proj["signature_date"] = st.session_state.get("proj_signdate", proj.get("signature_date", ""))
    proj["signature"] = st.session_state.get("proj_sign", proj.get("signature", ""))

    num_units = int(st.session_state.get("num_units", data.get("num_units", 1)) or 1)
    data["num_units"] = num_units
    units = data.setdefault("units", {})
    for n in range(1, 16):
        unit = units.setdefault(str(n), {"photos": [], "captions": [], "scope": []})
        unit["unit_id"] = st.session_state.get(f"u_id_{n}", unit.get("unit_id", ""))
        unit["serial"] = st.session_state.get(f"u_ser_{n}", unit.get("serial", ""))
        unit["age"] = st.session_state.get(f"u_age_{n}", unit.get("age", ""))
        unit["manufacturer"] = st.session_state.get(f"u_mfg_{n}", unit.get("manufacturer", ""))
        unit["model"] = st.session_state.get(f"u_mod_{n}", unit.get("model", ""))
        unit["supply_temp"] = st.session_state.get(f"u_sup_{n}", unit.get("supply_temp", ""))
        unit["return_temp"] = st.session_state.get(f"u_ret_{n}", unit.get("return_temp", ""))
        unit["delta_t"] = st.session_state.get(f"u_dlt_{n}", unit.get("delta_t", ""))
        unit["compressor_amps"] = st.session_state.get(f"u_ca_{n}", unit.get("compressor_amps", ""))
        unit["blower_amps"] = st.session_state.get(f"u_ba_{n}", unit.get("blower_amps", ""))
        unit["condenser_fan_amps"] = st.session_state.get(f"u_cfa_{n}", unit.get("condenser_fan_amps", ""))
        unit["voltage"] = st.session_state.get(f"u_vol_{n}", unit.get("voltage", ""))
        unit["refrigerant"] = st.session_state.get(f"u_ref_{n}", unit.get("refrigerant", ""))
        unit["observations"] = st.session_state.get(f"u_obs_{n}", unit.get("observations", ""))
        unit["priority_a"] = st.session_state.get(f"u_pa_{n}", unit.get("priority_a", ""))
        unit["priority_b"] = st.session_state.get(f"u_pb_{n}", unit.get("priority_b", ""))
        unit["priority_c"] = st.session_state.get(f"u_pc_{n}", unit.get("priority_c", ""))
        unit["scope"] = [item for si, item in enumerate(SCOPE_ITEMS) if st.session_state.get(f"u_sc_{n}_{si}", item in unit.get("scope", []))]
        captions = unit.setdefault("captions", [])
        for pi in range(len(captions)):
            captions[pi] = st.session_state.get(f"u_cap_{n}_{pi}", captions[pi])
    st.session_state.data = data
    return data

# ── Auto-save ──────────────────────────────────────────────────────────────────
def _autosave(status="Draft"):
    _collect_widgets_to_data()
    st.session_state.data["num_units"] = st.session_state.num_units
    snap = json.dumps(st.session_state.data)
    if snap == st.session_state.last_snapshot: return
    if not st.session_state.draft_id:
        st.session_state.draft_id = str(uuid.uuid4())
    name = st.session_state.data.get("project",{}).get("customer","") or "Untitled"
    sheet_save(st.session_state.draft_id, name, st.session_state.data, status)
    st.session_state.last_snapshot = snap
    st.session_state.last_saved_ts = datetime.now().strftime("%I:%M %p")

def _load_draft(rid):
    loaded = sheet_load_one(rid)
    if not loaded:
        st.warning("Could not load this draft."); return
    saved_n = loaded.get("num_units", 0)
    u = loaded.get("units", {})
    filled  = sum(1 for i in range(1,16) if u.get(str(i),{}).get("unit_id","").strip())
    num = max(saved_n, filled, 1)
    _sync_state_to_widgets(loaded, num)
    st.session_state.data          = loaded
    st.session_state.draft_id      = rid
    st.session_state.last_snapshot = json.dumps(loaded)
    st.session_state.pdf_ready     = None
    st.session_state.show_drafts   = False
    st.session_state.num_units     = num
    st.rerun()

def _new_report():
    empty = _empty()
    _sync_state_to_widgets(empty, 1)
    st.session_state.data          = empty
    st.session_state.draft_id      = None
    st.session_state.last_snapshot = ""
    st.session_state.pdf_ready     = None
    st.session_state.num_units     = 1
    st.session_state.show_drafts   = False
    st.session_state.last_saved_ts = ""
    st.rerun()

# ── Validation ─────────────────────────────────────────────────────────────────
def _validate() -> list:
    errors = []
    proj = st.session_state.data.get("project", {})
    if not proj.get("customer","").strip(): errors.append("Customer Name is required")
    if not proj.get("site","").strip():     errors.append("Site Address is required")
    return errors


# ══════════════════════════════════════════════════════════════════════════════
# CLEAN FIELD TECHNICIAN UI
# ══════════════════════════════════════════════════════════════════════════════

# ── PDF first-page preview ─────────────────────────────────────────────────────
def _pdf_first_page_image(pdf_bytes: bytes) -> bytes:
    """Render first page of PDF to a JPEG thumbnail for preview."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[0]
        mat  = fitz.Matrix(1.5, 1.5)
        pix  = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        doc.close()
        return pix.tobytes("jpeg")
    except Exception:
        return b""

# ── Email PDF ──────────────────────────────────────────────────────────────────
def _send_pdf_email(to_addr: str, pdf_bytes: bytes, fname: str, customer: str) -> bool:
    """Send PDF as email attachment using SMTP credentials from st.secrets."""
    try:
        smtp_host = st.secrets.get("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(st.secrets.get("SMTP_PORT", 587))
        smtp_user = st.secrets["SMTP_USER"]
        smtp_pass = st.secrets["SMTP_PASS"]
        from_addr = st.secrets.get("SMTP_FROM", smtp_user)

        msg = MIMEMultipart()
        msg["From"]    = from_addr
        msg["To"]      = to_addr
        msg["Subject"] = f"PM Report – {customer}"

        body = f"Please find attached the Preventive Maintenance Report for {customer}.\n\nGenerated by MRB PM Report App."
        msg.attach(MIMEText(body, "plain"))

        part = MIMEBase("application", "octet-stream")
        part.set_payload(pdf_bytes)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{fname}"')
        msg.attach(part)

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, to_addr, msg.as_string())
        return True
    except Exception as e:
        st.error(f"Email failed: {e}")
        return False

# ── Copy unit ──────────────────────────────────────────────────────────────────
def _copy_unit(src_n: int, dst_n: int):
    """Copy unit src_n data into dst_n (photos excluded)."""
    _collect_widgets_to_data()
    src = copy.deepcopy(st.session_state.data.get("units",{}).get(str(src_n), {}))
    src["photos"]   = []
    src["captions"] = []
    st.session_state.data["units"][str(dst_n)] = src
    # Push into widget keys so screen updates
    for prefix, key in [("u_id_","unit_id"),("u_ser_","serial"),("u_age_","age"),
                        ("u_mfg_","manufacturer"),("u_mod_","model"),
                        ("u_sup_","supply_temp"),("u_ret_","return_temp"),("u_dlt_","delta_t"),
                        ("u_ca_","compressor_amps"),("u_ba_","blower_amps"),
                        ("u_cfa_","condenser_fan_amps"),("u_vol_","voltage"),
                        ("u_ref_","refrigerant"),("u_obs_","observations"),
                        ("u_pa_","priority_a"),("u_pb_","priority_b"),("u_pc_","priority_c")]:
        st.session_state[f"{prefix}{dst_n}"] = src.get(key, "")
    for si, item in enumerate(SCOPE_ITEMS):
        st.session_state[f"u_sc_{dst_n}_{si}"] = (item in src.get("scope", []))
    st.session_state.active_unit = dst_n


def _generate_pdf_now():
    _collect_widgets_to_data()
    errs = _validate()
    if errs:
        for e in errs:
            st.error(f"⚠️ {e}")
        return
    with st.spinner("Building PDF…"):
        pdf_bytes = fill_pdf(st.session_state.data)
    if pdf_bytes:
        cname = st.session_state.data.get("project",{}).get("customer","PM") or "PM"
        fname = cname.replace(" ","_").replace("/","-") + "_PM_Report.pdf"
        preview = _pdf_first_page_image(pdf_bytes)
        st.session_state.pdf_ready   = (pdf_bytes, fname)
        st.session_state.pdf_preview = preview
        _autosave("Completed")
        st.success("PDF ready — preview and download below.")

# Header
ts = st.session_state.get("last_saved_ts", "")
saved_text = "Saved to cloud " + ts if ts else "Ready — click Save Draft"
st.markdown(f"""
<div class="mrb-header">
  <div>
    <h2>🔧 MRB PM Report</h2>
    <p>Simple field report app · Save drafts · Add photos · Generate PDF</p>
  </div>
  <div class="mrb-saved-ts">{saved_text}</div>
</div>
""", unsafe_allow_html=True)

# Sidebar: unit navigation only
with st.sidebar:
    st.markdown("### 🔧 Units")
    for i in range(1, st.session_state.num_units + 1):
        u = st.session_state.data.get("units", {}).get(str(i), {})
        pct = _unit_progress(u)
        uid = u.get("unit_id") or f"Unit {i}"
        icon = "✅" if pct == 100 else ("🟡" if pct > 0 else "⚪")
        if st.button(f"{icon} {uid}", key=f"jump_unit_{i}", use_container_width=True,
                     type="primary" if i == st.session_state.active_unit else "secondary"):
            st.session_state.active_unit = i
            st.rerun()

    st.divider()
    if TEMPLATE.exists():
        st.success("✅ template.pdf ready")
    else:
        st.error("❌ template.pdf missing")

# Top action bar
ac1, ac2, ac3, ac4 = st.columns([1, 1, 1, 2])
with ac1:
    if st.button("💾 Save Draft", use_container_width=True):
        _autosave("Draft")
        st.success("Saved")
with ac2:
    if st.button("➕ New Report", use_container_width=True):
        st.session_state.confirm_new = True
with ac3:
    drafts_open = st.session_state.get("show_drafts", False)
    if st.button(f"📂 Drafts {'▲' if drafts_open else '▼'}", use_container_width=True):
        st.session_state.show_drafts = not drafts_open
        st.rerun()
with ac4:
    if st.button("📄 Generate PDF", type="primary", use_container_width=True):
        _generate_pdf_now()

# Confirm new report dialog
if st.session_state.get("confirm_new"):
    st.warning("⚠️ Start a new blank report? Any unsaved changes will be lost.")
    nc1, nc2, _ = st.columns([1, 1, 4])
    with nc1:
        if st.button("✅ Yes, start new", type="primary", use_container_width=True):
            st.session_state.confirm_new = False
            _new_report()
    with nc2:
        if st.button("Cancel", use_container_width=True):
            st.session_state.confirm_new = False
            st.rerun()

# ── Drafts panel (main screen, full width) ────────────────────────────────────
if st.session_state.get("show_drafts", False):
    st.markdown("---")
    st.markdown("### 📂 Saved Drafts")

    # Search box
    search = st.text_input("🔍 Search by customer name", placeholder="Type to filter…",
                           key="draft_search_box").strip().lower()

    all_rows = sheet_load_all()
    filtered = [r for r in reversed(all_rows)
                if not search or search in (r.get("name","")).lower()]

    if filtered:
        for row in filtered:
            rid   = row.get("id","")
            if not rid: continue
            rname = row.get("name","Untitled") or "Untitled"
            rsite = row.get("site","")
            rdate = row.get("updated_at","")
            rstat = row.get("status","Draft")
            rn    = row.get("units_count", 1)
            is_cur = (rid == st.session_state.draft_id)

            status_icon = "✅" if rstat=="Completed" else ("🔵" if rstat=="In Progress" else "🟡")
            cur_marker  = " ▶ (current)" if is_cur else ""

            with st.container():
                d1, d2, d3, d4 = st.columns([3, 2, 1, 1])
                with d1:
                    st.markdown(f"**{rname}{cur_marker}**")
                    if rsite:
                        st.caption(f"📍 {rsite[:40]}")
                with d2:
                    st.caption(f"{status_icon} {rstat}  ·  {rn} unit{'s' if rn!=1 else ''}")
                    st.caption(f"🕒 {rdate}")
                with d3:
                    if st.button("Open", key=f"open_{rid}", use_container_width=True,
                                 type="primary" if is_cur else "secondary"):
                        st.session_state.show_drafts = False
                        _load_draft(rid)
                with d4:
                    if st.button("🗑️", key=f"del_{rid}", use_container_width=True):
                        st.session_state[f"confirm_del_{rid}"] = True

                # Delete confirmation inline
                if st.session_state.get(f"confirm_del_{rid}"):
                    st.error(f"Delete **{rname}**? This cannot be undone.")
                    yes_col, no_col, _ = st.columns([1,1,4])
                    with yes_col:
                        if st.button("Yes, delete", key=f"yes_del_{rid}", type="primary"):
                            sheet_delete(rid)
                            st.session_state.pop(f"confirm_del_{rid}", None)
                            if rid == st.session_state.draft_id:
                                _new_report()
                            else:
                                try: sheet_load_all.clear()
                                except: pass
                                st.rerun()
                    with no_col:
                        if st.button("Cancel", key=f"no_del_{rid}"):
                            st.session_state.pop(f"confirm_del_{rid}", None)
                            st.rerun()
                st.divider()
    else:
        if search:
            st.info(f"No drafts matching '{search}'")
        else:
            st.info("No saved drafts yet. Save your first report to see it here.")
    st.markdown("---")

if st.session_state.get("pdf_ready"):
    pdf_bytes, fname = st.session_state.pdf_ready
    st.success("✅ PDF ready")

    # Preview + download row
    prev_col, dl_col = st.columns([1, 2])
    with prev_col:
        preview_img = st.session_state.get("pdf_preview", b"")
        if preview_img:
            st.image(preview_img, caption="Page 1 preview", use_container_width=True)
    with dl_col:
        st.download_button("⬇️ Download PM Report PDF", data=pdf_bytes,
                           file_name=fname, mime="application/pdf", use_container_width=True)
        # Email section
        st.markdown("**📧 Email PDF**")
        email_to = st.text_input("Recipient email", placeholder="client@example.com", key="email_to_field")
        if st.button("Send Email", use_container_width=True, key="send_email_btn"):
            if not email_to or "@" not in email_to:
                st.error("Enter a valid email address")
            elif "SMTP_USER" not in st.secrets or "SMTP_PASS" not in st.secrets:
                st.warning("⚠️ Email not configured. Add SMTP_USER and SMTP_PASS to your Streamlit secrets.")
            else:
                cname = st.session_state.data.get("project",{}).get("customer","PM") or "PM"
                with st.spinner("Sending…"):
                    ok = _send_pdf_email(email_to, pdf_bytes, fname, cname)
                if ok:
                    st.success(f"✅ Sent to {email_to}")

st.divider()

proj  = st.session_state.data.setdefault("project", {})
units = st.session_state.data.setdefault("units", {})

# Quick job status
project_ok = bool(proj.get("customer", "").strip() and proj.get("site", "").strip())
all_pcts = [_unit_progress(units.get(str(n), {})) for n in range(1, st.session_state.num_units + 1)]
overall = int(sum(all_pcts) / max(st.session_state.num_units, 1))
photo_count = sum(len(units.get(str(n), {}).get("photos", [])) for n in range(1, st.session_state.num_units + 1))
sc1, sc2, sc3, sc4 = st.columns(4)
sc1.metric("Project", "Complete" if project_ok else "Missing")
sc2.metric("Units", f"{sum(1 for p in all_pcts if p == 100)}/{st.session_state.num_units}")
sc3.metric("Photos", photo_count)
sc4.metric("Progress", f"{overall}%")

# Project section
with st.expander("📋 Project Information", expanded=True):
    pc1, pc2 = st.columns(2)
    with pc1:
        proj["customer"]       = st.text_input("Customer Name *", key="proj_customer")
        proj["site"]           = st.text_input("Site Address *", key="proj_site")
        proj["work_order"]     = st.text_input("Work Order #", key="proj_wo")
    with pc2:
        proj["date"]           = st.text_input("Date", key="proj_date")
        proj["techs"]          = st.text_input("Technician(s)", key="proj_techs")
        proj["signature_date"] = st.text_input("Signature Date", key="proj_signdate")
    proj["signature"] = st.text_input("Lead Technician Signature", key="proj_sign")

    st.markdown("**🗺️ Site Map / Roof Plan**")
    sm = st.file_uploader("Upload roof plan or aerial image", type=["png", "jpg", "jpeg"], key="site_map_upload")
    if sm:
        proj["site_map_b64"] = _b64(_normalise_image(sm))
        st.rerun()
    if proj.get("site_map_b64"):
        st.image(_unb64(proj["site_map_b64"]), caption="Site map", width=320)
        if st.button("Remove site map"):
            proj.pop("site_map_b64", None)
            st.rerun()

# Units control
st.markdown("### 🔧 Units")
unit_col1, unit_col2 = st.columns([1, 3])
with unit_col1:
    c1,c2 = st.columns(2)
    with c1:
        new_n = st.number_input("Number of units", min_value=1, max_value=15, step=1, key="num_units_input")
    with c2:
        st.write("")
        st.write("")
        if st.button("➕ Add Unit", use_container_width=True):
            if st.session_state.num_units < 15:
                st.session_state.num_units += 1
                st.session_state.active_unit = st.session_state.num_units
                st.rerun()

    if new_n != st.session_state.num_units:
        st.session_state.num_units = int(new_n)
        if st.session_state.active_unit > st.session_state.num_units:
            st.session_state.active_unit = st.session_state.num_units
        st.rerun()
with unit_col2:
    st.markdown(f"""
    <div class="progress-wrap" style="padding-top:22px;">
      <div style="font-size:12px;color:#5F5E5A;margin-bottom:4px;">Overall unit completion</div>
      <div class="progress-bar-bg"><div class="progress-bar-fill" style="width:{overall}%;background:{_progress_color(overall)};"></div></div>
    </div>
    """, unsafe_allow_html=True)

# Unit selector pills
unit_tabs = st.columns(min(st.session_state.num_units, 5))
for idx in range(1, st.session_state.num_units + 1):
    col = unit_tabs[(idx - 1) % len(unit_tabs)]
    u = units.get(str(idx), {})
    pct = _unit_progress(u)
    icon = "✅" if pct == 100 else ("🟡" if pct > 0 else "⚪")
    with col:
        if st.button(f"{icon} Unit {idx}", key=f"select_unit_{idx}", use_container_width=True, type="primary" if idx == st.session_state.active_unit else "secondary"):
            st.session_state.active_unit = idx
            st.rerun()

n = int(st.session_state.active_unit)
unit = units.setdefault(str(n), {"photos": [], "captions": [], "scope": []})
pct = _unit_progress(unit)
stag, scls = _unit_status(unit)

uh1, uh2, uh3 = st.columns([3, 1, 1])
with uh1:
    st.markdown(f"#### Unit {n} <span class='unit-tag {scls}'>{stag}</span>", unsafe_allow_html=True)
    st.markdown(f"""
<div class="progress-wrap">
  <div class="progress-bar-bg"><div class="progress-bar-fill" style="width:{pct}%;background:{_progress_color(pct)};"></div></div>
</div>
""", unsafe_allow_html=True)
with uh2:
    if n < st.session_state.num_units:
        st.write("")
        if st.button(f"📋 Copy → Unit {n+1}", key=f"copy_unit_{n}", use_container_width=True):
            _copy_unit(n, n+1)
            st.success(f"Unit {n} copied to Unit {n+1}")
            st.rerun()
with uh3:
    if st.session_state.num_units > 1:
        st.write("")
        if st.button("🗑️ Clear Unit", key=f"clear_unit_{n}", use_container_width=True):
            empty_unit = {"photos":[],"captions":[],"scope":[]}
            st.session_state.data["units"][str(n)] = empty_unit
            for prefix in ["u_id_","u_ser_","u_age_","u_mfg_","u_mod_","u_sup_","u_ret_",
                           "u_dlt_","u_ca_","u_ba_","u_cfa_","u_vol_","u_ref_","u_obs_",
                           "u_pa_","u_pb_","u_pc_"]:
                st.session_state.pop(f"{prefix}{n}", None)
            for si in range(8):
                st.session_state[f"u_sc_{n}_{si}"] = False
            st.rerun()

# Unit editing sections: less overwhelming
with st.expander("1. Unit Info", expanded=True):
    ui1, ui2, ui3 = st.columns(3)
    with ui1: unit["unit_id"] = st.text_input("Unit ID / Tag", key=f"u_id_{n}")
    with ui2: unit["serial"] = st.text_input("Serial #", key=f"u_ser_{n}")
    with ui3: unit["age"] = st.text_input("Age", key=f"u_age_{n}")
    um1, um2 = st.columns(2)
    with um1: unit["manufacturer"] = st.text_input("Manufacturer", key=f"u_mfg_{n}")
    with um2: unit["model"] = st.text_input("Model", key=f"u_mod_{n}")

with st.expander("2. PM Scope", expanded=True):
    ba, bb, bc = st.columns([1, 1, 4])
    with ba:
        if st.button("Select All", key=f"selall_{n}"):
            unit["scope"] = list(SCOPE_ITEMS)
            for si in range(8):
                st.session_state[f"u_sc_{n}_{si}"] = True
            st.rerun()
    with bb:
        if st.button("Clear All", key=f"clrall_{n}"):
            unit["scope"] = []
            for si in range(8):
                st.session_state[f"u_sc_{n}_{si}"] = False
            st.rerun()
    scope = unit.setdefault("scope", [])
    cols = st.columns(4)
    for si, item in enumerate(SCOPE_ITEMS):
        with cols[si % 4]:
            chk = st.checkbox(item, key=f"u_sc_{n}_{si}")
            if chk and item not in scope:
                scope.append(item)
            elif not chk and item in scope:
                scope.remove(item)

with st.expander("3. Operational Readings", expanded=True):
    ot1, ot2, ot3 = st.columns(3)
    with ot1: unit["supply_temp"] = st.text_input("Supply Temp (°F)", key=f"u_sup_{n}")
    with ot2: unit["return_temp"] = st.text_input("Return Temp (°F)", key=f"u_ret_{n}")
    with ot3: unit["delta_t"] = st.text_input("Delta-T (°F)", key=f"u_dlt_{n}")
    oa1, oa2, oa3 = st.columns(3)
    with oa1: unit["compressor_amps"] = st.text_input("Compressor Amps", key=f"u_ca_{n}")
    with oa2: unit["blower_amps"] = st.text_input("Blower Amps", key=f"u_ba_{n}")
    with oa3: unit["condenser_fan_amps"] = st.text_input("Condenser Fan Amps", key=f"u_cfa_{n}")
    ov1, ov2 = st.columns(2)
    with ov1: unit["voltage"] = st.text_input("Voltage (L-L / L-N)", key=f"u_vol_{n}")
    with ov2: unit["refrigerant"] = st.text_input("Refrigerant Type", key=f"u_ref_{n}")

with st.expander("4. Observations & Recommendations", expanded=True):
    unit["observations"] = st.text_area("General Observations", key=f"u_obs_{n}", height=90)
    unit["priority_a"] = st.text_area("🟢 Priority A – Monitor", key=f"u_pa_{n}", height=70)
    unit["priority_b"] = st.text_area("🟡 Priority B – Attention Recommended", key=f"u_pb_{n}", height=70)
    unit["priority_c"] = st.text_area("🔴 Priority C – Action Recommended", key=f"u_pc_{n}", height=70)

with st.expander("5. Photos", expanded=True):
    st.caption("On phone/tablet, Upload usually lets the technician choose Camera. Camera capture is also available below.")
    photos = unit.setdefault("photos", [])
    captions = unit.setdefault("captions", [])

    cam_col, up_col = st.columns(2)
    with cam_col:
        camera_photo = st.camera_input(f"Take photo – Unit {n}", key=f"u_cam_{n}")
    with up_col:
        uploaded = st.file_uploader(f"Upload photos – Unit {n}", type=["png", "jpg", "jpeg"], accept_multiple_files=True, key=f"u_up_{n}")

    if camera_photo:
        ib = _normalise_image(camera_photo)
        sig = (f"camera_unit_{n}_{len(photos)+1}.jpg", len(ib))
        if sig not in {(p.get("name"), p.get("size")) for p in photos}:
            photos.append({"name": sig[0], "size": sig[1], "b64": _b64(ib)})
            captions.append("")
            st.success("Camera photo added")
            st.rerun()

    if uploaded:
        existing = {(p.get("name"), p.get("size")) for p in photos}
        added = 0
        for f in uploaded:
            fk = (f.name, getattr(f, "size", None))
            if fk not in existing:
                ib = _normalise_image(f)
                photos.append({"name": f.name, "size": getattr(f, "size", None), "b64": _b64(ib)})
                captions.append("")
                existing.add(fk)
                added += 1
        if added:
            st.success(f"Added {added} photo(s)")
            st.rerun()

    while len(captions) < len(photos):
        captions.append("")

    if photos:
        for pi in range(0, len(photos), 2):
            gc1, gc2 = st.columns(2)
            for col, idx in zip([gc1, gc2], [pi, pi + 1]):
                if idx < len(photos):
                    with col:
                        slot = f"Slot {idx+1}" if idx < 6 else f"Extra {idx-5}"
                        st.image(_unb64(photos[idx]["b64"]), caption=slot, use_container_width=True)
                        captions[idx] = st.text_area("Photo notes / description", value=captions[idx], key=f"u_cap_{n}_{idx}", height=70)
                        if st.button("Remove", key=f"u_del_{n}_{idx}"):
                            photos.pop(idx)
                            if idx < len(captions):
                                captions.pop(idx)
                            st.rerun()
    else:
        st.info("No photos yet.")

# Next/previous quick buttons
st.divider()
nav1, nav2, nav3 = st.columns([1, 1, 2])
with nav1:
    if n > 1 and st.button("← Previous Unit", use_container_width=True):
        st.session_state.active_unit = n - 1
        st.rerun()
with nav2:
    if n < st.session_state.num_units and st.button("Next Unit →", use_container_width=True):
        st.session_state.active_unit = n + 1
        st.rerun()
with nav3:
    if st.button("📄 Generate PDF", key="gen_bottom_clean", type="primary", use_container_width=True):
        _generate_pdf_now()

# No auto-save on every render.
# This avoids Google Sheets quota errors. Use Save Draft or Generate PDF.
