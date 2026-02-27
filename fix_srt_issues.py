"""
Fix systematic issues in the SRT identified by Claude's review:
1. "Heilagur Andi" declension — fix accusative, genitive, dative contexts
2. Double periods (..)
3. "býr okkur" → correct verb form
"""
import re

srt_path = "/Users/haukurhauksson/Azotus/4_DELIVERY/SRT/DONE_i2608_iceland-20260219T220753150282Z_FULL_FINALIZER.srt"

with open(srt_path, "r", encoding="utf-8") as f:
    content = f.read()

fixes = 0

# 1. Fix "Heilagur Andi" declension
# Accusative contexts: after "sendi", "um", as object
# "sendi Heilagur Andi" → "sendi Heilagan Anda"
acc_patterns = [
    (r'sendi Heilagur Andi\b', 'sendi Heilagan Anda'),
    (r'um Heilagur Andi\b', 'um Heilagan Anda'),
    (r'hjálparann, Heilagur Andi\b', 'hjálparann, Heilagan Anda'),
    (r'fá Heilagur Andi\b', 'fá Heilagan Anda'),
    (r'senda Heilagur Andi\b', 'senda Heilagan Anda'),
    (r'þekkja Heilagur Andi\b', 'þekkja Heilagan Anda'),
    (r'með Heilagur Andi\b', 'með Heilögum Anda'),  # dative after "með"
]

# Genitive contexts: after "án", "Heilags Anda"
gen_patterns = [
    (r'[Áá]n Heilagur Andi\b', lambda m: m.group(0)[0:2] + ' Heilags Anda' if m.group(0)[0] == 'Á' else 'án Heilags Anda'),
    (r'Án Heilagur Andi\b', 'Án Heilags Anda'),
    (r'án Heilagur Andi\b', 'án Heilags Anda'),
    (r'kraft Heilagur Andi\b', 'kraft Heilags Anda'),
    (r'vegna Heilagur Andi\b', 'vegna Heilags Anda'),
    (r'af Heilagur Andi\b', 'af Heilögum Anda'),  # dative after "af"
]

for pattern, replacement in acc_patterns + gen_patterns:
    new_content, count = re.subn(pattern, replacement, content)
    if count > 0:
        fixes += count
        print(f"  Fixed {count}x: {pattern} → {replacement}")
    content = new_content

# Also catch "hugsa um Heilagur Andi" and similar preposition + acc patterns
preposition_acc = ['fyrir', 'gegnum', 'kringum']
for prep in preposition_acc:
    pattern = f'{prep} Heilagur Andi'
    if pattern in content:
        content = content.replace(pattern, f'{prep} Heilagan Anda')
        fixes += 1
        print(f"  Fixed: {pattern} → {prep} Heilagan Anda")

# Dative contexts: after "frá", "úr", "hjá"
preposition_dat = ['frá', 'úr', 'hjá']
for prep in preposition_dat:
    pattern = f'{prep} Heilagur Andi'
    if pattern in content:
        content = content.replace(pattern, f'{prep} Heilögum Anda')
        fixes += 1
        print(f"  Fixed: {pattern} → {prep} Heilögum Anda")

# 2. Fix double periods
old_count = content.count('..')
content = re.sub(r'\.\.(?!\.)', '.', content)  # ".." → "." but not "..."
new_count = old_count - content.count('..')
if new_count > 0:
    fixes += new_count
    print(f"  Fixed {new_count}x double periods")

# 3. Fix "Heilagur Andi býr okkur" → "Heilagur Andi hefur útbúið okkur"
if "Andi býr okkur" in content:
    content = content.replace("Andi býr okkur", "Andi hefur útbúið okkur")
    fixes += 1
    print(f"  Fixed: 'býr okkur' → 'hefur útbúið okkur'")

# Save
with open(srt_path, "w", encoding="utf-8") as f:
    f.write(content)

# Also update the tmp copy for burn
with open("/tmp/subs.srt", "w", encoding="utf-8") as f:
    f.write(content)

print(f"\n✅ Total fixes applied: {fixes}")
print(f"Saved to: {srt_path}")
print(f"Copied to: /tmp/subs.srt")
