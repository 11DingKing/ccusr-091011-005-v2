"""
库房管理模型
"""
from django.db import models
from django.db.models import CheckConstraint, Q
from apps.authentication.models import User


class ImmutableRecordError(Exception):
    """谱系台账只增不改，任何更新或删除尝试均抛出本异常"""


class Unit(models.Model):
    """单位模型"""
    name = models.CharField('单位名称', max_length=5, unique=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_units', verbose_name='创建人'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_unit'
        verbose_name = '单位'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    @property
    def is_linked(self):
        """是否已关联至品类"""
        return self.categories.exists()


class Category(models.Model):
    """品类模型"""
    name = models.CharField('品类名称', max_length=10, unique=True)
    unit = models.ForeignKey(
        Unit, on_delete=models.PROTECT,
        related_name='categories', verbose_name='单位'
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_categories', verbose_name='创建人'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_category'
        verbose_name = '品类'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    @property
    def is_linked(self):
        """是否已关联至品种"""
        return self.varieties.exists()


class Variety(models.Model):
    """品种模型"""
    name = models.CharField('品种名称', max_length=20)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT,
        related_name='varieties', verbose_name='所属品类'
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_varieties', verbose_name='创建人'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_variety'
        verbose_name = '品种'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        unique_together = ['category', 'name']
    
    def __str__(self):
        return f"{self.category.name} - {self.name}"
    
    @property
    def is_in_stock(self):
        """是否已入库"""
        return self.goods.exists()
    
    @property
    def unit_name(self):
        """获取单位名称"""
        return self.category.unit.name if self.category and self.category.unit else ''


class Goods(models.Model):
    """货物模型"""
    variety = models.ForeignKey(
        Variety, on_delete=models.CASCADE,
        related_name='goods', verbose_name='所属品种'
    )
    name = models.CharField('货物名称', max_length=200)
    code = models.CharField('货物编码', max_length=50, unique=True)
    specification = models.CharField('规格型号', max_length=200, blank=True)
    quantity = models.DecimalField('库存数量', max_digits=12, decimal_places=2, default=0)
    warning_threshold = models.DecimalField('预警阈值', max_digits=12, decimal_places=2, default=10)
    location = models.CharField('存放位置', max_length=100, blank=True)
    remark = models.TextField('备注', blank=True)
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_goods'
        verbose_name = '货物'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    @property
    def is_warning(self):
        """是否预警"""
        return self.quantity <= self.warning_threshold


class StockIn(models.Model):
    """入库记录模型"""
    goods = models.ForeignKey(
        Goods, on_delete=models.CASCADE,
        related_name='stock_ins', verbose_name='货物'
    )
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='stock_in_operations', verbose_name='操作人'
    )
    quantity = models.DecimalField('入库数量', max_digits=12, decimal_places=2)
    batch_no = models.CharField('批次号', max_length=50, blank=True)
    supplier = models.CharField('供应商', max_length=200, blank=True)
    stock_in_time = models.DateTimeField('入库时间', auto_now_add=True)
    remark = models.TextField('备注', blank=True)
    
    class Meta:
        db_table = 'wh_stock_in'
        verbose_name = '入库记录'
        verbose_name_plural = verbose_name
        ordering = ['-stock_in_time']
    
    def __str__(self):
        return f"{self.goods.name} - {self.quantity}"


class StockOut(models.Model):
    """出库记录模型"""
    STATUS_CHOICES = [
        ('pending', '待审批'),
        ('approved', '已通过'),
        ('rejected', '已拒绝'),
        ('completed', '已完成'),
    ]
    
    goods = models.ForeignKey(
        Goods, on_delete=models.CASCADE,
        related_name='stock_outs', verbose_name='货物'
    )
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='stock_out_operations', verbose_name='操作人'
    )
    receiver = models.CharField('领用人', max_length=100)
    receiver_dept = models.CharField('领用部门', max_length=100, blank=True)
    quantity = models.DecimalField('出库数量', max_digits=12, decimal_places=2)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='pending')
    stock_out_time = models.DateTimeField('出库时间', null=True, blank=True)
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    
    class Meta:
        db_table = 'wh_stock_out'
        verbose_name = '出库记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.goods.name} - {self.quantity}"


