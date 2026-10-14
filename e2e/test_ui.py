"""Real-browser end-to-end tests of the user journeys people actually click through."""
from playwright.sync_api import expect

from e2e.conftest import PASSWORD

PATCH_JS = """([id, p]) => fetch('/api/customers/' + id, {method: 'PATCH',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({notes: p, tags: ['<b onmouseover=alert(1)>']})})"""


def nav(page, name):
    page.click(f".top a[data-v={name}]")


def test_login_rejects_bad_password_then_accepts_good_and_signs_out(page):
    page.goto(page.base)
    expect(page.locator("#login")).to_be_visible()
    page.fill("#lu", "admin")
    page.fill("#lp", "wrong-password")
    page.click("#login button")
    expect(page.locator("#lerr")).to_have_text("Invalid username or password")
    page.fill("#lp", PASSWORD)
    page.click("#login button")
    expect(page.locator("h1")).to_contain_text("understood")
    expect(page.locator("#logout")).to_be_visible()
    page.click("#logout")
    expect(page.locator("#login")).to_be_visible()  # session is gone
    assert page.js_errors == []


def test_api_is_not_reachable_without_session_in_the_browser(page):
    page.goto(page.base)
    assert page.evaluate("fetch('/api/customers').then(r => r.status)") == 401


def test_dashboard_shows_real_data(authed):
    expect(authed.locator(".tile .lab", has_text="Customers")).to_be_visible()
    expect(authed.locator(".stage")).to_have_count(5)
    expect(authed.locator(".cu").first).not_to_have_text("0")  # count-up animation finished with real numbers
    assert authed.js_errors == []


def test_new_order_flows_through_pipeline_to_a_confirmation_email(authed):
    authed.get_by_role("button", name="New order").click()
    expect(authed.locator("#toast")).to_contain_text("webhook verified", timeout=10000)
    nav(authed, "comm")
    expect(authed.locator(".tile", has_text="We got your order").first).to_be_visible()


def test_ship_order_updates_timeline_and_sends_completion_email(authed):
    authed.get_by_role("button", name="New order").click()
    expect(authed.locator("#toast")).to_contain_text("webhook verified", timeout=10000)
    authed.get_by_role("button", name="Ship order").click()
    expect(authed.locator("#toast")).to_contain_text("order shipped", timeout=10000)
    nav(authed, "comm")
    expect(authed.locator(".tile", has_text="is complete").first).to_be_visible()


def test_agent_drafts_then_human_approves(authed):
    authed.get_by_role("button", name="Run agent").click()
    expect(authed.locator("#toast")).to_contain_text("Agent drafted", timeout=10000)
    nav(authed, "comm")
    approve = authed.get_by_role("button", name="Approve & send")
    expect(approve.first).to_be_visible()
    before = approve.count()  # nothing is sent until a human clicks
    approve.first.click()
    expect(authed.locator("#toast")).to_contain_text("Approved")
    expect(authed.get_by_role("button", name="Approve & send")).to_have_count(before - 1)


def test_command_palette_finds_customer_and_opens_profile(authed):
    name = authed.evaluate("fetch('/api/customers').then(r => r.json()).then(c => c[0].name)")
    authed.keyboard.press("Control+k")
    authed.fill("#pq", name.split()[0])
    authed.locator(".pal .it", has_text=name).first.click()
    expect(authed.locator("#panel.open")).to_contain_text(name)
    authed.keyboard.press("Escape")
    expect(authed.locator("#panel.open")).to_have_count(0)


def test_customer_filter_chips_and_search(authed):
    nav(authed, "cust")
    expect(authed.locator(".cgrid .tile").first).to_be_visible()
    authed.locator(".chip", has_text="vip").click()
    expect(authed.locator(".cgrid .tile").first).to_be_visible()
    assert authed.locator(".cgrid .tile .pill.vip").count() == authed.locator(".cgrid .tile").count()  # only VIPs shown
    authed.fill("#q", "zzzz-no-such-person")
    expect(authed.locator(".cgrid .tile")).to_have_count(0)


def test_workflow_toggle_persists(authed):
    nav(authed, "wf")
    expect(authed.locator(".sw").first).to_be_visible()
    state = authed.locator(".sw input").first.is_checked()
    authed.locator(".sw").first.click()
    expect(authed.locator("#toast")).to_contain_text("Workflow")
    authed.reload()
    nav(authed, "wf")
    expect(authed.locator(".sw").first).to_be_visible()
    assert authed.locator(".sw input").first.is_checked() != state


def test_stored_xss_payload_is_rendered_as_text_and_never_executes(authed):
    payload = '<img src=x onerror="window.__xss=1"><script>window.__xss=2</script>'
    cid = authed.evaluate("fetch('/api/customers').then(r => r.json()).then(c => c[0].id)")
    authed.evaluate(PATCH_JS, [cid, payload])
    nav(authed, "cust")
    authed.wait_for_selector(".cgrid .tile")
    authed.evaluate(f"openCust({cid})")
    expect(authed.locator("#nt")).to_have_value(payload)  # shown literally inside the textarea
    authed.keyboard.press("Escape")
    nav(authed, "cust")
    authed.wait_for_timeout(500)
    assert authed.evaluate("window.__xss") is None and authed.dialogs == []


def test_all_views_render_without_javascript_errors(authed):
    for v in ["cust", "ord", "wf", "comm", "agent", "dash"]:
        nav(authed, v)
        authed.wait_for_selector("h1")
        authed.wait_for_timeout(300)
    assert authed.js_errors == []


def test_agent_chat_answers_with_visible_tool_calls(authed):
    nav(authed, "agent")
    authed.fill("#msg", "Who are my VIP customers?")
    authed.keyboard.press("Enter")
    expect(authed.locator(".msg.bot .tc").first).to_be_visible(timeout=10000)
    expect(authed.locator(".msg.bot").last).to_contain_text("VIP", ignore_case=True)


def test_mobile_viewport_has_no_horizontal_overflow(browser, server):
    ctx = browser.new_context(viewport={"width": 390, "height": 800})
    pg = ctx.new_page()
    pg.goto(server)
    pg.fill("#lu", "admin")
    pg.fill("#lp", PASSWORD)
    pg.click("#login button")
    pg.wait_for_selector("h1:has-text('understood')")
    pg.wait_for_timeout(1200)
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    ctx.close()


def test_google_button_is_hidden_when_google_is_not_configured(page):
    page.goto(page.base)
    expect(page.locator("#login")).to_be_visible()
    expect(page.locator("#gwrap")).to_be_hidden()


def test_google_button_starts_oauth_with_pkce_and_shows_login_errors(browser, google_server):
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.goto(google_server)
    button = pg.get_by_role("link", name="Sign in with Google")
    expect(button).to_be_visible()
    assert button.get_attribute("href") == "/api/auth/google/login"
    # what the browser would follow: our 303 to Google (not followed here, so no network call to Google)
    r = pg.request.get(google_server + "/api/auth/google/login", max_redirects=0)
    assert r.status == 303
    loc = r.headers["location"]
    assert loc.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    for needle in ("code_challenge_method=S256", "client_id=cid.apps.googleusercontent.com", "state=", "scope=openid+email"):
        assert needle in loc, needle
    # an allowlist rejection comes back as ?login_error=... and is shown on the login card, then removed from the URL
    pg.goto(google_server + "/?login_error=This%20Google%20account%20is%20not%20allowed%20to%20access%20WooCRM.")
    expect(pg.locator("#lerr")).to_contain_text("not allowed")
    assert "login_error" not in pg.url
    ctx.close()
