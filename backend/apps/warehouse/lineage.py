"""封签谱系服务。

本模块是封签全生命周期的唯一写入口，负责：

1. 封装 / 拆分 / 合并 / 重新封装 / 领用 / 冻结 / 解冻七类操作；
2. 每次重组在单个数据库事务内完成「行锁 → 状态校验 → 数量守恒校验 → 落库」，
   任一环节失败，整次操作回滚，不允许留下半张谱系；
3. 写入只增不改的操作台账（``SealOperation``）、谱系边（``LineageEdge``）
   和操作节点关联（``OperationNode``）；
4. 提供祖先 / 后代递归查询、操作历史查询，以及任意历史时点的数量与状态重放。
"""
import uuid
from decimal import Decimal, InvalidOperation
from typing import Iterable

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from .models import LineageEdge, OperationNode, Seal, SealOperation


ZERO = Decimal('0.00')


class LineageError(Exception):
    """谱系业务规则违例（状态非法、数量不守恒、封签不存在等）。

    抛出本异常会导致当前事务回滚，整次操作作废。
    """


# ==================== 编号与基础校验 ====================

def generate_op_no() -> str:
    return f"OP{timezone.now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:8].upper()}"


def generate_seal_no() -> str:
    return f"SL{timezone.now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:8].upper()}"


def _to_quantity(value, field='数量') -> Decimal:
    try:
        qty = Decimal(str(value)).quantize(ZERO)
    except (InvalidOperation, ValueError, TypeError):
        raise LineageError(f'{field}必须是有效数字')
    if qty <= ZERO:
        raise LineageError(f'{field}必须大于0')
    return qty


def _lock_seals(seal_ids: Iterable[int]) -> list[Seal]:
    """按主键排序加行锁（MySQL/PostgreSQL 下行锁生效），同时校验存在性。"""
    ids = list(dict.fromkeys(seal_ids))
    if not ids:
        raise LineageError('请至少选择一个封签')
    seals = list(
        Seal.objects.select_for_update().filter(pk__in=ids).order_by('id')
    )
    if len(seals) != len(ids):
        found = {s.pk for s in seals}
        missing = [str(i) for i in ids if i not in found]
        raise LineageError(f'封签不存在：{",".join(missing)}')
    # 按调用方传入的顺序返回
    by_id = {s.pk: s for s in seals}
    return [by_id[i] for i in ids]


def _assert_recombinable(seals: Iterable[Seal]):
    """已领用、已冻结、已终结的节点不得参与任何重组。"""
    illegal = [
        f'{s.seal_no}（{s.get_status_display()}）'
        for s in seals if not s.is_recombinable
    ]
    if illegal:
        raise LineageError(f'以下封签当前状态不允许参与重组：{"、".join(illegal)}')


def _assert_unique_seal_nos(outputs: list[dict]):
    nos = [o['seal_no'] for o in outputs if o.get('seal_no')]
    if len(nos) != len(set(nos)):
        raise LineageError('新封签号不能重复')


def _create_output_seals(outputs: list[dict], goods) -> list[Seal]:
    seals = []
    for o in outputs:
        try:
            seal = Seal.objects.create(
                seal_no=o.get('seal_no') or generate_seal_no(),
                goods=goods,
                batch_no=o.get('batch_no', '') or '',
                quantity=o['quantity'],
                status=Seal.STATUS_IN_STOCK,
            )
        except IntegrityError:
            raise LineageError(f'封签号已存在：{o.get("seal_no")}')
        seals.append(seal)
    return seals


def _record(op_type, *, operator, total_quantity, remark,
            receiver='', receiver_dept='') -> SealOperation:
    return SealOperation.objects.create(
        op_no=generate_op_no(),
        type=op_type,
        operator=operator if operator and operator.is_authenticated else None,
        receiver=receiver or '',
        receiver_dept=receiver_dept or '',
        total_quantity=total_quantity,
        remark=remark or '',
    )


