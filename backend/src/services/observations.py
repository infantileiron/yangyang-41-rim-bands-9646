import csv
import hashlib
import io
import json
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.config.project import SPEC
from src.domain import analysis
from src.models.workflow import AuditEvent, Observation
from src.services.errors import BusinessError
from src.services.validation import validated


MAX_OBSERVATIONS = 5000


def clean_observation(raw):
    row = validated(SPEC['fields'], raw)
    try:
        if hasattr(analysis, 'validate'):
            analysis.validate(row)
    except ValueError as exc:
        raise BusinessError(str(exc)) from exc
    return row


def _ticket_facts(rows):
    facts = defaultdict(lambda: {'day': None, 'wobble': None, 'versions': {}})
    for row in rows:
        name = row['ticket']
        fact = facts[name]
        if fact['day'] is not None and (fact['day'] != row['day'] or fact['wobble'] != row['wobble']):
            raise BusinessError(f'工单 {name} 的测量日或跳动与同批记录不一致，请核对工单号')
        fact['day'], fact['wobble'] = row['day'], row['wobble']
        if row['grade'] == analysis.PENDING:
            continue
        version = row.get('version')
        if not version or not row.get('received_at'):
            raise BusinessError(f'工单 {name} 的正式签认必须填写签认版本和签认收到时间')
        prior = fact['versions'].get(version)
        if prior and (prior['grade'] != row['grade'] or prior['received_at'] != row['received_at']):
            raise BusinessError(f'工单 {name} 的签认版本 {version} 与同批记录冲突')
        fact['versions'][version] = row
    return facts


def check_ticket_conflicts(db, rows):
    """入库前拦截工单测量信息冲突与同版本签认冲突；完全相同的记录仍按内容键幂等跳过。"""
    facts = _ticket_facts(rows)
    stored = db.scalars(select(Observation).where(Observation.payload['ticket'].in_(list(facts))))
    for item in stored:
        payload = item.payload
        name = payload['ticket']
        fact = facts[name]
        if payload.get('day') != fact['day'] or payload.get('wobble') != fact['wobble']:
            raise BusinessError(f'工单 {name} 的测量日或跳动与既有记录不一致，请核对工单号')
        if payload.get('grade') == analysis.PENDING:
            continue
        version = payload.get('version')
        if version is None:
            raise BusinessError(f'工单 {name} 存在缺少签认版本的历史记录，需先整理历史数据后再补录')
        incoming = fact['versions'].get(version)
        if incoming and (incoming['grade'] != payload.get('grade')
                         or incoming['received_at'] != payload.get('received_at')):
            raise BusinessError(f'工单 {name} 的签认版本 {version} 与既有记录冲突')


def content_key(row):
    return 'data:' + hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def parse_csv(raw):
    if len(raw) > 512 * 1024:
        raise BusinessError('CSV 文件上限 512 KB', 413)
    try:
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')), strict=True)
        header = reader.fieldnames or []
        known = {item['key'] for item in SPEC['fields']}
        required = {item['key'] for item in SPEC['fields'] if item.get('required', True)}
        if len(set(header)) != len(header) or not required.issubset(header) or not set(header).issubset(known):
            raise BusinessError('CSV 表头与导入模板不匹配')
        rows = []
        for row in reader:
            try:
                if None in row or any(value is None for value in row.values()):
                    raise BusinessError('列数与表头不匹配')
                rows.append(clean_observation(row))
            except BusinessError as exc:
                raise BusinessError(f'CSV 第 {reader.line_num} 行，{exc}') from exc
            if len(rows) > 500:
                raise BusinessError('单次导入上限 500 条')
        if not rows:
            raise BusinessError('CSV 里没有观测记录')
        return rows
    except (UnicodeDecodeError, csv.Error) as exc:
        raise BusinessError('请上传 UTF-8 编码的有效 CSV') from exc


def add_observations(db, rows, actor, action):
    check_ticket_conflicts(db, rows)
    keyed = {content_key(row): row for row in rows}
    existing = set(db.scalars(select(Observation.source_key).where(Observation.source_key.in_(keyed))))
    fresh = {key: row for key, row in keyed.items() if key not in existing}
    if db.scalar(select(func.count(Observation.id))) + len(fresh) > MAX_OBSERVATIONS:
        raise BusinessError('当前项目最多保留 5000 条观测记录', 409)
    items = [Observation(source_key=key, payload=row) for key, row in fresh.items()]
    db.add_all(items)
    if items:
        db.add(AuditEvent(actor=actor, action=action, detail=f'新增 {len(items)} 条观测'))
    try:
        db.flush()
    except IntegrityError as exc:
        raise BusinessError('有相同记录刚刚写入，请刷新后重试', 409) from exc
    return items
