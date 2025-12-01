from flask import Flask, render_template, request, session, redirect, url_for, jsonify
import requests
from datetime import datetime, timedelta
import re
import os
import json
from collections import Counter
import time
import hashlib

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')

# In-memory storage for data
data_store = {}

class BIMIFetcher:
    def __init__(self):
        self.session = requests.Session()
        self.setup_headers()
        self.is_logged_in = False
    
    def setup_headers(self):
        self.headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Origin': 'https://missionary.bimi.org',
            'Referer': 'https://missionary.bimi.org/'
        }
    
    def login_to_bimi(self, credentials):
        """Login to BIMI portal"""
        login_url = "https://missionary.bimi.org/home.php"
        print("🔐 Logging into BIMI...")
        
        try:
            response = self.session.post(
                login_url, 
                data=credentials,
                headers=self.headers,
                allow_redirects=True,
                timeout=30
            )
            
            if response.status_code == 200 and "Missionary Login" not in response.text:
                print("✅ Login successful!")
                self.is_logged_in = True
                return True
            else:
                print("❌ Login failed")
                self.is_logged_in = False
                return False
        except Exception as e:
            print(f"❌ Login error: {e}")
            return False
    
    def fetch_financial_text(self, year, month, credentials):
        """Fetch text version of financial statement"""
        text_url = "https://missionary.bimi.org/common/Finances/ViewStatementText.php"
        params = {
            'MissionaryNumber': credentials['account_number'],
            'StatementYear': year,
            'StatementMonth': month
        }
        
        print(f"📊 Fetching {month}/{year}...")
        
        if not self.is_logged_in:
            if not self.login_to_bimi(credentials):
                return None
        
        try:
            response = self.session.get(text_url, params=params, headers=self.headers, timeout=30)
            
            if response.status_code == 200 and "Missionary Login" not in response.text:
                return response.text
            else:
                print(f"❌ Failed to fetch {month}/{year}")
                return None
        except Exception as e:
            print(f"❌ Error fetching {month}/{year}: {e}")
            return None

    def parse_financial_data_simple(self, text_data):
        """Simple parser that focuses on key data"""
        if not text_data or "Missionary Login" in text_data:
            return None
            
        donors = []
        financial_totals = {}
        lines = text_data.split('\n')
        in_donor_section = False
        
        i = 0
        while i < len(lines):
            line = lines[i].rstrip()
            
            # Section detection
            if "YOUR DONATIONS FOR THIS MONTH" in line:
                in_donor_section = True
            elif "YOUR DEDUCTIONS" in line and in_donor_section:
                in_donor_section = False
            elif "TOTAL DONATIONS FOR THIS MONTH" in line:
                total_match = re.search(r'\$([\d,]+\.\d+)', line)
                if total_match:
                    financial_totals['total_donations'] = float(total_match.group(1).replace(',', ''))
            elif "YOUR NET AVAILABLE CASH" in line:
                cash_match = re.search(r'\$([\d,]+\.\d+)', line)
                if cash_match:
                    financial_totals['net_available_cash'] = float(cash_match.group(1).replace(',', ''))
            
            # Donor parsing
            if in_donor_section and line.strip():
                donor_match = re.match(r'^\s*(\d+)\s+(.*?)\s+\$([\d,]+\.\d+)$', line)
                if donor_match:
                    donor_num = donor_match.group(1).strip()
                    donor_name = donor_match.group(2).strip()
                    amount = float(donor_match.group(3).replace(',', ''))
                    
                    # Simple name cleaning
                    clean_name = re.sub(r'\s+P\s*O\s*BOX\s+\d+.*', '', donor_name, flags=re.IGNORECASE)
                    clean_name = ' '.join(clean_name.split())
                    
                    donors.append({
                        'donor_number': donor_num,
                        'name': clean_name,
                        'amount': amount
                    })
            
            i += 1
        
        print(f"   ✅ Parsed {len(donors)} donors")
        return donors, financial_totals

    def fetch_monthly_data_fast(self, year, month, credentials):
        """Fast version that only gets essential data"""
        text_data = self.fetch_financial_text(year, month, credentials)
        if not text_data:
            return None
        
        result = self.parse_financial_data_simple(text_data)
        if not result:
            return None
            
        donors, financial_totals = result
        
        return {
            'gross_donations': financial_totals.get('total_donations', 0),
            'net_cash': financial_totals.get('net_available_cash', 0),
            'donors': donors
        }