def _add_edges(operation, triples):
    LineageEdge.objects.bulk_create([
        LineageEdge(operation=operation, source=src, target=tgt, quantity=qty)
        for src, tgt, qty in triples
    ])


def _add_nodes(operation, seals, role):
    OperationNode.objects.bulk_create([
        OperationNode(operation=operation, seal=s, role=role) for s in seals
    ])


# ==================== 写操作 ====================

def seal(*, goods, quantity, seal_no='', batch_no='', operator=None, remark='') -> tuple[Seal, SealOperation]:
    """初始封装：形成谱系的根节点。"""
    if goods is None or not getattr(goods, 'pk', None):
        raise LineageError('货物不存在，无法封装')
    qty = _to_quantity(quantity, '封装数量')
    if seal_no and Seal.objects.filter(seal_no=seal_no).exists():
        raise LineageError(f'封签号已存在：{seal_no}')

    try:
        with transaction.atomic():
            try:
                node = Seal.objects.create(
                    seal_no=seal_no or generate_seal_no(),
                    goods=goods,
                    batch_no=batch_no or '',
                    quantity=qty,
                    status=Seal.STATUS_IN_STOCK,
                )
            except IntegrityError:
                raise LineageError(f'封签号已存在：{seal_no}')
            op = _record(
                SealOperation.TYPE_SEAL, operator=operator,
                total_quantity=qty, remark=remark,
            )
            _add_edges(op, [(None, node, qty)])
            _add_nodes(op, [node], OperationNode.ROLE_OUTPUT)
            return node, op
    except LineageError:
        raise
    except Exception:
        raise LineageError('封装失败，请检查货物与封签信息')


def split(*, parent: Seal, outputs: list[dict], operator=None, remark='') -> tuple[SealOperation, list[Seal]]:
    """拆分：一个大包装 -> 多个小包装。

    守恒约束：所有子包装数量之和必须严格等于父封签的标称数量。
    """
    if len(outputs) < 2:
        raise LineageError('拆分至少需要两个子包装')
    _assert_unique_seal_nos(outputs)
    for o in outputs:
        o['quantity'] = _to_quantity(o['quantity'], '子包装数量')
        o.setdefault('batch_no', parent.batch_no)

    with transaction.atomic():
        locked = _lock_seals([parent.pk])[0]
        _assert_recombinable([locked])

        total = sum((o['quantity'] for o in outputs), ZERO)
        if total != locked.quantity:
            raise LineageError(
                f'数量不守恒：子包装合计 {total} ≠ 原封签数量 {locked.quantity}，操作已回滚'
            )

        children = _create_output_seals(outputs, locked.goods)
        op = _record(
            SealOperation.TYPE_SPLIT, operator=operator,
            total_quantity=locked.quantity, remark=remark,
        )
        _add_edges(op, [(locked, child, child.quantity) for child in children])
        _add_nodes(op, [locked], OperationNode.ROLE_INPUT)
        _add_nodes(op, children, OperationNode.ROLE_OUTPUT)

        locked.status = Seal.STATUS_TERMINATED
        locked.save(update_fields=['status'])
        return op, children


