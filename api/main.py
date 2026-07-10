import os
from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

@app.get("/api/sites")
def get_sites():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM monitored_sites")
    rows = cursor.fetchall()
    conn.close()
    return rows

@app.post("/api/sites/add")
def add_site(name: str = Form(...), url: str = Form(...), interval_days: float = Form(3.0)):
    clean_url = url.strip().rstrip('/')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM monitored_sites WHERE url = %s", (clean_url,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="This URL is already being tracked.")
    
    cursor.execute(
        "INSERT INTO monitored_sites (name, url, interval_days) VALUES (%s, %s, %s)",
        (name, clean_url, interval_days)
    )
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.put("/api/sites/{site_id}")
def edit_site(site_id: int, name: str = Form(...), url: str = Form(...), interval_days: float = Form(...)):
    clean_url = url.strip().rstrip('/')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM monitored_sites WHERE url = %s AND id != %s", (clean_url, site_id))
    if cursor.fetchone():
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail="This URL is already being tracked by another entry.")

    cursor.execute(
        "UPDATE monitored_sites SET name = %s, url = %s, interval_days = %s WHERE id = %s",
        (name, clean_url, interval_days, site_id)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success"}

@app.get("/api/announcements")
def get_announcements():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT a.id, s.name, s.url, a.summary, a.detected_at 
        FROM announcements a JOIN monitored_sites s ON a.site_id = s.id
        ORDER BY a.detected_at DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows

@app.delete("/api/sites/{site_id}")
def delete_site(site_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM monitored_sites WHERE id = %s", (site_id,))
    conn.commit()
    conn.close()
    return {"status": "success"}