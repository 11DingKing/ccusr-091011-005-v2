# 监管物资保管服务

该项目为监管仓、证物室和受控物资保管点提供服务端 API，覆盖人员授权、物资分类、批次登记、收发记录、审批、预警、审计日志与统计报表。数据保存在 SQLite，所有测试和接口验收均可在单个 Linux 应用容器内离线完成。

## 运行环境

- Python 3.11
- Django REST Framework
- SQLite

## 安装与初始化

```bash
python -m pip install -r backend/requirements.txt
cd backend
python manage.py migrate --run-syncdb
```

## 测试

```bash
cd backend
pytest -q
```

## 编译检查

```bash
python -m compileall -q backend
```

## API 验收

```bash
cd backend
python manage.py migrate --run-syncdb
python manage.py shell -c "from rest_framework.test import APIClient; from apps.authentication.models import User; u=User.objects.create_user('smoke','safe-pass',role='admin'); c=APIClient(); r=c.post('/api/auth/login/',{'username':'smoke','password':'safe-pass'},format='json'); print(r.status_code, bool(r.json()['data']['token']))"
```

## 容器

```bash
docker build -t custody-service .
docker run --rm custody-service
```

## 封签谱系

针对「大包装拆成小包装后无法反证来源」的审计问题，系统为**封装、拆分、合并、重新封装、领用、冻结、解冻**建立了不可断裂的数量谱系：

- **Seal（封签节点）**：每个实物包装一个封签号，封装后数量、货物、封签号不可变更；状态机为 `在库 → 冻结 ⇄ 在库` 以及 `在库 → 已领用`，重组后投入封签转为「已终结」。
- **SealOperation（操作台账）**：每次操作一条只增不改记录，含操作人、时间、总量、领用人与备注。
- **LineageEdge（谱系边）**：记录操作中「源封签 → 目标封签」的精确数量转移。封装边 `source=NULL`（根），领用边 `target=NULL`（离开体系）；拆分/合并/重封的每条边都可逐分逐厘追溯。
- **OperationNode（节点关联）**：让冻结/解冻这类不产生数量转移的操作也进入封签历史。

### 守恒与回滚

每次重组在**单个数据库事务**内按「行锁 → 状态校验 → 数量严格相等校验 → 落库」执行：

- 子包装合计 ≠ 原封签数量、合并/重封前后总量不等，或边缘数量无法精确配平时，抛错并**整体回滚**，不留下新封签、台账或边；
- 已领用、已冻结、已终结的节点，以及跨货物的合并/重封，一律拒绝；
- 台账、谱系边、节点关联均只增不改（ORM 层拦截更新/删除），`GET /api/seals/conservation/` 可随时全量复核。

### 查询与时点解释

| 方式 | 端点 |
| --- | --- |
| 初始封装 | `POST /api/seals/` |
| 拆分 | `POST /api/seals/split/` |
| 合并 | `POST /api/seals/merge/` |
| 重新封装 | `POST /api/seals/repack/` |
| 领用 | `POST /api/seals/issue/` |
| 冻结 / 解冻 | `POST /api/seals/freeze/`、`POST /api/seals/unfreeze/` |
| 封签列表 | `GET /api/seals/` |
| 谱系（祖先/后代/操作流） | `GET /api/seals/<id>/lineage/`、`GET /api/seals/by-no/<封签号>/lineage/` |
| 历史时点解释 | `GET /api/seals/<id>/timeline/?at=2026-10-01T12:00:00+08:00`、`GET /api/seals/by-no/<封签号>/timeline/` |
| 操作台账 | `GET /api/seals/operations/` |
| 守恒审计 | `GET /api/seals/conservation/` |

时点解释通过重放截至该时刻的全部操作，给出当时的状态、标称数量、实际在控量、累计流入/流出，并生成可读的审计说明。
