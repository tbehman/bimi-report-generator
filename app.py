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

# CONFIGURATION - Easy to change!
MONTHS_OF_HISTORY = 12  # Change this to 6, 9, 12, etc.

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

    def parse_financial_data(self, text_data):
        """Parse financial data with donor information"""
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
                        'amount': amount,
                        'city': '',
                        'state': ''
                    })
            
            i += 1
        
        print(f"   ✅ Parsed {len(donors)} donors")
        return donors, financial_totals

    def fetch_monthly_data(self, year, month, credentials):
        """Fetch monthly data"""
        text_data = self.fetch_financial_text(year, month, credentials)
        if not text_data:
            return None
        
        result = self.parse_financial_data(text_data)
        if not result:
            return None
            
        donors, financial_totals = result
        
        return {
            'gross_donations': financial_totals.get('total_donations', 0),
            'net_cash': financial_totals.get('net_available_cash', 0),
            'donors': donors
        }

    def analyze_giving_pattern(self, donor_id, donor_history):
        """Analyze donor's giving pattern"""
        if donor_id not in donor_history or len(donor_history[donor_id]) < 2:
            return "One-Time", "Low", 0
        
        gifts = donor_history[donor_id]
        gifts.sort(key=lambda x: x['date'])
        
        # Calculate intervals between gifts
        intervals = []
        for i in range(1, len(gifts)):
            current_date = datetime.strptime(gifts[i]['date'], '%Y-%m')
            previous_date = datetime.strptime(gifts[i-1]['date'], '%Y-%m')
            months_diff = (current_date.year - previous_date.year) * 12 + (current_date.month - previous_date.month)
            intervals.append(months_diff)
        
        # Calculate typical amount (mode)
        amounts = [g['amount'] for g in gifts]
        amount_counter = Counter(amounts)
        typical_amount = amount_counter.most_common(1)[0][0] if amount_counter else sum(amounts) / len(amounts)
        
        if not intervals:
            return "One-Time", "Low", typical_amount
        
        # Determine frequency pattern
        interval_counter = Counter(intervals)
        most_common_interval, count = interval_counter.most_common(1)[0]
        consistency = count / len(intervals)
        
        # Map to frequency names
        frequency_map = {
            1: "Monthly",
            2: "Bi-Monthly", 
            3: "Quarterly",
            6: "Semi-Annual",
            12: "Annual"
        }
        
        frequency = frequency_map.get(most_common_interval, f"Every {most_common_interval} Months")
        
        # Determine confidence
        if consistency >= 0.8 and len(gifts) >= 4:
            confidence = "High"
        elif consistency >= 0.6 and len(gifts) >= 3:
            confidence = "Medium"
        else:
            confidence = "Low"
            frequency = "Variable"
        
        return frequency, confidence, typical_amount

    def classify_donors_smart(self, current_month_donors, donor_history):
        """Smart donor classification with 12-month history"""
        new_donors = []
        changed_donors = []
        normal_donors = []
        missed_donors = []
        
        current_month_dict = {d['donor_number']: d for d in current_month_donors}
        
        print(f"🔍 Analyzing {len(donor_history)} historical donors...")
        
        # Analyze each donor in history
        for donor_id, history in donor_history.items():
            frequency, confidence, typical_amount = self.analyze_giving_pattern(donor_id, donor_history)
            
            if donor_id in current_month_dict:
                current_gift = current_month_dict[donor_id]['amount']
                change = current_gift - typical_amount
                change_percent = (change / typical_amount * 100) if typical_amount > 0 else 0
                
                donor_data = {
                    **current_month_dict[donor_id],
                    'frequency': frequency,
                    'confidence': confidence,
                    'typical_amount': typical_amount,
                    'change_amount': change,
                    'change_percent': change_percent
                }
                
                # Classification logic
                if len(history) == 1:  # Only one previous gift
                    new_donors.append(donor_data)
                elif abs(change_percent) > 50:  # Major changes
                    changed_donors.append(donor_data)
                else:
                    normal_donors.append(donor_data)
            else:
                # Donor didn't give this month
                if frequency != "One-Time" and confidence in ["High", "Medium"]:
                    missed_donors.append({
                        'donor_number': donor_id,
                        'name': history[0]['name'],
                        'frequency': frequency,
                        'confidence': confidence,
                        'typical_amount': typical_amount
                    })
        
        # Add truly new donors (not in history at all)
        for donor in current_month_donors:
            if donor['donor_number'] not in donor_history:
                new_donors.append({
                    **donor,
                    'frequency': 'One-Time',
                    'confidence': 'Low',
                    'typical_amount': donor['amount'],
                    'change_amount': 0,
                    'change_percent': 0
                })
        
        return new_donors, changed_donors, missed_donors, normal_donors

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
        
        # Store session data
        data_store[session_id] = {
            'credentials': credentials,
            'login_time': datetime.now().isoformat(),
            'data_loaded': False,
            'months_of_history': MONTHS_OF_HISTORY  # Store the config
        }
        
        return redirect(url_for('loading'))
    else:
        print("❌ Login failed")
        return render_template('login.html', error="Login failed - check your credentials")

