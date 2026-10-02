"""
封签谱系服务

所有改变谱系的操作都收敛在本模块，并在数据库事务内完成：
- 拆分/合并/重新封装先做数量守恒校验，失败抛出 ConservationError，
  事务整体回滚，不留下任何操作单、边或状态变更；
- 已被领用、已冻结、已转化的节点一律拒绝参与重组；
- 每次操作都会追加不可变的 SealOperation 与 SealOperationNode，
  构成可双向追溯的谱系 DAG。
"""
import uuid
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import NotFoundException
from .exceptions import ConservationError, NodeStateError
from .models import Seal, SealOperation, SealOperationNode

# 消耗型操作：父节点数量清零并退出在库状态
CONSUMING_TYPES = ('split', 'merge', 'repack')

ZERO = Decimal('0.00')


def _generate_no(prefix):
    """生成带时间戳与随机后缀的单号，唯一性由数据库约束兜底"""
    now = timezone.now()
    return f"{prefix}{now.strftime('%Y%m%d%H%M%S%f')}{uuid.uuid4().hex[:4].upper()}"


def _lock_seal(seal_id):
    """在事务内锁定并读取封签，防止并发重组同一节点"""
    try:
        return Seal.objects.select_for_update().get(pk=seal_id)
    except Seal.DoesNotExist:
        raise NotFoundException(f'封签不存在：{seal_id}')


def _ensure_active(seal, action):
    """只有在库节点才能参与重组/领用"""
    if seal.status == 'frozen':
        raise NodeStateError(f'封签 {seal.seal_no} 已被冻结，不能参与{action}')
    if seal.status == 'issued':
        raise NodeStateError(f'封签 {seal.seal_no} 已被领用，不能参与{action}')
    if seal.status == 'consumed':
        raise NodeStateError(f'封签 {seal.seal_no} 已转化为其他封签，不能参与{action}')


def _record_operation(operation_type, operator, input_quantity, output_quantity,
                      conserved=None, receiver='', receiver_dept='', remark=''):
    """追加一条不可变的谱系操作单"""
    prefixes = {
        'register': 'RG', 'split': 'SP', 'merge': 'MG', 'repack': 'RP',
        'issue': 'IS', 'freeze': 'FZ', 'unfreeze': 'UF',
    }
    return SealOperation.objects.create(
        operation_no=_generate_no(prefixes[operation_type]),
        operation_type=operation_type,
        operator=operator,
        receiver=receiver,
        receiver_dept=receiver_dept,
        input_quantity=input_quantity,
        output_quantity=output_quantity,
        conserved=conserved,
        remark=remark,
    )


def _record_edge(operation, seal, direction, quantity, status_before, status_after):
    """追加一条不可变的谱系边，并快照操作前后状态"""
    return SealOperationNode.objects.create(
        operation=operation,
        seal=seal,
        direction=direction,
        quantity=quantity,
        status_before=status_before,
        status_after=status_after,
    )


def _consume(seal, operation, new_status):
    """消耗一个节点：数量清零、状态迁移，并记录投入边"""
    _record_edge(
        operation=operation, seal=seal, direction='in',
        quantity=seal.quantity, status_before=seal.status, status_after=new_status,
    )
    seal.quantity = ZERO
    seal.status = new_status
    seal.save(update_fields=['quantity', 'status', 'updated_at'])


def _spawn(operation, goods, quantity, operator, location=''):
    """产出一个新节点，并记录产出边"""
    child = Seal.objects.create(
        seal_no=_generate_no('SEAL'),
        goods=goods,
        quantity=quantity,
        initial_quantity=quantity,
        status='active',
        location=location,
        created_by=operator,
    )
    _record_edge(
        operation=operation, seal=child, direction='out',
        quantity=quantity, status_before='', status_after='active',
    )
    return child


@transaction.atomic
def register_seal(*, goods, quantity, operator, seal_no='', location='', remark=''):
    """初始登记：建立谱系根节点"""
    if quantity <= ZERO:
        raise ConservationError('登记数量必须大于 0')
    seal = Seal.objects.create(
        seal_no=seal_no or _generate_no('SEAL'),
        goods=goods,
        quantity=quantity,
        initial_quantity=quantity,
        status='active',
        location=location,
        remark=remark,
        created_by=operator,
    )
    operation = _record_operation(
        'register', operator, ZERO, quantity, remark=remark,
    )
    _record_edge(
        operation=operation, seal=seal, direction='out',
        quantity=quantity, status_before='', status_after='active',
    )
    return seal, operation


