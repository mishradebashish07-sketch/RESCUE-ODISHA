import math
import os
import ssl
import uuid
from typing import Optional

import requests
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
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


# --- CELL TOWER VERIFICATION & FALLBACK (Unwired Labs / OpenCelliD) ---
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

        # Seed Shelters across Odisha
        conn.execute(text("""
            INSERT IGNORE INTO shelters (id, name, contact_phone, total_capacity, current_occupancy, location) VALUES
            ('sh-1', 'Chandipur Cyclone Shelter (Baleswar)', '+91-6782-270001', 500, 50, ST_PointFromText('POINT(87.0130 21.4680)', 4326)),
            ('sh-2', 'Barabati Multipurpose Shelter (Cuttack)', '+91-671-2300002', 600, 80, ST_PointFromText('POINT(85.8732 20.4812)', 4326)),
            ('sh-3', 'Chhatrapur Relief Camp (Ganjam)', '+91-6811-263003', 450, 40, ST_PointFromText('POINT(84.9920 19.3550)', 4326)),
            ('sh-4', 'Puri Coastal Cyclone Shelter (Puri)', '+91-6752-222004', 700, 120, ST_PointFromText('POINT(85.8249 19.7983)', 4326)),
            ('sh-5', 'Paradip Port Multipurpose Shelter (Jagatsinghpur)', '+91-6722-222005', 650, 90, ST_PointFromText('POINT(86.6114 20.3166)', 4326)),
            ('sh-6', 'Rajnagar Coastal Shelter (Kendrapara)', '+91-6729-272006', 550, 60, ST_PointFromText('POINT(86.7267 20.5736)', 4326)),
            ('sh-7', 'Dhamra Relief Shelter (Bhadrak)', '+91-6784-251007', 500, 70, ST_PointFromText('POINT(86.9061 20.7931)', 4326)),
            ('sh-8', 'Bhubaneswar Unit-6 Relief Center (Khordha)', '+91-674-2530008', 800, 100, ST_PointFromText('POINT(85.8266 20.2689)', 4326)),
            ('sh-9', 'Hirakud Flood Shelter (Sambalpur)', '+91-663-2400009', 500, 30, ST_PointFromText('POINT(83.9812 21.4669)', 4326)),
            ('sh-10', 'Baripada Multipurpose Shelter (Mayurbhanj)', '+91-6792-252010', 450, 25, ST_PointFromText('POINT(86.7337 21.9347)', 4326)),
            ('sh-11', 'Jajpur Town Flood Relief Camp (Jajpur)', '+91-6728-222011', 500, 45, ST_PointFromText('POINT(86.3377 20.8484)', 4326)),
            ('sh-12', 'Rayagada Emergency Relief Center (Rayagada)', '+91-6856-222012', 400, 20, ST_PointFromText('POINT(83.4163 19.1712)', 4326));
        """))

        # Seed ODRAF Units
        conn.execute(text("""
            INSERT IGNORE INTO responder_units (id, team_name, team_type, is_available, location) VALUES
            ('odraf-3', 'ODRAF Unit 3 (Inflatable Boat Team)', 'Flood Rescue', TRUE, ST_PointFromText('POINT(86.9100 21.4800)', 4326)),
            ('odraf-1', 'ODRAF Unit 1 (B
