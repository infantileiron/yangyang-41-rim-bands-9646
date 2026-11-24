import json
from datetime import date, datetime, timedelta
from math import isfinite
from pathlib import Path
from collections import Counter, defaultdict

_SPEC = json.loads(Path(__file__).with_name("spec.json").read_text(encoding="utf-8"))


def _validate_fields(values, fields, *, defaults=False):
    result = {}
    for field in fields:
        key = field["key"]
        required = field.get("required", True)
        present = key in values and values[key] is not None \
            and not (isinstance(values[key], str) and not values[key].strip())
        if not present:
            if defaults and field.get("default") is not None:
                value = field["default"]
            elif required:
                raise ValueError(f"「{field['label']}」格式或范围无效")
            else:
                continue
        else:
            value = values[key]
        kind = field["type"]
        if kind == "number" or kind == "integer":
            valid = type(value) in (int, float) and isfinite(value) and field["min"] <= value <= field["max"]
            valid = valid and (kind != "integer" or int(value) == value)
            if valid:
                value = int(value) if kind == "integer" else float(value)
        elif kind == "choice":
            valid = value in field["choices"]
        elif kind == "datetime":
            valid = isinstance(value, str)
            if valid:
                try:
                    parsed = datetime.fromisoformat(value.strip())
                except ValueError:
                    valid = False
                else:
                    value = parsed.replace(tzinfo=None).isoformat(timespec="seconds")
        else:
            valid = isinstance(value, str) and 0 < len(value.strip()) <= 300
            if valid and kind == "date":
                try:
                    valid = date.fromisoformat(value).isoformat() == value
                except ValueError:
                    valid = False
            if valid:
                value = value.strip()
        if not valid:
            raise ValueError(f"「{field['label']}」格式或范围无效")
        result[key] = value
    return result


def validate(row):
    _validate_fields(row, _SPEC["fields"])


def merge_tickets(rows):
    """把测量单行按工单合并，保留每张工单的历次签认（按版本号排序）。

    同一工单的测量日与跳动必须一致；正式签认必须带版本号与收到时间；
    同版本重复且内容一致时幂等忽略，内容冲突则报错。乱序补录不靠到达
    时间判新旧，统一以版本号为准。
    """
    tickets = {}
    order = []
    for raw in rows:
        row = _validate_fields(raw, _SPEC["fields"])
        name = row["ticket"]
        ticket = tickets.get(name)
        if ticket is None:
            ticket = {"ticket": name, "day": row["day"], "wobble": row["wobble"],
                      "signoffs": [], "pending": False}
            tickets[name] = ticket
            order.append(name)
        elif ticket["day"] != row["day"] or ticket["wobble"] != row["wobble"]:
            raise ValueError(f"工单 {name} 的测量日或跳动与既有记录不一致，请核对工单号")
        if row["grade"] == PENDING:
            ticket["pending"] = True
            continue
        if not row.get("version") or not row.get("received_at"):
            raise ValueError(f"工单 {name} 的正式签认必须填写签认版本和签认收到时间")
        for old in ticket["signoffs"]:
            if old["version"] == row["version"]:
                if old["grade"] == row["grade"] and old["received_at"] == row["received_at"]:
                    break
                raise ValueError(f"工单 {name} 的签认版本 {row['version']} 与既有记录冲突")
        else:
            ticket["signoffs"].append({"version": row["version"], "grade": row["grade"],
                                      "received_at": row["received_at"]})
    for ticket in tickets.values():
        ticket["signoffs"].sort(key=lambda item: item["version"])
    return [tickets[name] for name in order]


def _inputs(rows, parameters):
    params = _validate_fields(parameters, _SPEC["parameters"], defaults=True)
    return merge_tickets(rows), params


def _holdout_cutoff(days, holdout, minimum):
    if len(days) < holdout + minimum:
        raise ValueError(f"时间验证需要至少 {holdout + minimum} 个已完成日期")
    return days[-holdout]


def _day(start, offset):
    return (date.fromisoformat(start) + timedelta(days=offset)).isoformat()


def _shown(value, places=2):
    return "—" if value is None else round(float(value), places)


def _output(headline, summary, columns, rows, metrics, chart_name, chart_unit, chart_data):
    return {
        "headline": headline,
        "summary": summary,
        "columns": [{"key": key, "label": label} for key, label in columns],
        "rows": rows,
        "metrics": [{"label": label, "value": value, "unit": unit} for label, value, unit in metrics],
        "chart": {
            "label": chart_name, "unit": chart_unit,
            "points": [{"label": str(label), "value": value} for label, value in chart_data],
        },
    }


