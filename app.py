from flask import Flask, render_template, request, session, redirect, url_for, jsonify, Response
import requests
from datetime import datetime, timedelta
import re
import os
import json
import time
import threading
from dateutil.relativedelta import relativedelta  # For safe month calculations

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-key-123')

# Simple in-memory storage
fetchers = {}
background_jobs = {}

class WorkingBIMIFetcher:
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
        self.session.headers.update(self.headers)
    
    def login(self, credentials):
        """EXACT same as working desktop version"""
        print(f"🔐 Logging into BIMI for {credentials['account_number']}...")
        
        response = self.session.post(
            'https://missionary.bimi.org/home.php',
            data=credentials,
            headers=self.headers,
            allow_redirects=True,
            timeout=30
        )
        
        self.is_logged_in = (
            response.status_code == 200 and 
            "Missionary Login" not in response.text and
            len(response.text) > 1000
        )
        
        if self.is_logged_in:
            print(f"✅ Login successful ({len(response.text)} chars)")
        else:
            print(f"❌ Login failed")
        
        return self.is_logged_in
    
    def fetch_month_data(self, year, month, credentials):
        """Fetch monthly data"""
        text_url = "https://missionary.bimi.org/common/Finances/ViewStatementText.php"
        params = {
            'MissionaryNumber': credentials['account_number'],
            'StatementYear': year,
            'StatementMonth': month
        }
        
        response = self.session.get(
            text_url,
            params=params,
            timeout=30
        )
        
        if response.status_code == 200 and "Missionary Login" not in response.text:
            return self.parse_simple(response.text)
        
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
        
        return donors, financial_totals

# ============================================================================
# PROVEN 12-MONTH FETCH STRATEGY (FIXED Date Calculation)
# ============================================================================

def fetch_12_months_proven(fetcher, credentials, job_id=None):
    """
    PROVEN STRATEGY: 1.5s delays, one retry on failure
    FIXED: Date calculation bug using relativedelta
    """
    if job_id:
        background_jobs[job_id] = {
            'status': 'running',
            'progress': 0,
            'messages': [],
            'results': [],
            'started': datetime.now().isoformat()
        }
    
    def update_job(message, progress=None):
        if job_id and job_id in background_jobs:
            if progress is not None:
                background_jobs[job_id]['progress'] = progress
            background_jobs[job_id]['messages'].append({
                'time': datetime.now().strftime('%H:%M:%S'),
                'message': message
            })
    
    all_data = []
    # Use 1st of previous month to avoid day-of-month issues
    current_date = datetime.now().replace(day=1)
    report_date = current_date - relativedelta(months=1)  # Previous month
    
    update_job("🚀 Starting 12-month fetch (Proven Strategy)", 0)
    update_job("⚙️ Using 1.5s delays (tested & reliable)", 0)
    update_job(f"📅 Report month: {report_date.strftime('%B %Y')}", 0)
    
    total_start = time.time()
    
    for month_num in range(12):
        # FIXED: Safe month calculation using relativedelta
        target_date = report_date - relativedelta(months=month_num)
        year, month = target_date.year, target_date.month
        
        progress = int((month_num + 1) / 12 * 100)
        
        update_job(f"📅 [{month_num+1}/12] {target_date.strftime('%B %Y')}: Fetching...", progress)
        
        # Fetch with one retry if needed
        fetch_start = time.time()
        data = None
        try:
            data = fetcher.fetch_month_data(year, month, credentials)
        except Exception as e:
            update_job(f"  ❌ Exception: {str(e)}", progress)
        
        fetch_time = time.time() - fetch_start
        
        if data:
            donors, totals = data
            month_data = {
                'month': target_date.strftime('%B %Y'),
                'year': year,
                'month_num': month,
                'donors': donors,
                'totals': totals,
                'donor_count': len(donors),
                'total_amount': totals.get('total_donations', 0),
                'net_cash': totals.get('net_available_cash', 0),
                'fetch_time': fetch_time
            }
            all_data.append(month_data)
            
            update_job(
                f"  ✅ {len(donors)} donors, ${totals.get('total_donations', 0):.2f} ({fetch_time:.2f}s)",
                progress
            )
            
            # Store current month immediately for dashboard
            if month_num == 0:  # Current report month
                # Find session_id for this fetcher
                for sid, session_data in fetchers.items():
                    if session_data.get('fetcher') == fetcher:
                        fetchers[sid]['current_data'] = {
                            'donors': donors,
                            'totals': totals,
                            'report_month': target_date.strftime('%B %Y')
                        }
                        break
        else:
            # Try re-login and retry
            update_job(f"  ⚠️ First attempt failed, re-authenticating...", progress)
            fetcher.login(credentials)
            time.sleep(1.0)
            
            # Retry
            fetch_start = time.time()
            try:
                data = fetcher.fetch_month_data(year, month, credentials)
            except Exception as e:
                update_job(f"  ❌ Retry also failed: {str(e)}", progress)
            
            fetch_time = time.time() - fetch_start
            
            if data:
                donors, totals = data
                month_data = {
                    'month': target_date.strftime('%B %Y'),
                    'year': year,
                    'month_num': month,
                    'donors': donors,
                    'totals': totals,
                    'donor_count': len(donors),
                    'total_amount': totals.get('total_donations', 0),
                    'net_cash': totals.get('net_available_cash', 0),
                    'fetch_time': fetch_time
                }
                all_data.append(month_data)
                update_job(f"  ✅ Retry successful: {len(donors)} donors", progress)
            else:
                update_job(f"  ❌ Failed after retry, skipping month", progress)
        
        # PROVEN optimal delay: 1.5 seconds
        if month_num < 11:
            update_job(f"  ⏳ Waiting 1.5s...", progress)
            time.sleep(1.5)
    
    # Complete
    total_time = time.time() - total_start
    
    if all_data:
        total_donors = sum(item['donor_count'] for item in all_data)
        total_amount = sum(item['total_amount'] for item in all_data)
        
        update_job(f"\n🎯 FETCH COMPLETE: {len(all_data)}/12 months", 100)
        update_job(f"📊 {total_donors} total donors", 100)
        update_job(f"💰 ${total_amount:.2f} total donations", 100)
        update_job(f"⏱️ {total_time:.1f} seconds total", 100)
        
        # Store full history
        for sid, session_data in fetchers.items():
            if session_data.get('fetcher') == fetcher:
                session_data['full_history'] = all_data
                session_data['history_loaded'] = True
                break
        
        result = {
            'success': True,
            'months_retrieved': len(all_data),
            'total_donors': total_donors,
            'total_amount': total_amount,
            'total_time': total_time
        }
    else:
        update_job("❌ No data retrieved", 100)
        result = {'success': False, 'error': 'No data retrieved'}
    
    if job_id:
        background_jobs[job_id].update({
            'status': 'complete',
            'progress': 100,
            'completed': datetime.now().isoformat(),
            'result': result
        })
    
    return result

