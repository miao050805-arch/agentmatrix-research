# Factor Lab 真值对照数据入口运行手册

本文档对应入口一（真值对照）的可执行闭环。入口契约与裁决逻辑见
`docs/FACTOR_LAB_TWO_ENTRY_BACKEND_FLOW.md`，本文只讲怎么跑、怎么验证、怎么上云。

## 1. 运行形态与端口约定

```text
浏览器（GitHub Pages / 本地）
  └─ 数据入口页：创建任务 + 展示队列与对照结果
       │  POST /api/agents/factor-lab/intake/truth-compare   （非 demo 模式）
       │  GET  /api/agents/factor-lab/agent-tasks[/id]
       │  demo 模式只读 Supabase public_dashboard_tasks
       ▼
云端服务器（Render，gunicorn backend.factor_lab_api:app --bind 0.0.0.0:$PORT）
  └─ 冻结 artifacts/criteria.json（sha256 锁定），维护 runtime/factor_lab/agent_tasks/<task_id>/
       ▼
Agent（同一台可信机器，CLI）
  └─ python scripts/run_truth_compare.py --task-id <id> --submission-dir <dir>
       写 status.json（gates G0-G7 / standard_truth / final_decision / truth_execution）
       写 artifacts/standard_truth_comparison.json + artifacts/supabase_sync_payload.json
       ▼
Supabase（云端数据库）
  └─ python scripts/sync_truth_compare_to_supabase.py --task-id <id>
       upsert factor_truth_comparisons / public_dashboard_factors / public_dashboard_tasks
```

端口约定（本 PR 预留，云端尚未部署）：

- 本地开发预留 **8012**：`backend/factor_lab_api.py` 默认 `PORT=8012`，`config.js`
  在未配置云端地址时也回退到 `http://127.0.0.1:8012`。
- 云端（Render）由平台注入 `$PORT`，`render.yaml` 的 startCommand 已按此运行，
  代码无需改动；部署后把前端 `config.js` 的 `FACTOR_LAB_API_HOST` 指向云端地址
  （或用 `?api=https://<host>` 临时指定）。

## 2. 本地三步验证

```bash
pip install -r requirements-factor-lab.txt
python backend/factor_lab_api.py            # 监听 127.0.0.1:8012

# ① 建任务（冻结 criteria，返回 task_id）
python scripts/submit_factor_lab_intake.py truth-compare submissions/example_truth_compare_submission

# ② 执行对照（回写 status.json / artifacts）
python scripts/run_truth_compare.py --task-id <task_id> \
    --submission-dir submissions/example_truth_compare_submission \
    --library-truth-dir <库内真值目录>

# ③ 同步 Supabase（需要 FACTOR_LAB_SUPABASE_URL / FACTOR_LAB_SUPABASE_WRITE_KEY）
python scripts/sync_truth_compare_to_supabase.py --task-id <task_id>
```

无后端时也可独立运行（不触碰任务文件，结果打印到 stdout）：

```bash
python scripts/run_truth_compare.py --factor-family wq101 --factor-name alpha1 \
    --submission-dir submissions/example_truth_compare_submission \
    --library-truth-dir <库内真值目录>
```

## 3. 三态分支与确定性样本

```bash
python scripts/dev/make_truth_compare_samples.py --out-dir /tmp/truth_samples
```

| 样本 | 预期 verdict | 关键指标 |
| --- | --- | --- |
| `sample_passed`（与真值完全一致） | `passed` / `accept` | overlap 1.0、exact 1.0、max_err 0 |
| `sample_failed`（25% 点位偏移 +0.251） | `failed` / `reject` | exact 0.75 < 0.99，max_err 0.251 > 1e-8 |
| `sample_not_comparable`（alpha999 无库内真值） | `not_comparable` / `reject` | reason=`no_library_truth` |

本次改动的实测输出与页面截图见 PR 描述「测试证据」一节。

## 4. 输入契约

- 提交包：`factor_values.csv` 长表 `date,symbol,factor_value`（也兼容宽表
  `date,code,<factor_name>` 与 `factor_values.parquet`）。
- 库内真值解析顺序：提交包内 `truth_values.csv|parquet` →
  `--library-truth-dir/<factor_name>.csv|parquet` → `$FACTOR_LAB_TRUTH_DIR`。
  全部缺失 → `not_comparable` + `reject`（绝不放行）。
- 验收口径以 intake 时冻结的 `artifacts/criteria.json` 为准（executor 会校验
  sha256，篡改即拒绝执行）；独立模式使用与后端一致的 registry 镜像
  （alpha101/wq101/gtja191：tolerance 1e-8、min_overlap 0.9、pass_exact 0.99）。

## 5. 看板展示

- 数据入口页：连接横幅（demo=黄 / API=绿）、双入口卡片、拖放上传、任务队列
  （状态列含「云端」「对照 passed/failed」「不可对照」徽章）。
- 任务监控页：选中任务后阶段面板下方渲染「真值对照结果」面板
  （overlap / exact_match / max_abs_error / compared_count + run_id）。
- GitHub Pages demo 模式只读：队列实时读取 Supabase `public_dashboard_tasks`
  （由同步脚本写入）；创建任务按钮在非 API 模式下不可用属预期行为。

## 6. 未验证边界（如实声明）

- Supabase 实库写入未在本 PR 内实测：本地无 service key；`--dry-run` 与缺凭据
  退出码（exit=2）已验证，表结构见
  `supabase/migrations/202608070001_factor_truth_comparisons.sql`。
- Render 尚未部署，`render.yaml` 仅预留 `$PORT` 与 Supabase 环境变量位；
  Pages 线上 demo 的创建任务按钮在 API 部署前保持禁用是预期行为。
- `truth_values.parquet` 读取需要 `pyarrow`（不在 requirements 内），缺失时
  executor 会给出明确报错；CSV 路径无任何额外依赖。