@transaction.atomic
def split_seal(*, seal_id, quantities, operator, remark=''):
    """拆分：一个父封签拆成多个子封签，子签数量合计必须等于父签数量"""
    parent = _lock_seal(seal_id)
    _ensure_active(parent, '拆分')
    if len(quantities) < 2:
        raise ConservationError('拆分至少需要 2 个子封签；一对一换签请使用重新封装')
    for quantity in quantities:
        if quantity <= ZERO:
            raise ConservationError('子封签数量必须大于 0')
    total = sum(quantities, ZERO)
    if total != parent.quantity:
        raise ConservationError(
            f'数量守恒校验失败：父封签 {parent.seal_no} 数量 {parent.quantity}，'
            f'子封签合计 {total}'
        )
    operation = _record_operation(
        'split', operator, parent.quantity, total, conserved=True, remark=remark,
    )
    _consume(parent, operation, 'consumed')
    children = [
        _spawn(operation, parent.goods, quantity, operator, location=parent.location)
        for quantity in quantities
    ]
    return children, operation


@transaction.atomic
def merge_seals(*, seal_ids, operator, expected_quantity=None, remark=''):
    """合并：多个同货物封签合并为一个新封签，产出数量等于投入合计"""
    if len(seal_ids) < 2:
        raise ConservationError('合并至少需要 2 个封签')
    if len(set(seal_ids)) != len(seal_ids):
        raise NodeStateError('合并请求中存在重复的封签')
    # 按主键顺序加锁，避免并发事务互相等待
    seals = [_lock_seal(seal_id) for seal_id in sorted(seal_ids)]
    for seal in seals:
        _ensure_active(seal, '合并')
    goods_ids = {seal.goods_id for seal in seals}
    if len(goods_ids) > 1:
        raise NodeStateError('只有同一货物的封签才能合并')
    total = sum((seal.quantity for seal in seals), ZERO)
    if expected_quantity is not None and expected_quantity != total:
        raise ConservationError(
            f'数量守恒校验失败：合并投入合计 {total}，申报产出 {expected_quantity}'
        )
    operation = _record_operation(
        'merge', operator, total, total, conserved=True, remark=remark,
    )
    for seal in seals:
        _consume(seal, operation, 'consumed')
    primary = next(seal for seal in seals if seal.pk == seal_ids[0])
    child = _spawn(operation, primary.goods, total, operator, location=primary.location)
    return child, operation


@transaction.atomic
def repack_seal(*, seal_id, operator, expected_quantity=None, remark=''):
    """重新封装：一对一换签，数量不变"""
    parent = _lock_seal(seal_id)
    _ensure_active(parent, '重新封装')
    if expected_quantity is not None and expected_quantity != parent.quantity:
        raise ConservationError(
            f'数量守恒校验失败：封签 {parent.seal_no} 数量 {parent.quantity}，'
            f'申报产出 {expected_quantity}'
        )
    operation = _record_operation(
        'repack', operator, parent.quantity, parent.quantity,
        conserved=True, remark=remark,
    )
    quantity = parent.quantity
    _consume(parent, operation, 'consumed')
    child = _spawn(operation, parent.goods, quantity, operator,
                   location=parent.location)
    return child, operation


@transaction.atomic
def issue_seal(*, seal_id, operator, receiver, receiver_dept='', remark=''):
    """领用：整个封签节点离开保管体系，属于终态操作"""
    seal = _lock_seal(seal_id)
    _ensure_active(seal, '领用')
    operation = _record_operation(
        'issue', operator, seal.quantity, ZERO,
        receiver=receiver, receiver_dept=receiver_dept, remark=remark,
    )
    _consume(seal, operation, 'issued')
    return seal, operation


@transaction.atomic
def freeze_seal(*, seal_id, operator, remark=''):
    """冻结：在库节点暂时锁定，冻结后不解除不能参与任何重组与领用"""
    seal = _lock_seal(seal_id)
    if seal.status != 'active':
        raise NodeStateError(
            f'只有在库封签才能冻结（当前状态：{seal.get_status_display()}）'
        )
    operation = _record_operation(
        'freeze', operator, seal.quantity, seal.quantity, remark=remark,
    )
    _record_edge(
        operation=operation, seal=seal, direction='in',
        quantity=seal.quantity, status_before='active', status_after='frozen',
    )
    seal.status = 'frozen'
    seal.save(update_fields=['status', 'updated_at'])
    return seal, operation


@transaction.atomic
def unfreeze_seal(*, seal_id, operator, remark=''):
    """解冻：恢复在库状态"""
    seal = _lock_seal(seal_id)
    if seal.status != 'frozen':
        raise NodeStateError(
            f'只有已冻结的封签才能解冻（当前状态：{seal.get_status_display()}）'
        )
    operation = _record_operation(
        'unfreeze', operator, seal.quantity, seal.quantity, remark=remark,
    )
    _record_edge(
        operation=operation, seal=seal, direction='in',
        quantity=seal.quantity, status_before='frozen', status_after='active',
    )
    seal.status = 'active'
    seal.save(update_fields=['status', 'updated_at'])
    return seal, operation