GRADES = ("轻调", "重调", "复验")
PENDING = "待排工"
ROLE_FIT = "拟合样本"
ROLE_HOLDOUT = "留出计分"
ROLE_LATE = "晚到签认·未入训练"
ROLE_UNSCORED = "留出暂不计分"
ROLE_PENDING = "待排建议"
COLUMNS = (("ticket", "测量工单"), ("day", "测量日"), ("wobble", "跳动（mm）"),
           ("role", "参与环节"), ("version", "采用签认版本"), ("actual", "现场签认"),
           ("received_at", "签认收到时间"), ("predicted", "分档建议"),
           ("reason", "适用区段"), ("cost", "误档代价"))


def _cost(actual, predicted, under_cost):
    return abs(predicted - actual) * (under_cost if predicted < actual else 1)


def _fit(training, penalty):
    groups = defaultdict(Counter)
    for row in training:
        groups[row["wobble"]][GRADES.index(row["grade"])] += 1
    values = sorted(groups)
    if len(values) < 3 or {row["grade"] for row in training} != set(GRADES):
        raise ValueError("训练需有三种签认工序和至少三个不同跳动值")
    prefix = [[0.0] for _ in GRADES]
    for value in values:
        for assigned, costs in enumerate(prefix):
            increment = sum(count * _cost(actual, assigned, penalty) for actual, count in groups[value].items())
            costs.append(costs[-1] + increment)
    light, heavy, recheck = prefix
    best_first = (float("inf"), 0)
    best_pair = (float("inf"), 0, 0)
    for second in range(2, len(values)):
        first = second - 1
        best_first = min(best_first, (light[first] - heavy[first], first))
        cost = best_first[0] + heavy[second] + recheck[-1] - recheck[second]
        best_pair = min(best_pair, (cost, best_first[1], second))
    _, left, right = best_pair
    return tuple((values[i - 1] + values[i]) / 2 for i in (left, right))


def _latest_before(signoffs, moment):
    """moment 之前已收到的签认中，版本号最大的一次（与录入顺序无关）。"""
    eligible = [item for item in signoffs
                if datetime.fromisoformat(item["received_at"]) < moment]
    return max(eligible, key=lambda item: item["version"], default=None)


def _decision(ticket, lower, upper):
    value = ticket["wobble"]
    assigned = int(value > lower) + int(value > upper)
    reason = (f"≤ {lower:.3f} mm", f"> {lower:.3f} mm 且 ≤ {upper:.3f} mm", f"> {upper:.3f} mm")
    return value, assigned, reason[assigned]


def _row(ticket, role, signoff, assigned, reason, *, actual, cost):
    return {"ticket": ticket["ticket"], "day": ticket["day"], "wobble": ticket["wobble"],
            "role": role,
            "version": "—" if signoff is None else signoff["version"],
            "actual": actual,
            "received_at": "—" if signoff is None else signoff["received_at"],
            "predicted": GRADES[assigned], "reason": reason, "cost": cost}


