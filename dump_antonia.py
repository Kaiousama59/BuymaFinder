from playwright.sync_api import sync_playwright

URL = "https://www.antonia.it/en-us/products/balenciaga-black-leather-duchesse-pumps-869817wcdh11080"
OUT = "antonia_product_debug.html"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(locale="en-US")
    page.goto(URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(page.content())
    browser.close()

print(f"Saved {OUT}")