# ==================== 谱系查询 ====================

def _walk_up(seal):
    """沿产出边回溯全部祖先，返回 [{'seal', 'operation', 'depth'}]"""
    results = []
    visited = {seal.pk}
    frontier = [seal]
    depth = 1
    while frontier:
        birth_edges = (
            SealOperationNode.objects
            .filter(seal__in=frontier, direction='out')
            .select_related('operation')
        )
        birth_op_by_seal = {edge.seal_id: edge.operation for edge in birth_edges}
        parent_edges = (
            SealOperationNode.objects
            .filter(operation__in=[op.pk for op in birth_op_by_seal.values()],
                    direction='in')
            .select_related('seal')
        )
        parents_by_op = {}
        for edge in parent_edges:
            parents_by_op.setdefault(edge.operation_id, []).append(edge.seal)
        next_frontier = []
        for node in frontier:
            operation = birth_op_by_seal.get(node.pk)
            if operation is None:
                continue
            for parent in parents_by_op.get(operation.pk, []):
                if parent.pk in visited:
                    continue
                visited.add(parent.pk)
                results.append({'seal': parent, 'operation': operation, 'depth': depth})
                next_frontier.append(parent)
        frontier = next_frontier
        depth += 1
    return results


def _walk_down(seal):
    """沿消耗边下探全部后代，返回 [{'seal', 'operation', 'depth'}]"""
    results = []
    visited = {seal.pk}
    frontier = [seal]
    depth = 1
    while frontier:
        consuming_edges = (
            SealOperationNode.objects
            .filter(seal__in=frontier, direction='in',
                    operation__operation_type__in=CONSUMING_TYPES)
            .select_related('operation')
        )
        child_edges = (
            SealOperationNode.objects
            .filter(operation__in=[edge.operation_id for edge in consuming_edges],
                    direction='out')
            .select_related('seal')
        )
        children_by_op = {}
        for edge in child_edges:
            children_by_op.setdefault(edge.operation_id, []).append(edge.seal)
        next_frontier = []
        for edge in consuming_edges:
            for child in children_by_op.get(edge.operation_id, []):
                if child.pk in visited:
                    continue
                visited.add(child.pk)
                results.append({'seal': child, 'operation': edge.operation, 'depth': depth})
                next_frontier.append(child)
        frontier = next_frontier
        depth += 1
    return results


def get_lineage(seal):
    """查询任意封签的祖先、后代与发生过的操作"""
    operations = (
        SealOperation.objects
        .filter(nodes__seal=seal)
        .distinct()
        .prefetch_related('nodes__seal', 'operator')
        .order_by('created_at', 'id')
    )
    return {
        'ancestors': _walk_up(seal),
        'descendants': _walk_down(seal),
        'operations': list(operations),
    }


def explain_state_at(seal, at):
    """重演截至某历史时点发生在该封签上的操作，解释当时的数量与状态"""
    edges = list(
        seal.operation_nodes
        .filter(operation__created_at__lte=at)
        .select_related('operation__operator')
        .order_by('operation__created_at', 'operation_id', 'id')
    )
    if not any(edge.direction == 'out' for edge in edges):
        birth_edge = (
            seal.operation_nodes
            .filter(direction='out')
            .select_related('operation')
            .order_by('operation__created_at')
            .first()
        )
        return {
            'existed': False,
            'birth_time': birth_edge.operation.created_at if birth_edge else None,
            'events': [],
        }
    quantity = ZERO
    status = None
    events = []
    for edge in edges:
        operation = edge.operation
        if edge.direction == 'out':
            quantity, status = edge.quantity, 'active'
        elif operation.operation_type in CONSUMING_TYPES:
            quantity, status = ZERO, 'consumed'
        elif operation.operation_type == 'issue':
            quantity, status = ZERO, 'issued'
        elif operation.operation_type == 'freeze':
            status = 'frozen'
        elif operation.operation_type == 'unfreeze':
            status = 'active'
        events.append({
            'operation_no': operation.operation_no,
            'operation_type': operation.operation_type,
            'operation_type_display': operation.get_operation_type_display(),
            'direction': edge.direction,
            'quantity': edge.quantity,
            'resulting_status': status,
            'resulting_quantity': quantity,
            'operator': operation.operator.username if operation.operator else None,
            'time': operation.created_at,
        })
    return {
        'existed': True,
        'quantity': quantity,
        'status': status,
        'events': events,
    }
