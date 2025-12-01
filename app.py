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

# CONFIGURATION - 6 months for reliability
MONTHS_OF_HISTORY = 6

# US State abbreviations for validation
US_STATES = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas', 'CA': 'California',
    'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware', 'FL': 'Florida', 'GA': 'Georgia',
    'HI': 'Hawaii', 'ID': 'Idaho', 'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa',
    'KS': 'Kansas', 'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine', 'MD': 'Maryland',
    'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota', 'MS': 'Mississippi', 'MO': 'Missouri',
    'MT': 'Montana', 'NE': 'Nebraska', 'NV': 'Nevada', 'NH': 'New Hampshire', 'NJ': 'New Jersey',
    'NM': 'New Mexico', 'NY': 'New York', 'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio',
    'OK': 'Oklahoma', 'OR': 'Oregon', 'PA': 'Pennsylvania', 'RI': 'Rhode Island', 'SC': 'South Carolina',
    'SD': 'South Dakota', 'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah', 'VT': 'Vermont',
    'VA': 'Virginia', 'WA': 'Washington', 'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming',
    'DC': 'District of Columbia'
}

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

    def normalize_city_name(self, city):
        """Normalize city name capitalization"""
        if not city:
            return ''
        
        # Common city name exceptions
        exceptions = {
            'st': 'St.',
            'saint': 'St.',
            'ft': 'Ft.',
            'fort': 'Ft.',
            'mt': 'Mt.',
            'mount': 'Mt.',
            'new york': 'New York',
            'los angeles': 'Los Angeles',
            'san francisco': 'San Francisco',
            'san diego': 'San Diego',
            'grand rapids': 'Grand Rapids',
            'spring hill': 'Spring Hill',
            'colorado springs': 'Colorado Springs',
            'kansas city': 'Kansas City',
            'las vegas': 'Las Vegas',
            'oak brook': 'Oak Brook',
            'mount pleasant': 'Mt. Pleasant',
            'st louis': 'St. Louis',
            'st paul': 'St. Paul',
            'st petersburg': 'St. Petersburg'
        }
        
        city_lower = city.strip().lower()
        
        # Check for exceptions first
        if city_lower in exceptions:
            return exceptions[city_lower]
        
        # Handle Mc/Mac names
        if city_lower.startswith('mc'):
            parts = city.split()
            normalized_parts = []
            for part in parts:
                if part.lower().startswith('mc'):
                    # McXXX -> McXxx (capitalize third letter too)
                    if len(part) > 2:
                        part = 'Mc' + part[2:].capitalize()
                else:
                    part = part.capitalize()
                normalized_parts.append(part)
            return ' '.join(normalized_parts)
        
        # Standard capitalization: Title case with exceptions
        words = city.split()
        capitalized_words = []
        
        for word in words:
            if '-' in word:
                # Handle hyphenated names
                sub_words = word.split('-')
                capitalized_sub_words = []
                for sub_word in sub_words:
                    if sub_word.lower() in ['st', 'fort', 'mount', 'saint']:
                        capitalized_sub_words.append(sub_word.capitalize())
                    else:
                        capitalized_sub_words.append(sub_word.capitalize())
                capitalized_words.append('-'.join(capitalized_sub_words))
            else:
                capitalized_words.append(word.capitalize())
        
        return ' '.join(capitalized_words)

    def normalize_state_name(self, state):
        """Validate and normalize state abbreviation"""
        if not state:
            return ''
        
        state = state.strip().upper()
        
        # Validate it's a US state
        if state in US_STATES:
            return state
        
        # Try Canadian provinces
        canadian_provinces = {
            'AB': 'Alberta', 'BC': 'British Columbia', 'MB': 'Manitoba',
            'NB': 'New Brunswick', 'NL': 'Newfoundland and Labrador',
            'NS': 'Nova Scotia', 'NT': 'Northwest Territories',
            'NU': 'Nunavut', 'ON': 'Ontario', 'PE': 'Prince Edward Island',
            'QC': 'Quebec', 'SK': 'Saskatchewan', 'YT': 'Yukon'
        }
        
        if state in canadian_provinces:
            return state
        
        return ''

    def extract_city_state(self, donor_name):
        """Extract and normalize city and state from donor name"""
        # Common patterns in BIMI data
        patterns = [
            r'(.+?),\s*([A-Z]{2})\s*\d*$',  # "City, ST" or "City, ST 12345"
            r'(.+?)\s+([A-Z]{2})\s*\d*$',   # "City ST" or "City ST 12345"
            r'(.+?)\s+([A-Z]{2}),?$',       # "City ST," or "City ST"
        ]
        
        original_name = donor_name
        city = ''
        state = ''
        
        for pattern in patterns:
            match = re.search(pattern, donor_name, re.IGNORECASE)
            if match:
                potential_city = match.group(1).strip()
                potential_state = match.group(2).strip().upper()
                
                # Validate state
                normalized_state = self.normalize_state_name(potential_state)
                if normalized_state:
                    city = self.normalize_city_name(potential_city)
                    state = normalized_state
                    
                    # Clean the donor name by removing the city/state part
                    name_pattern = r'^(.*?)\s*' + re.escape(match.group(0)) + r'$'
                    name_match = re.match(name_pattern, original_name, re.IGNORECASE)
                    if name_match:
                        donor_name = name_match.group(1).strip()
                    
                    break
        
        return donor_name, city, state

    def parse_financial_data(self, text_data):
        """Parse financial data with city/state extraction"""
        if not text_data or "Missionary Login" in text_data:
            return None
            
        donors = []
        financial_totals = {}
        lines = text_data.split('\n')
        in_donor_section = False
        
        i = 0
        while i < len(lines):
            line = lines[i].rstrip()
            
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
            
            if in_donor_section and line.strip():
                donor_match = re.match(r'^\s*(\d+)\s+(.*?)\s+\$([\d,]+\.\d+)$', line)
                if donor_match:
                    donor_num = donor_match.group(1).strip()
                    original_name = donor_match.group(2).strip()
                    amount = float(donor_match.group(3).replace(',', ''))
                    
                    # Extract and normalize city/state
                    cleaned_name, city, state = self.extract_city_state(original_name)
                    
                    # Additional cleaning for donor name
                    clean_name = re.sub(r'\s+P\s*O\s*BOX\s+\d+.*', '', cleaned_name, flags=re.IGNORECASE)
                    clean_name = re.sub(r'\s+\d+.*?(?:AVE|ST|RD|BLVD|DR|LN|CT|WAY|PL|TERR).*', '', clean_name, flags=re.IGNORECASE)
                    clean_name = ' '.join(clean_name.split())
                    
                    donors.append({
                        'donor_number': donor_num,
                        'name': clean_name,
                        'original_name': original_name,
                        'amount': amount,
                        'city': city,
                        'state': state,
                        'location': f"{city}, {state}" if city and state else ""
                    })
                    
                    if city and state:
                        print(f"   📍 Found: {clean_name} -> {city}, {state}")
            
            i += 1
        
        print(f"   ✅ Found {len(donors)} donors")
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
        
        session_id = generate_session_id(credentials)
        session['session_id'] = session_id
        
        data_store[session_id] = {
            'credentials': credentials,
            'login_time': datetime.now().isoformat(),
            'data_loaded': False,
            'months_of_history': MONTHS_OF_HISTORY
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
        current_date = datetime.now()
        report_month = current_date.replace(day=1) - timedelta(days=1)
        
        current_data = fetcher.fetch_monthly_data(
            report_month.year, 
            report_month.month, 
            credentials
        )
        
        if not current_data:
            return jsonify({'status': 'error', 'message': 'Failed to fetch current data'})
        
        # Count donors with locations
        donors_with_locations = sum(1 for donor in current_data.get('donors', []) if donor.get('city') and donor.get('state'))
        print(f"📍 Found {donors_with_locations} donors with city/state information")
        
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
            'gross_amount': current_data.get('gross_donations', 0),
            'donors_with_locations': donors_with_locations
        })
        
    except Exception as e:
        print(f"❌ Error in load_initial_data: {e}")
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/dashboard')
def dashboard():
    """Dashboard with current month + optional history"""
    session_id = session.get('session_id')
    
    if not session_id or session_id not in data_store:
        return redirect(url_for('login_page'))
    
    session_data = data_store[session_id]
    
    if not session_data.get('data_loaded'):
        return redirect(url_for('loading'))
    
    print("✅ Showing dashboard")
    
    current_data = session_data.get('current_data', {})
    current_donors = current_data.get('donors', [])
    report_display = session_data.get('report_month', 'Current Month')
    
    # Simple donor classification
    donor_history = session_data.get('donor_history', {})
    new_donors = []
    existing_donors = []
    
    for donor in current_donors:
        if donor['donor_number'] in donor_history:
            existing_donors.append(donor)
        else:
            new_donors.append(donor)
    
    # Build location summary
    location_summary = {}
    for donor in current_donors:
        if donor.get('city') and donor.get('state'):
            location_key = f"{donor['city']}, {donor['state']}"
            if location_key not in location_summary:
                location_summary[location_key] = {
                    'count': 0,
                    'total_amount': 0,
                    'city': donor['city'],
                    'state': donor['state']
                }
            location_summary[location_key]['count'] += 1
            location_summary[location_key]['total_amount'] += donor['amount']
    
    # Sort locations by donor count
    sorted_locations = sorted(location_summary.items(), key=lambda x: x[1]['count'], reverse=True)
    
    return render_template('dashboard.html', 
                         current_data=current_data,
                         new_donors=new_donors,
                         existing_donors=existing_donors,
                         total_donors=len(current_donors),
                         report_display=report_display,
                         gross_donations=current_data.get('gross_donations', 0),
                         net_cash=current_data.get('net_cash', 0),
                         location_summary=sorted_locations,
                         months_count=MONTHS_OF_HISTORY,
                         has_full_history=session_data.get('full_history_loaded', False))

