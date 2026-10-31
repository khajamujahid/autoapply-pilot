"""Capture product screenshots for docs. Usage: python tests/ui_screens.py <outdir>"""
import sys
from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots"
B = "http://localhost:8077"
with sync_playwright() as pw:
    br = pw.chromium.launch()
    pg = br.new_page(viewport={"width": 1360, "height": 900})
    pg.goto(B + "/login"); pg.screenshot(path=f"{OUT}/01_login.png")
    pg.goto(B + "/signup"); pg.screenshot(path=f"{OUT}/02_signup.png")
    pg.goto(B + "/login"); pg.fill("input[name=email]", "alex.placeholder@example.com")
    pg.fill("input[name=password]", "placeholder-pass-123"); pg.click("button.btn"); pg.wait_for_load_state()
    for name, path in (("03_tracker", "/"), ("04_discover", "/jobs"), ("05_profile", "/profile"), ("06_settings", "/settings"),
                       ("08_architecture", "/docs/architecture")):
        pg.goto(B + path); pg.wait_for_timeout(500); pg.screenshot(path=f"{OUT}/{name}.png", full_page=True)
    pg.goto(B + "/settings"); pg.click("text=Test connection"); pg.wait_for_timeout(2000)
    pg.screenshot(path=f"{OUT}/06b_settings_test_connection.png")
    pg.goto(B + "/"); href = pg.locator("a[href^='/applications/']").first.get_attribute("href")
    pg.goto(B + href); pg.wait_for_timeout(800); pg.screenshot(path=f"{OUT}/07_application_detail.png", full_page=True)
    br.close()
print("ok")