def merge(*, inputs: list[Seal], output: dict, operator=None, remark='') -> tuple[SealOperation, Seal]:
    """合并：多个封签 -> 一个新封签。

    守恒约束：新封签数量必须严格等于所有投入封签数量之和；
    且只允许合并同一货物的包装。
    """
    if not output:
        raise LineageError('请提供合并后的封签信息')
    output.setdefault('quantity', None)

    with transaction.atomic():
        locked = _lock_seals([s.pk for s in inputs])
        if len(locked) < 2:
            raise LineageError('合并至少需要两个封签')
        _assert_recombinable(locked)

        goods_ids = {s.goods_id for s in locked}
        if len(goods_ids) != 1:
            raise LineageError('只有同一货物的封签才能合并')

        total = sum((s.quantity for s in locked), ZERO)
        expected = _to_quantity(output['quantity'], '合并后数量') if output.get('quantity') else total
        if expected != total:
            raise LineageError(
                f'数量不守恒：合并后数量 {expected} ≠ 投入合计 {total}，操作已回滚'
            )
        output['quantity'] = expected
        _assert_unique_seal_nos([output])

        child = _create_output_seals([output], locked[0].goods)[0]
        op = _record(
            SealOperation.TYPE_MERGE, operator=operator,
            total_quantity=total, remark=remark,
        )
        _add_edges(op, [(src, child, src.quantity) for src in locked])
        _add_nodes(op, locked, OperationNode.ROLE_INPUT)
        _add_nodes(op, [child], OperationNode.ROLE_OUTPUT)

        Seal.objects.filter(pk__in=[s.pk for s in locked]).update(
            status=Seal.STATUS_TERMINATED
        )
        return op, child


def repack(*, inputs: list[Seal], outputs: list[dict], operator=None, remark='') -> tuple[SealOperation, list[Seal]]:
    """重新封装：N 个投入 -> M 个产出的通用重组（含 1→1 换封签）。

    守恒约束：产出数量之和必须严格等于投入数量之和，且必须是同一货物。
    源到目标的具体数量按贪心配平（同质物资倒灌模型）精确分配，
    每条边的数量均为二位小数原值，不做四舍五入，因此配平后
    每个投入的出边合计等于其标称数量，每个产出的入边合计同理。
    """
    if not inputs or not outputs:
        raise LineageError('重新封装需要投入和产出封签')
    _assert_unique_seal_nos(outputs)
    for o in outputs:
        o['quantity'] = _to_quantity(o['quantity'], '新封装数量')

    with transaction.atomic():
        locked = _lock_seals([s.pk for s in inputs])
        _assert_recombinable(locked)

        goods_ids = {s.goods_id for s in locked}
        if len(goods_ids) != 1:
            raise LineageError('只有同一货物的封签才能重新封装')

        in_total = sum((s.quantity for s in locked), ZERO)
        out_total = sum((o['quantity'] for o in outputs), ZERO)
        if in_total != out_total:
            raise LineageError(
                f'数量不守恒：产出合计 {out_total} ≠ 投入合计 {in_total}，操作已回滚'
            )

        children = _create_output_seals(outputs, locked[0].goods)

        # 贪心配平：依次把投入余量灌入当前产出
        triples = []
        in_rem = [s.quantity for s in locked]
        i = 0
        for child in children:
            need = child.quantity
            while need > ZERO:
                if i >= len(locked):
                    raise LineageError('数量配平失败，操作已回滚')
                take = min(in_rem[i], need)
                if take > ZERO:
                    triples.append((locked[i], child, take))
                in_rem[i] -= take
                need -= take
                if in_rem[i] == ZERO:
                    i += 1

        op = _record(
            SealOperation.TYPE_REPACK, operator=operator,
            total_quantity=in_total, remark=remark,
        )
        _add_edges(op, triples)
        _add_nodes(op, locked, OperationNode.ROLE_INPUT)
        _add_nodes(op, children, OperationNode.ROLE_OUTPUT)

        Seal.objects.filter(pk__in=[s.pk for s in locked]).update(
            status=Seal.STATUS_TERMINATED
        )
        return op, children


