"""签认版本化与点-in-time 回测的域级测试（仅依赖标准库）。

运行：python3 -I backend/tests/test_signoff_versions.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.domain import analysis  # noqa: E402

GRADES = analysis.GRADES
PARAMS = {"holdout": 2, "under_cost": 3}
ANCHOR = "2026-11-20T12:00:00"


def row(ticket, day, wobble, grade, version=None, received_at=None):
    item = {"ticket": ticket, "day": day, "wobble": wobble, "grade": grade}
    if version is not None:
        item["version"] = version
    if received_at is not None:
        item["received_at"] = received_at
    return item


def signoff(ticket, day, wobble, version, grade, received_at):
    return row(ticket, day, wobble, grade, version, received_at)


def sample_rows():
    # 测量日 d1..d6；holdout=2 时留出起点为 d5（2026-11-09）。
    d = ["2026-11-01", "2026-11-03", "2026-11-05", "2026-11-07",
         "2026-11-09", "2026-11-11"]
    rows = []
    # 训练日：每个跳动档各 2 张，共 6 张，三种工序齐全。
    rows += [
        signoff("T-L1", d[0], 0.30, 1, "轻调", "2026-11-02T09:00:00"),
        signoff("T-L2", d[1], 0.35, 1, "轻调", "2026-11-04T09:00:00"),
        signoff("T-M1", d[0], 0.90, 1, "重调", "2026-11-02T09:00:00"),
        signoff("T-M2", d[2], 0.95, 1, "重调", "2026-11-06T09:00:00"),
        signoff("T-H1", d[1], 1.60, 1, "复验", "2026-11-04T09:00:00"),
        signoff("T-H2", d[3], 1.70, 1, "复验", "2026-11-08T09:00:00"),
    ]
    # T-REVISE：训练期工单，版本 2 在留出起点前收到 → 训练采用复验。
    rows += [
        signoff("T-REVISE", d[0], 1.55, 1, "重调", "2026-11-02T09:00:00"),
        signoff("T-REVISE", d[0], 1.55, 2, "复验", "2026-11-08T18:00:00"),
    ]
    # T-OOO：乱序补录，版本 2 先到、版本 1 后补录，仍按版本号取最新（轻调）。
    rows += [
        signoff("T-OOO", d[1], 0.40, 2, "轻调", "2026-11-03T09:00:00"),
        signoff("T-OOO", d[1], 0.40, 1, "重调", "2026-11-05T15:00:00"),
    ]
    # T-CORR：训练期工单，改判晚于留出起点 → 训练仍用版本 1（轻调），版本 2 不进本次训练。
    rows += [
        signoff("T-CORR", d[2], 0.45, 1, "轻调", "2026-11-06T09:00:00"),
        signoff("T-CORR", d[2], 0.45, 2, "复验", "2026-11-10T09:00:00"),
    ]
    # T-LATE：训练期测量，但首张签认就晚于留出起点 → 本次完全不进训练。
    rows += [
        signoff("T-LATE", d[3], 0.50, 1, "重调", "2026-11-10T08:00:00"),
    ]
    # 留出工单：T-HO 在锚点前改判 → 按版本 2 复验计分。
    rows += [
        signoff("T-HO", d[4], 1.50, 1, "重调", "2026-11-10T09:00:00"),
        signoff("T-HO", d[4], 1.50, 2, "复验", "2026-11-12T09:00:00"),
    ]
    # 留出工单：T-FUTURE 的签认在锚点之后才收到 → 本次暂不计分。
    rows += [
        signoff("T-FUTURE", d[5], 0.80, 1, "重调", "2026-11-22T09:00:00"),
    ]
    # 待排工单：无任何签认。
    rows.append(row("T-PEND", d[5], 1.20, "待排工"))
    return rows


def by_ticket(result):
    return {item["ticket"]: item for item in result["rows"]}


class SignoffVersionTests(unittest.TestCase):

    def test_merge_keeps_every_version_and_orders_by_version(self):
        tickets = {item["ticket"]: item for item in analysis.merge_tickets(sample_rows())}
        self.assertEqual([s["version"] for s in tickets["T-OOO"]["signoffs"]], [1, 2])
        self.assertEqual(tickets["T-OOO"]["signoffs"][-1]["grade"], "轻调")
        self.assertEqual(len(tickets["T-REVISE"]["signoffs"]), 2)
        self.assertTrue(tickets["T-PEND"]["pending"])

    def test_conflicting_same_version_is_rejected(self):
        rows = sample_rows() + [signoff("T-OOO", "2026-11-03", 0.40, 1, "复验", "2026-11-06T08:00:00")]
        with self.assertRaises(ValueError):
            analysis.merge_tickets(rows)

    def test_duplicate_same_version_is_idempotent(self):
        rows = sample_rows()
        duplicated = rows + [signoff("T-OOO", "2026-11-03", 0.40, 1, "重调", "2026-11-05T15:00:00")]
        first = analysis.merge_tickets(rows)
        second = analysis.merge_tickets(duplicated)
        self.assertEqual(first, second)

    def test_measurement_conflict_is_rejected(self):
        rows = sample_rows() + [signoff("T-OOO", "2026-11-03", 0.99, 3, "复验", "2026-11-07T08:00:00")]
        with self.assertRaises(ValueError):
            analysis.merge_tickets(rows)

    def test_formal_signoff_requires_version_and_received_at(self):
        bad = [{"ticket": "X", "day": "2026-11-01", "wobble": 0.5,
                "grade": "轻调", "version": 1}]
        with self.assertRaises(ValueError):
            analysis.merge_tickets(bad)
        bad = [{"ticket": "X", "day": "2026-11-01", "wobble": 0.5,
                "grade": "轻调", "received_at": "2026-11-02T09:00:00"}]
        with self.assertRaises(ValueError):
            analysis.merge_tickets(bad)

    def test_analyze_point_in_time_roles_and_versions(self):
        result = analysis.analyze(sample_rows(), PARAMS, ANCHOR)
        tickets = by_ticket(result)
        self.assertEqual(tickets["T-REVISE"]["role"], analysis.ROLE_FIT)
        self.assertEqual(tickets["T-REVISE"]["version"], 2)
        self.assertEqual(tickets["T-REVISE"]["actual"], "复验")
        self.assertEqual(tickets["T-OOO"]["role"], analysis.ROLE_FIT)
        self.assertEqual(tickets["T-OOO"]["version"], 2)
        self.assertEqual(tickets["T-CORR"]["role"], analysis.ROLE_FIT)
        self.assertEqual(tickets["T-CORR"]["version"], 1)
        self.assertEqual(tickets["T-CORR"]["actual"], "轻调")
        self.assertEqual(tickets["T-LATE"]["role"], analysis.ROLE_LATE)
        self.assertEqual(tickets["T-LATE"]["version"], 1)
        self.assertEqual(tickets["T-HO"]["role"], analysis.ROLE_HOLDOUT)
        self.assertEqual(tickets["T-HO"]["version"], 2)
        self.assertEqual(tickets["T-HO"]["actual"], "复验")
        self.assertIsInstance(tickets["T-HO"]["cost"], (int, float))
        self.assertEqual(tickets["T-FUTURE"]["role"], analysis.ROLE_UNSCORED)
        self.assertEqual(tickets["T-FUTURE"]["version"], "—")
        self.assertEqual(tickets["T-PEND"]["role"], analysis.ROLE_PENDING)
        self.assertEqual(tickets["T-PEND"]["actual"], "待排工")
        # 每条参与拟合/计分的明细都标明采用版本与收到时间。
        for item in result["rows"]:
            if item["role"] in (analysis.ROLE_FIT, analysis.ROLE_HOLDOUT):
                self.assertNotEqual(item["version"], "—")
                self.assertNotEqual(item["received_at"], "—")

    def test_holdout_uses_latest_before_anchor(self):
        # 锚点前移到 T-HO 版本 2 收到之前：留出只能按版本 1（重调）计分。
        early = analysis.analyze(sample_rows(), PARAMS, "2026-11-11T00:00:00")
        ticket = by_ticket(early)["T-HO"]
        self.assertEqual(ticket["version"], 1)
        self.assertEqual(ticket["actual"], "重调")
        late = analysis.analyze(sample_rows(), PARAMS, ANCHOR)
        self.assertEqual(by_ticket(late)["T-HO"]["version"], 2)

    def test_late_correction_after_cutoff_keeps_training_on_v1(self):
        result = analysis.analyze(sample_rows(), PARAMS, ANCHOR)
        fit = [item for item in result["rows"] if item["role"] == analysis.ROLE_FIT]
        # 首签即晚到的工单不进训练。
        self.assertEqual([item for item in fit if item["ticket"] == "T-LATE"], [])
        # 改判晚到的工单仍以版本 1 参与训练。
        corr = next(item for item in fit if item["ticket"] == "T-CORR")
        self.assertEqual(corr["version"], 1)
        # 训练集拟合样本数：6 张基础 + T-REVISE + T-OOO + T-CORR = 9。
        self.assertEqual(len(fit), 9)
        # T-LATE 单列为晚到签认。
        self.assertIn("T-LATE", [item["ticket"] for item in result["rows"]
                                 if item["role"] == analysis.ROLE_LATE])

    def test_queued_run_is_deterministic_against_later_backfill(self):
        # 已排队分析用旧锚点与固化样本：补录版本 3 不改变其结果。
        before = analysis.analyze(sample_rows(), PARAMS, ANCHOR)
        backfilled = sample_rows() + [
            signoff("T-HO", "2026-11-09", 1.50, 3, "轻调", "2026-11-21T09:00:00")]
        rerun = analysis.analyze(backfilled, PARAMS, ANCHOR)
        self.assertEqual(before["rows"], rerun["rows"])
        self.assertEqual(before["metrics"], rerun["metrics"])
        # 新分析（更晚锚点）纳入版本 3。
        renewed = analysis.analyze(backfilled, PARAMS, "2026-11-23T09:00:00")
        self.assertEqual(by_ticket(renewed)["T-HO"]["version"], 3)

    def test_seed_is_self_consistent(self):
        result = analysis.analyze(analysis.seed(), PARAMS, "2026-11-23T12:00:00")
        tickets = by_ticket(result)
        roles = {item["role"] for item in result["rows"]}
        self.assertIn(analysis.ROLE_FIT, roles)
        self.assertIn(analysis.ROLE_HOLDOUT, roles)
        self.assertEqual(tickets["TH-04-08"]["version"], 2)
        self.assertEqual(tickets["TH-04-08"]["actual"], "轻调")
        self.assertEqual(tickets["TH-05-02"]["role"], analysis.ROLE_FIT)
        self.assertEqual(tickets["TH-05-02"]["version"], 1)
        self.assertEqual(tickets["TH-05-07"]["role"], analysis.ROLE_LATE)
        self.assertEqual(tickets["TH-09-04"]["version"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