# ============================================================================
# FLASK ROUTES
# ============================================================================

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
    
    # Create a fetcher
    fetcher = WorkingBIMIFetcher()
    
    # Try login
    if fetcher.login(credentials):
        # Store the WHOLE fetcher (with its session) in memory
        session_id = f"{credentials['account_number']}_{int(time.time())}"
        session['session_id'] = session_id
        fetchers[session_id] = {
            'fetcher': fetcher,
            'credentials': credentials,
            'created': datetime.now(),
            'history_loaded': False
        }
        
        # Immediately fetch current month data
        current_date = datetime.now().replace(day=1)
        report_date = current_date - relativedelta(months=1)
        
        data = fetcher.fetch_month_data(
            report_date.year,
            report_date.month,
            credentials
        )
        
        if data:
            donors, totals = data
            fetchers[session_id]['current_data'] = {
                'donors': donors,
                'totals': totals,
                'report_month': report_date.strftime('%B %Y')
            }
            
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error='Login succeeded but could not fetch data')
    
    return render_template('login.html', error='Login failed - check credentials')

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
    
    # Check if we have full history
    has_full_history = session_data.get('history_loaded', False)
    
    # Prepare history summary if available
    history_summary = []
    total_amount = 0
    total_donors = 0
    avg_amount = 0
    avg_donors = 0
    max_amount = 0
    min_amount = float('inf')
    
    if has_full_history and 'full_history' in session_data:
        # Prepare data for chart
        for month_data in session_data['full_history']:
            amount = month_data['total_amount']
            donor_count = month_data['donor_count']
            
            history_summary.append({
                'month': month_data['month'],
                'total_amount': amount,
                'net_cash': month_data.get('net_cash', 0),
                'donor_count': donor_count
            })
            
            # Calculate statistics
            total_amount += amount
            total_donors += donor_count
            max_amount = max(max_amount, amount)
            min_amount = min(min_amount, amount)
        
        # Calculate averages
        if history_summary:
            avg_amount = total_amount / len(history_summary)
            avg_donors = total_donors / len(history_summary)
    
    return render_template('dashboard.html',
                         donors=donors,
                         totals=totals,
                         report_month=report_month,
                         donor_count=len(donors),
                         has_full_history=has_full_history,
                         history_summary=history_summary,
                         total_amount=total_amount,
                         total_donors=total_donors,
                         avg_amount=avg_amount,
                         avg_donors=avg_donors,
                         max_amount=max_amount,
                         min_amount=min_amount if min_amount != float('inf') else 0)

