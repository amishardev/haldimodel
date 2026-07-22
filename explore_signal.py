"""
Exploration: figure out the best signal to distinguish 100% vs 50% purity.
"""
import numpy as np

# Raw data from test results
data = [
    {"label": "100% pair 1", "purity": 100,
     "reaction_strength": 0.20986, "before_yellow": 0.42411,
     "reaction_delta": -0.20986,
     "before_rgb": [136.73, 125.78, 37.07],
     "after_rgb": [103.56, 88.32, 54.9],
     "before_white": [222.33, 222.33, 222.98],
     "after_white": [208.68, 207.65, 203.67],
     "before_norm": [0.615, 0.5657, 0.1663],
     "after_norm": [0.4963, 0.4253, 0.2696],
     "before_abs": [0.2111, 0.2474, 0.7792],
     "after_abs": [0.3043, 0.3713, 0.5693],
     },
    {"label": "100% pair 2", "purity": 100,
     "reaction_strength": 0.24065, "before_yellow": 0.5858,
     "reaction_delta": 0.24065,
     "before_rgb": [169.18, 146.42, 43.21],
     "after_rgb": [89.13, 67.44, 27.8],
     "before_white": [195.03, 190.63, 186.29],
     "after_white": [215.65, 213.15, 208.57],
     "before_norm": [0.8675, 0.7681, 0.232],
     "after_norm": [0.4133, 0.3164, 0.1333],
     "before_abs": [0.0617, 0.1146, 0.6346],
     "after_abs": [0.3837, 0.4998, 0.8752],
     },
    {"label": "50% pair 1", "purity": 50,
     "reaction_strength": 0.36138, "before_yellow": 0.61321,
     "reaction_delta": -0.36138,
     "before_rgb": [152.42, 115.37, 9.79],
     "after_rgb": [95.74, 72.46, 26.09],
     "before_white": [203.33, 199.04, 190.43],
     "after_white": [222.38, 222.1, 220.83],
     "before_norm": [0.7496, 0.5796, 0.0514],
     "after_norm": [0.4305, 0.3262, 0.1181],
     "before_abs": [0.1252, 0.2369, 1.289],
     "after_abs": [0.366, 0.4865, 0.9277],
     },
    {"label": "50% pair 2", "purity": 50,
     "reaction_strength": 0.29005, "before_yellow": 0.44733,
     "reaction_delta": -0.29005,
     "before_rgb": [124.09, 102.0, 11.52],
     "after_rgb": [103.2, 73.39, 20.53],
     "before_white": [226.94, 226.94, 226.74],
     "after_white": [214.86, 211.36, 207.24],
     "before_norm": [0.5468, 0.4495, 0.0508],
     "after_norm": [0.4803, 0.3472, 0.0991],
     "before_abs": [0.2622, 0.3473, 1.2941],
     "after_abs": [0.3185, 0.4594, 1.004],
     },
]

print("="*80)
print("SIGNAL EXPLORATION: What distinguishes 100% from 50%?")
print("="*80)

print("\n--- Raw signals ---")
for d in data:
    print(f"  {d['label']:20s}  purity={d['purity']}%  "
          f"reaction_strength={d['reaction_strength']:.5f}  "
          f"before_yellow={d['before_yellow']:.5f}")

# Try various combined signals
print("\n--- Candidate signals ---")

# 1. reaction_strength (current primary) — INVERTED
print("\n1. reaction_strength (current)")
for d in data:
    print(f"  {d['label']:20s} purity={d['purity']}%  signal={d['reaction_strength']:.5f}")

# 2. before_yellow
print("\n2. before_yellow")
for d in data:
    print(f"  {d['label']:20s} purity={d['purity']}%  signal={d['before_yellow']:.5f}")

# 3. before blue norm (low norm = more blue absorption = less blue = more yellow)
print("\n3. before_norm_blue (low = more yellow)")
for d in data:
    print(f"  {d['label']:20s} purity={d['purity']}%  signal={d['before_norm'][2]:.5f}")

# 4. before R/B ratio
print("\n4. before R/B ratio")
for d in data:
    r, g, b = d['before_rgb']
    ratio = r / max(b, 1)
    print(f"  {d['label']:20s} purity={d['purity']}%  signal={ratio:.3f}")

# 5. after_abs_blue - before_abs_blue (signed)
print("\n5. A_blue(after) - A_blue(before) [signed]")
for d in data:
    delta = d['after_abs'][2] - d['before_abs'][2]
    print(f"  {d['label']:20s} purity={d['purity']}%  signal={delta:.5f}")

# 6. (R+G)/2 of before sample / 255
print("\n6. before sample (R+G)/2 brightness")
for d in data:
    val = (d['before_rgb'][0] + d['before_rgb'][1]) / 2.0
    print(f"  {d['label']:20s} purity={d['purity']}%  signal={val:.2f}")

# 7. before_norm_blue directly (curcumin lets LESS blue through)
# Higher curcumin = lower norm_blue = higher (1 - norm_blue)
print("\n7. (1 - before_norm_blue) [curcumin absorbs blue]")
for d in data:
    val = 1.0 - d['before_norm'][2]
    print(f"  {d['label']:20s} purity={d['purity']}%  signal={val:.5f}")

# 8. Chroma of before sample in normalized space
print("\n8. before norm chroma: (normR + normG)/2 - normB")
for d in data:
    nr, ng, nb = d['before_norm']
    chroma = (nr + ng) / 2 - nb
    print(f"  {d['label']:20s} purity={d['purity']}%  signal={chroma:.5f}")

