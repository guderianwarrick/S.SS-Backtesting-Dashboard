"""
Twitter/X.com scraper using Browserbase and Playwright.
With login support to access more tweets.
"""
import json
import logging
import random
import sys
import time
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import os  # noqa: E402

API_KEY=os.getenv("BROWSERBASE_API_KEY") or ""
PROJECT_ID = os.getenv("BROWSERBASE_PROJECT_ID") or ""

# X.com credentials
X_USERNAME = "centauereaeton"
X_PASSWORD = "Switch_2017"

from browserbase import Browserbase  # noqa: E402
from playwright.sync_api import sync_playwright, Page  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TARGET_URL = "https://x.com/aleabitoreddit"
LOGIN_URL = "https://x.com/i/flow/login"
MAX_SCROLLS = 60
EARLY_EXIT_THRESHOLD = 8
SCROLL_WAIT_MIN = 2.5
SCROLL_WAIT_MAX = 4.5
SCROLL_Y_MIN = 600
SCROLL_Y_MAX = 1500
PAGE_LOAD_TIMEOUT_MS = 90_000
OUTPUT_DIR = Path("data")
OUTPUT_FILE = OUTPUT_DIR / "latest_tweets.json"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scrape")

# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
def login_x(page: Page, username: str, password: str) -> bool:
    """Login to X.com. Returns True on success."""
    log.info("Navigating to login page...")
    page.goto(LOGIN_URL, timeout=PAGE_LOAD_TIMEOUT_MS)
    time.sleep(5)
    
    # Step 1: Enter username
    log.info("Entering username...")
    try:
        username_input = page.wait_for_selector('input[name="username_or_email"]', timeout=30000)
        username_input.click()
        time.sleep(0.5)
        username_input.fill(username)
        time.sleep(1)
        
        # Click "Continue" button
        continue_btn = page.query_selector('button:has-text("Continue")')
        if continue_btn:
            continue_btn.click()
        else:
            username_input.press("Enter")
        
        time.sleep(4)
    except Exception as e:
        log.error(f"Username step failed: {e}")
        page.screenshot(path='data/login_debug.png')
        return False
    
    # Step 2: Check for verification challenge
    try:
        challenge_input = page.query_selector('input[data-testid="ocfEnterTextTextInput"]')
        if challenge_input:
            log.warning("Verification challenge detected")
            challenge_input.fill(username)
            time.sleep(0.5)
            confirm_btn = page.query_selector('button:has-text("Next")')
            if confirm_btn:
                confirm_btn.click()
            time.sleep(4)
    except Exception:
        pass
    
    # Step 3: Enter password
    log.info("Entering password...")
    try:
        password_input = page.wait_for_selector('input[type="password"]', timeout=30000)
        
        # Remove any overlay/mask that intercepts clicks
        page.evaluate('''() => {
            const masks = document.querySelectorAll('[data-testid="mask"]');
            masks.forEach(m => m.remove());
            const layers = document.querySelectorAll('#layers');
            layers.forEach(l => {
                if (l.querySelector('[data-testid="mask"]') === null && 
                    l.innerText.includes('Log in') === false) {
                    // Keep layers that contain login form
                }
            });
        }''')
        time.sleep(1)
        
        password_input.click(force=True)
        time.sleep(0.5)
        password_input.fill(password)
        time.sleep(1)
        
        # Click "Log in" button via JS to bypass overlay
        login_btn = page.query_selector('button:has-text("Log in")')
        if login_btn:
            login_btn.click(force=True)
        else:
            password_input.press("Enter")
        
        time.sleep(5)
    except Exception as e:
        log.error(f"Password step failed: {e}")
        page.screenshot(path='data/login_debug.png')
        return False
    
    # Step 4: Check for phone verification
    time.sleep(3)
    current_url = page.url
    log.info(f"Current URL after login: {current_url}")
    
    if "signup_phone" in current_url or "verification" in current_url:
        log.info("Phone verification required. Entering phone number...")
        try:
            # Find phone input
            phone_input = page.wait_for_selector('input[type="tel"], input[name="phone_number"], input[placeholder*="Phone"]', timeout=30000)
            if phone_input:
                # Clear and fill phone number (without +86 prefix, just the number)
                phone_input.click()
                time.sleep(0.5)
                phone_input.fill('13320850615')
                time.sleep(1)
                
                # Click Continue
                continue_btn = page.query_selector('button:has-text("Continue")')
                if continue_btn:
                    continue_btn.click(force=True)
                    log.info("Phone number submitted, sending verification code...")
                else:
                    phone_input.press("Enter")
                
                time.sleep(5)
                page.screenshot(path='data/verification_sent.png')
                log.info("Verification code sent! Check your phone.")
                return "pending_verification"
        except Exception as e:
            log.error(f"Failed to enter phone number: {e}")
            page.screenshot(path='data/login_debug.png')
            return False
    
    # Step 5: Verify login success
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass
    time.sleep(3)
    
    current_url = page.url
    log.info(f"Current URL after login: {current_url}")
    
    if "x.com" in current_url and "login" not in current_url and "onboarding" not in current_url:
        log.info("Login successful!")
        return True
    
    # Check for error
    try:
        error_text = page.inner_text('[data-testid="LoginForm_Error"]')
        if error_text:
            log.error(f"Login error: {error_text}")
            page.screenshot(path='data/login_debug.png')
            return False
    except:
        pass
    
    page.screenshot(path='data/login_debug.png')
    log.warning("Login status unclear. Check data/login_debug.png")
    return False


