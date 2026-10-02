"""
封签谱系模型

谱系由三类表构成有向无环图（DAG）：
- Seal：封签节点，一个贴有封签的包装单元；
- SealOperation：不可变的谱系操作（登记/拆分/合并/重新封装/领用/冻结/解冻）；
- SealOperationNode：操作与封签之间的有向边（in=投入消耗，out=产出新生）。

约束：
- 所有数量与状态变更必须经过 apps.custody.services 在数据库事务内完成，
  数量守恒校验失败会整次回滚，不留半成品状态；
- SealOperation 与 SealOperationNode 提交后不可修改、不可删除，
  谱系事实只能追加，不能改写；
- Seal 节点不可删除，只能随操作改变状态与数量。
"""
from django.db import models

from apps.authentication.models import User
from apps.warehouse.models import Goods
from .exceptions import ImmutableLineageError


class Seal(models.Model):
    """封签节点：一个被封装、贴签的物资包装单元"""

    STATUS_CHOICES = [
        ('active', '在库'),
        ('frozen', '已冻结'),
        ('issued', '已领用'),
        ('consumed', '已转化'),
    ]

    seal_no = models.CharField('封签编号', max_length=40, unique=True)
    goods = models.ForeignKey(
        Goods, on_delete=models.PROTECT,
        related_name='seals', verbose_name='货物'
    )
    quantity = models.DecimalField('当前数量', max_digits=12, decimal_places=2)
    initial_quantity = models.DecimalField('封装数量', max_digits=12, decimal_places=2)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='active')
    location = models.CharField('存放位置', max_length=100, blank=True)
    remark = models.TextField('备注', blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_seals', verbose_name='登记人'
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'cu_seal'
        verbose_name = '封签'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.seal_no}({self.get_status_display()} {self.quantity})"

    def delete(self, *args, **kwargs):
        raise ImmutableLineageError('封签节点不可删除，谱系必须保持完整')


class SealOperation(models.Model):
    """谱系操作：不可变的操作事实，提交后禁止修改与删除"""

    TYPE_CHOICES = [
        ('register', '初始登记'),
        ('split', '拆分'),
        ('merge', '合并'),
        ('repack', '重新封装'),
        ('issue', '领用'),
        ('freeze', '冻结'),
        ('unfreeze', '解冻'),
    ]

    operation_no = models.CharField('操作单号', max_length=40, unique=True)
    operation_type = models.CharField('操作类型', max_length=20, choices=TYPE_CHOICES)
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='seal_operations', verbose_name='操作人'
    )
    receiver = models.CharField('领用人', max_length=100, blank=True)
    receiver_dept = models.CharField('领用部门', max_length=100, blank=True)
    input_quantity = models.DecimalField('投入数量', max_digits=12, decimal_places=2, default=0)
    output_quantity = models.DecimalField('产出数量', max_digits=12, decimal_places=2, default=0)
    conserved = models.BooleanField(
        '数量守恒校验通过', null=True, blank=True, default=None,
        help_text='仅拆分/合并/重新封装需要校验；为空表示该操作类型不适用'
    )
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('操作时间', auto_now_add=True)

    class Meta:
        db_table = 'cu_seal_operation'
        verbose_name = '谱系操作'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.operation_no}({self.get_operation_type_display()})"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableLineageError('谱系操作一旦提交不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableLineageError('谱系操作不可删除')


class SealOperationNode(models.Model):
    """操作-封签有向边：in 表示投入消耗父节点，out 表示产出新生节点"""

    DIRECTION_CHOICES = [
        ('in', '投入'),
        ('out', '产出'),
    ]

    operation = models.ForeignKey(
        SealOperation, on_delete=models.CASCADE,
        related_name='nodes', verbose_name='谱系操作'
    )
    seal = models.ForeignKey(
        Seal, on_delete=models.CASCADE,
        related_name='operation_nodes', verbose_name='封签'
    )
    direction = models.CharField('方向', max_length=4, choices=DIRECTION_CHOICES)
    quantity = models.DecimalField('涉及数量', max_digits=12, decimal_places=2)
    status_before = models.CharField('操作前状态', max_length=20, blank=True)
    status_after = models.CharField('操作后状态', max_length=20)
    created_at = models.DateTimeField('记录时间', auto_now_add=True)

    class Meta:
        db_table = 'cu_seal_operation_node'
        verbose_name = '谱系边'
        verbose_name_plural = verbose_name
        ordering = ['id']
        unique_together = ['operation', 'seal', 'direction']
        indexes = [
            models.Index(fields=['seal', 'direction']),
            models.Index(fields=['operation', 'direction']),
        ]

    def __str__(self):
        return f"{self.operation_id}:{self.seal_id}:{self.direction}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableLineageError('谱系边一旦提交不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableLineageError('谱系边不可删除')
