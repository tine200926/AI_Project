"""Build Chrome Web Store images from the e2e screenshots.

    node tests/e2e.mjs --screenshots store/raw
    python3 tools/make_store_assets.py

Writes store/screenshot-1.png, store/screenshot-2.png (1280x800) and
store/promo-440x280.png.
"""
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(ROOT, 'store', 'raw')
OUT = os.path.join(ROOT, 'store')
ACCENT = (75, 91, 220)
FONT_CANDIDATES = [
    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
    'C:/Windows/Fonts/msjhbd.ttc',
    'C:/Windows/Fonts/msjh.ttc',
    '/System/Library/Fonts/PingFang.ttc',
]


def font(size):
    for f in FONT_CANDIDATES:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def flat(img):
    """Store images must not have transparency."""
    bg = Image.new('RGB', img.size, (255, 255, 255))
    bg.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
    return bg


def main():
    icon = Image.open(os.path.join(ROOT, 'icons', 'icon128.png')).convert('RGBA')

    # 1: options page as-is.
    Image.open(os.path.join(RAW, 'options-1280x800.png')).convert('RGB') \
        .resize((1280, 800)).save(os.path.join(OUT, 'screenshot-1.png'))

    # 2: popup on a branded background with a caption.
    canvas = Image.new('RGB', (1280, 800), (238, 240, 252))
    d = ImageDraw.Draw(canvas)
    pop = Image.open(os.path.join(RAW, 'popup.png')).convert('RGB')
    if pop.height > 640:
        pop = pop.crop((0, 0, pop.width, 640))
    x, y = 1280 - pop.width - 100, (800 - pop.height) // 2
    d.rounded_rectangle([x - 6, y - 6, x + pop.width + 6, y + pop.height + 6], radius=18, fill=(210, 214, 235))
    canvas.paste(pop, (x, y))
    canvas.paste(icon, (100, 190), icon)
    d.text((100, 350), '補貨了，', font=font(56), fill=(31, 35, 40))
    d.text((100, 420), '馬上通知你', font=font(56), fill=(31, 35, 40))
    d.text((100, 520), '點工具列圖示，', font=font(26), fill=(100, 107, 117))
    d.text((100, 560), '一眼看到每個選項的庫存', font=font(26), fill=(100, 107, 117))
    canvas.save(os.path.join(OUT, 'screenshot-2.png'))

    # Small promo tile 440x280.
    tile = Image.new('RGB', (440, 280), ACCENT)
    d = ImageDraw.Draw(tile)
    small = icon.resize((96, 96), Image.LANCZOS)
    tile.paste(small, (36, 92), small)
    d.text((152, 98), '補貨通知', font=font(46), fill=(255, 255, 255))
    d.text((154, 158), '售完 → 有貨，立刻提醒', font=font(22), fill=(220, 225, 255))
    tile.save(os.path.join(OUT, 'promo-440x280.png'))
    print('store assets written to', OUT)


if __name__ == '__main__':
    main()
