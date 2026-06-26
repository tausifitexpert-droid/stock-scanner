#!/usr/bin/env python3
"""
APEX Pre-Delivery Validator
============================
Run this before EVERY file delivery.
All checks must pass before presenting to user.

Usage:
  python3 validate.py [filepath]
  python3 validate.py  # defaults to apex.html
"""
import re, sys

SOURCE = '/mnt/user-data/outputs/apex.html'

def validate(content):
    errors, warnings = [], []
    
    scripts = re.findall(r'<script>(.*?)</script>', content, re.DOTALL)
    if not scripts:
        errors.append("No <script> block found")
        return errors, warnings
    js = scripts[0]

    # ── 1. Script tag not embedded inside JS ─────────────────
    inner = re.findall(r'</script>', js)
    if inner:
        errors.append(f"</script> embedded inside JS ({len(inner)}x) — browser cuts script early")

    # ── 2. Balance checks ─────────────────────────────────────
    for o, c, name in [('{','}','Braces'), ('(',')', 'Parens'), ('[',']','Brackets')]:
        oc, cc = js.count(o), js.count(c)
        if oc != cc:
            errors.append(f"{name} imbalanced: {oc} open vs {cc} close")

    # ── 3. Backtick check ─────────────────────────────────────
    if js.count('`') % 2 != 0:
        errors.append(f"Odd backtick count ({js.count('`')}) — unclosed template literal")

    # ── 3b. No await in non-async functions (SyntaxError killer) ────
    for _m in re.finditer(r'(?<!async )function (\w+)\s*\([^)]*\)\s*\{', js):
        _fn, _fs = _m.group(1), _m.start()
        _d, _st, _e = 0, False, _fs
        for _ii, _cc in enumerate(js[_fs:], _fs):
            if _cc=='{': _d+=1; _st=True
            elif _cc=='}': _d-=1
            if _st and _d==0: _e=_ii+1; break
        if 'await ' in js[_fs:_e]:
            errors.append(f"Non-async function '{_fn}' uses await — causes SyntaxError on load")

    # ── 4. Required functions ─────────────────────────────────
    required = [
        ('async function runScan()',     'runScan'),
        ('async function fetchActives',  'fetchActives'),
        ('async function fetchQuotes',   'fetchQuotes'),
        ('function scoreStock(',         'scoreStock'),
        ('function renderCards(',        'renderCards'),
        ('function getSB()',             'getSB'),
        ('function setStep(',            'setStep'),
        ('function lg(',                 'lg (logger)'),
        ('async function runEODVerification', 'runEODVerification'),
        ('async function saveToDb(',     'saveToDb'),
        ('async function loadHistory',   'loadHistory'),
    ]
    for pattern, name in required:
        if pattern not in js:
            errors.append(f"Missing function: {name}")

    # ── 5. Button wiring ──────────────────────────────────────
    if 'onclick="runScan()"' not in content:
        errors.append('Scan button missing onclick="runScan()"')

    # ── 6. Key constants (var or const) ───────────────────────
    for c1, c2 in [('const FMP','var FMP'), ('const SB_URL','var SB_URL'),
                   ('const SB_KEY','var SB_KEY'), ('const SKIP_SYM','var SKIP_SYM')]:
        if c1 not in js and c2 not in js:
            errors.append(f"Missing constant: {c1}")

    # ── 7. No legacy v3 endpoints ─────────────────────────────
    v3 = re.findall(r'financialmodelingprep\.com/api/v3', js)
    if v3:
        warnings.append(f"Legacy FMP v3 endpoint used ({len(v3)}x)")

    # ── 8. No credential inputs in UI ────────────────────────
    for cred in ['id="sbUrl"', 'id="sbKey"']:
        if cred in content:
            warnings.append(f"Credential input visible in UI: {cred}")

    # ── 9. CONSISTENCY: gate value must match UI messages ─────
    # Find the score gate value
    gate_match = re.search(r'score>=(\d+)&&s\.pct>0', js)
    if gate_match:
        gate_val = gate_match.group(1)
        # Check if any UI text still shows old gate value (common bug)
        # Look for threshold mentions in UI-facing strings
        ui_mentions = re.findall(r'(\d+)/100 quality threshold', content)
        for mention in ui_mentions:
            if mention != gate_val:
                errors.append(
                    f"Gate mismatch: code uses {gate_val} but UI says '{mention}/100 quality threshold'"
                )
        # Check log messages
        log_mentions = re.findall(r"No stocks scored (\d+)\+", js)
        for mention in log_mentions:
            if mention != gate_val:
                errors.append(
                    f"Gate mismatch: code gate={gate_val} but log says 'No stocks scored {mention}+'"
                )
    else:
        warnings.append("Could not find score gate pattern (score>=N&&s.pct>0)")

    # ── 10. CONSISTENCY: no stale threshold numbers ───────────
    # If gate is not 55, make sure 55 doesn't appear in critical logic paths
    if gate_match and gate_match.group(1) != '55':
        # Check for 55 in score comparisons (not in comments or strings)
        stale = re.findall(r'score[><=!]+55\b', js)
        if stale:
            errors.append(f"Stale score threshold '55' still in JS logic: {stale}")

    # ── 11. null-safety check ─────────────────────────────────
    if 'document.getElementById(id).className' in js:
        errors.append("setStep/stepState not null-safe — crashes on missing element")

    # ── 12. EOD verification integrity ───────────────────────
    if 'async function runEODVerification' in js:
        eod_idx = js.find('async function runEODVerification')
        eod_end = js.find('\nasync function ', eod_idx + 10)
        eod_body = js[eod_idx:eod_end] if eod_end > 0 else js[eod_idx:eod_idx+3000]
        if 'q.open' not in eod_body and 'actualOpen' not in eod_body:
            errors.append("EOD verification doesn't use actual market open price")
        if 'q.price' not in eod_body and 'actualClose' not in eod_body:
            errors.append("EOD verification doesn't use actual close price")

    # ── 13. saveToDb integrity ────────────────────────────────
    if 'async function saveToDb' in js:
        save_idx = js.find('async function saveToDb')
        save_body = js[save_idx:save_idx+1500]
        if 'open_price' not in save_body:
            errors.append("saveToDb doesn't save open_price")
        if 'verdict' not in save_body:
            errors.append("saveToDb doesn't save verdict field")
        if 'price_at_scan' not in save_body:
            errors.append("saveToDb doesn't save price_at_scan")

    # ── 14. fetchQuotes integrity ─────────────────────────────
    if 'async function fetchQuotes' in js:
        fq_idx = js.find('async function fetchQuotes')
        fq_body = js[fq_idx:fq_idx+1200]
        if 's0.open' not in fq_body and 'q.open' not in fq_body and '.open' not in fq_body:
            warnings.append("fetchQuotes may not be fetching open price field")
        if 'previousClose' not in fq_body and 'prevClose' not in fq_body:
            warnings.append("fetchQuotes may not be fetching previousClose field")

    # ── 15. Market holiday check present ─────────────────────
    if 'checkMarketStatus' not in js:
        warnings.append("No market holiday check (checkMarketStatus missing)")
    if 'isHoliday' not in js:
        warnings.append("No holiday gate on scan")

    # ── 16. Auto EOD scheduler present ───────────────────────
    if 'checkAutoEOD' not in js:
        warnings.append("Auto EOD scheduler (checkAutoEOD) missing")
    if 'eodAutoRan' not in js:
        warnings.append("EOD dedup (eodAutoRan) missing — could double-run")

    # ── 17. biggest-gainers endpoint present ─────────────────
    if 'biggest-gainers' not in js:
        warnings.append("biggest-gainers endpoint missing — candidate pool may be too narrow")

    # ── 18. Deduplication on save ─────────────────────────────
    if 'Already saved today' not in js:
        warnings.append("No save deduplication — duplicate picks possible")

    # ── 19. HTML structure ────────────────────────────────────
    for tag in ['<html', '<head', '<body', '</html>']:
        if tag not in content:
            warnings.append(f"Missing HTML tag: {tag}")

    # ── 20. Sleep >= 3000 in ALL hist fetch loops ─────────────
    # FMP free tier rate limit requires >= 3s between symbol fetches.
    # Catches the bug where a fix was applied to one function but not others.
    import re as _re
    short_sleeps = _re.findall(r'await sleep\((\d+)\)', js)
    for ms_str in short_sleeps:
        ms = int(ms_str)
        # Allow short sleeps only for non-hist contexts (scan enrichment uses 80-150ms)
        # Any sleep inside a hist fetch loop must be >= 3000
        # We detect by checking if sleep(N<3000) appears near a hist fetch URL
        pass  # fine-grained check below

    # Check specifically: no sleep < 3000 in verification functions
    for fn_name, fn_pattern in [
        ('runEODVerification', 'async function runEODVerification'),
        ('reVerifyAll',        'async function reVerifyAll'),
        ('runVerification',    'async function runVerification'),
    ]:
        if fn_pattern not in js:
            continue
        fn_start = js.find(fn_pattern)
        fn_end   = js.find('\nasync function ', fn_start + 10)
        fn_body  = js[fn_start:fn_end] if fn_end > 0 else js[fn_start:fn_start+5000]
        bad_sleeps = _re.findall(r'await sleep\((\d+)\)', fn_body)
        for ms_str in bad_sleeps:
            ms = int(ms_str)
            if ms < 3000 and ms > 0:
                errors.append(
                    f"{fn_name}(): sleep({ms}) is too short — FMP rate limit requires >= 3000ms between hist fetches"
                )

    # ── 21. reVerifyAll must NOT call fetchHistorical ────────
    # fetchHistorical fetches 5yr (~1260 rows) which silently fails on FMP free tier.
    # Verification functions must use 90-day date-range fetches (~63 rows).
    if 'async function reVerifyAll' in js:
        rv_start = js.find('async function reVerifyAll')
        rv_end   = js.find('\nasync function ', rv_start + 10)
        rv_body  = js[rv_start:rv_end] if rv_end > 0 else js[rv_start:rv_start+5000]
        # Check for actual CALL (not comment mentioning the name)
        if _re.search(r'(?<!// )(?<!NOT )fetchHistorical\s*\(', rv_body):
            errors.append(
                "reVerifyAll() calls fetchHistorical() — this fetches 5yr (~1260 rows) which silently fails on FMP free tier. Use 90-day date-range fetch instead."
            )

    # ── 22. All 3 verification functions must detect empty hist response ──
    for fn_name, fn_pattern in [
        ('runEODVerification', 'async function runEODVerification'),
        ('reVerifyAll',        'async function reVerifyAll'),
    ]:
        if fn_pattern not in js:
            continue
        fn_start = js.find(fn_pattern)
        fn_end   = js.find('\nasync function ', fn_start + 10)
        fn_body  = js[fn_start:fn_end] if fn_end > 0 else js[fn_start:fn_start+5000]
        if 'hist.length' not in fn_body and 'rvhist.length' not in fn_body:
            errors.append(
                f"{fn_name}(): does not check for empty hist response — FMP silent rate limit (HTTP 200 + empty array) will silently fail and leave picks PENDING"
            )


    return errors, warnings


def run(filepath=None):
    fp = filepath or SOURCE
    try:
        with open(fp) as f:
            content = f.read()
    except FileNotFoundError:
        print(f"❌ File not found: {fp}")
        return False

    errors, warnings = validate(content)

    print(f"\n{'='*58}")
    print(f"  APEX VALIDATOR — {fp.split('/')[-1]}")
    print(f"{'='*58}")

    if warnings:
        print(f"\n⚠  WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"   • {w}")

    if errors:
        print(f"\n❌ ERRORS ({len(errors)}) — DO NOT DELIVER:")
        for e in errors:
            print(f"   • {e}")
        print(f"\n{'='*58}")
        print(f"  RESULT: FAILED — fix errors before delivering")
        print(f"{'='*58}\n")
        return False
    else:
        print(f"\n✅ ALL {22} CHECKS PASSED")
        print(f"   Warnings: {len(warnings)} | Errors: 0")
        print(f"{'='*58}\n")
        return True


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    ok = run(path)
    sys.exit(0 if ok else 1)
