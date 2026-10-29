import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
import sys
sys.path.insert(0, str(ROOT))
from replay_agent import ReplayAgent, dt, ist_day


class ReplayAgentTests(unittest.TestCase):
    def test_timezone_and_duplicate_state_are_deterministic(self):
        agent = ReplayAgent(ROOT, Path(tempfile.mkdtemp()))
        before = len(agent.events["U-02344"])
        event = {"event_id": "duplicate-test", "user_id": "U-02344", "event": "app_open",
                 "received_at": "2026-09-30T18:00:00Z"}
        agent.apply_event(event)
        agent.apply_event(event)
        self.assertEqual(len(agent.events["U-02344"]), before + 1)
        self.assertEqual(ist_day(dt("2026-10-01T00:00:00Z")), "2026-10-01")

    def test_late_event_is_sorted_by_received_time(self):
        agent = ReplayAgent(ROOT, Path(tempfile.mkdtemp()))
        user = "U-02344"
        agent.apply_event({"event_id": "late-test", "user_id": user, "event": "play_started",
                           "received_at": "2026-09-01T00:00:00Z"})
        received = [e["received_at"] for e in agent.events[user]]
        self.assertEqual(received, sorted(received))

    def test_duplicate_trigger_is_idempotent_and_stale_trigger_abstains(self):
        agent = ReplayAgent(ROOT, Path(tempfile.mkdtemp()))
        trigger = next(__import__("replay_agent").read_jsonl(ROOT / "replay/triggers.jsonl"))
        agent.handle_trigger(trigger, set())
        first_count = len(agent.decisions)
        agent.handle_trigger(trigger, set())
        self.assertEqual(len(agent.decisions), first_count)
        stale = dict(trigger)
        stale["occurred_at"] = "2026-09-20T00:00:00Z"
        stale["sent_at"] = "2026-10-01T00:00:00Z"
        stale["received_at"] = "2026-10-01T00:00:00Z"
        stale["deliver_at"] = stale["received_at"]
        stale["event_id"] = "stale-trigger-test"
        agent.handle_trigger(stale, set())
        self.assertIn("stale_offline_trigger", agent.decisions[-1]["no_action_reason"])

    def test_corrected_goal_rule_is_in_effective_decision_contract(self):
        agent = ReplayAgent(ROOT, Path(tempfile.mkdtemp()))
        agent.apply_control({
            "at": "2026-10-01T08:30:00Z",
            "type": "goal_rule_corrected",
            "goal_id": "G03",
            "new_success": {
                "event": "play_started",
                "condition": "on a later IST calendar day within the window",
                "window_hours": 48,
            },
        })
        trigger = next(e for e in __import__("replay_agent").read_jsonl(ROOT / "replay/triggers.jsonl")
                       if e["user_id"] == "U-02344")
        agent.handle_trigger(trigger, set())
        self.assertEqual(agent.decisions[-1]["goal"]["id"], "G05")
        goal = next(g for g in agent.goals if g["goal_id"] == "G03")
        self.assertEqual(agent._effective_success(goal)["event"], "play_started")

    def test_holdout_and_faults_cannot_publish(self):
        out = Path(tempfile.mkdtemp())
        agent = ReplayAgent(ROOT, out)
        holdout = next(e for e in __import__("replay_agent").read_jsonl(ROOT / "replay/triggers.jsonl")
                       if agent.arms[e["user_id"]] == "holdout")
        agent.handle_trigger(holdout, set())
        self.assertEqual(agent.actions, [])
        self.assertIn("randomized_holdout", agent.decisions[-1]["no_action_reason"])
        faulty = next(e for e in __import__("replay_agent").read_jsonl(ROOT / "replay/triggers.jsonl")
                      if e["event_id"] in agent.faults)
        agent.handle_trigger(faulty, set())
        self.assertIn("model_fault_rejected", agent.decisions[-1]["no_action_reason"])

    def test_policy_gates_consent_quiet_hours_and_daily_cap(self):
        agent = ReplayAgent(ROOT, Path(tempfile.mkdtemp()))
        state = agent.states["U-02344"]
        when = dt("2026-10-01T12:00:00Z")
        self.assertEqual(agent._policy(state, "push", when, set())[0], True)
        state["profile"]["notifications_opt_in"] = "false"
        self.assertEqual(agent._policy(state, "push", when, set())[1], "push_consent_missing_or_revoked")
        state["profile"]["notifications_opt_in"] = "true"
        self.assertEqual(agent._policy(state, "push", dt("2026-10-01T01:00:00Z"), set())[1], "quiet_hours_IST")
        self.assertEqual(agent._policy(state, "push", when, {"2026-10-02"})[0], True)
        self.assertEqual(agent._policy(state, "push", when, {"2026-10-01"})[1], "combined_message_daily_cap")

    def test_withdrawal_is_enforced(self):
        agent = ReplayAgent(ROOT, Path(tempfile.mkdtemp()))
        agent.apply_control({"at": "2026-10-01T07:00:00Z", "type": "title_withdrawn", "title_id": "T0001"})
        title = agent.catalogue["T0001"]
        self.assertFalse(agent._available(title, datetime(2026, 10, 1, 8, tzinfo=timezone.utc)))

    def test_state_checkpoint_is_written(self):
        output = Path(tempfile.mkdtemp())
        state_dir = output / "state"
        agent = ReplayAgent(ROOT, output, state_dir=state_dir)
        agent.run()
        self.assertTrue((state_dir / "U-02344.json").exists())

    def test_outcome_judge_does_not_claim_unsupported_goals(self):
        from replay_agent import judge_outcomes
        out = Path(tempfile.mkdtemp())
        (out / "actions.jsonl").write_text("", encoding="utf-8")
        events = out / "outcome.jsonl"
        events.write_text(json.dumps({"user_id": "U-1", "event": "tv_app_open"}) + "\n", encoding="utf-8")
        result = judge_outcomes(ROOT, events, out)
        self.assertFalse(result["goals"]["G10"]["supported"])
        self.assertFalse(result["goals"]["G11"]["supported"])


if __name__ == "__main__":
    unittest.main()