def issue(*, seals: list[Seal], receiver: str, receiver_dept='', operator=None, remark='') -> SealOperation:
    """领用出库：封签整体离开可重组体系。冻结或已领用的节点拒绝操作。"""
    receiver = (receiver or '').strip()
    if not receiver:
        raise LineageError('请填写领用人')

    with transaction.atomic():
        locked = _lock_seals([s.pk for s in seals])
        _assert_recombinable(locked)  # 冻结/已领用/已终结均不可领用

        total = sum((s.quantity for s in locked), ZERO)
        op = _record(
            SealOperation.TYPE_ISSUE, operator=operator,
            total_quantity=total, remark=remark,
            receiver=receiver, receiver_dept=receiver_dept,
        )
        _add_edges(op, [(s, None, s.quantity) for s in locked])
        _add_nodes(op, locked, OperationNode.ROLE_INPUT)

        Seal.objects.filter(pk__in=[s.pk for s in locked]).update(
            status=Seal.STATUS_ISSUED
        )
        return op


def _change_freeze_state(*, seals, expect_status, target_status, op_type, operator, remark) -> SealOperation:
    with transaction.atomic():
        locked = _lock_seals([s.pk for s in seals])
        illegal = [
            f'{s.seal_no}（{s.get_status_display()}）'
            for s in locked if s.status != expect_status
        ]
        if illegal:
            verb = '冻结' if op_type == SealOperation.TYPE_FREEZE else '解冻'
            raise LineageError(f'以下封签当前状态无法{verb}：{"、".join(illegal)}')

        total = sum((s.quantity for s in locked), ZERO)
        op = _record(op_type, operator=operator, total_quantity=total, remark=remark)
        _add_nodes(op, locked, OperationNode.ROLE_FREEZING)
        Seal.objects.filter(pk__in=[s.pk for s in locked]).update(status=target_status)
        return op


def freeze(*, seals, operator=None, remark='') -> SealOperation:
    """冻结：在库封签暂停一切重组与领用。"""
    return _change_freeze_state(
        seals=seals, expect_status=Seal.STATUS_IN_STOCK,
        target_status=Seal.STATUS_FROZEN,
        op_type=SealOperation.TYPE_FREEZE, operator=operator, remark=remark,
    )


def unfreeze(*, seals, operator=None, remark='') -> SealOperation:
    """解冻：冻结封签恢复在库。"""
    return _change_freeze_state(
        seals=seals, expect_status=Seal.STATUS_FROZEN,
        target_status=Seal.STATUS_IN_STOCK,
        op_type=SealOperation.TYPE_UNFREEZE, operator=operator, remark=remark,
    )


# ==================== 只读查询 ====================

def _seal_brief(seal: Seal, depth=None, via_quantity=None) -> dict:
    data = {
        'id': seal.pk,
        'seal_no': seal.seal_no,
        'batch_no': seal.batch_no,
        'goods': seal.goods_id,
        'quantity': str(seal.quantity),
        'status': seal.status,
        'status_display': seal.get_status_display(),
        'created_at': seal.created_at.isoformat(),
    }
    if depth is not None:
        data['depth'] = depth
    if via_quantity is not None:
        data['via_quantity'] = str(via_quantity)
    return data


def _walk(seal, outward: bool) -> list[dict]:
    """沿谱系边做广度遍历。outward=True 查后代，False 查祖先。"""
    seen = {seal.pk}
    result = []
    queue = [(seal, 0)]
    while queue:
        current, depth = queue.pop(0)
        edges = current.out_edges.all() if outward else current.in_edges.all()
        for edge in edges:
            nxt = edge.target if outward else edge.source
            if nxt is None or nxt.pk in seen:
                continue
            seen.add(nxt.pk)
            brief = _seal_brief(nxt, depth=depth + 1, via_quantity=edge.quantity)
            result.append(brief)
            queue.append((nxt, depth + 1))
    result.sort(key=lambda x: (x['depth'], x['id']))
    return result


