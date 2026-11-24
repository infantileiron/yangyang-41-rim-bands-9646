# 塘环单车·回正轮圈工序分档

用已签认的轮圈跳动和维修级别训练双阈值，再按测量日留出验证，解释新单分档。同一工单的各次签认全部保留，新旧按签认版本号判定（乱序补录同样适用）。

## 模拟场景

[业务剧本](yangyang-business.md)记录团队、上游资料、计算口径和交接去向。业务模块包括回正台测量、三道工序阈值拟合、待排工单解释。

观测以 CSV 导入或逐行登记，同一工单每次签认各占一行（带签认版本与收到时间）。后台按提交时固化的观测快照与发起分析时刻运行 Python 算法；训练只用留出期开始前已收到的最新签认，留出工单按发起时刻前的最新签认计分，后到补录不影响已排队分析。负责人签核后，使用方可查看结果、下载 CSV 或按计划接收报告。

## 启动

```sh
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```

浏览器打开 `http://127.0.0.1:18420`。API 健康检查位于 `http://127.0.0.1:19420/api/health`，数据库本地端口 `17920`。停机用 `docker compose down`。只有要重做演示数据时才加 `-v` 清除命名卷。

## 演示账号

| 角色 | 邮箱 | 演示密码 | 可执行动作 |
|---|---|---|---|
| 业务分析员 | `analyst@demo.local` | `Analyst-2026!` | 登记或导入样本、提交分析、下载内部结果 |
| 复核负责人 | `supervisor@demo.local` | `Supervisor-2026!` | 导入资料、签核或退回、安排或停止分发、查审计 |
| 只读查看 | `viewer@demo.local` | `Viewer-2026!` | 查看台账、已签核结果和本地收件箱 |

`.env` 以 `.env.example` 为模板。`FRONTEND_PORT`、`BACKEND_PORT`、`DB_PORT` 可做本机端口调整。Docker 私有子网可通过 `NETWORK_SUBNET` 覆盖。`JWT_SECRET` 和 `DB_PASSWORD` 为本地演示配置。`SEED_DEMO=false` 可跳过自动种子。所有公开端口绑定在回环地址。首次执行种子后，后续种子只填缺失观测，不覆盖已录入数据。

## 模块和本地开发

| 服务 | 技术 | 用途 |
|---|---|---|
| frontend | HTML、CSS、ES Modules、Nginx | 工作台、录入与报告 |
| backend | Python 3.12、FastAPI、SQLAlchemy | 校验、快照、业务 API 和 JWT |
| worker | Python 3.12、Redis | 计算排队、心跳和到时导出 |
| db | PostgreSQL 16 | 观测、快照、签核、分发计划和审计 |
| redis | Redis 7 | 唤醒、心跳和登录限流 |

在 `backend` 中可用 `pip install -r requirements.txt` 安装本地依赖，设置 `DATABASE_URL`、`REDIS_URL` 和可写 `ARTIFACT_ROOT` 后运行 `uvicorn src.main:app --port 8000`，计算进程运行 `python -m src.worker`。前端修改在 `frontend/src`，用 `docker compose up -d --build frontend` 通过 Nginx 检查同源代理。业务输入定义在 `backend/src/domain/spec.json`，算法在同目录的 `analysis.py`。角色、任务状态和签核状态在后端 `backend/src/constants/enums.py` 和前端 `frontend/src/constants/enums.js` 共同定义。数据库表在 `backend/src/models/workflow.py`。

分发计划锁定特定已签核快照，按北京时间显示。收件箱可下载真实 CSV，当前收件组是本地交接标签。停机跨过多个周期时补送最早未投递的一期，然后追到下一个将来的周期。新快照需重新签核并建新计划。

## 签认版本与点-in-time 回测

- 测量单是只追加台账：同一工单的每次签认各占一行，`version` 为签认版本、`received_at` 为签认实际收到时间。新旧一律按版本号判定，与录入先后无关；同版本冲突和工单测量信息冲突会在入库时拦截。
- 回测按工单合并签认记录，留出仍按测量日切分。训练只用留出期开始前已收到的最新签认；留出工单按发起分析时刻（`analysis_runs.anchor_at`）前的最新签认计分，发起之后才补录的更正不进入本次分析。
- 新发起的分析纳入已收到的更正；已排队或已完成分析的重跑仍使用固化的观测清单和锚点时刻，样本与签认不被替换。
- 结果明细逐单标注参与环节（拟合样本/留出计分/晚到签认·未入训练/留出暂不计分/待排建议）以及本次采用的签认版本与收到时间；数据台账的「工单签认版本」面板展示每张工单的完整签认沿革。
- 后端域测试：`python3 -I tests/test_signoff_versions.py`（仅标准库）；服务层测试：`python3 -I tests/test_observation_conflicts.py`（需 SQLAlchemy）。

许可证为 [MIT](LICENSE)。