@app.route('/load-full-history')
def load_full_history():
    """Background task to load 6 months of history"""
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
        
        # Re-login to ensure fresh session
        fetcher.login_to_bimi(credentials)
        
        start_time = time.time()
        
        for i in range(min(months_count, 6)):
            # Check timeout (30 seconds max)
            if time.time() - start_time > 30:
                print("⏰ Timeout reached, stopping history load")
                break
            
            month_date = current_date.replace(day=1)
            for _ in range(i):
                if month_date.month == 1:
                    month_date = month_date.replace(year=month_date.year-1, month=12)
                else:
                    month_date = month_date.replace(month=month_date.month-1)
            
            print(f"  Loading month {i+1}/{min(months_count, 6)}: {month_date.strftime('%B %Y')}")
            
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
                    existing_dates = [g['date'] for g in donor_history[donor_id]]
                    if month_key not in existing_dates:
                        donor_history[donor_id].append({
                            'date': month_key,
                            'amount': donor['amount'],
                            'name': donor['name'],
                            'city': donor.get('city', ''),
                            'state': donor.get('state', '')
                        })
            
            time.sleep(1)  # Short delay
        
        if donor_history:
            existing_history = data_store[session_id].get('donor_history', {})
            for donor_id, history in donor_history.items():
                if donor_id in existing_history:
                    existing_dates = [g['date'] for g in existing_history[donor_id]]
                    for gift in history:
                        if gift['date'] not in existing_dates:
                            existing_history[donor_id].append(gift)
                else:
                    existing_history[donor_id] = history
            
            data_store[session_id]['donor_history'] = existing_history
            data_store[session_id]['full_history_loaded'] = True
            data_store[session_id]['months_actually_loaded'] = loaded_months
            
            print(f"✅ History loaded: {len(existing_history)} donors across {loaded_months} months")
            return jsonify({
                'status': 'complete', 
                'donors_loaded': len(existing_history),
                'months_loaded': loaded_months
            })
        else:
            return jsonify({'status': 'error', 'message': 'No history data could be loaded'})
        
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
        'months_config': MONTHS_OF_HISTORY,
        'has_data': session_data.get('data_loaded', False),
        'has_full_history': session_data.get('full_history_loaded', False),
        'current_donors': len(session_data.get('current_data', {}).get('donors', [])),
        'history_donors': len(session_data.get('donor_history', {}))
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
