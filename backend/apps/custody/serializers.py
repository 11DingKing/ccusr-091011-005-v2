"""
封签谱系序列化器
"""
from decimal import Decimal

from rest_framework import serializers

from apps.warehouse.models import Goods
from .models import Seal, SealOperation, SealOperationNode

QUANTITY_KWARGS = dict(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))


class SealSerializer(serializers.ModelSerializer):
    """封签序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    variety_name = serializers.CharField(source='goods.variety.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = Seal
        fields = [
            'id', 'seal_no', 'goods', 'goods_name', 'variety_name',
            'quantity', 'initial_quantity', 'status', 'status_display',
            'location', 'remark', 'created_by', 'created_by_name',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


class SealOperationNodeSerializer(serializers.ModelSerializer):
    """谱系边序列化器"""
    seal_no = serializers.CharField(source='seal.seal_no', read_only=True)
    direction_display = serializers.CharField(source='get_direction_display', read_only=True)

    class Meta:
        model = SealOperationNode
        fields = [
            'id', 'seal', 'seal_no', 'direction', 'direction_display',
            'quantity', 'status_before', 'status_after', 'created_at',
        ]
        read_only_fields = fields


class SealOperationSerializer(serializers.ModelSerializer):
    """谱系操作序列化器（含全部投入产出边）"""
    operator_name = serializers.CharField(source='operator.username', read_only=True)
    operation_type_display = serializers.CharField(
        source='get_operation_type_display', read_only=True
    )
    nodes = SealOperationNodeSerializer(many=True, read_only=True)

    class Meta:
        model = SealOperation
        fields = [
            'id', 'operation_no', 'operation_type', 'operation_type_display',
            'operator', 'operator_name', 'receiver', 'receiver_dept',
            'input_quantity', 'output_quantity', 'conserved', 'remark',
            'nodes', 'created_at',
        ]
        read_only_fields = fields


class SealRegisterSerializer(serializers.Serializer):
    """初始登记序列化器"""
    goods = serializers.IntegerField(required=True, error_messages={
        'required': '请选择货物',
    })
    quantity = serializers.DecimalField(**QUANTITY_KWARGS, error_messages={
        'required': '请输入登记数量',
    })
    seal_no = serializers.CharField(max_length=40, required=False, allow_blank=True)
    location = serializers.CharField(max_length=100, required=False, allow_blank=True)
    remark = serializers.CharField(required=False, allow_blank=True)

    def validate_goods(self, value):
        if not Goods.objects.filter(pk=value, is_active=True).exists():
            raise serializers.ValidationError('货物不存在或已停用')
        return value

    def validate_seal_no(self, value):
        if value and Seal.objects.filter(seal_no=value).exists():
            raise serializers.ValidationError('封签编号已存在')
        return value


class SealSplitSerializer(serializers.Serializer):
    """拆分序列化器"""
    seal_id = serializers.IntegerField(required=True, error_messages={
        'required': '请选择要拆分的封签',
    })
    quantities = serializers.ListField(
        child=serializers.DecimalField(**QUANTITY_KWARGS),
        min_length=2,
        error_messages={'required': '请提供子封签数量列表'},
    )
    remark = serializers.CharField(required=False, allow_blank=True)


class SealMergeSerializer(serializers.Serializer):
    """合并序列化器"""
    seal_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        min_length=2,
        error_messages={'required': '请选择要合并的封签'},
    )
    expected_quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True,
    )
    remark = serializers.CharField(required=False, allow_blank=True)


class SealRepackSerializer(serializers.Serializer):
    """重新封装序列化器"""
    seal_id = serializers.IntegerField(required=True, error_messages={
        'required': '请选择要重新封装的封签',
    })
    expected_quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True,
    )
    remark = serializers.CharField(required=False, allow_blank=True)


class SealIssueSerializer(serializers.Serializer):
    """领用序列化器"""
    receiver = serializers.CharField(max_length=100, required=True, error_messages={
        'required': '请填写领用人',
        'blank': '领用人不能为空',
    })
    receiver_dept = serializers.CharField(max_length=100, required=False, allow_blank=True)
    remark = serializers.CharField(required=False, allow_blank=True)


class SealRemarkSerializer(serializers.Serializer):
    """冻结/解冻序列化器"""
    remark = serializers.CharField(required=False, allow_blank=True)
