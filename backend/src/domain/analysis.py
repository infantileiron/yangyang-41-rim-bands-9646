import json
from datetime import date, timedelta
from math import isfinite
from pathlib import Path
from collections import Counter, defaultdict

_SPEC = json.loads(Path(__file__).with_name("spec.json").read_text(encoding="utf-8"))


def _validate_fields(values, fields, *, defaults=False):
    result = {}
    for field in fields:
        key = field["key"]
        value = values.get(key, field.get("default") if defaults else None)
        kind = field["type"]
        if kind == "number" or kind == "integer":
            valid = type(value) in (int, float) and isfinite(value) and field["min"] <= value <= field["max"]
            valid = valid and (kind != "integer" or int(value) == value)
            if valid:
                value = int(value) if kind == "integer" else float(value)
        elif kind == "choice":
            valid = value in field["choices"]
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


def _inputs(rows, parameters, identity="ticket"):
    clean = [_validate_fields(row, _SPEC["fields"]) for row in rows]
    if len({row[identity] for row in clean}) != len(clean):
        raise ValueError(f"存在重复的{next(field['label'] for field in _SPEC['fields'] if field['key'] == identity)}，请检查单据")
    params = _validate_fields(parameters, _SPEC["parameters"], defaults=True)
    return sorted(clean, key=lambda row: (row.get("day", ""), row[identity])), params


def _split_days(rows, holdout, minimum):
    days = sorted({row["day"] for row in rows})
    if len(days) < holdout + minimum:
        raise ValueError(f"时间验证需要至少 {holdout + minimum} 个已完成日期")
    cutoff = days[-holdout]
    return [row for row in rows if row["day"] < cutoff], [row for row in rows if row["day"] >= cutoff]


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
COLUMNS = (("ticket", "测量工单"), ("day", "测量日"), ("wobble", "跳动（mm）"),
           ("actual", "现场签认"), ("predicted", "分档建议"), ("reason", "适用区段"), ("cost", "误档代价"))


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


def analyze(rows, parameters):
    rows, options = _inputs(rows, parameters)
    known = [row for row in rows if row["grade"] != PENDING]
    train, holdout = _split_days(known, options["holdout"], 3)
    penalty = options["under_cost"]
    lower, upper = _fit(train, penalty)

    def result(row):
        value = row["wobble"]
        assigned = int(value > lower) + int(value > upper)
        reason = (f"≤ {lower:.3f} mm", f"> {lower:.3f} mm 且 ≤ {upper:.3f} mm", f"> {upper:.3f} mm")
        cost = "—" if row["grade"] == PENDING else _cost(GRADES.index(row["grade"]), assigned, penalty)
        return {"ticket": row["ticket"], "day": row["day"], "wobble": value, "actual": row["grade"],
                "predicted": GRADES[assigned], "cost": cost, "reason": reason[assigned]}

    pending = [result(row) for row in rows if row["grade"] == PENDING]
    checked = [result(row) for row in holdout]
    counts = Counter(row["predicted"] for row in pending)
    hits = sum(row["actual"] == row["predicted"] for row in checked)
    under = sum(GRADES.index(row["actual"]) > GRADES.index(row["predicted"]) for row in checked)
    return _output("轮圈回正工序分档", f"从 {len(train)} 张已签认测量单拟合两个切点。最近 {options['holdout']} 个签认日中，{hits}/{len(checked)} 单命中，低估 {under} 单。",
                   COLUMNS, pending + checked,
                   [("轻调上界", _shown(lower, 3), "mm"), ("重调上界", _shown(upper, 3), "mm"),
                    ("留出平均误档代价", _shown(sum(row["cost"] for row in checked) / len(checked)), "档·权重")],
                   "待排工单建议", "单", [(grade, counts[grade]) for grade in GRADES])


def seed():
    rows = []
    for day_index in range(9):
        for position, (value, grade) in enumerate(zip((0.32, 0.58, 0.87, 1.3, 1.61, 2.1), (0, 0, 1, 1, 2, 2))):
            actual = 2 if (day_index, position) in {(2, 3), (8, 3)} else grade
            rows.append({"ticket": f"TH-{day_index + 1:02}-{position + 1:02}", "day": _day("2026-11-01", day_index * 2),
                         "wobble": round(value + (day_index % 4) * 0.03, 2), "grade": GRADES[actual]})
    for position, value in enumerate((0.42, 0.95, 1.72), 1):
        rows.append({"ticket": f"TH-P{position:02}", "day": "2026-11-22", "wobble": value, "grade": PENDING})
    return rows
