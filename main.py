import math
import os
import uuid
from typing import Optional

import requests
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

# --- DATABASE SETUP (Built-in SQLite — 100% Free, No External DB Needed) ---
DATABASE_URL = "sqlite:///./rescue_odisha.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- DISTANCE & CELL TOWER VERIFICATION (Unwired Labs / OpenCelliD) ---
UNWIRED_TOKEN = os.getenv("UNWIREDLABS_TOKEN")
UNWIRED_URL = "https://us1.unwiredlabs.com/v2/process"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


def verify_cell_tower(
    gps_lat: Optional[float],
    gps_lon: Optional[float],
    mcc: int,
    mnc: int,
    lac: int,
    cid: int,
    radio: str = "lte"
) -> dict:
    if not UNWIRED_TOKEN:
        return {"verified": False, "reason": "UNWIREDLABS_TOKEN not set in Render Environment"}

    req_payload = {
        "token": UNWIRED_TOKEN,
        "radio": radio,
        "mcc": mcc,
        "mnc": mnc,
        "cells": [{"lac": lac, "cid": cid}],
        "address": 1
    }
    try:
        res = requests.post(UNWIRED_URL, json=req_payload, timeout=15)
        data = res.json()
    except Exception as e:
        return {"verified": False, "reason": f"Cell API error: {e}"}

    if data.get("status") != "ok":
        return {"verified": False, "reason": data.get("message", "Cell tower not found")}

    tower_lat = float(data["lat"])
    tower_lon = float(data["lon"])
    accuracy_m = data.get("accuracy", 5000)
    max_radius_km = max(accuracy_m / 1000.0, 10.0)  # 10 km minimum tolerance

    distance_km = None
    verified = True
    if gps_lat is not None and gps_lon is not None:
        distance_km = round(haversine_km(gps_lat, gps_lon, tower_lat, tower_lon), 2)
        verified = distance_km <= max_radius_km

    return {
        "verified": verified,
        "tower_lat": tower_lat,
        "tower_lon": tower_lon,
        "distance_from_tower_km": distance_km,
        "tower_accuracy_m": accuracy_m,
        "approx_address": data.get("address")
    }


# --- AUTO-CREATE TABLES & SEED DATA ON STARTUP ---
def init_db():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS shelters (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                contact_phone TEXT,
                total_capacity INTEGER DEFAULT 500,
                current_occupancy INTEGER DEFAULT 0,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL
            );
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS responder_units (
                id TEXT PRIMARY KEY,
                team_name TEXT NOT NULL,
                team_type TEXT,
                is_available INTEGER DEFAULT 1,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL
            );
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS distress_reports (
                id TEXT PRIMARY KEY,
                reporter_contact TEXT,
                raw_message TEXT,
                category TEXT,
                severity_score INTEGER,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                status TEXT,
                assigned_responder_id TEXT,
                nearest_shelter_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))

        # Seed Shelters across Odisha
        conn.execute(text("""
            INSERT OR IGNORE INTO shelters (id, name, contact_phone, total_capacity, current_occupancy, latitude, longitude) VALUES
            ('sh-1', 'Chandipur Cyclone Shelter (Baleswar)', '+91-6782-270001', 500, 50, 21.4680, 87.0130),
            ('sh-2', 'Barabati Multipurpose Shelter (Cuttack)', '+91-671-2300002', 600, 80, 20.4812, 85.8732),
            ('sh-3', 'Chhatrapur Relief Camp (Ganjam)', '+91-6811-263003', 450, 40, 19.3550, 84.9920),
            ('sh-4', 'Puri Coastal Cyclone Shelter (Puri)', '+91-6752-222004', 700, 120, 19.7983, 85.8249),
            ('sh-5', 'Paradip Port Multipurpose Shelter (Jagatsinghpur)', '+91-6722-222005', 650, 90, 20.3166, 86.6114),
            ('sh-6', 'Rajnagar Coastal Shelter (Kendrapara)', '+91-6729-272006', 550, 60, 20.5736, 86.7267),
            ('sh-7', 'Dhamra Relief Shelter (Bhadrak)', '+91-6784-251007', 500, 70, 20.7931, 86.9061),
            ('sh-8', 'Bhubaneswar Unit-6 Relief Center (Khordha)', '+91-674-2530008', 800, 100, 20.2689, 85.8266),
            ('sh-9', 'Hirakud Flood Shelter (Sambalpur)', '+91-663-2400009', 500, 30, 21.4669, 83.9812),
            ('sh-10', 'Baripada Multipurpose Shelter (Mayurbhanj)', '+91-6792-252010', 450, 25, 21.9347, 86.7337),
            ('sh-11', 'Jajpur Town Flood Relief Camp (Jajpur)', '+91-6728-222011', 500, 45, 20.8484, 86.3377),
            ('sh-12', 'Rayagada Emergency Relief Center (Rayagada)', '+91-6856-222012', 400, 20, 19.1712, 83.4163);
        """))

        # Seed ODRAF Units
        conn.execute(text("""
            INSERT OR IGNORE INTO responder_units (id, team_name, team_type, is_available, latitude, longitude) VALUES
            ('odraf-3', 'ODRAF Unit 3 (Inflatable Boat Team)', 'Flood Rescue', 1, 21.4800, 86.9100),
            ('odraf-1', 'ODRAF Unit 1 (Bhubaneswar Rapid Action)', 'Rapid Response', 1, 20.2961, 85.8200);
        """))


