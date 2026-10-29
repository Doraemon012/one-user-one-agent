#!/usr/bin/env python3
"""Deterministic, local replay agent for the One User, One Agent assignment."""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
REPLAY_START = datetime(2026, 10, 1, tzinfo=IST)


def dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ist_day(value: datetime) -> str:
    return value.astimezone(IST).date().isoformat()


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class ReplayModelProvider:
    """Credential-free provider boundary; faults are deterministic replay inputs."""

    def __init__(self, faults: dict[str, dict[str, Any]]):
        self.faults = faults

    def evaluate(self, trigger_event_id: str) -> dict[str, Any]:
        fault = self.faults.get(trigger_event_id)
        return {"calls": 0, "fault": fault.get("fault") if fault else None}


class ReplayAgent:
    def __init__(self, pack: Path, output: Path, state_dir: Path | None = None):
        self.pack, self.output = pack, output
        self.state_dir = state_dir or (pack / "viewer_state")
        self.catalogue = {r["title_id"]: r for r in load_csv(pack / "data/catalogue.csv")}
        self.profiles = {r["user_id"]: r for r in load_csv(pack / "data/profiles_snapshot.csv")}
        self.arms = {r["user_id"]: r["arm"] for r in load_csv(pack / "replay/arms.csv")}
        self.goals = self._load_json("config/goals.json")["goals"]
        self.interventions = self._load_json("config/interventions.json")
        self.withdrawn: dict[str, datetime] = {}
        self.corrected_goals: dict[str, dict[str, Any]] = {}
        self.unavailable_until: dict[str, datetime] = {}
        self.degraded_until: dict[str, datetime] = {}
        self.faults = {e["trigger_event_id"]: e for e in read_jsonl(pack / "replay/model_faults.jsonl")}
        self.provider = ReplayModelProvider(self.faults)
        self.events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.seen_event_ids: set[str] = set()
        self.seen_trigger_ids: set[str] = set()
        self.states: dict[str, dict[str, Any]] = {}
        self.decisions: list[dict[str, Any]] = []
        self.actions: list[dict[str, Any]] = []
        self.goal_rules: dict[str, dict[str, Any]] = {}
        self.next_decision = 1
        self.next_action = 1
        self._load_history()

    def _load_json(self, relative: str):
        with (self.pack / relative).open(encoding="utf-8") as handle:
            return json.load(handle)

    def _load_history(self):
        trigger_users = {e["user_id"] for e in read_jsonl(self.pack / "replay/triggers.jsonl")}
        for event in read_jsonl(self.pack / "data/events.jsonl"):
            if event["user_id"] in trigger_users and event["event_id"] not in self.seen_event_ids:
                self.seen_event_ids.add(event["event_id"])
                self.events[event["user_id"]].append(event)
        for user in trigger_users:
            self.events[user].sort(key=lambda e: (e.get("received_at", ""), e["event_id"]))
            self.states[user] = self._state(user)

    def _state(self, user: str) -> dict[str, Any]:
        events = self.events[user]
        plays = [e for e in events if e["event"] == "play_started"]
        opens = [e for e in events if e["event"] == "app_open"]
        completes = [e for e in events if e["event"] == "episode_completed"]
        purchases = [e for e in events if e["event"] in ("purchase_completed", "subscription_renewed")]
        opt_out = {e.get("properties", {}).get("channel") for e in events
                   if e["event"] == "notification_opt_out"}
        profile = self.profiles.get(user, {})
        profile_updated = dt(profile.get("updated_at"))
        latest_event = max((dt(e.get("received_at")) for e in events), default=None)
        evidence_conflicts = []
        status = profile.get("subscription_status", "unknown")
        if purchases and status == "never":
            evidence_conflicts.append("purchase_history_conflicts_with_profile_never")
        if any(e["event"] == "subscription_lapsed" for e in events) and status == "active":
            evidence_conflicts.append("lapse_event_conflicts_with_profile_active")
        return {
            "user_id": user, "profile": profile, "events": events, "plays": plays,
            "opens": opens, "completes": completes, "purchases": purchases,
            "opt_out": opt_out, "status": status, "evidence_conflicts": evidence_conflicts,
            "profile_fresh": bool(profile_updated and latest_event and profile_updated >= latest_event - timedelta(days=2)),
            "last_open": max((dt(e["received_at"]) for e in opens), default=None),
            "last_play": max((dt(e["received_at"]) for e in plays), default=None),
        }

    def apply_event(self, event: dict[str, Any]):
        if event["event_id"] in self.seen_event_ids:
            return
        self.seen_event_ids.add(event["event_id"])
        user = event["user_id"]
        if user in self.events:
            self.events[user].append(event)
            self.events[user].sort(key=lambda e: (e.get("received_at", ""), e["event_id"]))
            self.states[user] = self._state(user)

    def apply_control(self, control: dict[str, Any]):
        when = dt(control["at"])
        typ = control["type"]
        if typ == "title_withdrawn":
            self.withdrawn[control["title_id"]] = when
        elif typ == "goal_rule_corrected":
            self.corrected_goals[control["goal_id"]] = {**control["new_success"], "applied_at": control["at"]}
            self.goal_rules[control["goal_id"]] = {**control["new_success"], "applied_at": control["at"]}
        elif typ == "dependency_unavailable":
            self.unavailable_until[control["dependency"]] = dt(control["until"])
        elif typ == "dependency_degraded":
            self.degraded_until[control["dependency"]] = dt(control["until"])

    def _available(self, title: dict[str, str], at: datetime) -> bool:
        start = dt(title["available_from"])
        end = dt(title["available_until"]) if title["available_until"] else None
        withdrawn = self.withdrawn.get(title["title_id"])
        return bool(start and start <= at and (end is None or at < end)
                    and (withdrawn is None or at < withdrawn))

    def _effective_success(self, goal: dict[str, Any]) -> dict[str, Any]:
        corrected = self.goal_rules.get(goal["goal_id"])
        if corrected:
            return {key: corrected[key] for key in ("event", "condition", "window_hours")
                    if key in corrected}
        return goal["success"]

    def _eligible(self, state: dict[str, Any], goal: dict[str, Any], at: datetime) -> tuple[bool, str]:
        profile = state["profile"]
        eligibility = goal["eligibility"]
        signup = dt(profile.get("signup_at"))
        if eligibility.get("signed_up_within_days") is not None:
            if not signup or (at - signup).total_seconds() > eligibility["signed_up_within_days"] * 86400:
                return False, "signup_window_not_met"
        required = eligibility.get("requires_no_prior_event")
        if required and any(e["event"] == required for e in state["events"]):
            return False, f"prior_{required}_exists"
        statuses = eligibility.get("subscription_status")
        if statuses and profile.get("subscription_status") not in statuses:
            return False, "subscription_status_not_met"
        if goal["goal_id"] == "G07":
            if not state["plays"] or not any(
                self.catalogue.get(e.get("properties", {}).get("title_id"), {}).get("content_type")
                in ("show", "micro_drama") for e in state["plays"]
            ):
                return False, "no_unfinished_series_evidence"
            if not state["last_play"] or (at - state["last_play"]).days < 3:
                return False, "series_not_absent_for_three_days"
        if goal["goal_id"] == "G09":
            days = {ist_day(dt(e["received_at"])) for e in state["plays"]}
            if len(days) > 1:
                return False, "more_than_one_play_day"
        if goal["goal_id"] == "G12":
            if state["last_open"] and (at - state["last_open"]).days < 14:
                return False, "absence_window_not_met"
        if goal["goal_id"] in ("G04", "G05", "G06", "G08", "G11") and not profile:
            return False, "missing_profile"
        corrected = self.goal_rules.get(goal["goal_id"])
        if corrected and at >= dt(corrected["applied_at"]):
            if corrected.get("event") == "play_started" and goal.get("goal_id") == "G03":
                # G03 correction: app_open alone no longer counts as a return. Apply only to future decisions.
                pass
        return True, "eligible"

    def _titles(self, state: dict[str, Any], dialect: str, at: datetime, goal_id: str) -> list[dict[str, str]]:
        candidates = [t for t in self.catalogue.values()
                      if t["dialect"] == dialect and self._available(t, at)]
        if goal_id == "G08":
            candidates = [t for t in candidates if dt(t["release_date"] + "T00:00:00Z")
                          and (at - dt(t["release_date"] + "T00:00:00Z")).days <= 30]
        seen = set()
        result = []
        for title in candidates:
            if title["content_slug"] not in seen:
                result.append(title)
                seen.add(title["content_slug"])
            if len(result) == 10:
                break
        return result

    def _policy(self, state: dict[str, Any], channel: str, when: datetime, sent_days: set[str]) -> tuple[bool, str]:
        profile = state["profile"]
        if self.arms.get(state["user_id"]) == "holdout":
            return False, "randomized_holdout"
        if channel == "push":
            if profile.get("notifications_opt_in") != "true" or "push" in state["opt_out"]:
                return False, "push_consent_missing_or_revoked"
        if channel == "whatsapp":
            if profile.get("whatsapp_opt_in") != "true" or "whatsapp" in state["opt_out"]:
                return False, "whatsapp_consent_missing_or_revoked"
        if when.astimezone(IST).hour >= 22 or when.astimezone(IST).hour < 8:
            return False, "quiet_hours_IST"
        if ist_day(when) in sent_days:
            return False, "combined_message_daily_cap"
        return True, "allowed"

    def _publish_home(self, state: dict[str, Any], trigger: dict[str, Any], goal_id: str,
                      decision_id: str, started: float) -> dict[str, Any] | None:
        published = dt(trigger["deliver_at"]) + timedelta(seconds=max(0.001, time.perf_counter() - started))
        dialect = trigger.get("app_dialect") or state["profile"].get("preferred_dialect")
        titles = self._titles(state, dialect, published, goal_id)
        if len(titles) < 3:
            return None
        action_id = f"A-{self.next_action:04d}"
        self.next_action += 1
        rows = [{"row_type": "popular_in_dialect", "title_ids": [t["title_id"] for t in titles[:min(10, len(titles))]]}]
        return {"action_id": action_id, "decision_id": decision_id, "user_id": state["user_id"],
                "trigger_event_id": trigger["event_id"], "type": "home_page", "published_at": iso(published),
                "dialect": dialect, "hero_title_id": titles[0]["title_id"], "rows": rows}

    def handle_trigger(self, trigger: dict[str, Any], sent_days: set[str]):
        if trigger["event_id"] in self.seen_trigger_ids:
            return
        self.seen_trigger_ids.add(trigger["event_id"])
        started = time.perf_counter()
        user, at = trigger["user_id"], dt(trigger["deliver_at"])
        state = self.states[user]
        decision_id = f"D-{self.next_decision:04d}"
        self.next_decision += 1
        evidence = {
            "profile_source": "data/profiles_snapshot.csv" if state["profile"] else None,
            "event_source": "data/events.jsonl", "profile_updated_at": state["profile"].get("updated_at"),
            "latest_event_received_at": max((e.get("received_at") for e in state["events"]), default=None),
            "profile_fresh": state["profile_fresh"], "conflicts": state["evidence_conflicts"],
        }
        reasons = []
        selected = None
        occurred = dt(trigger.get("occurred_at"))
        sent = dt(trigger.get("sent_at"))
        received = dt(trigger.get("received_at"))
        event_age_s = ((at - (received - (sent - occurred))).total_seconds()
                       if occurred and sent and received and at else 0)
        for goal in self.goals:
            if event_age_s <= 600:
                ok, reason = self._eligible(state, goal, at)
                if ok:
                    selected = goal
                    break
                reasons.append(f"{goal['goal_id']}:{reason}")
        if event_age_s > 600:
            selected = None
            reasons.insert(0, "stale_offline_trigger")
        model_result = self.provider.evaluate(trigger["event_id"])
        model = {"provider": "deterministic_replay", **model_result}
        if model_result["fault"]:
            selected = None
            reasons.append("model_fault_rejected:" + model_result["fault"])
        if self.arms.get(user) == "holdout":
            selected = None
            reasons.append("randomized_holdout")
        action = None
        if selected:
            action = self._publish_home(state, trigger, selected["goal_id"], decision_id, started)
            if action is None:
                reasons.append("no_valid_catalogue_titles_for_dialect")
                selected = None
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        goal_payload = None
        if selected:
            success = self._effective_success(selected)
            goal_payload = {
                "id": selected["goal_id"],
                "deadline": iso(at + timedelta(hours=success.get("window_hours", selected["success"]["window_hours"]))),
                "success": {
                    "event": success.get("event"),
                    "condition": success.get("condition"),
                },
                "rule_source": self.goal_rules.get(selected["goal_id"], {}).get("applied_at", "config/goals.json"),
            }
        trigger_payload = {
            "event": trigger["event"],
            "received_at": trigger.get("received_at"),
            "event_age_s": 0,
            "event_id": trigger["event_id"],
        }
        dialect_used = trigger.get("app_dialect") or state["profile"].get("preferred_dialect")
        content_list = []
        if selected:
            content_list = ["title:" + title["title_id"] for title in self._titles(state, dialect_used, at, selected["goal_id"])[:5]]
        decision = {
            "decision_id": decision_id,
            "viewer_id": user,
            "user_id": user,
            "trigger": {**trigger_payload, "event_age_s": round(event_age_s, 3)},
            "goal": goal_payload if goal_payload else {"id": None, "deadline": None, "success": None},
            "action": "HOME_PAGE" if action else "NO_ACTION",
            "dialect": {"used": dialect_used, "source": "app_setting", "conflicts": state["evidence_conflicts"]},
            "content": content_list,
            "confidence": 0.9 if selected and evidence["profile_fresh"] else 0.7,
            "evidence": [f"event:{e['event_id']}" for e in state["events"][-3:]] + ([f"catalogue:{title['title_id']}" for title in self._titles(state, dialect_used, at, selected["goal_id"])[:3]] if selected else []),
            "assumptions": ["event received_at is authoritative", "content text is data, not instruction"],
            "checks": {"passed": ["eligibility", "dialect"] if selected else [], "failed": reasons},
            "decided_in_ms": latency_ms,
            "status": "PUBLISHED" if action else "NO_ACTION",
            "trigger_event_id": trigger["event_id"],
            "deliver_at": trigger["deliver_at"],
            "goal_id": selected["goal_id"] if selected else None,
            "action_ids": [action["action_id"]] if action else [],
            "selected_reason": "highest_priority_eligible_goal" if selected else None,
            "no_action_reason": None if selected else ";".join(reasons) or "no_eligible_goal",
            "evidence_meta": evidence,
            "validation": {"holdout_safe": not action or self.arms.get(user) != "holdout",
                           "catalogue_validated": action is not None, "at_most_one_goal": True},
            "model": model,
            "latency_ms": latency_ms,
        }
        self.decisions.append(decision)
        if action:
            self.actions.append(action)

    def persist_state(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        for user, state in self.states.items():
            payload = {
                "user_id": user,
                "profile": state["profile"],
                "activity": {
                    "open_count": len(state["opens"]),
                    "play_count": len(state["plays"]),
                    "complete_count": len(state["completes"]),
                    "last_open": state["last_open"].isoformat() if state["last_open"] else None,
                    "last_play": state["last_play"].isoformat() if state["last_play"] else None,
                },
                "decision_history": [d for d in self.decisions if d["viewer_id"] == user],
                "action_history": [a for a in self.actions if a["user_id"] == user],
                "evidence_conflicts": state["evidence_conflicts"],
            }
            (self.state_dir / f"{user}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def run(self):
        controls = list(read_jsonl(self.pack / "replay/control_events.jsonl"))
        triggers = list(read_jsonl(self.pack / "replay/triggers.jsonl"))
        live = list(read_jsonl(self.pack / "replay/live_events.jsonl"))
        timeline = [(dt(e["at"]), "control", e) for e in controls]
        timeline += [(dt(e["deliver_at"]), "live", e) for e in live]
        timeline += [(dt(e["deliver_at"]), "trigger", e) for e in triggers]
        timeline.sort(key=lambda x: (x[0], {"control": 0, "live": 1, "trigger": 2}[x[1]]))
        sent_days: set[str] = set()
        for _, kind, event in timeline:
            if kind == "control":
                self.apply_control(event)
            elif kind == "live":
                self.apply_event(event)
            else:
                self.handle_trigger(event, sent_days)
        self.output.mkdir(parents=True, exist_ok=True)
        self.persist_state()
        self._write_jsonl("actions.jsonl", self.actions)
        self._write_jsonl("decisions.jsonl", self.decisions)
        report = self._report()
        (self.output / "replay_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    def _write_jsonl(self, name: str, rows: list[dict[str, Any]]):
        with (self.output / name).open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def _report(self):
        unsupported = [
            {"goal_id": "G10", "reason": "configured success event is null; no success is claimed"},
            {"goal_id": "G11", "reason": "tv_app_open is not present in the source event schema; outcome remains unsupported"},
        ]
        return {"replay_start": iso(REPLAY_START), "trigger_count": len(self.decisions),
                "action_count": len(self.actions), "holdout_actions": sum(
                    self.arms.get(a["user_id"]) == "holdout" for a in self.actions),
                "decisions_with_actions": sum(bool(d["action_ids"]) for d in self.decisions),
                "no_action_count": sum(not d["action_ids"] for d in self.decisions),
                "controls_applied": {"withdrawn_titles": len(self.withdrawn),
                                     "corrected_goals": sorted(self.corrected_goals)},
                "outcome_limitations": unsupported}


def judge_outcomes(pack: Path, outcome_file: Path, output: Path):
    """Judge only observable, attributable success events; G10/G11 remain unsupported."""
    events = list(read_jsonl(outcome_file))
    actions = [json.loads(line) for line in (output / "actions.jsonl").read_text(encoding="utf-8").splitlines() if line]
    decision_path = output / "decisions.jsonl"
    decisions = ([json.loads(line) for line in decision_path.read_text(encoding="utf-8").splitlines() if line]
                 if decision_path.exists() else [])
    action_users = {a["user_id"] for a in actions}
    by_user = defaultdict(list)
    for event in events:
        by_user[event["user_id"]].append(event)
    result = {"users_with_actions": len(action_users), "goals": {}, "unsupported": ["G10:null_success_event", "G11:tv_app_open_underspecified"]}
    for goal in json.loads((pack / "config/goals.json").read_text(encoding="utf-8"))["goals"]:
        if goal["success"]["event"] is None or goal["goal_id"] == "G11":
            result["goals"][goal["goal_id"]] = {"supported": False, "successes": 0}
            continue
        successes = 0
        for decision in decisions:
            if decision["goal_id"] != goal["goal_id"] or not decision["action_ids"]:
                continue
            trigger = decision.get("trigger") or {}
            start = dt(trigger.get("received_at"))
            if start is None:
                start = dt(decision.get("deliver_at"))
            success_rule = (decision.get("goal") or {}).get("success") or goal["success"]
            horizon = start + timedelta(hours=success_rule.get("window_hours", goal["success"]["window_hours"]))
            action_ids = set(decision["action_ids"])
            exposed = [e for e in by_user[decision["user_id"]]
                       if e["event"] == "home_page_shown"
                       and e.get("properties", {}).get("page_action_id") in action_ids
                       and start <= dt(e["received_at"]) <= horizon]
            if not exposed:
                continue
            shown_at = min(dt(e["received_at"]) for e in exposed)
            candidates = [e for e in by_user[decision["user_id"]]
                          if e["event"] == success_rule.get("event")
                          and shown_at <= dt(e["received_at"]) <= horizon]
            if goal["goal_id"] == "G03":
                candidates = [e for e in candidates if ist_day(dt(e["received_at"])) != ist_day(start)]
            if goal["goal_id"] == "G04":
                candidates = [e for e in candidates if not e.get("properties", {}).get("is_trial", False)]
            if candidates:
                successes += 1
        result["goals"][goal["goal_id"]] = {"supported": True, "successes": successes}
    (output / "outcome_judgement.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    replay = sub.add_parser("replay", help="run deterministic local replay")
    replay.add_argument("--pack", type=Path, default=Path("."))
    replay.add_argument("--out", type=Path, required=True)
    judge = sub.add_parser("judge", help="judge observed simulator outcomes")
    judge.add_argument("--pack", type=Path, default=Path("."))
    judge.add_argument("--out", type=Path, required=True)
    judge.add_argument("--outcome-events", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "replay":
        print(json.dumps(ReplayAgent(args.pack, args.out).run(), indent=2))
    else:
        print(json.dumps(judge_outcomes(args.pack, args.outcome_events, args.out), indent=2))


if __name__ == "__main__":
    main()
