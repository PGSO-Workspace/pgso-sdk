"""Render neutral SVGs from LikeC4 layout JSON, following the vault's SVG format."""
import html
import json
from pathlib import Path
import textwrap
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
model = json.loads((ROOT / 'likec4/model.json').read_text())

def escape(text):
    return html.escape(str(text), quote=True)

for key, view in model['views'].items():
    if key == 'index':
        continue
    width, height = view['bounds']['width'], view['bounds']['height']
    assert width > 0 and height > 0 and view['nodes'] and view['edges'], key
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" role="img" viewBox="-12 -12 {width+24} {height+24}" width="{width+24}" height="{height+24}" font-family="Helvetica,Arial,sans-serif">',
             f'<title>{escape(view["title"])}</title>',
             '<rect width="100%" height="100%" fill="white"/>',
             '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,1 L9,5 L0,9 z" fill="#333"/></marker></defs>']
    for node in sorted(view['nodes'], key=lambda n: n['level']):
        x, y, w, h = (node[k] for k in ('x', 'y', 'width', 'height'))
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="#f5f5f5" stroke="#333"/>')
        lines = textwrap.wrap(node['title'], max(12, int((w - 32) / 10)))
        assert len(lines) * 23 < h - 16, (key, node['id'])
        top = y + h / 2 - (len(lines) - 1) * 11.5 + 6
        for i, line in enumerate(lines):
            parts.append(f'<text x="{x+w/2}" y="{top+i*23}" text-anchor="middle" font-size="18" fill="#111">{escape(line)}</text>')
    for edge in view['edges']:
        points = edge['points']
        assert len(points) >= 2, (key, edge['id'])
        path = 'M ' + ' L '.join(f'{x},{y}' for x, y in points)
        parts.append(f'<path d="{path}" fill="none" stroke="#333" stroke-width="1.3" marker-end="url(#arrow)"/>')
        box = edge.get('labelBBox')
        label = edge.get('label')
        if box and label:
            parts.append(f'<rect x="{box["x"]-3}" y="{box["y"]-2}" width="{box["width"]+6}" height="{box["height"]+4}" fill="white"/>')
            for i, line in enumerate(textwrap.wrap(label, max(10, int(box['width'] / 7)))):
                parts.append(f'<text x="{box["x"]}" y="{box["y"]+14+i*18}" font-size="14" fill="#222">{escape(line)}</text>')
    parts.append('</svg>')
    svg = '\n'.join(parts)
    ET.fromstring(svg)
    target = ROOT / 'views' / (key.removesuffix('_view') + '.svg')
    target.write_text(svg + '\n')
    print(target.name)
