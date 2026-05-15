"""Temporary P7 analysis script — step 6 & 7."""
import csv
import os

def load(sym, part):
    p = f'runtime/experiments/tactical_proof/{sym}/{part}.csv'
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return list(csv.DictReader(f))

baselines = {'uvxy_4h': 82.81, 'vxx_4h': 226.79}

print("=== Step 6: 综合摘要 ===")
for sym in ('uvxy_4h', 'vxx_4h'):
    base = baselines[sym]
    print(f'\n=== {sym}: TURBO=OFF baseline = +{base}% ===')
    for part in ('single_dim', 'joint', 'cost'):
        rows = load(sym, part)
        if not rows:
            print(f'  {part}: 缺失')
            continue
        b_all_ok = []
        b1_ok_count = b2_ok_count = b3_ok_count = 0
        for r in rows:
            b1 = int(r.get('b1_trigger_total', 0)) > 0
            b2 = float(r.get('b2_avg_session_bars', 999)) <= 20
            b3 = float(r.get('b3_total_return_pct', -999)) >= base
            if b1: b1_ok_count += 1
            if b2: b2_ok_count += 1
            if b3: b3_ok_count += 1
            if b1 and b2 and b3:
                b_all_ok.append(r)
        max_ret = max(float(r.get('b3_total_return_pct', -999)) for r in rows)
        print(f'  {part}: {len(rows)} trials, B1>0: {b1_ok_count}, '
              f'B2<=20: {b2_ok_count}, B3>=baseline: {b3_ok_count}, '
              f'B 全满足: {len(b_all_ok)}, max_ret: {max_ret:+.2f}%')
        for r in b_all_ok[:5]:
            print(f'    [REBUTTAL] {r["trial_id"]}: ret={r["b3_total_return_pct"]}% '
                  f'b1={r["b1_trigger_total"]} avg_age={r["b2_avg_session_bars"]}')

print('\n=== Step 7: 成本敏感性 ===')
for sym in ('uvxy_4h', 'vxx_4h'):
    rows_path = f'runtime/experiments/tactical_proof/{sym}/cost.csv'
    if not os.path.exists(rows_path):
        print(f'{sym}: cost.csv 缺失')
        continue
    with open(rows_path) as f:
        rows = list(csv.DictReader(f))
    on = sorted([r for r in rows if 'OFF' not in r['trial_id']], key=lambda r: float(r.get('slip_bps', 0)))
    off = sorted([r for r in rows if 'OFF' in r['trial_id']], key=lambda r: float(r.get('slip_bps', 0)))
    print(f'\n{sym}:')
    print('  slip / comm                | TURBO=ON ret | TURBO=OFF ret | gap')
    for ron, roff in zip(on, off):
        on_ret = float(ron['b3_total_return_pct'])
        off_ret = float(roff['b3_total_return_pct'])
        print(f'  slip={float(ron["slip_bps"]):>5.1f}bps comm={float(ron["comm_per_share"]):>7.5f} '
              f'| {on_ret:+8.2f}% | {off_ret:+8.2f}% | {on_ret - off_ret:+.2f}pp')
