#!/usr/bin/env python3
"""Compare C final frames to the preserved PNGs. Optional dependency: Pillow."""
import argparse
import json
import math
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageStat
from archive import ARCHIVE, catalog, write_json

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--renders', type=Path, default=Path('videos'))
p.add_argument('--output', type=Path, default=Path('docs/validation'))
args = p.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
entries = catalog()['paintings']
board = Image.new('RGB', (1024, len(entries)*220), '#f8f8f8')
draw = ImageDraw.Draw(board)
metrics = []
for i, entry in enumerate(entries):
    pid = entry['id']
    original = Image.open(ARCHIVE / str(pid) / 'original.png').convert('RGB')
    rendered = Image.open(args.renders / f'{pid}.png').convert('RGB')
    resized = rendered.resize(original.size, Image.Resampling.LANCZOS)
    stats = ImageStat.Stat(ImageChops.difference(original, resized))
    mae = sum(stats.mean)/3
    rmse = math.sqrt(sum(v*v for v in stats.rms)/3)
    metrics.append({'id':pid, 'title':entry['title'], 'mae_rgb_0_255':mae, 'rmse_rgb_0_255':rmse,
                    'reference_size':list(original.size), 'render_size':list(rendered.size)})
    draw.text((12,i*220+5), f"{pid} — {entry['title']} | PNG du site / reconstruction C | MAE {mae:.2f}/255",fill='#111111')
    for j, image in enumerate((original, rendered)):
        image.thumbnail((500,192), Image.Resampling.LANCZOS)
        board.paste(image,(j*512+(512-image.width)//2,i*220+24))
board.save(args.output / 'comparison.jpg', quality=92)
write_json(args.output / 'metrics.json', {'method':'RGB mean absolute error and RMSE; rendered PNG resized to reference with Pillow Lanczos; no registration or color correction',
          'metrics':metrics})
print(args.output / 'comparison.jpg')
