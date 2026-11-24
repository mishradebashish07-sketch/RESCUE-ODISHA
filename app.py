import streamlit as st
import folium
from streamlit_folium import st_folium
import requests

st.set_page_config(page_title="Rescue Odisha - Command Center", layout="wide")

st.title("🌊 RESCUE ODISHA — COMMAND CENTER")
st.caption("Live Emergency Operations & ODRAF Dispatch Monitor | Innovate Odisha")

# --- LIVE RENDER BACKEND URL ---
API_BASE = "https://rescue-odisha.onrender.com/api/v1"

# --- KNOWN UNITS & SHELTERS ---
odraf_units = [
    {"name": "ODRAF Unit 3 (Inflatable Boat Team)", "loc": [21.4800, 86.9100]},
    {"name": "ODRAF Unit 1 (Bhubaneswar Rapid Action)", "loc": [20.2961, 85.8200]}
]

shelters = [
    {"name": "Chandipur Cyclone Shelter (Baleswar)", "loc": [21.4680, 87.0130]},
    {"name": "Barabati Multipurpose Shelter (Cuttack)", "loc": [20.4812, 85.8732]},
    {"name": "Chhatrapur Relief Camp (Ganjam)", "loc": [19.3550, 84.9920]},
    {"name": "Puri Coastal Cyclone Shelter (Puri)", "loc": [19.7983, 85.8249]},
    {"name": "Paradip Port Multipurpose Shelter (Jagatsinghpur)", "loc": [20.3166, 86.6114]},
    {"name": "Rajnagar Coastal Shelter (Kendrapara)", "loc": [20.5736, 86.7267]},
    {"name": "Dhamra Relief Shelter (Bhadrak)", "loc": [20.7931, 86.9061]},
    {"name": "Bhubaneswar Unit-6 Relief Center (Khordha)", "loc": [20.2689, 85.8266]},
    {"name": "Hirakud Flood Shelter (Sambalpur)", "loc": [21.4669, 83.9812]},
    {"name": "Baripada Multipurpose Shelter (Mayurbhanj)", "loc": [21.9347, 86.7337]},
    {"name": "Jajpur Town Flood Relief Camp (Jajpur)", "loc": [20.8484, 86.3377]},
    {"name": "Rayagada Emergency Relief Center (Rayagada)", "loc": [19.1712, 83.4163]}
]

# --- FETCH LIVE DISPATCHES FROM MYSQL ---
def fetch_dispatches():
    try:
        res = requests.get(f"{API_BASE}/dispatches", timeout=30)
        if res.status_code == 200:
            return res.json(), None
        return [], f"Backend returned {res.status_code}"
    except Exception as e:
        return [], str(e)

if "live_alerts" not in st.session_state:
    alerts, err = fetch_dispatches()
    st.session_state.live_alerts = alerts
    if err:
        st.sidebar.warning(f"Initial load failed: {err}")

# --- SIDEBAR CONTROLS ---
st.sidebar.header("🎛️ Command Controls")

if st.sidebar.button("🔄 Refresh Live Citizen Alerts", use_container_width=True):
    with st.sidebar:
        with st.spinner("Fetching latest SOS alerts..."):
            alerts, err = fetch_dispatches()
    if err:
        st.sidebar.error(f"Could not refresh: {err}")
    else:
        st.session_state.live_alerts = alerts
        st.sidebar.success(f"Synced {len(alerts)} active alerts")

status_filter = st.sidebar.multiselect(
    "Filter by Status",
    options=["ASSIGNED", "QUEUED"],
    default=["ASSIGNED", "QUEUED"]
)

st.sidebar.markdown("---")

if st.sidebar.button("🚤 Reset ODRAF Availability", use_container_width=True):
    try:
        with st.sidebar:
            with st.spinner("Resetting ODRAF units..."):
                r = requests.post(f"{API_BASE}/responders/reset", timeout=60)
        if r.status_code == 200:
            st.sidebar.success("All ODRAF teams are now available!")
        else:
            st.sidebar.error(f"Could not reset responders ({r.status_code}).")
    except Exception as e:
        st.sidebar.error(f"Failed to reset: {e}")