class Warning(models.Model):
    """预警记录模型"""
    TYPE_CHOICES = [
        ('low_stock', '库存不足'),
        ('expiring', '即将过期'),
        ('expired', '已过期'),
    ]
    
    goods = models.ForeignKey(
        Goods, on_delete=models.CASCADE,
        related_name='warnings', verbose_name='货物'
    )
    type = models.CharField('预警类型', max_length=20, choices=TYPE_CHOICES)
    message = models.TextField('预警信息')
    is_read = models.BooleanField('是否已读', default=False)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    
    class Meta:
        db_table = 'wh_warning'
        verbose_name = '预警记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.goods.name} - {self.get_type_display()}"


class Approval(models.Model):
    """审批记录模型"""
    STATUS_CHOICES = [
        ('pending', '待审批'),
        ('approved', '已通过'),
        ('rejected', '已拒绝'),
    ]
    
    stock_out = models.ForeignKey(
        StockOut, on_delete=models.CASCADE,
        related_name='approvals', verbose_name='出库记录'
    )
    approver = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='approvals', verbose_name='审批人'
    )
    status = models.CharField('审批状态', max_length=20, choices=STATUS_CHOICES, default='pending')
    remark = models.TextField('审批意见', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_approval'
        verbose_name = '审批记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.stock_out} - {self.get_status_display()}"


# ==================== 封签谱系 ====================

class Seal(models.Model):
    """封签节点

    每个封签对应一个实物包装单元，其 ``quantity`` 在封装时落定后即不可变；
    包装的流转（拆分/合并/重新封装/领用/冻结）全部通过不可变的
    :class:`SealOperation` 与 :class:`LineageEdge` 记录。
    """

    STATUS_IN_STOCK = 'in_stock'
    STATUS_FROZEN = 'frozen'
    STATUS_ISSUED = 'issued'
    STATUS_TERMINATED = 'terminated'
    STATUS_CHOICES = [
        (STATUS_IN_STOCK, '在库'),
        (STATUS_FROZEN, '冻结'),
        (STATUS_ISSUED, '已领用'),
        (STATUS_TERMINATED, '已终结'),
    ]

    seal_no = models.CharField('封签号', max_length=64, unique=True)
    goods = models.ForeignKey(
        Goods, on_delete=models.PROTECT,
        related_name='seals', verbose_name='货物'
    )
    batch_no = models.CharField('批次号', max_length=50, blank=True)
    quantity = models.DecimalField('封装数量', max_digits=12, decimal_places=2)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default=STATUS_IN_STOCK)
    created_at = models.DateTimeField('封装时间', auto_now_add=True)

    class Meta:
        db_table = 'wh_seal'
        verbose_name = '封签'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    _IMMUTABLE_FIELDS = ('seal_no', 'goods_id', 'quantity')

    def __str__(self):
        return f"{self.seal_no}({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            original = (
                type(self).objects
                .filter(pk=self.pk)
                .values(*self._IMMUTABLE_FIELDS)
                .first()
            )
            if original and any(
                getattr(self, f) != original[f] for f in self._IMMUTABLE_FIELDS
            ):
                raise ImmutableRecordError('封签号、货物、封装数量一经封装即不可变更')
        super().save(*args, **kwargs)

    @property
    def is_recombinable(self):
        """是否允许参与拆分、合并、重新封装等重组"""
        return self.status == self.STATUS_IN_STOCK