def generate_session_id(credentials):
    """Generate a unique session ID"""
    cred_string = f"{credentials['account_number']}_{credentials['user_name']}"
    return hashlib.md5(cred_string.encode()).hexdigest()

# Routes
@app.route('/')
def login_page():
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    account_number = request.form['account_number']
    username = request.form['username']
    password = request.form['password']
    
    fetcher = BIMIFetcher()
    credentials = {
        'account_number': account_number,
        'user_name': username,
        'password': password,
        'submit': 'Login'
    }
    
    print(f"🔐 Attempting login for account: {account_number}")
    
    if fetcher.login_to_bimi(credentials):
        print("✅ Login successful!")
        
        # Generate session ID
        session_id = generate_session_id(credentials)
        session['session_id'] = session_id
        
        # Store minimal session data
        data_store[session_id] = {
            'credentials': credentials,
            'login_time': datetime.now().isoformat()
        }
        
        # Immediately redirect to loading page
        return redirect(url_for('loading'))
    else:
        print("❌ Login failed")
        return render_template('login.html', error="Login failed - check your credentials")

@app.route('/loading')
def loading():
    """Simple loading page that starts data collection"""
    if 'session_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('loading_simple.html')

@app.route('/load-initial-data')
def load_initial_data():
    """Load only essential data quickly"""
    session_id = session.get('session_id')
    if not session_id or session_id not in data_store:
        return jsonify({'status': 'error', 'message': 'Not authenticated'})
    
    credentials = data_store[session_id]['credentials']
    fetcher = BIMIFetcher()
    
    print("🚀 Loading essential data...")
    
    try:
        # Get current month only (fast)
        current_date = datetime.now()
        report_month = current_date.replace(day=1) - timedelta(days=1)
        
        current_data = fetcher.fetch_monthly_data_fast(
            report_month.year, 
            report_month.month, 
            credentials
        )
        
        if not current_data:
            return jsonify({'status': 'error', 'message': 'Failed to fetch current data'})
        
        # Store only current month data initially
        data_store[session_id].update({
            'current_data': current_data,
            'data_loaded': True,
            'loaded_at': datetime.now().isoformat()
        })
        
        print(f"✅ Initial data loaded: {len(current_data.get('donors', []))} donors")
        
        return jsonify({
            'status': 'complete',
            'donors_loaded': len(current_data.get('donors', [])),
            'gross_amount': current_data.get('gross_donations', 0)
        })
        
    except Exception as e:
        print(f"❌ Error in load_initial_data: {e}")
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/dashboard')
def dashboard():
    """Smart dashboard that shows current data immediately, enhances later"""
    session_id = session.get('session_id')
    if not session_id or session_id not in data_store:
        return redirect(url_for('login_page'))
    
    session_data = data_store[session_id]
    
    # Get current data (always available)
    current_data = session_data.get('current_data', {})
    current_donors = current_data.get('donors', [])
    
    # Calculate report month
    current_date = datetime.now()
    report_month = current_date.replace(day=1) - timedelta(days=1)
    report_display = report_month.strftime('%B %Y')
    
    # Check if we have full history for enhanced analytics
    has_full_history = session_data.get('full_history_loaded', False)
    donor_history = session_data.get('donor_history', {})
    
    # Simple classification if no full history
    if not has_full_history:
        new_donors = current_donors
        changed_donors = []
        missed_donors = []
        normal_donors = []
        months_data = []
        avg_gross = current_data.get('gross_donations', 0)
    else:
        # Full analytics with 12 months of data
        fetcher = BIMIFetcher()
        new_donors, changed_donors, missed_donors, normal_donors = fetcher.classify_donors_smart(current_donors, donor_history)
        
        # Build 12-month history for charts
        months_data = []
        for i in range(12):
            month_date = current_date.replace(day=1)
            for _ in range(i):
                if month_date.month == 1:
                    month_date = month_date.replace(year=month_date.year-1, month=12)
                else:
                    month_date = month_date.replace(month=month_date.month-1)
            
            year_val = month_date.year
            month_val = month_date.month
            month_key = f"{year_val}-{month_val:02d}"
            
            month_gross = 0
            for donor_id, history in donor_history.items():
                for gift in history:
                    if gift['date'] == month_key:
                        month_gross += gift['amount']
            
            if month_gross > 0:
                months_data.append({
                    'month': f"{month_date.strftime('%B %Y')}",
                    'gross_donations': month_gross,
                    'net_cash': month_gross
                })
        
        # Calculate average
        if months_data:
            amounts = [m['gross_donations'] for m in months_data]
            if len(amounts) >= 3:
                sorted_amounts = sorted(amounts)
                trimmed_amounts = sorted_amounts[1:-1]
                avg_gross = sum(trimmed_amounts) / len(trimmed_amounts)
            else:
                avg_gross = sum(amounts) / len(amounts)
            
            for month_data in months_data:
                dollar_diff = month_data['gross_donations'] - avg_gross
                percent_diff = (dollar_diff / avg_gross) * 100
                month_data['dollar_diff'] = dollar_diff
                month_data['percent_diff'] = percent_diff
        else:
            avg_gross = 0
    
    return render_template('dashboard_enhanced.html', 
                         months_data=months_data,
                         average_gross=avg_gross,
                         current_month=months_data[0] if months_data else {
                             'month': report_display,
                             'gross_donations': current_data.get('gross_donations', 0),
                             'net_cash': current_data.get('net_cash', 0)
                         },
                         new_donors=new_donors,
                         changed_donors=changed_donors,
                         missed_donors=missed_donors,
                         normal_donors=normal_donors,
                         total_donors=len(current_donors),
                         report_display=report_display,
                         has_full_history=has_full_history,
                         history_progress=session_data.get('history_progress', 0))

