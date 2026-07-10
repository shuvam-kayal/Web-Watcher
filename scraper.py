import os
import hashlib
import datetime
import re
import json
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import psycopg2
from psycopg2.extras import RealDictCursor

# ==================== CONFIGURATION ====================
NOTIFICATION_SINK = os.getenv("NOTIFICATION_SINK")
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
# =======================================================

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def send_email_alert(site_name, url, summary):
    if not RESEND_API_KEY:
        print("❌ Resend API key missing.")
        return

    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "from": "onboarding@resend.dev", 
        "to": [NOTIFICATION_SINK], 
        "subject": f"🚨 Update Detected: {site_name}",
        "text": f"Website: {url}\n\nSummary:\n{summary}"
    }
    try:
        requests.post("https://api.resend.com/emails", headers=headers, json=payload)
        print(f"📧 HTTP Email successfully sent for {site_name}")
    except Exception as e:
        print(f"❌ Failed to send HTTP email: {e}")

def generate_change_summary(site_name, old_text, new_text):
    if not GEMINI_API_KEY:
        return {"is_important": False, "summary": "Missing API Key."}

    if not old_text:
        return {"is_important": True, "summary": "Initial scan complete. Tracking started for this page."}
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key={GEMINI_API_KEY}"
    
    prompt = f"""
    You are an intelligent website monitoring assistant.
    The website '{site_name}' has modified its content.
    
    PREVIOUS TEXT:
    \"\"\"{old_text[:3000]}\"\"\"
    
    NEW TEXT:
    \"\"\"{new_text[:3000]}\"\"\"
    
    STEP 1: Analyze the context and the changes. 
    - High Priority: New internships, hackathons, application portals, deadlines, or career opportunities.
    - Medium Priority: Any significant new announcements, program launches, or major contextual updates relevant to the site's apparent purpose.
    - Ignore: Purely structural HTML/CSS changes, minor wording tweaks, expired dates, copyright year updates, or irrelevant generic news.
    
    STEP 2: Return your analysis STRICTLY as a valid JSON object. Do not include markdown formatting or code blocks outside the JSON.
    
    EXPECTED JSON FORMAT:
    {{
        "is_important": true/false,
        "summary": "If important, provide a concise numbered list of the actionable or significant changes here. If not, leave as an empty string."
    }}
    """
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        response = requests.post(url, json=payload, timeout=20)
        if response.status_code == 200:
            raw_response = response.json()['candidates'][0]['content']['parts'][0]['text']
            clean_json_string = raw_response.strip().strip('```json').strip('```')
            return json.loads(clean_json_string)
    except Exception as e:
        print(f"🤖 Gemini Error: {e}")
        
    return {"is_important": False, "summary": "Error generating summary."}

def scrape_advanced_page(url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        print(f"🌐 Loading {url} via Playwright...")
        page.goto(url, wait_until="networkidle", timeout=45000)
        
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)
        page.evaluate("window.scrollTo(0, 0)")
        
        expand_phrases = ["read more", "show more", "expand", "load more", "accept"]
        for phrase in expand_phrases:
            try:
                elements = page.get_by_text(re.compile(f"(?i){phrase}")).all()
                for el in elements:
                    if el.is_visible():
                        el.click(timeout=1000, force=True)
                        page.wait_for_timeout(500)
            except Exception:
                pass 
                
        html_content = page.content()
        browser.close()
        
    soup = BeautifulSoup(html_content, 'html.parser')
    for element in soup(["script", "style", "nav", "footer"]):
        element.extract()
    return soup.get_text(separator=" ", strip=True)

def run_scraper():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM monitored_sites")
    sites = cursor.fetchall()
    now = datetime.datetime.now()

    for site in sites:
        # Check if it's time to scrape this site
        if site['last_checked']:
            last_dt = datetime.datetime.fromisoformat(site['last_checked'])
            required_seconds = site['interval_days'] * 86400
            if (now - last_dt).total_seconds() < required_seconds:
                continue # Not due yet, skip
                
        print(f"🔄 Processing target: {site['name']}")
        try:
            page_text = scrape_advanced_page(site['url'])
            current_hash = hashlib.sha256(page_text.encode('utf-8')).hexdigest()
            old_hash = site['last_content_hash']
            old_text = site['last_raw_text']
            
            if old_hash and current_hash != old_hash:
                ai_decision = generate_change_summary(site['name'], old_text, page_text)
                
                if ai_decision.get("is_important") is True:
                    summary_text = ai_decision.get("summary", "Important update detected.")
                    cursor.execute(
                        "INSERT INTO announcements (site_id, summary, detected_at) VALUES (%s, %s, %s)",
                        (site['id'], summary_text, now.isoformat())
                    )
                    send_email_alert(site['name'], site['url'], summary_text)
                    print(f"🚨 Important update logged and sent for {site['name']}")
                else:
                    print(f"💤 Change detected on {site['name']}, but AI deemed it minor. Skipping alert.")
            
            cursor.execute(
                "UPDATE monitored_sites SET last_checked = %s, last_content_hash = %s, last_raw_text = %s WHERE id = %s",
                (now.isoformat(), current_hash, page_text, site['id'])
            )
            conn.commit()
        except Exception as e:
            print(f"❌ Error scraping {site['name']}: {e}")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    run_scraper()