def analyze(rows, parameters, as_of=None):
    """按工单合并历次签认后做双阈值回测。

    留出仍按测量日切：训练只取留出期开始前已收到的最新签认（按版本号），
    留出工单按 as_of（发起分析时刻）前的最新签认计分；as_of 之后才收到
    的补录不会进入本次分析。
    """
    tickets, options = _inputs(rows, parameters)
    anchor = as_of or datetime.now().replace(second=0, microsecond=0)
    if isinstance(anchor, str):
        anchor = datetime.fromisoformat(anchor)
    penalty = options["under_cost"]

    known = [ticket for ticket in tickets if ticket["signoffs"]]
    cutoff_day = _holdout_cutoff(sorted({ticket["day"] for ticket in known}),
                                 options["holdout"], 3)
    cutoff = datetime.fromisoformat(cutoff_day)

    fit_pairs, holdout_pairs, late, unscored = [], [], [], []
    for ticket in known:
        if ticket["day"] >= cutoff_day:
            chosen = _latest_before(ticket["signoffs"], anchor)
            if chosen is not None:
                holdout_pairs.append((ticket, chosen))
            else:
                unscored.append(ticket)
        else:
            chosen = _latest_before(ticket["signoffs"], cutoff)
            if chosen is None:
                late.append(ticket)
            else:
                fit_pairs.append((ticket, chosen))

    lower, upper = _fit([{"wobble": ticket["wobble"], "grade": signoff["grade"]}
                         for ticket, signoff in fit_pairs], penalty)

    output_rows = []

    def add(ticket, role, signoff, *, actual, cost):
        _, assigned, reason = _decision(ticket, lower, upper)
        output_rows.append(_row(ticket, role, signoff, assigned, reason,
                                actual=actual, cost=cost))

    for ticket, signoff in sorted(fit_pairs, key=lambda pair: (pair[0]["day"], pair[0]["ticket"])):
        add(ticket, ROLE_FIT, signoff, actual=signoff["grade"], cost="—")
    for ticket, signoff in sorted(holdout_pairs, key=lambda pair: (pair[0]["day"], pair[0]["ticket"])):
        _, assigned, _ = _decision(ticket, lower, upper)
        add(ticket, ROLE_HOLDOUT, signoff, actual=signoff["grade"],
            cost=_cost(GRADES.index(signoff["grade"]), assigned, penalty))
    for ticket in sorted(late, key=lambda item: (item["day"], item["ticket"])):
        add(ticket, ROLE_LATE, ticket["signoffs"][-1], actual=ticket["signoffs"][-1]["grade"], cost="—")
    for ticket in sorted(unscored, key=lambda item: (item["day"], item["ticket"])):
        add(ticket, ROLE_UNSCORED, None, actual=PENDING, cost="—")

    pending_tickets = [ticket for ticket in tickets if not ticket["signoffs"]]
    for ticket in sorted(pending_tickets, key=lambda item: (item["day"], item["ticket"])):
        add(ticket, ROLE_PENDING, None, actual=PENDING, cost="—")

    counts = Counter(row["predicted"] for row in output_rows if row["role"] == ROLE_PENDING)
    hits = sum(row["actual"] == row["predicted"] for row in output_rows if row["role"] == ROLE_HOLDOUT)
    under = sum(GRADES.index(row["actual"]) > GRADES.index(row["predicted"])
                for row in output_rows if row["role"] == ROLE_HOLDOUT)
    scored = [row for row in output_rows if row["role"] == ROLE_HOLDOUT]
    average_cost = sum(row["cost"] for row in scored) / len(scored) if scored else None
    summary = (f"以 {anchor:%Y-%m-%d %H:%M} 为截止，{len(fit_pairs)} 张工单按留出起点（{cutoff_day}）前已收到的"
               f"最新签认拟合两个切点；{len(late)} 张工单签认晚到未入训练，{len(unscored)} 张留出工单暂无已收到签认。"
               f"最近 {options['holdout']} 个测量日中，{hits}/{len(holdout_pairs)} 单命中，低估 {under} 单。")
    return _output("轮圈回正工序分档", summary, COLUMNS, output_rows,
                   [("轻调上界", _shown(lower, 3), "mm"), ("重调上界", _shown(upper, 3), "mm"),
                    ("拟合工单数", len(fit_pairs), "张"),
                    ("留出平均误档代价", _shown(average_cost), "档·权重")],
                   "待排工单建议", "单", [(grade, counts[grade]) for grade in GRADES])


def seed():
    """演示数据：每张工单保留历次签认，覆盖改判、乱序补录与晚到签认。"""
    rows = []

    def signoff(ticket, day, wobble, version, grade, received):
        rows.append({"ticket": ticket, "day": day, "wobble": wobble, "grade": grade,
                     "version": version, "received_at": received})

    for day_index in range(9):
        day = _day("2026-11-01", day_index * 2)
        for position, (value, grade) in enumerate(zip((0.32, 0.58, 0.87, 1.3, 1.61, 2.1),
                                                      (0, 0, 1, 1, 2, 2))):
            wobble = round(value + (day_index % 4) * 0.03, 2)
            ticket = f"TH-{day_index + 1:02}-{position + 1:02}"
            signoff(ticket, day, wobble, 1, GRADES[grade], _day(day, 1) + "T09:00:00")

    # 训练期内、留出起点（2026-11-15）前完成改判：训练采用版本 2（复验）。
    signoff("TH-03-04", "2026-11-05", 1.36, 2, "复验", "2026-11-10T14:20:00")
    # 乱序补录：版本 2 先到，纸质版本 1 后补录；按版本号仍以版本 2（轻调）为最新。
    signoff("TH-04-08", "2026-11-07", 0.41, 2, "轻调", "2026-11-12T10:00:00")
    signoff("TH-04-08", "2026-11-07", 0.41, 1, "重调", "2026-11-13T16:40:00")
    # 训练期工单的改判晚于留出起点：训练仍采用版本 1（轻调），版本 2 不进本次训练。
    signoff("TH-05-02", "2026-11-09", 0.58, 2, "重调", "2026-11-16T11:05:00")
    # 训练期测量但首张签认就晚于留出起点：本次完全不进训练，单列为晚到签认。
    signoff("TH-05-07", "2026-11-09", 0.64, 1, "重调", "2026-11-16T09:30:00")
    # 留出工单在发起分析前已改判：留出计分按版本 2（复验）。
    signoff("TH-09-04", "2026-11-17", 1.3, 2, "复验", "2026-11-19T15:00:00")

    for position, value in enumerate((0.42, 0.95, 1.72), 1):
        rows.append({"ticket": f"TH-P{position:02}", "day": "2026-11-22", "wobble": value,
                     "grade": PENDING})
    return rows