# ---------------------------------------------------------------------------
# Tweet extraction
# ---------------------------------------------------------------------------
def _extract_tweet_data(page: Page) -> list[dict[str, Any]]:
    """Parse all tweet elements currently in the DOM using 'article' selector."""
    tweets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    
    articles = page.query_selector_all("article")
    
    for el in articles:
        try:
            full_text = el.inner_text()
            
            link_el = el.query_selector('a[href*="/status/"]')
            href = (link_el.get_attribute("href") or "") if link_el else ""
            
            tid = ""
            if "/status/" in href:
                tid = href.split("/status/")[1].split("?")[0]
            
            time_el = el.query_selector("time")
            time_str = (time_el.get_attribute("datetime") or "") if time_el else ""
            
            if tid and tid not in seen_ids and full_text.strip():
                seen_ids.add(tid)
                tweets.append({
                    "id": tid,
                    "text": full_text.strip(),
                    "created_at": time_str,
                    "url": f"https://x.com/aleabitoreddit/status/{tid}",
                })
        except Exception:
            continue
    
    return tweets


def scrape_profile(session) -> list[dict[str, Any]]:
    """Login, navigate to target profile, scroll, and collect tweets."""
    all_tweets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(session.connect_url)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()

        # Login first
        login_result = login_x(page, X_USERNAME, X_PASSWORD)
        if login_result is False:
            log.error("Login failed. Aborting.")
            return []
        elif login_result == "pending_verification":
            log.info("Waiting for SMS verification code...")
            log.info("Please check your phone and tell me the code.")
            log.info("I'll wait here for you to provide it.")
            
            # Wait for code file to appear
            code_file = Path('data/sms_code.txt')
            for i in range(60):  # Wait up to 10 minutes
                time.sleep(10)
                if code_file.exists():
                    code = code_file.read_text().strip()
                    if code:
                        log.info(f"Found verification code: {code}")
                        # Find code input and fill it
                        try:
                            code_input = page.wait_for_selector('input[type="tel"], input[name="code"], input[placeholder*="code"]', timeout=10000)
                            code_input.click()
                            time.sleep(0.5)
                            code_input.fill(code)
                            time.sleep(1)
                            
                            # Click Next/Confirm
                            next_btn = page.query_selector('button:has-text("Next"), button:has-text("Confirm")')
                            if next_btn:
                                next_btn.click(force=True)
                            else:
                                code_input.press("Enter")
                            
                            time.sleep(5)
                            log.info("Verification code submitted!")
                            
                            # Cleanup
                            code_file.unlink(missing_ok=True)
                            
                            # Check if login succeeded
                            if "x.com" in page.url and "login" not in page.url and "onboarding" not in page.url:
                                log.info("Login successful after verification!")
                                break
                            else:
                                log.warning(f"Verification may have failed. URL: {page.url}")
                        except Exception as e:
                            log.error(f"Failed to enter verification code: {e}")
                            break
                else:
                    log.info(f"Waiting for SMS code... ({(i+1)*10}s)")
            else:
                log.error("Timeout waiting for SMS code. Aborting.")
                return []

        # Navigate to target profile
        log.info("Navigating to %s …", TARGET_URL)
        page.goto(TARGET_URL, timeout=PAGE_LOAD_TIMEOUT_MS)

        try:
            page.wait_for_selector("article", timeout=PAGE_LOAD_TIMEOUT_MS)
        except Exception:
            log.warning("No article selector appeared within timeout")

        log.info("Page loaded, starting to scroll …")
        consecutive_empty = 0

        for scroll_idx in range(1, MAX_SCROLLS + 1):
            page_height_before = page.evaluate("document.body.scrollHeight")
            new_tweets = _extract_tweet_data(page)

            unique_new = [t for t in new_tweets if t["id"] not in seen_ids]
            for t in unique_new:
                seen_ids.add(t["id"])
                all_tweets.append(t)

            log.info(
                "Scroll %d/%d: +%d new (total %d)",
                scroll_idx,
                MAX_SCROLLS,
                len(unique_new),
                len(all_tweets),
            )

            if unique_new:
                consecutive_empty = 0
            else:
                consecutive_empty += 1

            if consecutive_empty >= EARLY_EXIT_THRESHOLD:
                log.info("No new tweets for %d scrolls – stopping", consecutive_empty)
                break

            scroll_y = random.randint(SCROLL_Y_MIN, SCROLL_Y_MAX)
            page.evaluate(f"window.scrollBy(0, {scroll_y})")

            try:
                page.wait_for_function(
                    "document.body.scrollHeight > arguments[0]",
                    page_height_before,
                    timeout=10_000,
                )
            except Exception:
                pass

            time.sleep(random.uniform(SCROLL_WAIT_MIN, SCROLL_WAIT_MAX))

    return all_tweets


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    # Load env
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    
    # Refresh globals from environment after .env loading
    _k = os.environ.get("BROWSERBASE_API_KEY", "")
    _p = os.environ.get("BROWSERBASE_PROJECT_ID", "")
    if _k:
        globals()["API_KEY"] = _k
    if _p:
        globals()["PROJECT_ID"] = _p

    if not API_KEY or not PROJECT_ID:
        log.error("Missing BROWSERBASE_API_KEY or BROWSERBASE_PROJECT_ID")
        sys.exit(1)

    log.info("API Key: %s… (len=%d)", API_KEY[:15], len(API_KEY))

    # Create session
    bb = Browserbase(api_key=API_KEY)
    log.info("Creating session …")
    session = bb.sessions.create(project_id=PROJECT_ID)
    log.info("Session: %s", session.id)

    tweets = []
    try:
        tweets = scrape_profile(session)
    finally:
        try:
            bb.sessions.update(session.id, status="REQUEST_RELEASE")
            log.info("Session released")
        except Exception:
            pass

    # Save
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(tweets, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("Done! %d tweets saved to %s", len(tweets), OUTPUT_FILE)


if __name__ == "__main__":
    main()
