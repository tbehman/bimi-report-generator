from flask import Flask, render_template, request, session, redirect, url_for
import requests
from datetime import datetime, timedelta
import re
import os
import json
from collections import Counter

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')

# BIMI Data Fetcher with Enhanced Analytics
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
        
        response = self.session.post(
            login_url, 
            data=credentials,
            headers=self.headers,
            allow_redirects=True
        )
        
        if response.status_code == 200 and "Missionary Login" not in response.text:
            print("✅ Login successful!")
            self.is_logged_in = True
            return True
        else:
            print("❌ Login failed")
            self.is_logged_in = False
            return False
    
    def fetch_financial_text(self, year, month, credentials):
        """Fetch text version of financial statement"""
        text_url = "https://missionary.bimi.org/common/Finances/ViewStatementText.php"
        params = {
            'MissionaryNumber': credentials['account_number'],
            'StatementYear': year,
            'StatementMonth': month
        }
        
        print(f"📊 Fetching financial data for {month}/{year}...")
        
        if not self.is_logged_in:
            print("   ⚠️ Not logged in, attempting login...")
            if not self.login_to_bimi(credentials):
                return None
        
        response = self.session.get(text_url, params=params, headers=self.headers)
        
        if response.status_code == 200:
            return response.text
        else:
            print(f"❌ Failed to fetch data for {month}/{year}: {response.status_code}")
            return None

    def is_address_line(self, line):
        """Check if line is likely an address line (city/state/zip)"""
        line = line.rstrip()
        
        # Empty or very short lines are not address lines
        if len(line.strip()) < 5:
            return False
            
        patterns = [
            r'^\s{30,}.*[A-Z]{2}\s+\d{5}',  # Padded + STATE ZIP
            r'^\s{30,}.*Canada',             # Padded + Canada
            r'^\s{30,}[A-Z][A-Za-z\s]+,?\s*[A-Z]{2}',  # Padded + City, ST
            r'^\s{30,}[A-Z][A-Za-z\s]+\s+[A-Z]{2}\s+\d{5}',  # Padded + City ST ZIP
        ]
        return any(re.search(pattern, line) for pattern in patterns)

    def extract_city_state_from_address(self, address_line):
        """Extract city and state with high accuracy from address line"""
        address_line = address_line.strip()
        
        # Pattern 1: "CITY STATE ZIP" (most common)
        match = re.search(r'^([A-Z][A-Za-z\s]+?)\s+([A-Z]{2})\s+\d{5}', address_line)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        
        # Pattern 2: "CITY, STATE ZIP" 
        match = re.search(r'^([A-Z][A-Za-z\s]+?),?\s+([A-Z]{2})\s+\d{5}', address_line)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        
        # Pattern 3: Canadian addresses
        if 'Canada' in address_line:
            # Extract everything before "Canada" as city
            city_match = re.search(r'^([A-Z][A-Za-z\s]+)\s+Canada', address_line)
            if city_match:
                return city_match.group(1).strip(), 'Canada'
        
        # Pattern 4: Just city and state without zip
        match = re.search(r'^([A-Z][A-Za-z\s]+?)\s+([A-Z]{2})\s*$', address_line)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        
        return '', ''

    def clean_donor_name(self, donor_name):
        """Clean donor name by removing common address patterns"""
        # Remove PO Box patterns
        clean_name = re.sub(r'\s+P\s*O\s*BOX\s+\d+.*', '', donor_name, flags=re.IGNORECASE)
        
        # Remove street address patterns (numbers followed by street types)
        clean_name = re.sub(r'\s+\d+.*?(?:AVE|AVENUE|ST|STREET|RD|ROAD|BLVD|DR|DRIVE|LANE|LN).*', '', clean_name, flags=re.IGNORECASE)
        
        # Remove extra spaces and trim
        clean_name = ' '.join(clean_name.split())
        
        return clean_name

    def parse_financial_data_multiline(self, text_data):
        """Multi-line parser that handles donor numbers, names, amounts, and addresses"""
        if "Missionary Login" in text_data:
            print("   ❌ Received login page instead of financial data")
            return None
            
        donors = []
        financial_totals = {}
        
        lines = text_data.split('\n')
        in_donor_section = False
        
        print("🔍 Parsing financial data with multi-line parser...")
        
        i = 0
        while i < len(lines):
            line = lines[i].rstrip()
            
            # Section detection
            if "YOUR DONATIONS FOR THIS MONTH" in line:
                in_donor_section = True
                i += 1
                continue
            elif "YOUR DEDUCTIONS" in line and in_donor_section:
                in_donor_section = False
                i += 1
                continue
            elif "TOTAL DONATIONS FOR THIS MONTH" in line:
                total_match = re.search(r'\$([\d,]+\.\d+)', line)
                if total_match:
                    financial_totals['total_donations'] = float(total_match.group(1).replace(',', ''))
                    print(f"   ✅ Found total donations: ${financial_totals['total_donations']:,.2f}")
            elif "YOUR NET AVAILABLE CASH" in line:
                cash_match = re.search(r'\$([\d,]+\.\d+)', line)
                if cash_match:
                    financial_totals['net_available_cash'] = float(cash_match.group(1).replace(',', ''))
                    print(f"   ✅ Found net available cash: ${financial_totals['net_available_cash']:,.2f}")
            
            # Donor parsing in donor section
            if in_donor_section and line.strip():
                # Check if this is a donor line (has donor number and amount)
                donor_match = re.match(r'^\s*(\d+)\s+(.*?)\s+\$([\d,]+\.\d+)$', line)
                
                if donor_match:
                    donor_num = donor_match.group(1).strip()
                    donor_name = donor_match.group(2).strip()
                    amount = float(donor_match.group(3).replace(',', ''))
                    
                    # Initialize city/state
                    city, state = '', ''
                    
                    # Look ahead for address line
                    if i + 1 < len(lines) and self.is_address_line(lines[i + 1]):
                        address_line = lines[i + 1].strip()
                        city, state = self.extract_city_state_from_address(address_line)
                        print(f"   📍 Found location: {city}, {state} for donor {donor_num}")
                        i += 1  # Skip the address line
                    
                    # Clean donor name
                    clean_name = self.clean_donor_name(donor_name)
                    
                    donors.append({
                        'donor_number': donor_num,
                        'name': clean_name,
                        'original_name': donor_name,
                        'amount': amount,
                        'city': city,
                        'state': state
                    })
                    print(f"   ✅ Parsed donor: {clean_name} (${amount})")
            
            i += 1
        
        print(f"   ✅ Successfully parsed {len(donors)} donors with multi-line parser")
        return donors, financial_totals

    def fetch_monthly_data(self, year, month, credentials):
        """Single method that handles login and data fetching"""
        print(f"📅 Processing {month}/{year}...")
        
        text_data = self.fetch_financial_text(year, month, credentials)
        if not text_data:
            print(f"   ❌ No data retrieved for {month}/{year}")
            return None
        
        result = self.parse_financial_data_multiline(text_data)
        if not result:
            return None
            
        donors, financial_totals = result
        
        totals = {
            'gross_donations': financial_totals.get('total_donations', 0),
            'net_cash': financial_totals.get('net_available_cash', 0),
            'donors': donors
        }
        
        print(f"   📊 Totals: Gross=${totals['gross_donations']:,.2f}, Net=${totals['net_cash']:,.2f}")
        return totals

    def collect_donor_history(self, credentials, months=12):
        """Collect donor history for pattern analysis"""
        print(f"\n🕐 COLLECTING {months} MONTHS OF DONOR HISTORY...")
        
        today = datetime.now()
        donor_history = {}
        
        for i in range(months):
            month_date = today.replace(day=1)
            for _ in range(i):
                if month_date.month == 1:
                    month_date = month_date.replace(year=month_date.year-1, month=12)
                else:
                    month_date = month_date.replace(month=month_date.month-1)
            
            year = month_date.year
            month = month_date.month
            
            print(f"📅 Analyzing {year}-{month:02d}...")
            data = self.fetch_monthly_data(year, month, credentials)
            
            if data and data.get('donors'):
                for donor in data['donors']:
                    donor_id = donor['donor_number']
                    if donor_id not in donor_history:
                        donor_history[donor_id] = []
                    
                    donor_history[donor_id].append({
                        'date': f"{year}-{month:02d}",
                        'amount': donor['amount'],
                        'name': donor['name'],
                        'city': donor['city'],
                        'state': donor['state']
                    })
        
        print(f"✅ Collected history for {len(donor_history)} unique donors")
        return donor_history

    def analyze_giving_pattern(self, donor_id, donor_history):
        """Analyze donor's giving pattern and return frequency, confidence, and typical amount"""
        if donor_id not in donor_history or len(donor_history[donor_id]) < 2:
            return "One-Time", "Low", 0, 0
        
        gifts = donor_history[donor_id]
        gifts.sort(key=lambda x: x['date'])
        
        # Calculate intervals between consecutive gifts
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
            return "One-Time", "Low", typical_amount, 0
        
        # Determine frequency pattern
        avg_interval = sum(intervals) / len(intervals)
        interval_counter = Counter(intervals)
        most_common_interval, count = interval_counter.most_common(1)[0]
        consistency = count / len(intervals)
        
        # Map to frequency names
        frequency_map = {
            1: "Monthly",
            2: "Bi-Monthly", 
            3: "Quarterly",
            4: "Every 4 Months",
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
        
        return frequency, confidence, typical_amount, avg_interval

    def classify_donors(self, current_month_donors, donor_history):
        """Categorize donors into NEW, CHANGED, MISSED, NORMAL"""
        new_donors = []
        changed_donors = []
        normal_donors = []
        missed_donors = []
        
        current_month_dict = {d['donor_number']: d for d in current_month_donors}
        
        print(f"🔍 CLASSIFICATION DEBUG:")
        print(f"   Donor history entries: {len(donor_history)}")
        print(f"   Current month donors: {len(current_donors)}")
        print(f"   Donors in both: {len(set(current_month_dict.keys()) & set(donor_history.keys()))}")
        
        # Analyze each donor in history
        for donor_id, history in donor_history.items():
            frequency, confidence, typical_amount, avg_interval = self.analyze_giving_pattern(donor_id, donor_history)
            
            if donor_id in current_month_dict:
                # Donor gave this month
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
                
                if frequency == "One-Time" or confidence == "Low":
                    new_donors.append(donor_data)
                elif abs(change_percent) > 25:  # Significant change
                    changed_donors.append(donor_data)
                else:
                    normal_donors.append(donor_data)
            else:
                # Donor didn't give this month - check if they should have
                if frequency != "One-Time" and confidence in ["High", "Medium"]:
                    # Check if they're overdue
                    last_gift_date = max([g['date'] for g in history])
                    last_year, last_month = map(int, last_gift_date.split('-'))
                    current_year, current_month = datetime.now().year, datetime.now().month
                    months_since = (current_year - last_year) * 12 + (current_month - last_month)
                    
                    if months_since >= avg_interval:
                        missed_donors.append({
                            'donor_number': donor_id,
                            'name': history[0]['name'],
                            'city': history[0].get('city', ''),
                            'state': history[0].get('state', ''),
                            'frequency': frequency,
                            'confidence': confidence,
                            'typical_amount': typical_amount,
                            'expected_amount': typical_amount
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
        
        # Collect donor history for analysis (now 12 months)
        donor_history = fetcher.collect_donor_history(credentials, months=12)
        
        # Store in session
        session['bimi_credentials'] = credentials
        session['donor_history'] = donor_history
        print(f"💾 Stored donor history for {len(donor_history)} donors")
        
        return redirect(url_for('dashboard'))
    else:
        print("❌ Login failed")
        return "Login failed - check your credentials", 401

@app.route('/dashboard')
def dashboard():
    if 'bimi_credentials' not in session:
        return redirect(url_for('login_page'))
    
    credentials = session['bimi_credentials']
    donor_history = session.get('donor_history', {})
    
    print(f"📊 Loading dashboard with history for {len(donor_history)} donors")
    
    fetcher = BIMIFetcher()
    
    # Calculate report month (previous month)
    current_date = datetime.now()
    report_month = current_date.replace(day=1) - timedelta(days=1)
    report_display = report_month.strftime('%B %Y')
    
    # Get current month data (the report month)
    year = report_month.year
    month = report_month.month
    
    print(f"📊 Fetching report data for {report_display} ({month}/{year})...")
    current_data = fetcher.fetch_monthly_data(year, month, credentials)
    
    if not current_data:
        return "Failed to fetch current month data", 500
    
    current_donors = current_data.get('donors', [])
    
    # Get last 12 months for average calculation
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
        
        month_data = fetcher.fetch_monthly_data(year_val, month_val, credentials)
        if month_data and month_data['gross_donations'] > 0:
            months_data.append({
                'month': f"{month_date.strftime('%B %Y')}",
                'gross_donations': month_data['gross_donations'],
                'net_cash': month_data['net_cash']
            })
    
    # Calculate 12-month average and enhancements
    if months_data:
        avg_gross = sum(m['gross_donations'] for m in months_data) / len(months_data)
        for month_data in months_data:
            dollar_diff = month_data['gross_donations'] - avg_gross
            percent_diff = (dollar_diff / avg_gross) * 100
            month_data['dollar_diff'] = dollar_diff
            month_data['percent_diff'] = percent_diff
        current_month = months_data[0] if months_data else None
    else:
        avg_gross = 0
        current_month = None
    
    # Classify donors using the stored history
    print(f"🔍 Classifying {len(current_donors)} current donors...")
    new_donors, changed_donors, missed_donors, normal_donors = fetcher.classify_donors(current_donors, donor_history)
    
    print(f"📋 Classification results:")
    print(f"   🆕 NEW: {len(new_donors)}")
    print(f"   📈 CHANGED: {len(changed_donors)}")
    print(f"   ❌ MISSED: {len(missed_donors)}")
    print(f"   ✅ NORMAL: {len(normal_donors)}")
    
    return render_template('dashboard.html', 
                         months_data=months_data,
                         average_gross=avg_gross,
                         current_month=current_month,
                         new_donors=new_donors,
                         changed_donors=changed_donors,
                         missed_donors=missed_donors,
                         normal_donors=normal_donors,
                         total_donors=len(current_donors),
                         report_display=report_display,
                         current_date=current_date)

@app.route('/logout')
def logout():
    """Clear the session"""
    session.clear()
    return redirect(url_for('login_page'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
