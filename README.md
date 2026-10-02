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

## 封签谱系

封签（包装单元）以"节点 + 不可变操作单 + 有向边"构成谱系 DAG，任何子包装都可反向追溯到原始封签。

- `POST /api/seals/` 初始登记（建立谱系根节点）
- `POST /api/seals/split/` 拆分：一个父签拆成多个子签，子签数量合计必须等于父签数量
- `POST /api/seals/merge/` 合并：多个同货物封签合并为一个新签，可用 `expected_quantity` 申报产出
- `POST /api/seals/repack/` 重新封装：一对一换签，数量不变
- `POST /api/seals/{id}/issue/` 领用（终态，需登记领用人）
- `POST /api/seals/{id}/freeze/`、`POST /api/seals/{id}/unfreeze/` 冻结与解冻
- `GET /api/seals/{id}/lineage/` 查询该封签的祖先、后代与发生过的操作
- `GET /api/seals/{id}/state-at/?at=<ISO8601>` 重演操作历史，解释该时点的数量与状态
- `GET /api/seal-operations/` 谱系操作台账（可按操作类型、封签编号过滤）

不变量：

- 拆分/合并/重新封装在数据库事务内完成数量守恒校验，失败整次回滚，不留半成品状态；
- 已领用、已冻结、已转化的节点一律拒绝参与重组；
- 谱系操作单与谱系边提交后不可修改、不可删除，封签节点不可删除。

## 容器

```bash
docker build -t custody-service .
docker run --rm custody-service
```
