"""入库服务的工单级冲突校验测试。

仅在已安装 SQLAlchemy 且可创建 SQLite 内存库时运行：
    python3 -I backend/tests/test_observation_conflicts.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
except ImportError:  # 本机未安装后端依赖时跳过
    create_engine = None

if create_engine:
    from src.models.base import Base
    from src.models.workflow import Observation
    from src.services.errors import BusinessError
    from src.services.observations import add_observations


def row(ticket, day, wobble, grade, version=None, received_at=None):
    item = {'ticket': ticket, 'day': day, 'wobble': wobble, 'grade': grade}
    if version is not None:
        item['version'] = version
    if received_at is not None:
        item['received_at'] = received_at
    return item


@unittest.skipUnless(create_engine, '未安装 SQLAlchemy，跳过服务层测试')
class TicketConflictTests(unittest.TestCase):

    def setUp(self):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(engine)

    def test_versioned_signoffs_are_all_kept(self):
        rows = [
            row('T1', '2026-11-01', 0.4, '轻调', 1, '2026-11-02T09:00:00'),
            row('T1', '2026-11-01', 0.4, '复验', 2, '2026-11-05T09:00:00'),
        ]
        with self.Session.begin() as db:
            items = add_observations(db, rows, 'tester', 'observation.created')
            self.assertEqual(len(items), 2)

    def test_same_version_conflict_is_rejected(self):
        with self.Session.begin() as db:
            db.add(Observation(source_key='k1', payload=row(
                'T1', '2026-11-01', 0.4, '轻调', 1, '2026-11-02T09:00:00')))
        with self.Session.begin() as db:
            with self.assertRaises(BusinessError):
                add_observations(db, [row('T1', '2026-11-01', 0.4, '复验', 1,
                                          '2026-11-03T09:00:00')], 'tester', 'x')

    def test_measurement_conflict_is_rejected(self):
        with self.Session.begin() as db:
            db.add(Observation(source_key='k1', payload=row(
                'T1', '2026-11-01', 0.4, '轻调', 1, '2026-11-02T09:00:00')))
        with self.Session.begin() as db:
            with self.assertRaises(BusinessError):
                add_observations(db, [row('T1', '2026-11-01', 0.9, '复验', 2,
                                          '2026-11-03T09:00:00')], 'tester', 'x')

    def test_identical_row_is_idempotent(self):
        payload = row('T1', '2026-11-01', 0.4, '轻调', 1, '2026-11-02T09:00:00')
        with self.Session.begin() as db:
            add_observations(db, [payload], 'tester', 'x')
        with self.Session.begin() as db:
            items = add_observations(db, [dict(payload)], 'tester', 'x')
            self.assertEqual(items, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
