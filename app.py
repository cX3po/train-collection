#!/usr/bin/env python3
"""Dad's Train Collection - Streamlit App with AX Chat"""
import streamlit as st
import sqlite3
import os
import re
import io
import pandas as pd
from datetime import datetime

# Load API key
def load_api_key():
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY", "")
        if key: return key
    except: pass
    env_path = os.path.expanduser("~/axiom/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.strip().split("=", 1)[1].strip('"').strip("'")
    return os.getenv("ANTHROPIC_API_KEY", "")

API_KEY = load_api_key()
if API_KEY: os.environ["ANTHROPIC_API_KEY"] = API_KEY

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "trains.db")

NOTABLE_BRANDS = {"lionel","american flyer","marx","ives","mth","williams","k-line",
    "weaver","atlas","bachmann","kato","athearn","walthers","brass","tenshodo",
    "overland","3rd rail","sunset","rivarossi","marklin","fleischmann","hornby"}

def is_notable(item_name, brand, era=""):
    name = (item_name or "").lower()
    b = (brand or "").lower()
    if any(nb in b for nb in NOTABLE_BRANDS): return True
    if any(kw in name for kw in ["hudson","berkshire","gp-7","f3","gp-9","alco","emd",
        "steam","diesel","brass","prewar","standard gauge","blue comet"]): return True
    if era and any(kw in era.lower() for kw in ["prewar","1930","1940","1950","brass"]): return True
    return False

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS trains (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_name TEXT, brand TEXT, scale TEXT, era TEXT,
        item_type TEXT, catalog_number TEXT, quantity INTEGER DEFAULT 1,
        condition TEXT, has_box INTEGER DEFAULT 0,
        estimated_value TEXT, value_notes TEXT,
        location TEXT, description TEXT,
        is_notable INTEGER DEFAULT 0, notes TEXT, last_updated TEXT
    )""")
    conn.commit(); conn.close()

def get_db():
    init_db()
    return sqlite3.connect(DB_PATH)

def chat_with_ax(message, context="", history=None):
    import requests
    if not API_KEY: return "No API key. Add ANTHROPIC_API_KEY in settings."
    system = """You are AX, a friendly and knowledgeable AI assistant for the family.
You're an expert in model trains and railroadiana - Lionel, American Flyer, Marx, HO, N, O gauge, brass, vintage tinplate.
You help identify trains, estimate values, advise on selling via auction houses or eBay.
You know Heritage Auctions, Stout Auctions, and other train auction specialists.
Be warm, practical, and honest about what you know. Never invent prices."""
    if context: system += f"\n\nContext: {context}"
    messages = []
    if history:
        for msg in history[-10:]:
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": message})
    try:
        resp = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 1024,
                  "system": system, "messages": messages}, timeout=30)
        if resp.ok: return resp.json()["content"][0]["text"]
        return f"API error: {resp.status_code}"
    except Exception as e: return f"Error: {e}"

def generate_sell_listing(item_info, method="eBay"):
    if method == "eBay":
        return chat_with_ax(f"Create a ready-to-use eBay listing for this model train item: {item_info}. Include title (80 chars), description, category, starting price, and shipping notes for fragile model trains.")
    elif method == "Auction House":
        return chat_with_ax(f"Write a professional submission to a train auction house (like Stout Auctions or Heritage) for: {item_info}. Include item description, condition, and ask about consignment terms.")
    else:
        return chat_with_ax(f"Write a description for selling at a train show or hobby shop: {item_info}. Include fair asking price and negotiation tips.")

PHOTO_GUIDE_SVG = """
<svg viewBox="0 0 500 300" xmlns="http://www.w3.org/2000/svg" style="max-width:500px;font-family:sans-serif;">
  <rect x="100" y="80" width="300" height="100" rx="5" fill="#d4c4a8" stroke="#333" stroke-width="2"/>
  <text x="250" y="135" text-anchor="middle" font-size="14" fill="#666">MODEL TRAIN</text>
  <circle cx="150" cy="180" r="15" fill="none" stroke="#333" stroke-width="2"/>
  <circle cx="250" cy="180" r="15" fill="none" stroke="#333" stroke-width="2"/>
  <circle cx="350" cy="180" r="15" fill="none" stroke="#333" stroke-width="2"/>
  <line x1="90" y1="130" x2="40" y2="100" stroke="#e74c3c" stroke-width="1.5"/>
  <text x="5" y="95" font-size="10" fill="#e74c3c">Brand/Logo?</text>
  <line x1="410" y1="130" x2="450" y2="100" stroke="#3498db" stroke-width="1.5"/>
  <text x="415" y="95" font-size="10" fill="#3498db">Catalog #?</text>
  <line x1="250" y1="75" x2="250" y2="50" stroke="#2ecc71" stroke-width="1.5"/>
  <text x="250" y="45" text-anchor="middle" font-size="10" fill="#2ecc71">Paint condition?</text>
  <line x1="150" y1="200" x2="100" y2="230" stroke="#9b59b6" stroke-width="1.5"/>
  <text x="30" y="235" font-size="10" fill="#9b59b6">Wheels/trucks</text>
  <text x="250" y="270" text-anchor="middle" font-size="11" font-weight="bold" fill="#333">Photo flat, good light, show markings</text>
