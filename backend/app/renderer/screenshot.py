from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright


def screenshot(html: str, destination: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp.png')
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={'width': 1080, 'height': 1080}, device_scale_factor=1)
                browser_errors = []
                page.on('pageerror', lambda error: browser_errors.append(str(error)[:240]))
                page.route('**/*', lambda route: route.abort())
                page.set_content(html, wait_until='load')
                dom = page.evaluate('''async () => {
                    await document.fonts.ready;
                    // Fit unusually long names/prices without altering commercial text.
                    for (const el of document.querySelectorAll('[data-fit]')) {
                        const overflows = () => el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1;
                        if (el.classList.contains('price')) {
                            let scale = 1;
                            while (overflows() && scale > .72) {
                                scale = Math.round((scale - .02) * 100) / 100;
                                el.style.setProperty('--fit-price-scale', scale);
                            }
                        } else {
                            let size = parseFloat(getComputedStyle(el).fontSize);
                            while (overflows() && size > 8) el.style.fontSize = `${--size}px`;
                        }
                    }
                    const rect = el => { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; };
                    return {
                        canvas:{width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight},
                        products:[...document.querySelectorAll('[data-product-id]')].map(el => ({
                            id:el.dataset.productId,name:el.dataset.name,price:el.dataset.price,unit:el.dataset.unit,
                            role:el.dataset.role || (el.classList.contains('featured') ? 'featured' : 'standard'),
                            bounds:rect(el), images:[...el.querySelectorAll('img')].map(img => {
                                const clip=img.closest('.product-visual,.product-image') || img.parentElement;
                                return {loaded:img.complete && img.naturalWidth>0,bounds:rect(img),clip_bounds:rect(clip),
                                        clip_hidden:getComputedStyle(clip).overflow === 'hidden'};
                            })
                        })),
                        overflowing:[...document.querySelectorAll('[data-fit]')]
                            .filter(el => el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1)
                            .map(el => ({tag:el.tagName,className:String(el.className).slice(0,40),productId:el.closest('[data-product-id]')?.dataset.productId || null,scrollWidth:el.scrollWidth,clientWidth:el.clientWidth,
                                         scrollHeight:el.scrollHeight,clientHeight:el.clientHeight}))
                    };
                }''')
                if page.evaluate('document.documentElement.scrollWidth !== 1080 || document.documentElement.scrollHeight !== 1080'):
                    raise ValueError('Template excedeu 1080x1080.')
                page.screenshot(path=str(temporary), type='png', full_page=False, animations='disabled')
                dom['browser_errors'] = browser_errors
            finally:
                browser.close()
        with Image.open(temporary) as result:
            if result.size != (1080, 1080):
                raise ValueError('PNG deve medir exatamente 1080x1080.')
        temporary.replace(destination)
        return dom
    finally:
        temporary.unlink(missing_ok=True)