# 9. Try: reaction_strength / before_yellow
# If sample is thick, before_yellow high → ratio adjusts for thickness
print("\n9. reaction_strength / before_yellow")
for d in data:
    val = d['reaction_strength'] / max(d['before_yellow'], 0.01)
    print(f"  {d['label']:20s} purity={d['purity']}%  signal={val:.5f}")

# 10. Try: before_abs_blue (how much the extract absorbs blue before reagent)
# More curcumin = more blue absorbed = higher A_blue_before
print("\n10. A_blue(before) [curcumin absorbs blue]")
for d in data:
    print(f"  {d['label']:20s} purity={d['purity']}%  signal={d['before_abs'][2]:.5f}")

# 11. norm_blue(before) / norm_blue(after) — reaction efficiency
print("\n11. norm_blue(before) / norm_blue(after)")
for d in data:
    val = d['before_norm'][2] / max(d['after_norm'][2], 0.001)
    print(f"  {d['label']:20s} purity={d['purity']}%  signal={val:.5f}")

# 12. G channel change: norm_G(before) - norm_G(after)
print("\n12. norm_G(before) - norm_G(after)")
for d in data:
    val = d['before_norm'][1] - d['after_norm'][1]
    print(f"  {d['label']:20s} purity={d['purity']}%  signal={val:.5f}")

# 13. Multi-channel: (A_R(after)-A_R(before)) + (A_G(after)-A_G(before))
print("\n13. Total RG absorbance increase")
for d in data:
    dr = d['after_abs'][0] - d['before_abs'][0]
    dg = d['after_abs'][1] - d['before_abs'][1]
    val = dr + dg
    print(f"  {d['label']:20s} purity={d['purity']}%  signal={val:.5f}")

# 14. after_abs_blue * before_yellow
print("\n14. A_blue(after) * before_yellow")
for d in data:
    val = d['after_abs'][2] * d['before_yellow']
    print(f"  {d['label']:20s} purity={d['purity']}%  signal={val:.5f}")

# 15. (normR_before - normR_after) — R channel change
print("\n15. normR(before) - normR(after)")
for d in data:
    val = d['before_norm'][0] - d['after_norm'][0]
    print(f"  {d['label']:20s} purity={d['purity']}%  signal={val:.5f}")

# 16. Comprehensive: multi-channel absorbance sum of AFTER image only
print("\n16. Sum(A_RGB(after)) = total absorption of after")
for d in data:
    val = sum(d['after_abs'])
    print(f"  {d['label']:20s} purity={d['purity']}%  signal={val:.5f}")

# 17. after_sample_mean = overall brightness of after sample (darker=stronger)
print("\n17. 255 - after_mean (darker = stronger reaction)")
after_means = [82.26, 61.46, 64.76, 65.71]
for d, am in zip(data, after_means):
    val = 255 - am
    print(f"  {d['label']:20s} purity={d['purity']}%  signal={val:.2f}")

# Let's try a linear combo search
print("\n" + "="*80)
print("BEST LINEAR FIT SEARCH")
print("="*80)

# We want: signal = f(features) such that purity = a*signal + b
# gives ~100 for 100% and ~50 for 50%
# Try: using BOTH before_yellow and reaction_strength

# Multiple regression: purity = a*reaction_strength + b*before_yellow + c
X = np.array([[d['reaction_strength'], d['before_yellow']] for d in data])
y = np.array([d['purity'] for d in data])
X_aug = np.column_stack([X, np.ones(len(X))])
coefs, residuals, rank, sv = np.linalg.lstsq(X_aug, y, rcond=None)
a, b, c = coefs
print(f"\nMultiple regression: purity = {a:.3f}*reaction + {b:.3f}*yellow + {c:.3f}")
for d in data:
    pred = a*d['reaction_strength'] + b*d['before_yellow'] + c
    print(f"  {d['label']:20s} actual={d['purity']}%  predicted={pred:.1f}%")

# Try with more features
features = ['reaction_strength', 'before_yellow']
# Add before_abs_blue
X2 = np.array([[d['reaction_strength'], d['before_yellow'], d['before_abs'][2]] for d in data])
X2_aug = np.column_stack([X2, np.ones(len(X2))])
coefs2, _, _, _ = np.linalg.lstsq(X2_aug, y, rcond=None)
a2, b2, c2, d2 = coefs2
print(f"\n3-feature: purity = {a2:.3f}*reaction + {b2:.3f}*yellow + {c2:.3f}*A_blue_before + {d2:.3f}")
for d in data:
    pred = a2*d['reaction_strength'] + b2*d['before_yellow'] + c2*d['before_abs'][2] + d2
    print(f"  {d['label']:20s} actual={d['purity']}%  predicted={pred:.1f}%")

# Try simple: purity = a * (before_yellow - k*reaction_strength) + c
# Or try: the DIFFERENCE of before_yellow between 100% and 50% is small
# but the difference in reaction_strength is clearer... just INVERTED

# Since reaction_strength is higher for 50%, we can use INVERSE:
print("\n--- Using INVERSE reaction_strength ---")
for d in data:
    if d['reaction_strength'] > 0:
        inv = 1.0 / d['reaction_strength']
        print(f"  {d['label']:20s} purity={d['purity']}%  1/RS={inv:.3f}")

# Fit on inverse reaction_strength
inv_rs = np.array([1.0/d['reaction_strength'] for d in data])
X_inv = np.column_stack([inv_rs, np.ones(len(inv_rs))])
coefs_inv, _, _, _ = np.linalg.lstsq(X_inv, y, rcond=None)
a_inv, b_inv = coefs_inv
print(f"\npurity = {a_inv:.3f} * (1/RS) + {b_inv:.3f}")
for d in data:
    pred = a_inv / d['reaction_strength'] + b_inv
    print(f"  {d['label']:20s} actual={d['purity']}%  predicted={pred:.1f}%")