</svg>
"""

def main():
    st.set_page_config(page_title="Train Collection", page_icon="\U0001f682", layout="wide")

    if "app_title" not in st.session_state:
        st.session_state.app_title = "Dad's Train Collection"
    st.title(st.session_state.app_title)

    init_db()
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM trains").fetchone()[0]
    total_qty = conn.execute("SELECT COALESCE(SUM(quantity),0) FROM trains").fetchone()[0]
    notable = conn.execute("SELECT COUNT(*) FROM trains WHERE is_notable=1").fetchone()[0]
    with_values = conn.execute("SELECT COUNT(*) FROM trains WHERE estimated_value!='' AND estimated_value IS NOT NULL").fetchone()[0]
    brands = [r[0] for r in conn.execute("SELECT DISTINCT brand FROM trains WHERE brand!='' ORDER BY brand").fetchall()]
    scales = [r[0] for r in conn.execute("SELECT DISTINCT scale FROM trains WHERE scale!='' ORDER BY scale").fetchall()]
    conn.close()

    with st.sidebar:
        new_title = st.text_input("Collection Title", st.session_state.app_title)
        if new_title != st.session_state.app_title:
            st.session_state.app_title = new_title; st.rerun()
        st.markdown("---")
        search = st.text_input("Search", "")
        brand_filter = st.selectbox("Brand", ["All"] + brands)
        scale_filter = st.selectbox("Scale", ["All"] + scales)
        notable_only = st.checkbox("Valuable only", False)
        st.markdown("---")
        st.markdown(f"**Items:** {total:,} | **Pieces:** {total_qty:,}")
        st.markdown(f"**Notable:** {notable:,} | **Priced:** {with_values:,}")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "Collection", "Scan Items", "Ask AX", "Sell Items", "Stats", "Import/Export"
    ])

    # ── Collection ──
    with tab1:
        conn = get_db()
        q = "SELECT * FROM trains WHERE 1=1"
        p = []
        if search:
            q += " AND (LOWER(item_name) LIKE ? OR LOWER(brand) LIKE ? OR LOWER(catalog_number) LIKE ?)"
            s = f"%{search.lower()}%"; p.extend([s,s,s])
        if brand_filter != "All": q += " AND LOWER(brand) LIKE ?"; p.append(f"%{brand_filter.lower()}%")
        if scale_filter != "All": q += " AND scale=?"; p.append(scale_filter)
        if notable_only: q += " AND is_notable=1"
        q += " ORDER BY brand, item_name"
        df = pd.read_sql_query(q, conn, params=p); conn.close()

        if df.empty:
            if total == 0: st.info("No items yet. Use Scan Items to photograph your trains, or Import/Export to paste data.")
            else: st.info("No items match filters.")
        else:
            st.markdown(f"**{len(df)} items** | Edit directly, then Save.")
            edit_cols = ["id","item_name","brand","scale","era","item_type","catalog_number",
                        "quantity","condition","has_box","estimated_value","location","notes"]
            avail = [c for c in edit_cols if c in df.columns]
            col_config = {"id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
                         "has_box": st.column_config.CheckboxColumn("Box?")}
            edited = st.data_editor(df[avail], column_config=col_config,
                                   use_container_width=True, height=600, num_rows="dynamic")
            if st.button("Save Changes", type="primary"):
                conn = get_db()
                for _, row in edited.iterrows():
                    if pd.notna(row.get("id")):
                        conn.execute("""UPDATE trains SET item_name=?,brand=?,scale=?,era=?,item_type=?,
                            catalog_number=?,quantity=?,condition=?,has_box=?,estimated_value=?,
                            location=?,notes=?,last_updated=? WHERE id=?""",
                            (row.get("item_name",""),row.get("brand",""),row.get("scale",""),
                             row.get("era",""),row.get("item_type",""),row.get("catalog_number",""),
                             row.get("quantity",1),row.get("condition",""),
                             1 if row.get("has_box") else 0,
                             row.get("estimated_value",""),row.get("location",""),
                             row.get("notes",""),datetime.now().isoformat(),row["id"]))
                conn.commit(); conn.close(); st.success("Saved!")

    # ── Scan Items ──
    with tab2:
        st.subheader("Scan & Identify Trains")
        with st.expander("Photo tips for best identification"):
            st.markdown(PHOTO_GUIDE_SVG, unsafe_allow_html=True)
            st.markdown("""
            1. **Flat surface, good lighting** - no shadows
            2. **Show the brand/logo** - usually on the side or bottom
            3. **Show any numbers** - catalog numbers on the bottom
            4. **Multiple angles** - side, top, bottom for full ID
            5. **Include the box** if you have it - boxes add significant value
            """)

        scan_type = st.radio("Scan mode", ["Single Item ID", "Room/Shelf Scan (multiple items)"], horizontal=True)
        photos = st.file_uploader("Upload photo(s)", type=["jpg","jpeg","png","webp"], accept_multiple_files=True)

        if photos and st.button("Scan", type="primary"):
            if not API_KEY:
                st.error("No API key configured.")
            else:
                with st.spinner("AX is identifying your trains..."):
                    try:
                        from engine import VisionEngine
                        from train_prompts import TRAIN_IDENTIFIER, TRAIN_ROOM_SCAN
                        engine = VisionEngine(provider="haiku")
                        img_bytes = photos[0].read()
                        prompt = TRAIN_IDENTIFIER if scan_type.startswith("Single") else TRAIN_ROOM_SCAN
                        results = engine.analyze(img_bytes, prompt)
                        if results:
                            st.success(f"Found {len(results)} item(s)!")
                            col1, col2 = st.columns([1, 2])
                            with col1: st.image(img_bytes, width=300)
                            with col2:
                                for r in results:
                                    d = r.raw
                                    st.markdown(f"### {d.get('item_name','Unknown')}")
                                    st.markdown(f"**Brand:** {d.get('brand','')} | **Scale:** {d.get('scale','')} | **Era:** {d.get('era','')}")
                                    st.markdown(f"**Condition:** {d.get('condition','')} | **Box:** {'Yes' if d.get('has_original_box') else 'No'}")
                                    if d.get('estimated_value'):
                                        st.markdown(f"**Est. Value:** ${d['estimated_value']}")
                                    st.markdown(f"*{d.get('value_notes','')}*")
                                    if st.button(f"Add to Collection", key=f"add_{d.get('item_name','')}"):
                                        conn = get_db()
                                        conn.execute("""INSERT INTO trains (item_name,brand,scale,era,item_type,
                                            catalog_number,condition,has_box,estimated_value,value_notes,
                                            is_notable,last_updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                                            (d.get("item_name",""),d.get("brand",""),d.get("scale",""),
                                             d.get("era",""),d.get("type",""),d.get("catalog_number",""),
                                             d.get("condition",""),1 if d.get("has_original_box") else 0,
                                             str(d.get("estimated_value","")) if d.get("estimated_value") else "",
                                             d.get("value_notes",""),
                                             1 if is_notable(d.get("item_name",""),d.get("brand",""),d.get("era","")) else 0,
                                             datetime.now().isoformat()))
                                        conn.commit(); conn.close(); st.success("Added!")
                                    st.markdown("---")
                        else:
                            st.warning("Could not identify items. Try a clearer photo.")
                    except Exception as e:
                        st.error(f"Error: {e}")

    # ── Ask AX ──
    with tab3:
        st.subheader("Ask AX About Trains")
        st.caption("Your family AI assistant - ask about train identification, values, selling, anything.")

        if "train_chat" not in st.session_state: st.session_state.train_chat = []
        for msg in st.session_state.train_chat:
            with st.chat_message(msg["role"], avatar="\U0001f916" if msg["role"]=="assistant" else "\U0001f9d1"):
                st.markdown(msg["content"])

        if prompt := st.chat_input("Ask AX about trains..."):
            st.session_state.train_chat.append({"role":"user","content":prompt})
            with st.chat_message("user", avatar="\U0001f9d1"): st.markdown(prompt)
            with st.chat_message("assistant", avatar="\U0001f916"):
                with st.spinner("AX is thinking..."):
                    ctx = f"Collection has {total} items. Brands: {', '.join(brands[:5])}." if brands else ""
                    resp = chat_with_ax(prompt, ctx, st.session_state.train_chat)
                    st.markdown(resp)
                    st.session_state.train_chat.append({"role":"assistant","content":resp})

        if st.session_state.train_chat and st.button("Clear Chat"):
            st.session_state.train_chat = []; st.rerun()

    # ── Sell Items ──
    with tab4:
        st.subheader("Sell Your Trains")
        sell_col1, sell_col2 = st.columns(2)

        with sell_col1:
            st.markdown("### Create a Listing")
            sell_method = st.radio("Sell via", ["eBay","Auction House (Stout, Heritage)","Train Show/Hobby Shop","Sell Entire Collection"], horizontal=False)
            sell_name = st.text_input("Item Name", placeholder="e.g. Lionel 2056 Hudson Steam Locomotive")
            sc1,sc2 = st.columns(2)
            with sc1: sell_brand = st.text_input("Brand", placeholder="Lionel")
            with sc2: sell_condition = st.selectbox("Condition", ["Mint","Like New","Excellent","Good","Fair","Poor"])
            sell_box = st.checkbox("Has original box")
            sell_notes = st.text_input("Notes", placeholder="With tender, original box, runs well")

            if st.button("Generate Listing", type="primary"):
                if sell_name or sell_method == "Sell Entire Collection":
                    info = f"{sell_brand} {sell_name} - Condition: {sell_condition}. Box: {'Yes' if sell_box else 'No'}. {sell_notes}"
                    if sell_method == "Sell Entire Collection":
                        info = f"Complete train collection of {total} items. Brands include: {', '.join(brands[:10])}."
                        with st.spinner("AX is preparing your collection sale plan..."):
                            listing = chat_with_ax(
                                f"Help me sell my entire model train collection: {info}. "
                                f"Give me a step-by-step plan: 1) How to inventory for sale, "
                                f"2) Best auction houses for train collections (Stout Auctions, etc), "
                                f"3) How to get an appraisal, 4) Whether to sell as lot or individually, "
                                f"5) Expected timeline and costs. Be practical and specific.")
                    else:
                        with st.spinner("AX is writing your listing..."):
                            listing = generate_sell_listing(info, sell_method.split(" ")[0])
                    st.session_state["train_listing"] = listing

        with sell_col2:
            st.markdown("### Your Listing")
            if "train_listing" in st.session_state:
                st.markdown(st.session_state["train_listing"])
                st.download_button("Download as Text", st.session_state["train_listing"],
                    "train_listing.txt", "text/plain")
            else:
                st.info("Fill in details and click Generate Listing.")

    # ── Stats ──
    with tab5:
        st.subheader("Collection Statistics")
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Items", f"{total:,}")
        c2.metric("Pieces", f"{total_qty:,}")
        c3.metric("Notable", f"{notable:,}")
        c4.metric("Priced", f"{with_values:,}")
        if total > 0:
            conn = get_db()
            bd = pd.read_sql_query("SELECT brand, COUNT(*) as count FROM trains WHERE brand!='' GROUP BY brand ORDER BY count DESC LIMIT 10", conn)
            sd = pd.read_sql_query("SELECT scale, COUNT(*) as count FROM trains WHERE scale!='' GROUP BY scale ORDER BY count DESC", conn)
            td = pd.read_sql_query("SELECT item_type, COUNT(*) as count FROM trains WHERE item_type!='' GROUP BY item_type ORDER BY count DESC", conn)
            conn.close()
            if not bd.empty: st.subheader("By Brand"); st.bar_chart(bd.set_index("brand")["count"])
            if not sd.empty: st.subheader("By Scale"); st.bar_chart(sd.set_index("scale")["count"])
            if not td.empty: st.subheader("By Type"); st.bar_chart(td.set_index("item_type")["count"])

    # ── Import/Export ──
    with tab6:
        st.subheader("Import / Export")
        imp_col, exp_col = st.columns(2)

        with imp_col:
            st.markdown("### Import Items")
            st.markdown("Paste tab-separated: `Name | Brand | Scale | Era | Type | Catalog# | Qty | Condition | Box | Value | Location | Notes`")
            paste = st.text_area("Paste data", height=200)
            if st.button("Import", type="primary"):
                if paste.strip():
                    conn = get_db(); count = 0
                    for line in paste.strip().split('\n'):
                        parts = line.split('\t')
                        while len(parts) < 12: parts.append("")
                        try: qty = int(parts[6].strip() or "1")
                        except: qty = 1
                        conn.execute("""INSERT INTO trains (item_name,brand,scale,era,item_type,
                            catalog_number,quantity,condition,has_box,estimated_value,location,notes,is_notable)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (parts[0].strip(),parts[1].strip(),parts[2].strip(),parts[3].strip(),
                             parts[4].strip(),parts[5].strip(),qty,parts[7].strip(),
                             1 if parts[8].strip().lower() in ("yes","1","true","y") else 0,
                             parts[9].strip(),parts[10].strip(),parts[11].strip(),
                             1 if is_notable(parts[0].strip(),parts[1].strip(),parts[3].strip()) else 0))
                        count += 1
                    conn.commit(); conn.close(); st.success(f"Imported {count} items"); st.rerun()

            uploaded = st.file_uploader("Or upload CSV/TSV", type=["csv","tsv","txt"])
            if uploaded:
                content = uploaded.read().decode('utf-8', errors='replace')
                # Same import logic
                st.info("File uploaded - paste contents above or use CSV import in future update.")

        with exp_col:
            st.markdown("### Export")
            if total > 0:
                conn = get_db()
                all_df = pd.read_sql_query("SELECT * FROM trains ORDER BY brand, item_name", conn); conn.close()
                csv_buf = io.StringIO(); all_df.to_csv(csv_buf, index=False)
                st.download_button("Download CSV", csv_buf.getvalue(), "train_collection.csv", "text/csv")
                json_str = all_df.to_json(orient="records", indent=2)
                st.download_button("Download JSON", json_str, "train_collection.json", "application/json")

            st.markdown("---")
            if total > 0 and st.button("Clear All"):
                conn = get_db(); conn.execute("DELETE FROM trains"); conn.commit(); conn.close()
                st.success("Cleared."); st.rerun()

if __name__ == "__main__":
    main()
