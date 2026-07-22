"""
Test script: sends all 8 test images to the /analyze endpoint.

Test images (by filename):
  100% pure:
    - "100% pure haldi in isopropyl alcohol .jpeg"  (BEFORE - isopropyl)
    - "100% pure haldi with reagent .jpeg"          (AFTER  - reagent)
    - "100%pure with isopropyl alcohol.jpeg"         (BEFORE - isopropyl, alt)
    - "100%_pure_withreagent.jpeg"                   (AFTER  - reagent, alt)
  50% pure:
    - "50% pure in isopropyl alcohol .jpeg"          (BEFORE - isopropyl)
    - "50%pure haldi in reagent.jpeg"                (AFTER  - reagent)
    - "50% pure image in isopropyl alcohol 2.jpeg"   (BEFORE - isopropyl, alt)
    - "50%pure_with reagnet.jpeg"                    (AFTER  - reagent, alt)

We pair them:
  Pair 1 (100% pure):  before="100% pure haldi in isopropyl alcohol .jpeg"
                       after ="100% pure haldi with reagent .jpeg"
  Pair 2 (100% pure alt): before="100%pure with isopropyl alcohol.jpeg"
                           after ="100%_pure_withreagent.jpeg"
  Pair 3 (50% pure):  before="50% pure in isopropyl alcohol .jpeg"
                       after ="50%pure haldi in reagent.jpeg"
  Pair 4 (50% pure alt): before="50% pure image in isopropyl alcohol 2.jpeg"
                          after ="50%pure_with reagnet.jpeg"
"""
import requests
import os
import json
import sys

BASE = "http://localhost:8000"
TEST_DIR = os.path.join(os.path.dirname(__file__), "test images")

pairs = [
    {
        "label": "100% pure (pair 1)",
        "expected": "~100%",
        "before": "100% pure haldi in isopropyl alcohol .jpeg",
        "after": "100% pure haldi with reagent .jpeg",
    },
    {
        "label": "100% pure (pair 2 / alt)",
        "expected": "~100%",
        "before": "100%pure with isopropyl alcohol.jpeg",
        "after": "100%_pure_withreagent.jpeg",
    },
    {
        "label": "50% pure (pair 1)",
        "expected": "~50%",
        "before": "50% pure in isopropyl alcohol .jpeg",
        "after": "50%pure haldi in reagent.jpeg",
    },
    {
        "label": "50% pure (pair 2 / alt)",
        "expected": "~50%",
        "before": "50% pure image in isopropyl alcohol 2.jpeg",
        "after": "50%pure_with reagnet.jpeg",
    },
]

def test_pair(pair):
    print(f"\n{'='*70}")
    print(f"TEST: {pair['label']}  (expected: {pair['expected']})")
    print(f"  BEFORE: {pair['before']}")
    print(f"  AFTER:  {pair['after']}")
    print(f"{'='*70}")
    
    before_path = os.path.join(TEST_DIR, pair["before"])
    after_path = os.path.join(TEST_DIR, pair["after"])
    
    if not os.path.exists(before_path):
        print(f"  ERROR: BEFORE file not found: {before_path}")
        return None
    if not os.path.exists(after_path):
        print(f"  ERROR: AFTER file not found: {after_path}")
        return None
    
    with open(before_path, "rb") as bf, open(after_path, "rb") as af:
        files = {
            "before_image": (pair["before"], bf, "image/jpeg"),
            "after_image": (pair["after"], af, "image/jpeg"),
        }
        try:
            resp = requests.post(f"{BASE}/analyze", files=files, timeout=60)
        except Exception as e:
            print(f"  ERROR: {e}")
            return None
    
    data = resp.json()
    
    print(f"\n  Status: {resp.status_code}")
    print(f"  QC passed:          {data.get('qc_passed')}")
    print(f"  Comparable:         {data.get('comparable')}")
    print(f"  Sample OK:          {data.get('sample_ok')}")
    print(f"  Purity index:       {data.get('purity_index')}")
    print(f"  Purity display:     {data.get('purity_display')}")
    print(f"  Band:               {data.get('band')}")
    print(f"  Confidence:         {data.get('confidence')}")
    print(f"  Confidence score:   {data.get('confidence_score')}")
    print(f"  Low confidence:     {data.get('low_confidence')}")
    print(f"  Calibration mode:   {data.get('calibration_mode')}")
    print(f"  Reaction strength:  {data.get('reaction_strength')}")
    print(f"  Reaction delta:     {data.get('reaction_delta')}")
    print(f"  Before yellow:      {data.get('before_yellow')}")
    print(f"  White diff:         {data.get('white_diff')}")
    print(f"  Dye flag:           {data.get('dye_flag')}")
    print(f"  Over range:         {data.get('over_range')}")
    
    if data.get('warning'):
        print(f"  WARNING:            {data.get('warning')}")
    if data.get('sample_issues'):
        print(f"  Sample issues:      {data.get('sample_issues')}")
    if data.get('saturation_note'):
        print(f"  Saturation note:    {data.get('saturation_note')}")
    if data.get('low_confidence_message'):
        print(f"  Low conf message:   {data.get('low_confidence_message')}")
    if data.get('confidence_reasons'):
        print(f"  Conf reasons:       {data.get('confidence_reasons')}")
    
    # Debug info
    debug = data.get("debug", {})
    if debug:
        before_d = debug.get("before", {})
        after_d = debug.get("after", {})
        print(f"\n  --- Debug ---")
        print(f"  Before sample_rgb:  {before_d.get('sample_rgb')}")
        print(f"  Before white_rgb:   {before_d.get('white_rgb')}")
        print(f"  Before norm_rgb:    {before_d.get('norm_rgb')}")
        print(f"  Before absorbance:  {before_d.get('absorbance_rgb')}")
        print(f"  Before sample_method: {before_d.get('sample_method')}")
        print(f"  Before sample_pixels: {before_d.get('sample_pixels')}")
        print(f"  Before sample_mean:   {before_d.get('sample_mean')}")
        print(f"  After sample_rgb:   {after_d.get('sample_rgb')}")
        print(f"  After white_rgb:    {after_d.get('white_rgb')}")
        print(f"  After norm_rgb:     {after_d.get('norm_rgb')}")
        print(f"  After absorbance:   {after_d.get('absorbance_rgb')}")
        print(f"  After sample_method:  {after_d.get('sample_method')}")
        print(f"  After sample_pixels:  {after_d.get('sample_pixels')}")
        print(f"  After sample_mean:    {after_d.get('sample_mean')}")
        print(f"  After darkness:       {debug.get('after_darkness')}")
        print(f"  Calibration fit:      {debug.get('calibration_fit')}")
    
    return data

if __name__ == "__main__":
    # Check health first
    try:
        h = requests.get(f"{BASE}/health", timeout=5)
        print(f"Health: {h.json()}")
    except Exception as e:
        print(f"Cannot reach backend at {BASE}: {e}")
        print("Start the backend first: run-backend.bat")
        sys.exit(1)
    
    results = []
    for pair in pairs:
        data = test_pair(pair)
        results.append({"pair": pair, "result": data})
    
    print(f"\n\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    for r in results:
        p = r["pair"]
        d = r["result"]
        if d is None:
            status = "ERROR"
        elif d.get("purity_index") is not None:
            status = f"purity={d['purity_index']}% ({d.get('band', '?')})"
        elif d.get("warning"):
            status = f"BLOCKED: {d['warning'][:60]}"
        else:
            status = "NO SCORE"
        print(f"  {p['label']:30s} expected={p['expected']:8s}  -> {status}")