def operation_payload(op: SealOperation) -> dict:
    edges = []
    for e in op.edges.all():
        edges.append({
            'source': e.source.seal_no if e.source_id else None,
            'source_id': e.source_id,
            'target': e.target.seal_no if e.target_id else None,
            'target_id': e.target_id,
            'quantity': str(e.quantity),
        })
    nodes = [
        {
            'seal_id': n.seal_id,
            'seal_no': n.seal.seal_no,
            'role': n.role,
            'role_display': dict(OperationNode.ROLE_CHOICES).get(n.role, n.role),
        }
        for n in op.nodes.select_related('seal')
    ]
    return {
        'id': op.pk,
        'op_no': op.op_no,
        'type': op.type,
        'type_display': op.get_type_display(),
        'operator': op.operator.username if op.operator_id else None,
        'receiver': op.receiver,
        'receiver_dept': op.receiver_dept,
        'total_quantity': str(op.total_quantity),
        'remark': op.remark,
        'created_at': op.created_at.isoformat(),
        'edges': edges,
        'nodes': nodes,
    }


def get_lineage(seal: Seal) -> dict:
    """返回封签的祖先、后代及全部相关操作（按时间正序）。

    「相关操作」覆盖封签自身以及其全部祖先、后代参与过的操作，
    保证从任意子包装都能看到最初的封装事件，谱系不断裂。
    """
    ancestors = _walk(seal, outward=False)
    descendants = _walk(seal, outward=True)
    related_ids = [seal.pk] + [a['id'] for a in ancestors] + [d['id'] for d in descendants]

    op_ids = set()
    for related_id in related_ids:
        op_ids.update(
            OperationNode.objects.filter(seal_id=related_id)
            .values_list('operation_id', flat=True)
        )
        op_ids.update(
            LineageEdge.objects.filter(source_id=related_id)
            .values_list('operation_id', flat=True)
        )
        op_ids.update(
            LineageEdge.objects.filter(target_id=related_id)
            .values_list('operation_id', flat=True)
        )

    operations = [
        operation_payload(op)
        for op in SealOperation.objects.filter(pk__in=op_ids)
        .order_by('created_at', 'id')
    ]
    return {
        'seal': _seal_brief(seal),
        'ancestors': ancestors,
        'descendants': descendants,
        'operations': operations,
    }


def explain_at(seal: Seal, at) -> dict:
    """重放截至 ``at`` 的操作流，解释该时点封签的状态、在控数量与累计流向。

    状态推导规则：封装→在库；作为投入参与拆分/合并/重封→已终结，
    作为产出→在库；领用→已领用；冻结→冻结；解冻→在库。
    """
    nodes = list(
        seal.operation_nodes
        .filter(operation__created_at__lte=at)
        .select_related('operation')
        .order_by('operation__created_at', 'operation__id', 'id')
    )
    if not nodes:
        return {
            'seal': _seal_brief(seal),
            'at': at.isoformat(),
            'existed': False,
            'status': None,
            'status_display': '未封装',
            'nominal_quantity': str(seal.quantity),
            'held_quantity': str(ZERO),
            'cumulative_flow': {
                'produced': str(ZERO),
                'transferred_in': str(ZERO),
                'transferred_out': str(ZERO),
            },
            'operations_up_to_at': 0,
            'explanation': '该时点封签尚未封装',
        }

    status = None
    for n in nodes:
        op = n.operation
        if op.type == SealOperation.TYPE_SEAL:
            status = Seal.STATUS_IN_STOCK
        elif op.type in (SealOperation.TYPE_SPLIT, SealOperation.TYPE_MERGE,
                         SealOperation.TYPE_REPACK):
            status = Seal.STATUS_TERMINATED if n.role == OperationNode.ROLE_INPUT \
                else Seal.STATUS_IN_STOCK
        elif op.type == SealOperation.TYPE_ISSUE:
            status = Seal.STATUS_ISSUED
        elif op.type == SealOperation.TYPE_FREEZE:
            status = Seal.STATUS_FROZEN
        elif op.type == SealOperation.TYPE_UNFREEZE:
            status = Seal.STATUS_IN_STOCK

    out_flow = (seal.out_edges.filter(operation__created_at__lte=at).aggregate(
        total=Sum('quantity')
    )['total'] or ZERO).quantize(ZERO)
    in_flow = (seal.in_edges.filter(
        operation__created_at__lte=at, source__isnull=False
    ).aggregate(total=Sum('quantity'))['total'] or ZERO).quantize(ZERO)
    produced = (seal.in_edges.filter(
        operation__created_at__lte=at, source__isnull=True
    ).aggregate(total=Sum('quantity'))['total'] or ZERO).quantize(ZERO)

    held = seal.quantity if status in (Seal.STATUS_IN_STOCK, Seal.STATUS_FROZEN) else ZERO
    status_display = dict(Seal.STATUS_CHOICES).get(status, status)
    return {
        'seal': _seal_brief(seal),
        'at': at.isoformat(),
        'existed': True,
        'status': status,
        'status_display': status_display,
        'nominal_quantity': str(seal.quantity),
        'held_quantity': str(held),
        'cumulative_flow': {
            'produced': str(produced),
            'transferred_in': str(in_flow),
            'transferred_out': str(out_flow),
        },
        'operations_up_to_at': len(nodes),
        'explanation': (
            f'截至 {at:%Y-%m-%d %H:%M:%S}，封签 {seal.seal_no} 状态为「{status_display}」，'
            f'标称数量 {seal.quantity}，实际在控 {held}；'
            f'累计流入 {in_flow + produced}、流出 {out_flow}。'
        ),
    }