# --- PYDANTIC SCHEMAS ---
class SOSRequest(BaseModel):
    reporter_contact: Optional[str] = Field(None, example="+919876543210")
    message: str = Field(..., example="Flooding near market, 4 people stranded on roof")
    latitude: Optional[float] = Field(None, ge=-90.0, le=90.0, example=21.4680)
    longitude: Optional[float] = Field(None, ge=-180.0, le=180.0, example=87.0130)
    category: str = Field("flood_rescue", example="flood_rescue")
    severity_score: int = Field(4, ge=1, le=5)
    # Optional Cell Tower fields
    mcc: Optional[int] = Field(None, example=404)
    mnc: Optional[int] = Field(None, example=49)
    lac: Optional[int] = Field(None, example=31)
    cid: Optional[int] = Field(None, example=1161)
    radio: str = Field("lte", example="lte")


class TriageResponse(BaseModel):
    distress_id: str
    status: str
    resolved_location: dict
    assigned_team: Optional[dict]
    nearest_shelter: Optional[dict]
    cell_verification: Optional[dict] = None


# --- FASTAPI APP ---
app = FastAPI(title="Innovate Odisha - AI Disaster Response API (SQLite)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()


@app.api_route("/", methods=["GET", "HEAD"])
def health_check():
    return {"status": "online", "message": "Odisha Disaster Management API is running"}


@app.post("/api/v1/sos/triage", response_model=TriageResponse, status_code=status.HTTP_201_CREATED)
def handle_sos_report(payload: SOSRequest, db: Session = Depends(get_db)):
    lat = payload.latitude
    lon = payload.longitude
    location_source = "GPS"
    cell_info = None

    # 0. Cell Tower Fallback (when GPS fails) or Verification (when both are sent)
    if payload.mcc and payload.mnc and payload.lac and payload.cid:
        cell_info = verify_cell_tower(
            lat, lon,
            payload.mcc, payload.mnc, payload.lac, payload.cid, payload.radio
        )
        if (lat is None or lon is None) and cell_info.get("tower_lat") is not None:
            lat = cell_info["tower_lat"]
            lon = cell_info["tower_lon"]
            location_source = "CELL_TOWER"

    if lat is None or lon is None:
        raise HTTPException(
            status_code=400,
            detail="GPS failed and cell tower lookup could not locate the device."
        )

    report_id = str(uuid.uuid4())

    # 1. Closest shelter with available capacity
    shelters = db.execute(text("""
        SELECT id, name, contact_phone, latitude, longitude
        FROM shelters
        WHERE current_occupancy < total_capacity;
    """)).mappings().all()

    nearest_shelter = None
    nearest_shelter_dist = float("inf")
    for s in shelters:
        dist = haversine_km(lat, lon, s["latitude"], s["longitude"])
        if dist < nearest_shelter_dist:
            nearest_shelter_dist = dist
            nearest_shelter = s

    # 2. Closest available ODRAF / rescue unit
    responders = db.execute(text("""
        SELECT id, team_name, team_type, latitude, longitude
        FROM responder_units
        WHERE is_available = 1;
    """)).mappings().all()

    closest_responder = None
    closest_responder_dist = float("inf")
    for r in responders:
        dist = haversine_km(lat, lon, r["latitude"], r["longitude"])
        if dist < closest_responder_dist:
            closest_responder_dist = dist
            closest_responder = r

    shelter_id = str(nearest_shelter["id"]) if nearest_shelter else None
    responder_id = str(closest_responder["id"]) if closest_responder else None
    assigned_status = "ASSIGNED" if closest_responder else "QUEUED"

    # 3. Record distress report in database
    insert_sos = text("""
        INSERT INTO distress_reports (
            id, reporter_contact, raw_message, category,
            severity_score, latitude, longitude, status,
            assigned_responder_id, nearest_shelter_id, created_at
        ) VALUES (
            :id, :contact, :message, :category,
            :score, :lat, :lon, :status,
            :responder_id, :shelter_id, CURRENT_TIMESTAMP
        );
    """)
    db.execute(insert_sos, {
        "id": report_id,
        "contact": payload.reporter_contact,
        "message": payload.message,
        "category": payload.category,
        "score": payload.severity_score,
        "lat": lat,
        "lon": lon,
        "status": assigned_status,
        "responder_id": responder_id,
        "shelter_id": shelter_id
    })

    # Mark responder unit as busy
    if responder_id:
        db.execute(
            text("UPDATE responder_units SET is_available = 0 WHERE id = :resp_id;"),
            {"resp_id": responder_id}
        )

    db.commit()

    shelter_info = None
    if nearest_shelter:
        shelter_info = {
            "id": str(nearest_shelter["id"]),
            "name": nearest_shelter["name"],
            "distance_km": round(nearest_shelter_dist, 2),
            "contact": nearest_shelter["contact_phone"]
        }

    team_info = None
    if closest_responder:
        team_info = {
            "id": str(closest_responder["id"]),
            "team_name": closest_responder["team_name"],
            "team_type": closest_responder["team_type"],
            "distance_km": round(closest_responder_dist, 2)
        }

    return {
        "distress_id": report_id,
        "status": assigned_status,
        "resolved_location": {
            "latitude": lat,
            "longitude": lon,
            "source": location_source
        },
        "assigned_team": team_info,
        "nearest_shelter": shelter_info,
        "cell_verification": cell_info
    }


@app.post("/api/v1/responders/reset")
def reset_responders(db: Session = Depends(get_db)):
    """Resets all responder units back to available status for testing/demos."""
    db.execute(text("UPDATE responder_units SET is_available = 1;"))
    db.commit()
    return {"status": "success", "message": "All rescue units are now marked available."}


@app.get("/api/v1/dispatches")
def get_active_dispatches(db: Session = Depends(get_db)):
    """Fetches the latest 50 SOS dispatches for the Command Center."""
    query = text("""
        SELECT
            d.id,
            d.reporter_contact,
            d.raw_message,
            d.category,
            d.severity_score,
            d.status,
            d.longitude,
            d.latitude,
            r.team_name,
            s.name AS shelter_name
        FROM distress_reports d
        LEFT JOIN responder_units r ON d.assigned_responder_id = r.id
        LEFT JOIN shelters s ON d.nearest_shelter_id = s.id
        ORDER BY d.created_at DESC
        LIMIT 50;
    """)
    rows = db.execute(query).mappings().all()
    return [dict(row) for row in rows]
