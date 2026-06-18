import os
import hashlib
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from fastapi import FastAPI, Form
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import HTTPException

# Load environment variables
load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== CONFIGURATION ====================
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
NOTIFICATION_SINK = os.getenv("NOTIFICATION_SINK")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
# =======================================================

# Helper function to open a clean connection to Neon
def get_db_connection():
    # RealDictCursor makes PostgreSQL return rows as dictionaries, just like SQLite did!
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    if not DATABASE_URL:
        print("❌ DATABASE_URL missing! Make sure to set it in your .env file.")
        return
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # PostgreSQL uses slightly different types (SERIAL instead of AUTOINCREMENT, TEXT instead of strings)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS monitored_sites (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            interval_days FLOAT DEFAULT 3.0,
            last_checked TEXT,
            last_content_hash TEXT,
            last_raw_text TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS announcements (
            id SERIAL PRIMARY KEY,
            site_id INTEGER,
            summary TEXT,
            detected_at TEXT,
            FOREIGN KEY(site_id) REFERENCES monitored_sites(id) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    cursor.close()
    conn.close()
    print("✅ Neon tables verified/created successfully.")

init_db()

# --- Helper: Send Email Alerts ---
def send_email_alert(site_name, url, summary):
    if not all([EMAIL_ADDRESS, EMAIL_PASSWORD, NOTIFICATION_SINK]):
        print("❌ Email credentials missing. Skipping email.")
        return

    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_ADDRESS
        msg['To'] = NOTIFICATION_SINK
        msg['Subject'] = f"🚨 Update Detected: {site_name}"

        body = f"""
        Hello,

        An update or new announcement was detected on {site_name}.

        🎯 Website: {url}
        📝 AI Summary of Changes:
        --------------------------------------------------
        {summary}
        --------------------------------------------------

        Check your local Web Watcher Dashboard for complete history.
        """
        msg.attach(MIMEText(body, 'plain'))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, NOTIFICATION_SINK, msg.as_string())
        print(f"📧 Email notification successfully sent for {site_name}")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")

# --- Helper: AI Summary Generation via Gemini ---
def generate_change_summary(site_name, old_text, new_text):
    if not GEMINI_API_KEY:
        return "Change detected, but AI summary generation failed due to missing API key."

    if not old_text:
        return "Initial scan complete. Tracking started for this page."
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key={GEMINI_API_KEY}"
    
    prompt = f"""
    You are an automated website change monitoring assistant tracking internship positions, hackathons, and career updates.
    The website '{site_name}' has modified its content.
    
    PREVIOUS TEXT CONTENT:
    \"\"\"{old_text[:3000]}\"\"\"
    
    NEW TEXT CONTENT:
    \"\"\"{new_text[:3000]}\"\"\"
    
    Identify if there are any new deadlines, application portals, internship opportunities, or important announcements added in the NEW text compared to the PREVIOUS text.
    
    Provide a highly concise list focusing only on actionable changes.
    
    CRITICAL FORMATTING RULES:
    1. Do NOT use markdown syntax. 
    2. Do NOT use asterisks (**) for bold text.
    3. Do NOT use markdown bullets (* or -).
    4. Format your points cleanly using standard numbers (e.g., "1. Update: ...") and use regular line breaks between points.
    If it is just a generic structural update, say "Minor layout or structural update detected."
    """
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        response = requests.post(url, json=payload, timeout=20)
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f"🤖 Gemini Error: {e}")
    return "Change detected, but AI summary generation failed."