# Filter alerts
all_alerts = st.session_state.live_alerts
filtered_alerts = [a for a in all_alerts if a.get("status") in status_filter]

# --- TOP KPI METRICS ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total SOS Alerts", len(all_alerts))
col2.metric("Teams Assigned", sum(1 for a in all_alerts if a.get("status") == "ASSIGNED"))
col3.metric("Queued (Needs Unit)", sum(1 for a in all_alerts if a.get("status") == "QUEUED"))
col4.metric("Critical (Severity 4–5)", sum(1 for a in all_alerts if (a.get("severity_score") or 0) >= 4))

# --- BUILD LIVE ODISHA MAP ---
m = folium.Map(location=[20.55, 85.85], zoom_start=7)

# 1. Base Shelters (Green)
for s in shelters:
    folium.Marker(
        s["loc"],
        popup=s["name"],
        tooltip=s["name"],
        icon=folium.Icon(color="green", icon="home")
    ).add_to(m)

# 2. Base ODRAF Units (Blue)
for r in odraf_units:
    folium.Marker(
        r["loc"],
        popup=r["name"],
        tooltip=r["name"],
        icon=folium.Icon(color="blue", icon="info-sign")
    ).add_to(m)

# 3. Live Citizen SOS Pins & Dispatch Routes
for item in filtered_alerts:
    lat = item.get("latitude")
    lon = item.get("longitude")
    if lat is None or lon is None:
        continue

    popup_html = (
        f"<b>🚨 {item.get('category', 'SOS')} (Sev {item.get('severity_score', '-')})</b><br>"
        f"<b>Status:</b> {item.get('status')}<br>"
        f"<b>Phone:</b> {item.get('reporter_contact', 'N/A')}<br>"
        f"<b>Message:</b> {item.get('raw_message', '')}<br>"
        f"<b>Team:</b> {item.get('team_name') or 'Queued'}<br>"
        f"<b>Shelter:</b> {item.get('shelter_name') or 'N/A'}"
    )

    folium.Marker(
        [lat, lon],
        popup=folium.Popup(popup_html, max_width=280),
        tooltip=f"SOS: {item.get('category')} ({item.get('status')})",
        icon=folium.Icon(color="red", icon="flag")
    ).add_to(m)

    # Route from assigned ODRAF unit to SOS site
    team_name = item.get("team_name")
    if team_name:
        unit = next((u for u in odraf_units if team_name.lower() in u["name"].lower() or u["name"].lower() in team_name.lower()), None)
        if unit:
            folium.PolyLine(
                locations=[unit["loc"], [lat, lon]],
                color="blue",
                weight=3,
                opacity=0.8,
                tooltip=f"ODRAF Dispatch: {team_name}"
            ).add_to(m)

    # Dashed evacuation route from SOS site to nearest shelter
    shelter_name = item.get("shelter_name")
    if shelter_name:
        sh = next((s for s in shelters if shelter_name.lower() in s["name"].lower() or s["name"].lower() in shelter_name.lower()), None)
        if sh:
            folium.PolyLine(
                locations=[[lat, lon], sh["loc"]],
                color="green",
                weight=2,
                dash_array="6, 8",
                tooltip=f"Evacuation Route: {shelter_name}"
            ).add_to(m)

st_folium(m, width=1200, height=540, key=f"cmd_map_{len(filtered_alerts)}")

# --- LIVE DISPATCH TABLE ---
st.subheader("📋 Live Incoming SOS Dispatches")
if filtered_alerts:
    table_rows = [
        {
            "Status": a.get("status"),
            "Severity": a.get("severity_score"),
            "Category": a.get("category"),
            "Reporter Phone": a.get("reporter_contact"),
            "Message": a.get("raw_message"),
            "Assigned Team": a.get("team_name") or "QUEUED",
            "Nearest Shelter": a.get("shelter_name") or "N/A",
            "Lat": round(a["latitude"], 4) if a.get("latitude") is not None else None,
            "Lon": round(a["longitude"], 4) if a.get("longitude") is not None else None,
        }
        for a in filtered_alerts
    ]
    st.dataframe(table_rows, use_container_width=True, hide_index=True)
else:
    st.info("No active SOS alerts found. Submit an SOS from the Citizen App and click 'Refresh Live Citizen Alerts'.")
