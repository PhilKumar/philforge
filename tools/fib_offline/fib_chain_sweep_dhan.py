"""fib_chain_sweep.py, priced from the local Dhan archive instead of Upstox.

Upstox's expired options start in September 2024, so the auto-mother sweep
cannot see 2021-2024 on its own. This swaps its premium source for the Dhan
store (data/dhan_options{,_e2,_m1,_m2}) and runs the sweep unchanged:

    SYMBOL=NIFTY NEXT_MOTHER=breaking TRAIL=1 TARGET=0.25 CAP=75000 BARREN_CAP=999999 \
        python3.11 tools/fib_offline/fib_chain_sweep_dhan.py 2021-01-01 2024-10-02 OUT.csv

Dhan holds only ATM+/-10 strikes, so a winner that runs out of the band loses
its exit price -- a Dhan-priced Fib book is a FLOOR (see the 22-Aug-2026 run:
Rs 1,25,878 over 614 of 622 campaigns). Used by tools/tearsheet/ladder_report.py
for the four-book section of the five-year tearsheet (2026-09-22).
"""

import os
import runpy
import sys

R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, R)
sys.path.insert(0, R + "/tools/fib_offline")
import fib_replay

from tools.nifty_expiry_calendar import weekly_expiries
from tools.nifty_index_from_dhan import load_minutes, sessions
from tools.philforge_dhan_selector import DhanHistoricalPremiumSelector

STORES = {
    k: os.path.join(R, "data", v)
    for k, v in (
        ("e1", "dhan_options"),
        ("e2", "dhan_options_e2"),
        ("m1", "dhan_options_m1"),
        ("m2", "dhan_options_m2"),
    )
}


class DhanSource:
    def __init__(self):
        wk = weekly_expiries(sessions(load_minutes()))
        self.sel = DhanHistoricalPremiumSelector("26000", STORES, wk)
        self.served = self.missed = 0

    def expiries(self):
        return sorted(set(self.sel.weeklies) | set(self.sel.monthlies))

    def lookup(self, when, c):
        stamp = when.replace(second=0, microsecond=0, tzinfo=None)
        which = self.sel._store_for(stamp.date(), c.expiry)
        if which is None:
            self.missed += 1
            return None
        fr = self.sel._month(which, f"{stamp:%Y-%m}")
        if fr.empty or stamp not in fr.index:
            self.missed += 1
            return None
        rows = fr.loc[[stamp]]
        rows = rows[
            (rows["side"].str.upper() == str(c.option_type).upper()) & (rows["strike"].astype(float) == float(c.strike))
        ]
        if rows.empty or float(rows["open"].iloc[-1]) <= 0:
            self.missed += 1
            return None
        self.served += 1
        return float(rows["open"].iloc[-1])


SRC = DhanSource()
fib_replay._listed_source = lambda: SRC
sys.argv = ["fib_chain_sweep.py"] + sys.argv[1:]
try:
    runpy.run_path(R + "/tools/fib_offline/fib_chain_sweep.py", run_name="__main__")
finally:
    print("dhan lookups served", SRC.served, "missed", SRC.missed)
