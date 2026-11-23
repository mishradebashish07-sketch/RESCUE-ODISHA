import math
import os
import ssl
import uuid
from typing import Optional

import requests
from fastapi import FastAPI, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

# --- DATABASE SETUP (Aiven Cloud MySQL) ---
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://avnadmin:AVNS_jjLgFjzUZjJ_o9W6jHh@"
    "mysql-ded093-mishradebashish07-9af6.f.aivencloud.com:13345/defaultdb"
)

# SSL configuration required for Aiven MySQL with PyMySQL
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

engine = create_engine(
    DATABASE_URL,
    connect_args={"ssl": ssl_ctx},
    pool_pre_ping=True
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- CELL TOWER VERIFICATION (Unwired Labs / OpenCelliD) ---
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
                id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                contact_phone VARCHAR(50),
                total_capacity INT DEFAULT 500,
                current_occupancy INT DEFAULT 0,
                location POINT NOT NULL SRID 4326
            );
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS responder_units (
                id VARCHAR(36) PRIMARY KEY,
                team_name VARCHAR(255) NOT NULL,
                team_type VARCHAR(100),
                is_available BOOLEAN DEFAULT TRUE,
                location POINT NOT NULL SRID 4326
            );
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS distress_reports (
                id VARCHAR(36) PRIMARY KEY,
                reporter_contact VARCHAR(50),
                raw_message TEXT,
                category VARCHAR(100),
                severity_score INT,
                location POINT NOT NULL SRID 4326,
                status VARCHAR(50),
                assigned_responder_id VARCHAR(36),
                nearest_shelter_id VARCHAR(36),
                created_at DATETIME
            );
        """))

        # Seed Shelters if table is empty
        shelter_count = conn.execute(text("SELECT COUNT(*) FROM shelters")).scalar()
        if shelter_count == 0:
            conn.execute(text("""
                INSERT INTO shelters (id, name, contact_phone, total_capacity, current_occupancy, location) VALUES
                ('sh-1', 'Chandipur Cyclone Shelter (Baleswar)', '+91-6782-270001', 500, 50, ST_PointFromText('POINT(87.0130 21.4680)', 4326)),
                ('sh-2', 'Barabati Multipurpose Shelter (Cuttack)', '+91-671-2300002', 600, 80, ST_PointFromText('POINT(85.8732 20.4812)', 4326)),
                ('sh-3', 'Chhatrapur Relief Camp (Ganjam)', '+91-6811-263003', 450, 40, ST_PointFromText('POINT(84.9920 19.3550)', 4326));
            """))

        # Seed ODRAF Units if table is empty
        unit_count = conn.execute(text("SELECT COUNT(*) FROM responder_units")).scalar()
        if unit_count == 0:
            conn.execute(text("""
                INSERT INTO responder_units (id, team_name, team_type, is_available, location) VALUES
                ('odraf-3', 'ODRAF Unit 3 (Inflatable Boat Team)', 'Flood Rescue', TRUE, ST_PointFromText('POINT(86.9100 21.4800)', 4326)),
                ('odraf-1', 'ODRAF Unit 1 (Bhubaneswar Rapid Action)', 'Rapid Response', TRUE, ST_PointFromText('POINT(85.8200 20.2961)', 4326));
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
    assigned_team: Optional[dict]
    nearest_shelter: Optional[dict]
    cell_verification: Optional[dict] = None

# --- FASTAPI APP ---
app = FastAPI(title="Innovate Odisha - AI Disaster Response API (MySQL)")

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
    cell_info = None

    # 0. Optional Cell Tower Verification & GPS Fallback
    if payload.mcc and payload.mnc and payload.lac and payload.cid:
        cell_info = verify_cell_tower(
            lat, lon,
            payload.mcc, payload.mnc, payload.lac, payload.cid, payload.radio
        )
        if (lat is None or lon is None) and cell_info.get("tower_lat") is not None:
            lat = cell_info["tower_lat"]
            lon = cell_info["tower_lon"]

    if lat is None or lon is None:
        raise HTTPException(
            status_code=400,
            detail="Either GPS coordinates (latitude/longitude) or valid cell tower details are required."
        )

    sos_wkt = f"POINT({lon} {lat})"
    report_id = str(uuid.uuid4())

    # 1. Spatial Query: Closest shelter with available capacity
    shelter_query = text("""
        SELECT id, name, contact_phone, 
               ST_Distance_Sphere(location, ST_PointFromText(:sos_point, 4326)) AS distance_meters
        FROM shelters
        WHERE current_occupancy < total_capacity
        ORDER BY distance_meters ASC
        LIMIT 1;
    """)
    nearest_shelter = db.execute(shelter_query, {"sos_point": sos_wkt}).mappings().first()

    # 2. Spatial Query: Closest available ODRAF / rescue unit
    responder_query = text("""
        SELECT id, team_name, team_type, 
               ST_Distance_Sphere(location, ST_PointFromText(:sos_point, 4326)) AS distance_meters
        FROM responder_units
        WHERE is_available = TRUE
        ORDER BY distance_meters ASC
        LIMIT 1;
    """)
    closest_responder = db.execute(responder_query, {"sos_point": sos_wkt}).mappings().first()

    shelter_id = str(nearest_shelter["id"]) if nearest_shelter else None
    responder_id = str(closest_responder["id"]) if closest_responder else None
    assigned_status = "ASSIGNED" if closest_responder else "QUEUED"

    # 3. Record distress report in database
    insert_sos = text("""
        INSERT INTO distress_reports (
            id, reporter_contact, raw_message, category, 
            severity_score, location, status, assigned_responder_id, nearest_shelter_id, created_at
        ) VALUES (
            :id, :contact, :message, :category, 
            :score, ST_PointFromText(:sos_point, 4326), :status, :responder_id, :shelter_id, NOW()
        );
    """)
    db.execute(insert_sos, {
        "id": report_id,
        "contact": payload.reporter_contact,
        "message": payload.message,
        "category": payload.category,
        "score": payload.severity_score,
        "sos_point": sos_wkt,
        "status": assigned_status,
        "responder_id": responder_id,
        "shelter_id": shelter_id
    })

    # Mark responder unit as busy
    if responder_id:
        db.execute(
            text("UPDATE responder_units SET is_available = FALSE WHERE id = :resp_id;"),
            {"resp_id": responder_id}
        )

    db.commit()

    shelter_info = None
    if nearest_shelter:
        shelter_info = {
            "id": str(nearest_shelter["id"]),
            "name": nearest_shelter["name"],
            "distance_km": round(nearest_shelter["distance_meters"] / 1000.0, 2),
            "contact": nearest_shelter["contact_phone"]
        }

    team_info = None
    if closest_responder:
        team_info = {
            "id": str(closest_responder["id"]),
            "team_name": closest_responder["team_name"],
            "team_type": closest_responder["team_type"],
            "distance_km": round(closest_responder["distance_meters"] / 1000.0, 2)
        }

    return {
        "distress_id": report_id,
        "status": assigned_status,
        "assigned_team": team_info,
        "nearest_shelter": shelter_info,
        "cell_verification": cell_info
    }

@app.post("/api/v1/responders/reset")
def reset_responders(db: Session = Depends(get_db)):
    """Resets all responder units back to available status for testing/demos."""
    db.execute(text("UPDATE responder_units SET is_available = TRUE;"))
    db.commit()
    return {"status": "success", "message": "All rescue units are now marked available."}