@app.route('/loading')
def loading():
    """Loading page"""
    if 'session_id' not in session:
        return redirect(url_for('login_page'))
    
    session_id = session.get('session_id')
    months_count = data_store.get(session_id, {}).get('months_of_history', MONTHS_OF_HISTORY)
    
    return render_template('loading.html', months_count=months_count)

@app.route('/load-initial-data')
def load_initial_data():
    """Load current month data quickly"""
    session_id = session.get('session_id')
    if not session_id or session_id not in data_store:
        return jsonify({'status': 'error', 'message': 'Not authenticated'})
    
    credentials = data_store[session_id]['credentials']
    fetcher = BIMIFetcher()
    
    print("🚀 Loading current month data...")
    
    try:
        # Get current month only (fast)
        current_date = datetime.now()
        report_month = current_date.replace(day=1) - timedelta(days=1)
        
        current_data = fetcher.fetch_monthly_data(
            report_month.year, 
            report_month.month, 
            credentials
        )
        
        if not current_data:
            return jsonify({'status': 'error', 'message': 'Failed to fetch current data'})
        
        # Store current data
        data_store[session_id].update({
            'current_data': current_data,
            'data_loaded': True,
            'loaded_at': datetime.now().isoformat(),
            'report_month': report_month.strftime('%B %Y')
        })
        
        print(f"✅ Current data loaded: {len(current_data.get('donors', []))} donors")
        
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
    """Dashboard with full 12-month analytics"""
    session_id = session.get('session_id')
    
    if not session_id or session_id not in data_store:
        return redirect(url_for('login_page'))
    
    session_data = data_store[session_id]
    
    # Redirect to loading if no data
    if not session_data.get('data_loaded'):
        return redirect(url_for('loading'))
    
    print("✅ Showing dashboard with analytics")
    
    # Get current data
    current_data = session_data.get('current_data', {})
    current_donors = current_data.get('donors', [])
    
    # Get report period
    report_display = session_data.get('report_month', 'Current Month')
    months_count = session_data.get('months_of_history', MONTHS_OF_HISTORY)
    
    # Check if we have full history
    has_full_history = session_data.get('full_history_loaded', False)
    donor_history = session_data.get('donor_history', {})
    
    # Build monthly data for display
    months_data = []
    current_date = datetime.now()
    
    if has_full_history:
        # Full analytics with smart classification
        fetcher = BIMIFetcher()
        new_donors, changed_donors, missed_donors, normal_donors = fetcher.classify_donors_smart(current_donors, donor_history)
        
        # Build complete monthly history for charts
        for i in range(months_count):
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
            month_donors = []
            
            for donor_id, history in donor_history.items():
                for gift in history:
                    if gift['date'] == month_key:
                        month_gross += gift['amount']
                        month_donors.append(gift)
            
            if month_gross > 0 or i == 0:  # Always include current month
                months_data.append({
                    'month': f"{month_date.strftime('%B %Y')}",
                    'gross_donations': month_gross,
                    'net_cash': month_gross,
                    'donor_count': len(month_donors)
                })
        
        # Calculate 12-month average
        if months_data:
            amounts = [m['gross_donations'] for m in months_data]
            if len(amounts) >= 3:
                sorted_amounts = sorted(amounts)
                trimmed_amounts = sorted_amounts[1:-1]  # Remove outliers
                avg_gross = sum(trimmed_amounts) / len(trimmed_amounts)
            else:
                avg_gross = sum(amounts) / len(amounts)
            
            # Calculate differences from average
            for month_data in months_data:
                dollar_diff = month_data['gross_donations'] - avg_gross
                percent_diff = (dollar_diff / avg_gross) * 100 if avg_gross > 0 else 0
                month_data['dollar_diff'] = dollar_diff
                month_data['percent_diff'] = percent_diff
            
            current_month_data = months_data[0]
        else:
            avg_gross = 0
            current_month_data = {
                'month': report_display,
                'gross_donations': current_data.get('gross_donations', 0),
                'net_cash': current_data.get('net_cash', 0)
            }
    else:
        # Just show current month (fast mode)
        new_donors = current_donors
        changed_donors = []
        missed_donors = []
        normal_donors = []
        months_data = [{
            'month': report_display,
            'gross_donations': current_data.get('gross_donations', 0),
            'net_cash': current_data.get('net_cash', 0)
        }]
        avg_gross = current_data.get('gross_donations', 0)
        current_month_data = months_data[0]
    
    return render_template('dashboard.html', 
                         months_data=months_data,
                         average_gross=avg_gross,
                         current_month=current_month_data,
                         new_donors=new_donors,
                         changed_donors=changed_donors,
                         missed_donors=missed_donors,
                         normal_donors=normal_donors,
                         total_donors=len(current_donors),
                         report_display=report_display,
                         has_full_history=has_full_history,
                         months_count=months_count,
                         gross_donations=current_data.get('gross_donations', 0),
                         net_cash=current_data.get('net_cash', 0))