class SealOperation(models.Model):
    """封签操作台账（只增不改）

    一次重组操作（拆分/合并/重新封装）产生且只产生一条记录；
    具体的数量转移关系由 :class:`LineageEdge` 承载，
    操作与涉及节点（含冻结等无转移操作）由 :class:`OperationNode` 关联。
    """

    TYPE_SEAL = 'seal'
    TYPE_SPLIT = 'split'
    TYPE_MERGE = 'merge'
    TYPE_REPACK = 'repack'
    TYPE_ISSUE = 'issue'
    TYPE_FREEZE = 'freeze'
    TYPE_UNFREEZE = 'unfreeze'
    TYPE_CHOICES = [
        (TYPE_SEAL, '封装'),
        (TYPE_SPLIT, '拆分'),
        (TYPE_MERGE, '合并'),
        (TYPE_REPACK, '重新封装'),
        (TYPE_ISSUE, '领用'),
        (TYPE_FREEZE, '冻结'),
        (TYPE_UNFREEZE, '解冻'),
    ]

    op_no = models.CharField('操作单号', max_length=64, unique=True)
    type = models.CharField('操作类型', max_length=20, choices=TYPE_CHOICES)
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='seal_operations', verbose_name='操作人'
    )
    receiver = models.CharField('领用人', max_length=100, blank=True)
    receiver_dept = models.CharField('领用部门', max_length=100, blank=True)
    total_quantity = models.DecimalField('操作总量', max_digits=12, decimal_places=2)
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('操作时间', auto_now_add=True)

    class Meta:
        db_table = 'wh_seal_operation'
        verbose_name = '封签操作'
        verbose_name_plural = verbose_name
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f"{self.op_no} - {self.get_type_display()}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableRecordError('封签操作台账只增不改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError('封签操作台账不允许删除')


class LineageEdge(models.Model):
    """谱系边：一次操作中 ``source`` 向 ``target`` 转移的数量

    * 封装：``source`` 为空，``quantity`` 为新封签的封装数量；
    * 拆分/合并/重新封装：源、目标均非空，所有出边数量之和等于
      源封签的标称数量（守恒），且入边之和等于新封签数量；
    * 领用：``target`` 为空，代表物资离开可重组体系。

    谱系边一经写入即不可修改或删除。
    """

    operation = models.ForeignKey(
        SealOperation, on_delete=models.PROTECT,
        related_name='edges', verbose_name='所属操作'
    )
    source = models.ForeignKey(
        Seal, on_delete=models.PROTECT, null=True, blank=True,
        related_name='out_edges', verbose_name='源封签'
    )
    target = models.ForeignKey(
        Seal, on_delete=models.PROTECT, null=True, blank=True,
        related_name='in_edges', verbose_name='目标封签'
    )
    quantity = models.DecimalField('转移数量', max_digits=12, decimal_places=2)
    created_at = models.DateTimeField('记录时间', auto_now_add=True)

    class Meta:
        db_table = 'wh_lineage_edge'
        verbose_name = '谱系边'
        verbose_name_plural = verbose_name
        ordering = ['id']
        constraints = [
            CheckConstraint(
                check=~Q(source_id=models.F('target_id')) | Q(source_id__isnull=True) | Q(target_id__isnull=True),
                name='lineage_edge_no_self_loop'
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableRecordError('谱系边只增不改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError('谱系边不允许删除')


class OperationNode(models.Model):
    """操作与封签节点的关联及节点在操作中的角色"""

    ROLE_INPUT = 'input'
    ROLE_OUTPUT = 'output'
    ROLE_FREEZING = 'freezing'
    ROLE_CHOICES = [
        (ROLE_INPUT, '投入'),
        (ROLE_OUTPUT, '产出'),
        (ROLE_FREEZING, '冻结对象'),
    ]

    operation = models.ForeignKey(
        SealOperation, on_delete=models.PROTECT,
        related_name='nodes', verbose_name='所属操作'
    )
    seal = models.ForeignKey(
        Seal, on_delete=models.PROTECT,
        related_name='operation_nodes', verbose_name='封签'
    )
    role = models.CharField('节点角色', max_length=20, choices=ROLE_CHOICES)
    created_at = models.DateTimeField('记录时间', auto_now_add=True)

    class Meta:
        db_table = 'wh_operation_node'
        verbose_name = '操作节点关联'
        verbose_name_plural = verbose_name
        ordering = ['id']

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableRecordError('操作节点关联只增不改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError('操作节点关联不允许删除')