@app.route('/load-full-history')
def load_full_history():
    """Background task to load full history (called after dashboard loads)"""
    session_id = session.get('session_id')
    if not session_id or session_id not in data_store:
        return jsonify({'status': 'error', 'message': 'Not authenticated'})
    
    credentials = data_store[session_id]['credentials']
    fetcher = BIMIFetcher()
    
    print("📈 Loading full history in background...")
    
    try:
        current_date = datetime.now()
        donor_history = {}
        
        # Load only 6 months instead of 12 to be faster
        for i in range(6):
            month_date = current_date.replace(day=1)
            for _ in range(i):
                if month_date.month == 1:
                    month_date = month_date.replace(year=month_date.year-1, month=12)
                else:
                    month_date = month_date.replace(month=month_date.month-1)
            
            month_data = fetcher.fetch_monthly_data_fast(
                month_date.year, 
                month_date.month, 
                credentials
            )
            
            if month_data and month_data.get('donors'):
                for donor in month_data['donors']:
                    donor_id = donor['donor_number']
                    if donor_id not in donor_history:
                        donor_history[donor_id] = []
                    
                    month_key = f"{month_date.year}-{month_date.month:02d}"
                    donor_history[donor_id].append({
                        'date': month_key,
                        'amount': donor['amount'],
                        'name': donor['name']
                    })
            
            time.sleep(0.5)  # Shorter delay
        
        # Update with full history
        data_store[session_id]['donor_history'] = donor_history
        data_store[session_id]['full_history_loaded'] = True
        
        print(f"✅ Full history loaded: {len(donor_history)} donors")
        return jsonify({'status': 'complete', 'donors_loaded': len(donor_history)})
        
    except Exception as e:
        print(f"❌ Error loading full history: {e}")
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/status')
def status():
    return jsonify({'status': 'ok'})

@app.route('/debug')
def debug():
    session_id = session.get('session_id')
    return jsonify({
        'session_id': session_id,
        'data_store_keys': list(data_store.keys()),
        'session_data': data_store.get(session_id, {}) if session_id else {}
    })

@app.route('/logout')
def logout():
    session_id = session.get('session_id')
    if session_id and session_id in data_store:
        del data_store[session_id]
    session.clear()
    return redirect(url_for('login_page'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

