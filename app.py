from flask import Flask, render_template, request, session, redirect, url_for
import requests
from datetime import datetime, timedelta
import re
import os

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')

# BIMI Data Fetcher
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
        
        # Ensure we're logged in first
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
    
    def parse_financial_data_simple(self, text_data):
        """Improved parser - extract donor numbers, names, and amounts"""
        if "Missionary Login" in text_data:
            print("   ❌ Received login page instead of financial data")
            return None
            
        donors = []
        financial_totals = {}
        
        lines = text_data.split('\n')
        in_donor_section = False
        
        print("🔍 Parsing financial data...")
        
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
                    print(f"   ✅ Found total donations: ${financial_totals['total_donations']:,.2f}")
            elif "YOUR NET AVAILABLE CASH" in line:
                cash_match = re.search(r'\$([\d,]+\.\d+)', line)
                if cash_match:
                    financial_totals['net_available_cash'] = float(cash_match.group(1).replace(',', ''))
                    print(f"   ✅ Found net available cash: ${financial_totals['net_available_cash']:,.2f}")
            
            if in_donor_section and line.strip():
                donor_match = re.match(r'^\s*(\d+)\s+(.*?)\s+\$([\d,]+\.\d+)$', line)
                if donor_match:
                    donor_num = donor_match.group(1).strip()
                    donor_name = donor_match.group(2).strip()
                    amount = float(donor_match.group(3).replace(',', ''))
                    
                    donor_name = ' '.join(donor_name.split())
                    
                    donors.append({
                        'donor_number': donor_num,
                        'name': donor_name,
                        'amount': amount
                    })
        
        print(f"   ✅ Parsed {len(donors)} donors")
        return donors, financial_totals

    def fetch_monthly_data(self, year, month, credentials):
        """Single method that handles login and data fetching"""
        print(f"📅 Processing {month}/{year}...")
        
        text_data = self.fetch_financial_text(year, month, credentials)
        if not text_data:
            print(f"   ❌ No data retrieved for {month}/{year}")
            return None
        
        result = self.parse_financial_data_simple(text_data)
        if not result:
            return None
            
        donors, financial_totals = result
        
        totals = {
            'gross_donations': financial_totals.get('total_donations', 0),
            'net_cash': financial_totals.get('net_available_cash', 0)
        }
        
        print(f"   📊 Totals: Gross=${totals['gross_donations']:,.2f}, Net=${totals['net_cash']:,.2f}")
        return totals

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
        session['bimi_credentials'] = credentials
        return redirect(url_for('dashboard'))
    else:
        print("❌ Login failed")
        return "Login failed - check your credentials", 401

@app.route('/dashboard')
def dashboard():
    if 'bimi_credentials' not in session:
        return redirect(url_for('login_page'))
    
    credentials = session['bimi_credentials']
    fetcher = BIMIFetcher()
    
    # Get last 6 months of data
    months_data = []
    today = datetime.now()
    
    print(f"📊 Fetching data for account: {credentials['account_number']}")
    
    for i in range(6):
        month_date = today.replace(day=1)
        for _ in range(i):
            if month_date.month == 1:
                month_date = month_date.replace(year=month_date.year-1, month=12)
            else:
                month_date = month_date.replace(month=month_date.month-1)
        
        year = month_date.year
        month = month_date.month
        
        print(f"📅 Fetching {year}-{month:02d}...")
        data = fetcher.fetch_monthly_data(year, month, credentials)
        
        if data and (data['gross_donations'] > 0 or data['net_cash'] > 0):
            months_data.append({
                'month': f"{month_date.strftime('%B %Y')}",
                'gross_donations': data['gross_donations'],
                'net_cash': data['net_cash']
            })
    
    # Calculate 6-month average
    if months_data:
        avg_gross = sum(m['gross_donations'] for m in months_data) / len(months_data)
        current_month = months_data[0] if months_data else None
    else:
        avg_gross = 0
        current_month = None
    
    return render_template('dashboard.html', 
                         months_data=months_data,
                         average_gross=avg_gross,
                         current_month=current_month)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)