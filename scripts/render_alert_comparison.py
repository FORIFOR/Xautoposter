"""Render an evidence table, not a screenshot of the product."""
import hashlib
import json
from pathlib import Path
import unicodedata

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'artifacts/editorial-evidence/aisecure-threshold-comparison.json'
data = json.loads(SOURCE.read_text())
comparisons = {item['threshold']: item for item in data['comparisons']}
before = comparisons[100]['totals']['per_rule']['AS-003']
after = comparisons[800]['totals']['per_rule']['AS-003']
# The evaluator omits zero-count rule findings and records the missed case.
assert (before['fp'], before['tp']) == (100, 1)
assert after == {'fn': 1}
assert comparisons[800]['totals']['per_rule']['AS-002']['tp'] == 1
assert all(c['totals']['events'] == 96610 for c in comparisons.values())
for c in comparisons.values():
    source = SOURCE.parent / c['file']
    assert hashlib.sha256(source.read_bytes()).hexdigest() == c['sha256']

font_paths = list(Path('/System/Library/Fonts').glob('*.ttc'))
def jp(size, weight=6):
    path = next(p for p in font_paths if unicodedata.normalize('NFC', p.name) == f'ヒラギノ角ゴシック W{weight}.ttc')
    return ImageFont.truetype(str(path), size)
def number(size):
    return ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Bold.ttf', size)

S = 2
im = Image.new('RGB', (1200*S, 1100*S), '#FAFAF7')
d = ImageDraw.Draw(im)
ink, muted, line = '#182B36', '#52616A', '#CCD3D6'
green, red = '#167052', '#B43D2C'
def text(x, y, value, size, color=ink, weight=6, anchor='la', numeric=False):
    f = number(size*S) if numeric else jp(size*S, weight)
    d.text((x*S,y*S), value, font=f, fill=color, anchor=anchor)
def rule(y):
    d.line((64*S,y*S,1136*S,y*S), fill=line, width=2*S)

text(64, 49, 'AISecure  /  合成データでの検証', 28, muted, 3)
text(64, 118, '「誤警報ゼロ」の内訳', 60, ink, 7)
text(64, 219, '大量アクセス検知のしきい値を変更', 34, muted, 3)
text(640, 304, '100ファイル', 37, ink, 6, 'ma')
text(984, 304, '800ファイル', 37, ink, 6, 'ma')
text(1136, 360, '5分間に参照した異なるファイル数', 26, muted, 3, 'ra')
rule(410)

text(64, 465, '正常への', 38, ink, 6)
text(64, 518, '誤警報', 46, ink, 7)
text(640, 442, str(before['fp']), 138, ink, anchor='ma', numeric=True)
text(812, 491, '→', 55, muted, anchor='ma')
text(984, 442, '0', 138, green, anchor='ma', numeric=True)
rule(632)

text(64, 690, '攻撃への', 38, ink, 6)
text(64, 743, '警報', 46, ink, 7)
text(640, 667, str(before['tp']), 138, ink, anchor='ma', numeric=True)
text(812, 716, '→', 55, muted, anchor='ma')
text(984, 667, '0', 138, red, anchor='ma', numeric=True)
text(1136, 835, '数値は警報の件数', 26, muted, 3, 'ra')
rule(891)
text(64, 925, 'AS-003のみ  /  同一の合成データ  /  攻撃1パターン', 28, ink, 6)
text(64, 978, '他ルールは攻撃を検知。実運用の性能ではありません。', 28, muted, 3)
text(64, 1040, '4シナリオ・96,610イベントの比較結果を図表化', 24, muted, 3)

output = ROOT / 'assets/posts/as003-zero-alerts-2026-09-14.png'
im.resize((1200,1100), Image.Resampling.LANCZOS).save(output)
print(output)
