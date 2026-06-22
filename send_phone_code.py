import os, time, sys
from pathlib import Path

# Load .env
env_path = Path('.env')
for line in env_path.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    if '=' not in line:
        continue
    key, _, value = line.partition('=')
    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

from browserbase import Browserbase
from playwright.sync_api import sync_playwright

api_key = os.environ.get('BROWSERBASE_API_KEY', '')
project_id = os.environ.get('BROWSERBASE_PROJECT_ID', '')

bb = Browserbase(api_key=api_key)
print('Creating session...', flush=True)
session = bb.sessions.create(project_id=project_id)
print(f'Session: {session.id}', flush=True)

# Save session ID for later use
Path('data/pending_session.txt').write_text(session.id)

with sync_playwright() as p:
    print('Connecting to browser...', flush=True)
    browser = p.chromium.connect_over_cdp(session.connect_url)
    context = browser.contexts[0]
    page = context.pages[0] if context.pages else context.new_page()
    
    # Login
    print('Navigating to login...', flush=True)
    page.goto('https://x.com/i/flow/login', timeout=90000)
    print('Login page loaded', flush=True)
    time.sleep(5)
    
    print('Entering username...', flush=True)
    username_input = page.wait_for_selector('input[name="username_or_email"]', timeout=30000)
    username_input.fill('centauereaeton')
    time.sleep(1)
    
    continue_btn = page.query_selector('button:has-text("Continue")')
    if continue_btn:
        continue_btn.click()
    else:
        username_input.press("Enter")
    time.sleep(4)
    
    print('Entering password...', flush=True)
    password_input = page.wait_for_selector('input[type="password"]', timeout=30000)
    password_input.fill('Switch_2017')
    time.sleep(1)
    
    login_btn = page.query_selector('button:has-text("Log in")')
    if login_btn:
        login_btn.click(force=True)
    else:
        password_input.press("Enter")
    time.sleep(5)
    
    print(f'URL after login: {page.url}', flush=True)
    
    # Phone verification
    print('Entering phone number...', flush=True)
    phone_input = page.wait_for_selector('input[type="tel"], input[name="phone_number"]', timeout=30000)
    phone_input.fill('+861****0615')
    time.sleep(1)
    
    next_btn = page.query_selector('button:has-text("Next"), button:has-text("Continue")')
    if next_btn:
        next_btn.click()
        print('Clicked Next - sending SMS code', flush=True)
    else:
        phone_input.press("Enter")
    
    time.sleep(5)
    page.screenshot(path='data/phone_sent.png', full_page=True)
    print('Screenshot saved: data/phone_sent.png', flush=True)
    print(f'URL: {page.url}', flush=True)
    
    # Check for code input
    code_input = page.query_selector('input[type="tel"]')
    if code_input:
        print('VERIFICATION CODE INPUT READY', flush=True)
        print('Waiting for code input to be ready...', flush=True)
        
        # Wait for user to provide code (up to 10 minutes)
        for i in range(60):
            time.sleep(10)
            # Check if a code file exists
            code_file = Path('data/sms_code.txt')
            if code_file.exists():
                code = code_file.read_text().strip()
                if code:
                    print(f'Found code: {code}', flush=True)
                    code_input.fill(code)
                    time.sleep(1)
                    
                    # Click confirm
                    confirm_btn = page.query_selector('button:has-text("Next"), button:has-text("Confirm")')
                    if confirm_btn:
                        confirm_btn.click()
                    else:
                        code_input.press("Enter")
                    
                    time.sleep(5)
                    page.screenshot(path='data/after_verify.png', full_page=True)
                    print(f'After verify URL: {page.url}', flush=True)
                    
                    # Now scrape tweets
                    print('Navigating to profile...', flush=True)
                    page.goto('https://x.com/aleabitoreddit', timeout=90000)
                    time.sleep(5)
                    
                    # Scroll and collect tweets
                    import json, random
                    all_tweets = []
                    seen_ids = set()
                    
                    for scroll in range(40):
                        articles = page.query_selector_all('article')
                        new_count = 0
                        for el in articles:
                            try:
                                full_text = el.inner_text()
                                link_el = el.query_selector('a[href*="/status/"]')
                                href = (link_el.get_attribute('href') or '') if link_el else ''
                                tid = ''
                                if '/status/' in href:
                                    tid = href.split('/status/')[1].split('?')[0]
                                time_el = el.query_selector('time')
                                time_str = (time_el.get_attribute('datetime') or '') if time_el else ''
                                
                                if tid and tid not in seen_ids and full_text.strip():
                                    seen_ids.add(tid)
                                    all_tweets.append({
                                        'id': tid,
                                        'text': full_text.strip(),
                                        'created_at': time_str,
                                        'url': f'https://x.com/aleabitoreddit/status/{tid}',
                                    })
                                    new_count += 1
                            except:
                                continue
                        
                        print(f'Scroll {scroll+1}: +{new_count} new (total {len(all_tweets)})', flush=True)
                        
                        if new_count == 0 and scroll > 5:
                            break
                        
                        scroll_y = random.randint(500, 1200)
                        page.evaluate(f'window.scrollBy(0, {scroll_y})')
                        time.sleep(random.uniform(2, 4))
                    
                    # Save
                    Path('data').mkdir(exist_ok=True)
                    with open('data/latest_tweets.json', 'w') as f:
                        json.dump(all_tweets, f, ensure_ascii=False, indent=2)
                    print(f'Done! {len(all_tweets)} tweets saved', flush=True)
                    
                    # Cleanup
                    code_file.unlink(missing_ok=True)
                    break
            else:
                print(f'Waiting for SMS code... ({(i+1)*10}s)', flush=True)
        else:
            print('Timeout waiting for SMS code', flush=True)
    else:
        print('No code input found', flush=True)

# Release session
try:
    bb.sessions.update(session.id, status='REQUEST_RELEASE')
    print('Session released', flush=True)
except:
    pass