# --- ADVANCED PLAYWRIGHT INTERACTION ---
def scrape_advanced_page(url):
    """Simulates a human to trigger lazy-loads and expand hidden text."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        print(f"🌐 Loading {url} via Playwright...")
        
        # networkidle ensures initial React/Angular data fetches finish
        page.goto(url, wait_until="networkidle", timeout=45000)
        
        # 1. Trigger Lazy Loading by scrolling down and back up
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000) # Wait 2 seconds for elements to pop in
        page.evaluate("window.scrollTo(0, 0)")
        
        # 2. Attempt to click common "Read More" or "Accept Cookies" buttons to reveal text
        # Using regex to catch variations like "Read More", "read more", "SHOW MORE"
        expand_phrases = ["read more", "show more", "expand", "load more", "accept"]
        for phrase in expand_phrases:
            try:
                # Find all buttons/links that contain the phrase and click them forcefully
                elements = page.get_by_text(re.compile(f"(?i){phrase}")).all()
                for el in elements:
                    if el.is_visible():
                        el.click(timeout=1000, force=True)
                        page.wait_for_timeout(500) # Short pause to let text expand
            except Exception:
                pass # If it fails to click, just ignore and move on
                
        # Get the final, fully expanded HTML
        html_content = page.content()
        browser.close()
        
    # Clean the HTML
    soup = BeautifulSoup(html_content, 'html.parser')
    for element in soup(["script", "style", "nav", "footer"]):
        element.extract()
    return soup.get_text(separator=" ", strip=True)

# --- CIRCULAR QUEUE WORKER (Handles 1 site at a time) ---
def process_single_site(site_id=None, force=False):
    """Checks a specific site, or the single oldest due site if no ID is provided."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    now = datetime.datetime.now()
    target_site = None
    
    if site_id:
        # Instant check triggered by a new addition
        cursor.execute("SELECT * FROM monitored_sites WHERE id = %s", (site_id,))
        target_site = cursor.fetchone()
    else:
        # Standard Queue Mode: Find the oldest due site
        cursor.execute("SELECT * FROM monitored_sites")
        all_sites = cursor.fetchall()
        
        due_sites = []
        for site in all_sites:
            if not site['last_checked']:
                due_sites.append(site)
            else:
                last_dt = datetime.datetime.fromisoformat(site['last_checked'])
                required_seconds = site['interval_days'] * 86400 # 86400 seconds in a day
                if (now - last_dt).total_seconds() >= required_seconds:
                    due_sites.append(site)
                    
        if due_sites:
            # Sort to pick the site that has waited the longest (or NULL last_checked first)
            due_sites.sort(key=lambda x: x['last_checked'] or "")
            target_site = due_sites[0]

    if not target_site:
        cursor.close()
        conn.close()
        return # Queue is empty, nothing due right now

    print(f"🔄 Processing queue target: {target_site['name']}")
    
    try:
        # Run the advanced human-like scraper
        page_text = scrape_advanced_page(target_site['url'])
        current_hash = hashlib.sha256(page_text.encode('utf-8')).hexdigest()
        
        old_hash = target_site['last_content_hash']
        old_text = target_site['last_raw_text']
        
        if old_hash and current_hash != old_hash:
            summary = generate_change_summary(target_site['name'], old_text, page_text)
            cursor.execute(
                "INSERT INTO announcements (site_id, summary, detected_at) VALUES (%s, %s, %s)",
                (target_site['id'], summary, now.isoformat())
            )
            send_email_alert(target_site['name'], target_site['url'], summary)
            
        cursor.execute(
            "UPDATE monitored_sites SET last_checked = %s, last_content_hash = %s, last_raw_text = %s WHERE id = %s",
            (now.isoformat(), current_hash, page_text, target_site['id'])
        )
        conn.commit()
    except Exception as e:
        print(f"❌ Error scraping {target_site['name']}: {e}")

    cursor.close()
    conn.close()

# --- THE SCHEDULER ---
scheduler = BackgroundScheduler()
# Runs every 1 MINUTE. Checks exactly ONE website if it is due.
scheduler.add_job(process_single_site, 'interval', minutes=1)
scheduler.start()

# --- API ENDPOINTS ---
@app.get("/sites")
def get_sites():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM monitored_sites")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

@app.post("/sites/add")
def add_site(name: str = Form(...), url: str = Form(...), interval_days: float = Form(3.0)):
    # Clean up the URL slightly (strip trailing slashes or spaces)
    clean_url = url.strip().rstrip('/')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Check if the URL already exists in the database
    cursor.execute("SELECT id, name FROM monitored_sites WHERE url = %s", (clean_url,))
    existing_site = cursor.fetchone()
    
    if existing_site:
        cursor.close()
        conn.close()
        # Return an HTTP 400 error so the frontend knows it's a duplicate
        raise HTTPException(status_code=400, detail=f"This URL is already being tracked under '{existing_site['name']}'")
    
    # 2. If it's unique, proceed with the insertion
    cursor.execute(
        "INSERT INTO monitored_sites (name, url, interval_days) VALUES (%s, %s, %s) RETURNING id",
        (name, clean_url, interval_days)
    )
    new_id = cursor.fetchone()['id']
    conn.commit()
    cursor.close()
    conn.close()
    
    # Trigger the instant first-time background check
    scheduler.add_job(process_single_site, args=[new_id], next_run_time=datetime.datetime.now())
    
    return {"status": "success", "message": f"Successfully added {name}"}

@app.get("/announcements")
def get_announcements():
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
        SELECT a.id, s.name, s.url, a.summary, a.detected_at 
        FROM announcements a 
        JOIN monitored_sites s ON a.site_id = s.id
        ORDER BY a.detected_at DESC
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

@app.post("/test-check")
def test_check():
    # Force the queue to process the next available site immediately
    process_single_site()
    return {"status": "Test queue tick forced successfully."}

@app.delete("/sites/{site_id}")
def delete_site(site_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    # Delete the site (PostgreSQL handles cascading the deletion of its announcements if you set up the foreign key)
    cursor.execute("DELETE FROM monitored_sites WHERE id = %s", (site_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success", "message": "Site deleted successfully"}

@app.put("/sites/{site_id}")
def edit_site(site_id: int, name: str = Form(...), url: str = Form(...), interval_days: float = Form(...)):
    clean_url = url.strip().rstrip('/')
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if the NEW url belongs to a DIFFERENT site (prevent duplicates on edit)
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
    return {"status": "success", "message": "Site updated successfully"}