def verify_conservation() -> dict:
    """全量审计：逐笔复核守恒与状态约束，供审计人员核验谱系完整性。"""
    problems = []
    for op in SealOperation.objects.prefetch_related('edges', 'nodes').order_by('id'):
        edges = list(op.edges.all())
        if op.type == SealOperation.TYPE_SEAL:
            if len(edges) != 1 or edges[0].source_id is not None:
                problems.append(f'{op.op_no}：封装操作应有且仅有一条无源边')
                continue
            target = edges[0].target
            if target is None or edges[0].quantity != target.quantity:
                problems.append(f'{op.op_no}：封装边数量与封签标称数量不一致')
        elif op.type in (SealOperation.TYPE_SPLIT, SealOperation.TYPE_MERGE,
                         SealOperation.TYPE_REPACK):
            out_by_source, in_by_target = {}, {}
            for e in edges:
                if e.source_id is None or e.target_id is None:
                    problems.append(f'{op.op_no}：重组操作不允许出现端点为空的边')
                out_by_source[e.source_id] = out_by_source.get(e.source_id, ZERO) + e.quantity
                in_by_target[e.target_id] = in_by_target.get(e.target_id, ZERO) + e.quantity
            for n in op.nodes.all():
                if n.role == OperationNode.ROLE_INPUT:
                    actual = out_by_source.get(n.seal_id, ZERO)
                    if actual != n.seal.quantity:
                        problems.append(
                            f'{op.op_no}：投入封签 {n.seal.seal_no} 流出 {actual} ≠ 标称 {n.seal.quantity}'
                        )
                    if n.seal.status != Seal.STATUS_TERMINATED:
                        problems.append(f'{op.op_no}：投入封签 {n.seal.seal_no} 未终结')
                elif n.role == OperationNode.ROLE_OUTPUT:
                    actual = in_by_target.get(n.seal_id, ZERO)
                    if actual != n.seal.quantity:
                        problems.append(
                            f'{op.op_no}：产出封签 {n.seal.seal_no} 流入 {actual} ≠ 标称 {n.seal.quantity}'
                        )
        elif op.type == SealOperation.TYPE_ISSUE:
            for e in edges:
                if e.target_id is not None:
                    problems.append(f'{op.op_no}：领用边不应存在目标封签')
                elif e.source.quantity != e.quantity:
                    problems.append(f'{op.op_no}：领用量与封签标称数量不一致')
    return {
        'healthy': not problems,
        'operation_count': SealOperation.objects.count(),
        'problems': problems,
    }
