"""
仓库管理序列化器
"""
from rest_framework import serializers
from .models import Unit, Category, Variety, Goods, StockIn, StockOut, Warning, Approval, Seal


class UnitSerializer(serializers.ModelSerializer):
    """单位序列化器"""
    is_linked = serializers.BooleanField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    
    class Meta:
        model = Unit
        fields = [
            'id', 'name', 'is_linked', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class UnitCreateSerializer(serializers.Serializer):
    """单位创建序列化器"""
    name = serializers.CharField(min_length=1, max_length=5, required=True, error_messages={
        'required': '请输入单位名称',
        'blank': '单位名称不能为空',
        'min_length': '单位名称至少1个字',
        'max_length': '单位名称最多5个字',
    })
    
    def validate_name(self, value):
        instance = self.context.get('instance')
        if instance:
            if Unit.objects.filter(name=value).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('单位名称已存在')
        else:
            if Unit.objects.filter(name=value).exists():
                raise serializers.ValidationError('单位名称已存在')
        return value


class CategorySerializer(serializers.ModelSerializer):
    """品类序列化器"""
    is_linked = serializers.BooleanField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    unit_name = serializers.CharField(source='unit.name', read_only=True)
    
    class Meta:
        model = Category
        fields = [
            'id', 'name', 'unit', 'unit_name', 'is_linked', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class CategoryCreateSerializer(serializers.Serializer):
    """品类创建序列化器"""
    name = serializers.CharField(min_length=1, max_length=10, required=True, error_messages={
        'required': '请输入品类名称',
        'blank': '品类名称不能为空',
        'min_length': '品类名称至少1个字',
        'max_length': '品类名称最多10个字',
    })
    unit = serializers.IntegerField(required=True, error_messages={
        'required': '请选择单位',
    })
    
    def validate_name(self, value):
        instance = self.context.get('instance')
        if instance:
            if Category.objects.filter(name=value).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('品类名称已存在')
        else:
            if Category.objects.filter(name=value).exists():
                raise serializers.ValidationError('品类名称已存在')
        return value
    
    def validate_unit(self, value):
        if not Unit.objects.filter(pk=value).exists():
            raise serializers.ValidationError('单位不存在')
        return value


class VarietySerializer(serializers.ModelSerializer):
    """品种序列化器"""
    is_in_stock = serializers.BooleanField(read_only=True)
    unit_name = serializers.CharField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    
    class Meta:
        model = Variety
        fields = [
            'id', 'name', 'category', 'category_name', 'unit_name',
            'is_in_stock', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class VarietyCreateSerializer(serializers.Serializer):
    """品种创建序列化器"""
    name = serializers.CharField(min_length=1, max_length=20, required=True, error_messages={
        'required': '请输入品种名称',
        'blank': '品种名称不能为空',
        'min_length': '品种名称至少1个字',
        'max_length': '品种名称最多20个字',
    })
    category = serializers.IntegerField(required=True, error_messages={
        'required': '请选择品类',
    })
    
    def validate_category(self, value):
        if not Category.objects.filter(pk=value).exists():
            raise serializers.ValidationError('品类不存在')
        return value
    
    def validate(self, data):
        instance = self.context.get('instance')
        name = data['name']
        category_id = data['category']
        
        if instance:
            if Variety.objects.filter(name=name, category_id=category_id).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('该品类下已存在同名品种')
        else:
            if Variety.objects.filter(name=name, category_id=category_id).exists():
                raise serializers.ValidationError('该品类下已存在同名品种')
        return data


class GoodsSerializer(serializers.ModelSerializer):
    """货物序列化器"""
    variety_name = serializers.CharField(source='variety.name', read_only=True)
    category_name = serializers.CharField(source='variety.category.name', read_only=True)
    unit_name = serializers.CharField(source='variety.category.unit.name', read_only=True)
    is_warning = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = Goods
        fields = [
            'id', 'name', 'code', 'variety', 'variety_name',
            'category_name', 'unit_name', 'specification',
            'quantity', 'warning_threshold', 'location',
            'remark', 'is_active', 'is_warning',
            'created_at', 'updated_at'
        ]


class StockInSerializer(serializers.ModelSerializer):
    """入库记录序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    operator_name = serializers.CharField(source='operator.username', read_only=True)
    
    class Meta:
        model = StockIn
        fields = [
            'id', 'goods', 'goods_name', 'operator', 'operator_name',
            'quantity', 'batch_no', 'supplier', 'stock_in_time', 'remark'
        ]


class StockOutSerializer(serializers.ModelSerializer):
    """出库记录序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    operator_name = serializers.CharField(source='operator.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = StockOut
        fields = [
            'id', 'goods', 'goods_name', 'operator', 'operator_name',
            'receiver', 'receiver_dept', 'quantity', 'status', 'status_display',
            'stock_out_time', 'remark', 'created_at'
        ]


class WarningSerializer(serializers.ModelSerializer):
    """预警记录序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    
    class Meta:
        model = Warning
        fields = [
            'id', 'goods', 'goods_name', 'type', 'type_display',
            'message', 'is_read', 'created_at'
        ]


class ApprovalSerializer(serializers.ModelSerializer):
    """审批记录序列化器"""
    approver_name = serializers.CharField(source='approver.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Approval
        fields = [
            'id', 'stock_out', 'approver', 'approver_name',
            'status', 'status_display', 'remark', 'created_at', 'updated_at'
        ]


# ==================== 封签谱系 ====================

class SealSerializer(serializers.ModelSerializer):
    """封签序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    goods_code = serializers.CharField(source='goods.code', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    is_recombinable = serializers.BooleanField(read_only=True)

    class Meta:
        model = Seal
        fields = [
            'id', 'seal_no', 'goods', 'goods_name', 'goods_code',
            'batch_no', 'quantity', 'status', 'status_display',
            'is_recombinable', 'created_at'
        ]
        read_only_fields = fields


class SealCreateSerializer(serializers.Serializer):
    """初始封装入参"""
    seal_no = serializers.CharField(
        required=False, allow_blank=True, max_length=64,
        error_messages={'max_length': '封签号最多64个字符'}
    )
    goods = serializers.IntegerField(required=True, error_messages={'required': '请选择货物'})
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=True, min_value=0,
        error_messages={'required': '请填写封装数量', 'invalid': '封装数量必须是数字', 'min_value': '封装数量必须大于0'}
    )
    batch_no = serializers.CharField(required=False, allow_blank=True, max_length=50)
    remark = serializers.CharField(required=False, allow_blank=True, max_length=500)

    def validate_goods(self, value):
        if not Goods.objects.filter(pk=value).exists():
            raise serializers.ValidationError('货物不存在')
        return value

    def validate_quantity(self, value):
        if value <= 0:
            raise serializers.ValidationError('封装数量必须大于0')
        return value

    def validate(self, data):
        seal_no = data.get('seal_no')
        if seal_no and Seal.objects.filter(seal_no=seal_no).exists():
            raise serializers.ValidationError({'seal_no': '封签号已存在'})
        return data


class SealOutputSerializer(serializers.Serializer):
    """拆分/合并/重封的产出包装描述"""
    seal_no = serializers.CharField(required=False, allow_blank=True, max_length=64)
    batch_no = serializers.CharField(required=False, allow_blank=True, max_length=50)
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=True,
        error_messages={'required': '请填写包装数量', 'invalid': '包装数量必须是数字'}
    )

    def validate_quantity(self, value):
        if value <= 0:
            raise serializers.ValidationError('包装数量必须大于0')
        return value


class SplitSerializer(serializers.Serializer):
    """拆封入参：一个父封签拆为多个子封签"""
    seal_no = serializers.CharField(required=True, max_length=64, error_messages={'required': '请填写待拆分封签号'})
    outputs = SealOutputSerializer(many=True, required=True)
    remark = serializers.CharField(required=False, allow_blank=True, max_length=500)

    def validate_outputs(self, value):
        if len(value) < 2:
            raise serializers.ValidationError('拆分至少需要两个子包装')
        nos = [o.get('seal_no') for o in value if o.get('seal_no')]
        if len(nos) != len(set(nos)):
            raise serializers.ValidationError('子封签号不能重复')
        return value


class MergeSerializer(serializers.Serializer):
    """合并入参：多个封签合并为一个新封签"""
    seal_nos = serializers.ListField(
        child=serializers.CharField(max_length=64), required=True, allow_empty=False
    )
    output = SealOutputSerializer(required=True)
    remark = serializers.CharField(required=False, allow_blank=True, max_length=500)

    def validate_seal_nos(self, value):
        if len(set(value)) < 2:
            raise serializers.ValidationError('合并至少需要两个不同的封签')
        return value


class RepackSerializer(serializers.Serializer):
    """重新封装入参：N 个投入重新封装为 M 个产出"""
    seal_nos = serializers.ListField(
        child=serializers.CharField(max_length=64), required=True, allow_empty=False
    )
    outputs = SealOutputSerializer(many=True, required=True, allow_empty=False)
    remark = serializers.CharField(required=False, allow_blank=True, max_length=500)

    def validate_outputs(self, value):
        nos = [o.get('seal_no') for o in value if o.get('seal_no')]
        if len(nos) != len(set(nos)):
            raise serializers.ValidationError('新封签号不能重复')
        return value


class IssueSealsSerializer(serializers.Serializer):
    """领用封签入参"""
    seal_nos = serializers.ListField(
        child=serializers.CharField(max_length=64), required=True, allow_empty=False
    )
    receiver = serializers.CharField(required=True, max_length=100, error_messages={'required': '请填写领用人'})
    receiver_dept = serializers.CharField(required=False, allow_blank=True, max_length=100)
    remark = serializers.CharField(required=False, allow_blank=True, max_length=500)


class FreezeSealsSerializer(serializers.Serializer):
    """冻结/解冻封签入参"""
    seal_nos = serializers.ListField(
        child=serializers.CharField(max_length=64), required=True, allow_empty=False
    )
    remark = serializers.CharField(required=False, allow_blank=True, max_length=500)