@app.route('/load-full-history')
def load_full_history():
    """Background task to load full MONTHS_OF_HISTORY months"""
    session_id = session.get('session_id')
    if not session_id or session_id not in data_store:
        return jsonify({'status': 'error', 'message': 'Not authenticated'})
    
    credentials = data_store[session_id]['credentials']
    months_count = data_store[session_id].get('months_of_history', MONTHS_OF_HISTORY)
    fetcher = BIMIFetcher()
    
    print(f"📈 Loading {months_count} months of history in background...")
    
    try:
        current_date = datetime.now()
        donor_history = {}
        loaded_months = 0
        
        # Load specified number of months
        for i in range(months_count):
            month_date = current_date.replace(day=1)
            for _ in range(i):
                if month_date.month == 1:
                    month_date = month_date.replace(year=month_date.year-1, month=12)
                else:
                    month_date = month_date.replace(month=month_date.month-1)
            
            month_data = fetcher.fetch_monthly_data(
                month_date.year, 
                month_date.month, 
                credentials
            )
            
            if month_data and month_data.get('donors'):
                loaded_months += 1
                for donor in month_data['donors']:
                    donor_id = donor['donor_number']
                    if donor_id not in donor_history:
                        donor_history[donor_id] = []
                    
                    month_key = f"{month_date.year}-{month_date.month:02d}"
                    # Avoid duplicates
                    existing_dates = [g['date'] for g in donor_history[donor_id]]
                    if month_key not in existing_dates:
                        donor_history[donor_id].append({
                            'date': month_key,
                            'amount': donor['amount'],
                            'name': donor['name'],
                            'city': donor.get('city', ''),
                            'state': donor.get('state', '')
                        })
            
            # Small delay to be respectful
            time.sleep(0.8)
        
        # Update with full history
        data_store[session_id]['donor_history'] = donor_history
        data_store[session_id]['full_history_loaded'] = True
        data_store[session_id]['history_loaded_at'] = datetime.now().isoformat()
        
        print(f"✅ Full {months_count}-month history loaded: {len(donor_history)} donors across {loaded_months} months")
        return jsonify({
            'status': 'complete', 
            'donors_loaded': len(donor_history),
            'months_loaded': loaded_months,
            'total_months': months_count
        })
        
    except Exception as e:
        print(f"❌ Error loading full history: {e}")
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/status')
def status():
    return jsonify({'status': 'ok', 'months_config': MONTHS_OF_HISTORY})

@app.route('/debug')
def debug():
    session_id = session.get('session_id')
    session_data = data_store.get(session_id, {}) if session_id else {}
    return jsonify({
        'session_id': session_id,
        'data_store_keys': list(data_store.keys()),
        'months_config': MONTHS_OF_HISTORY,
        'session_months': session_data.get('months_of_history'),
        'has_full_history': session_data.get('full_history_loaded', False),
        'donor_history_count': len(session_data.get('donor_history', {}))
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
    print(f"🚀 Starting app with {MONTHS_OF_HISTORY} months of history...")
    app.run(host='0.0.0.0', port=port, debug=False)
