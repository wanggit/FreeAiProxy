import re
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "poc/dump/upload_TXT.html")
html = path.read_text(encoding="utf-8")

print("== filename mentions ==")
for m in re.finditer(r"[^<>]{0,150}sample[^<>]{0,150}", html):
    seg = m.group(0)
    if "sample" in seg:
        print("  ", seg[:250].replace("\n", " "))

print("\n== composer region after textarea ==")
i = html.find('placeholder="Message DeepSeek"')
if i > 0:
    seg = html[i : i + 4000]
    seg = re.sub(r'<svg.*?</svg>', '<svg/>', seg, flags=re.S)
    seg = re.sub(r'<path[^>]*/>', '', seg)
    print(seg[:3500])
