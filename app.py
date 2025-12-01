from flask import Flask, render_template, request, session, redirect, url_for
import requests
from datetime import datetime, timedelta
import re
import os
import json
import time

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-key-123')

class WorkingBIMIFetcher:
    def __init__(self):
        self.session = requests.Session()
        self.setup_headers()
        
    def setup_headers(self):
        self.headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Origin': 'https://missionary.bimi.org',
            'Referer': 'https://missionary.bimi.org/'
        }
    
    def login(self, credentials):
        """EXACT same as working desktop version"""
        print("🔐 Logging into BIMI...")
        
        response = self.session.post(
            'https://missionary.bimi.org/home.php',
            data=credentials,
            headers=self.headers,
            allow_redirects=True,
            timeout=30
        )
        
        success = response.status_code == 200 and "Missionary Login" not in response.text
        print(f"✅ Login {'successful' if success else 'failed'}")
        print(f"   Response size: {len(response.text)} chars")
        
        return success
    
    def fetch_month_data(self, year, month, credentials):
        """EXACT same as working desktop version"""
        text_url = "https://missionary.bimi.org/common/Finances/ViewStatementText.php"
        params = {
            'MissionaryNumber': credentials['account_number'],
            'StatementYear': year,
            'StatementMonth': month
        }
        
        print(f"📊 Fetching {month}/{year}...")
        
        response = self.session.get(
            text_url,
            params=params,
            headers=self.headers,
            timeout=30
        )
        
        if response.status_code == 200 and "Missionary Login" not in response.text:
            print(f"✅ Fetched {len(response.text)} chars")
            return self.parse_simple(response.text)
        
        print(f"❌ Fetch failed: {response.status_code}")
        return None
    
    def parse_simple(self, text_data):
        """EXACT same parser as working desktop version"""
        donors = []
        financial_totals = {}
        lines = text_data.split('\n')
        in_donor_section = False
        
        for line in lines:
            line = line.rstrip()
            
            if "YOUR DONATIONS FOR THIS MONTH" in line:
                in_donor_section = True
                continue
            elif "YOUR DEDUCTIONS" in line and in_donor_section:
                in_donor_section = False
                continue
            elif "TOTAL DONATIONS FOR THIS MONTH" in line:
                total_match = re.search(r'\$([\d,]+\.\d+)', line)
                if total_match:
                    financial_totals['total_donations'] = float(total_match.group(1).replace(',', ''))
            elif "YOUR NET AVAILABLE CASH" in line:
                cash_match = re.search(r'\$([\d,]+\.\d+)', line)
                if cash_match:
                    financial_totals['net_available_cash'] = float(cash_match.group(1).replace(',', ''))
            
            if in_donor_section and line.strip():
                donor_match = re.match(r'^\s*(\d+)\s+(.*?)\s+\$([\d,]+\.\d+)$', line)
                if donor_match:
                    donor_num = donor_match.group(1).strip()
                    donor_name = donor_match.group(2).strip()
                    amount = float(donor_match.group(3).replace(',', ''))
                    
                    donor_name = ' '.join(donor_name.split())
                    
                    if ' PO ' in donor_name or ' P O ' in donor_name:
                        donor_name = donor_name.split(' PO ')[0].split(' P O ')[0]
                    if ' RD' in donor_name or ' ST' in donor_name or ' AVE' in donor_name:
                        donor_name = donor_name.split(' RD')[0].split(' ST')[0].split(' AVE')[0]
                    
                    donors.append({
                        'donor_number': donor_num,
                        'name': donor_name,
                        'amount': amount
                    })
        
        print(f"✅ Parsed {len(donors)} donors")
        return donors, financial_totals

# Simple in-memory storage (for session persistence)
fetchers = {}

@app.route('/')
def home():
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    credentials = {
        'account_number': request.form['account_number'],
        'user_name': request.form['username'],
        'password': request.form['password'],
        'submit': 'Login'
    }
    
    # Create a fetcher (like the desktop version)
    fetcher = WorkingBIMIFetcher()
    
    # Try login
    if fetcher.login(credentials):
        # Store the WHOLE fetcher (with its session) in memory
        session_id = f"{credentials['account_number']}_{int(time.time())}"
        session['session_id'] = session_id
        fetchers[session_id] = {
            'fetcher': fetcher,
            'credentials': credentials,
            'created': datetime.now()
        }
        
        # Immediately fetch current month data (same session!)
        current_date = datetime.now()
        report_month = current_date.replace(day=1) - timedelta(days=1)
        
        data = fetcher.fetch_month_data(
            report_month.year,
            report_month.month,
            credentials
        )
        
        if data:
            donors, totals = data
            # Store the data
            fetchers[session_id]['current_data'] = {
                'donors': donors,
                'totals': totals,
                'report_month': report_month.strftime('%B %Y')
            }
            
            # Redirect to dashboard
            return redirect(url_for('dashboard'))
    
    return "Login failed", 401

@app.route('/dashboard')
def dashboard():
    session_id = session.get('session_id')
    if not session_id or session_id not in fetchers:
        return redirect(url_for('home'))
    
    session_data = fetchers[session_id]
    
    if 'current_data' not in session_data:
        return redirect(url_for('home'))
    
    data = session_data['current_data']
    donors = data['donors']
    totals = data['totals']
    report_month = data['report_month']
    
    return render_template('dashboard.html',
                         donors=donors,
                         totals=totals,
                         report_month=report_month,
                         donor_count=len(donors))

@app.route('/load-history')
def load_history():
    """Background loading of history - uses the SAME fetcher"""
    session_id = session.get('session_id')
    if not session_id or session_id not in fetchers:
        return jsonify({'status': 'error'})
    
    session_data = fetchers[session_id]
    fetcher = session_data['fetcher']
    credentials = session_data['credentials']
    
    # Load a few months of history in background
    current_date = datetime.now()
    history = []
    
    for i in range(1, 4):  # Just 3 months for now
        month_date = current_date.replace(day=1) - timedelta(days=1)
        for _ in range(i):
            if month_date.month == 1:
                month_date = month_date.replace(year=month_date.year-1, month=12)
            else:
                month_date = month_date.replace(month=month_date.month-1)
        
        data = fetcher.fetch_month_data(
            month_date.year,
            month_date.month,
            credentials
        )
        
        if data:
            donors, totals = data
            history.append({
                'month': month_date.strftime('%B %Y'),
                'donors': len(donors),
                'total': totals.get('total_donations', 0)
            })
        
        time.sleep(1)  # Be nice to BIMI
    
    return jsonify({'status': 'complete', 'history': history})

@app.route('/logout')
def logout():
    session_id = session.get('session_id')
    if session_id in fetchers:
        del fetchers[session_id]
    session.clear()
    return redirect(url_for('home'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
