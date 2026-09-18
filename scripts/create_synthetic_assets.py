"""Deterministic geometric fixtures, not commercial photos. No network or AI."""
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / 'assets/products/synthetic'
    target.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default(size=24)
    examples = {
        'vertical': ((300, 600), (96, 45, 204, 560), '#bf3e35'),
        'horizontal': ((600, 300), (35, 70, 565, 230), '#bf7048'),
        'square': ((400, 400), (45, 45, 355, 355), '#dea93f'),
        'transparent': ((600, 600), (175, 180, 425, 420), '#589249'),
    }
    for name, (size, box, color) in examples.items():
        image = Image.new('RGBA', size)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(box, radius=22, fill=color)
        x, y = (box[0]+box[2])//2, (box[1]+box[3])//2
        draw.text((x,y-20), 'TESTE', font=font, anchor='mm', fill='white')
        draw.text((x,y+20), 'SINTÉTICO', font=ImageFont.load_default(size=17), anchor='mm', fill='white')
        image.save(target / f'{name}.png')
    payload = json.loads((ROOT / 'examples/campaign.json').read_text())
    for index, product in enumerate(payload['products']):
        product['image'] = f'assets/products/synthetic/{list(examples)[index % 4]}.png'
    (ROOT / 'examples/campaign-real-assets.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n')


if __name__ == '__main__':
    main()