@app.route('/api/load-full-year', methods=['POST'])
def load_full_year():
    """Start background job to load 12 months using PROVEN strategy"""
    session_id = session.get('session_id')
    if not session_id or session_id not in fetchers:
        return jsonify({'error': 'Not logged in'}), 401
    
    # Create unique job ID
    job_id = f"{session_id}_{int(time.time())}"
    
    session_data = fetchers[session_id]
    fetcher = session_data['fetcher']
    credentials = session_data['credentials']
    
    # Start background thread
    def run_fetch():
        fetch_12_months_proven(fetcher, credentials, job_id)
    
    thread = threading.Thread(target=run_fetch)
    thread.daemon = True
    thread.start()
    
    return jsonify({
        'success': True,
        'job_id': job_id,
        'message': 'Started loading 12 months of data (Proven Strategy)'
    })

@app.route('/api/job-status/<job_id>')
def job_status(job_id):
    """Check status of background job"""
    if job_id in background_jobs:
        job = background_jobs[job_id]
        return jsonify({
            'status': job['status'],
            'progress': job['progress'],
            'messages': job.get('messages', [])[-10:],  # Last 10 messages
            'result': job.get('result'),
            'started': job.get('started'),
            'completed': job.get('completed')
        })
    return jsonify({'error': 'Job not found'}), 404

@app.route('/api/get-full-history')
def get_full_history():
    """Get complete stored history data"""
    session_id = session.get('session_id')
    if not session_id or session_id not in fetchers:
        return jsonify({'error': 'Not logged in'}), 401
    
    session_data = fetchers[session_id]
    
    if 'full_history' in session_data:
        return jsonify({
            'success': True,
            'history': session_data['full_history'],
            'total_months': len(session_data['full_history'])
        })
    
    return jsonify({'success': False, 'message': 'No history loaded yet'})

@app.route('/api/test-boundary')
def test_boundary():
    """Quick synchronous test of 12-month fetch"""
    session_id = session.get('session_id')
    if not session_id or session_id not in fetchers:
        return jsonify({'error': 'Not logged in'}), 401
    
    session_data = fetchers[session_id]
    fetcher = session_data['fetcher']
    credentials = session_data['credentials']
    
    # Run synchronous test
    print("🧪 Running boundary test...")
    all_data = []
    current_date = datetime.now().replace(day=1)
    report_date = current_date - relativedelta(months=1)
    
    for month_num in range(12):
        target_date = report_date - relativedelta(months=month_num)
        year, month = target_date.year, target_date.month
        
        print(f"  [{month_num+1}/12] {target_date.strftime('%B %Y')}: ", end="")
        
        data = fetcher.fetch_month_data(year, month, credentials)
        
        if data:
            donors, totals = data
            all_data.append({
                'month': target_date.strftime('%B %Y'),
                'donors': len(donors),
                'total': totals.get('total_donations', 0)
            })
            print(f"✅ {len(donors)} donors")
        else:
            print(f"❌ Failed")
        
        if month_num < 11:
            time.sleep(1.5)
    
    return jsonify({
        'success': True,
        'results': all_data,
        'retrieved': len(all_data)
    })

@app.route('/logout')
def logout():
    session_id = session.get('session_id')
    if session_id in fetchers:
        del fetchers[session_id]
    
    # Clean up old background jobs
    current_time = time.time()
    for job_id in list(background_jobs.keys()):
        job = background_jobs[job_id]
        if 'started' in job:
            try:
                job_time = datetime.fromisoformat(job['started']).timestamp()
                if current_time - job_time > 300:  # 5 minutes old
                    del background_jobs[job_id]
            except:
                pass
    
    session.clear()
    return redirect(url_for('home'))

# ============================================================================
# DEBUG ENDPOINTS
# ============================================================================

@app.route('/api/debug-next-month')
def debug_next_month():
    """Debug month calculation"""
    current_date = datetime.now().replace(day=1)
    report_date = current_date - relativedelta(months=1)
    
    months = []
    for i in range(12):
        target_date = report_date - relativedelta(months=i)
        months.append({
            'index': i,
            'date': target_date.strftime('%B %Y'),
            'year': target_date.year,
            'month': target_date.month
        })
    
    return jsonify({'months': months})

# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

