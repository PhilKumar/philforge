"""Regression coverage for a single exit saved before and after reconciliation."""

import unittest

import app


def _trade(pnl, reason):
    return {
        "symbol": "NIFTY 23700PE 2026-09-15",
        "transaction_type": "BUY",
        "option_type": "PE",
        "strike": 23700,
        "entry_time": "2026-09-10 09:25:09",
        "exit_time": "2026-09-10 15:26:36",
        "entry_premium": 265.00,
        "exit_premium": 279.45,
        "quantity": 130,
        "pnl": pnl,
        "exit_reason": reason,
    }


class ReconciledExitIsNotSavedTwice(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_list_runs = app._db_mod.list_runs
        self.original_create_run = app._db_mod.create_run_record

    async def asyncTearDown(self):
        app._db_mod.list_runs = self.original_list_runs
        app._db_mod.create_run_record = self.original_create_run

    def test_identity_ignores_only_mutable_reconciliation_fields(self):
        initial = _trade(1763.21, "EXIT_SIGNAL")
        reconciled = _trade(1798.44, "BROKER_MANUAL_EXIT")
        self.assertEqual(
            app._history_persistence_trade_signature(initial),
            app._history_persistence_trade_signature(reconciled),
        )
        self.assertNotEqual(
            app._history_trade_signature(initial),
            app._history_trade_signature(reconciled),
        )

    async def test_live_stop_does_not_create_a_bulk_copy_after_exit_save(self):
        async def list_runs(_user_id):
            return [
                {
                    "mode": "live",
                    "run_name": "PE_NoTarget",
                    "trade_count": 1,
                    "trades": [_trade(1763.21, "EXIT_SIGNAL")],
                }
            ]

        created = []

        async def create_run(_user_id, run):
            created.append(run)
            return {"id": 99, **run}

        app._db_mod.list_runs = list_runs
        app._db_mod.create_run_record = create_run
        await app._save_live_run_to_history(
            {
                "strategy_name": "PE_NoTarget",
                "closed_trades": [_trade(1798.44, "BROKER_MANUAL_EXIT")],
                "strategy": {},
            },
            explicit_user_id=1,
        )
        self.assertEqual(created, [])

    async def test_paper_stop_uses_the_same_guard(self):
        async def list_runs(_user_id):
            return [
                {
                    "mode": "paper",
                    "run_name": "My_First_Run_PE",
                    "trade_count": 1,
                    "trades": [_trade(-2859.18, "EXIT_SIGNAL")],
                }
            ]

        created = []

        async def create_run(_user_id, run):
            created.append(run)
            return {"id": 99, **run}

        app._db_mod.list_runs = list_runs
        app._db_mod.create_run_record = create_run
        await app._save_paper_run_to_history(
            {
                "strategy_name": "My_First_Run_PE",
                "closed_trades": [_trade(-2845.66, "MANUAL_EXIT")],
                "strategy": {},
            },
            explicit_user_id=1,
        )
        self.assertEqual(created, [])
