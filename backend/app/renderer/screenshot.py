from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright


def screenshot(html: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp.png')
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={'width': 1080, 'height': 1080}, device_scale_factor=1)
                page.route('**/*', lambda route: route.abort())
                page.set_content(html, wait_until='load')
                page.evaluate('''async () => {
                    await document.fonts.ready;
                    await Promise.all([...document.images].map(img => img.decode()));
                    // Fit unusually long names/prices without altering commercial text.
                    for (const el of document.querySelectorAll('[data-fit]')) {
                        let size = parseFloat(getComputedStyle(el).fontSize);
                        while ((el.scrollWidth > el.clientWidth || el.scrollHeight > el.clientHeight) && size > 8) {
                            el.style.fontSize = `${--size}px`;
                        }
                    }
                }''')
                if page.evaluate('document.documentElement.scrollWidth !== 1080 || document.documentElement.scrollHeight !== 1080'):
                    raise ValueError('Template excedeu 1080x1080.')
                page.screenshot(path=str(temporary), type='png', full_page=False, animations='disabled')
            finally:
                browser.close()
        with Image.open(temporary) as result:
            if result.size != (1080, 1080):
                raise ValueError('PNG deve medir exatamente 1080x1080.')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
