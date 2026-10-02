"""
封签谱系视图
"""
import logging

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.core.response import error_response, success_response
from apps.warehouse.models import Goods
from . import services
from .models import Seal, SealOperation
from .serializers import (
    SealIssueSerializer, SealMergeSerializer, SealOperationSerializer,
    SealRegisterSerializer, SealRemarkSerializer, SealRepackSerializer,
    SealSerializer, SealSplitSerializer,
)

logger = logging.getLogger('apps')

STATUS_DISPLAY = dict(Seal.STATUS_CHOICES)


def _get_seal(pk):
    try:
        return Seal.objects.get(pk=pk)
    except Seal.DoesNotExist:
        return None


def _first_error(serializer):
    first = list(serializer.errors.values())[0]
    if isinstance(first, list):
        first = first[0]
    return str(first)


def _paginate(request, queryset):
    page = int(request.query_params.get('page', 1))
    page_size = int(request.query_params.get('page_size', 10))
    start = (page - 1) * page_size
    return queryset.count(), queryset[start:start + page_size], page, page_size


class SealListView(APIView):
    """封签列表与初始登记"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = Seal.objects.select_related(
            'goods__variety__category', 'created_by'
        ).order_by('-created_at')
        status = request.query_params.get('status')
        goods = request.query_params.get('goods')
        keyword = request.query_params.get('keyword')
        if status:
            queryset = queryset.filter(status=status)
        if goods:
            queryset = queryset.filter(goods_id=goods)
        if keyword:
            queryset = queryset.filter(seal_no__icontains=keyword)

        total, seals, page, page_size = _paginate(request, queryset)
        return success_response(data={
            'list': SealSerializer(seals, many=True).data,
            'total': total,
            'page': page,
            'page_size': page_size,
        })

    def post(self, request):
        """初始登记：建立谱系根节点"""
        serializer = SealRegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))

        data = serializer.validated_data
        seal, operation = services.register_seal(
            goods=Goods.objects.get(pk=data['goods']),
            quantity=data['quantity'],
            operator=request.user,
            seal_no=data.get('seal_no', ''),
            location=data.get('location', ''),
            remark=data.get('remark', ''),
        )
        logger.info(f"User {request.user.username} registered seal {seal.seal_no}")
        return success_response(data={
            'seal': SealSerializer(seal).data,
            'operation': SealOperationSerializer(operation).data,
        }, message='登记成功')


class SealDetailView(APIView):
    """封签详情"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        seal = _get_seal(pk)
        if seal is None:
            return error_response(message='封签不存在', code=404)
        return success_response(data=SealSerializer(seal).data)


class SealSplitView(APIView):
    """拆分：一个父封签拆成多个子封签"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SealSplitSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))

        data = serializer.validated_data
        children, operation = services.split_seal(
            seal_id=data['seal_id'],
            quantities=data['quantities'],
            operator=request.user,
            remark=data.get('remark', ''),
        )
        logger.info(
            f"User {request.user.username} split seal #{data['seal_id']} "
            f"into {len(children)} children"
        )
        return success_response(data={
            'operation': SealOperationSerializer(operation).data,
            'children': SealSerializer(children, many=True).data,
        }, message='拆分成功')


class SealMergeView(APIView):
    """合并：多个同货物封签合并为一个新封签"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SealMergeSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))

        data = serializer.validated_data
        child, operation = services.merge_seals(
            seal_ids=data['seal_ids'],
            operator=request.user,
            expected_quantity=data.get('expected_quantity'),
            remark=data.get('remark', ''),
        )
        logger.info(
            f"User {request.user.username} merged seals {data['seal_ids']} "
            f"into {child.seal_no}"
        )
        return success_response(data={
            'operation': SealOperationSerializer(operation).data,
            'seal': SealSerializer(child).data,
        }, message='合并成功')


class SealRepackView(APIView):
    """重新封装：一对一换签，数量不变"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SealRepackSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))

        data = serializer.validated_data
        child, operation = services.repack_seal(
            seal_id=data['seal_id'],
            operator=request.user,
            expected_quantity=data.get('expected_quantity'),
            remark=data.get('remark', ''),
        )
        logger.info(
            f"User {request.user.username} repacked seal #{data['seal_id']} "
            f"into {child.seal_no}"
        )
        return success_response(data={
            'operation': SealOperationSerializer(operation).data,
            'seal': SealSerializer(child).data,
        }, message='重新封装成功')


class SealIssueView(APIView):
    """领用：整个封签节点离开保管体系"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = SealIssueSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))

        data = serializer.validated_data
        seal, operation = services.issue_seal(
            seal_id=pk,
            operator=request.user,
            receiver=data['receiver'],
            receiver_dept=data.get('receiver_dept', ''),
            remark=data.get('remark', ''),
        )
        logger.info(f"User {request.user.username} issued seal {seal.seal_no}")
        return success_response(data={
            'operation': SealOperationSerializer(operation).data,
            'seal': SealSerializer(seal).data,
        }, message='领用成功')


class SealFreezeView(APIView):
    """冻结封签"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = SealRemarkSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))

        seal, operation = services.freeze_seal(
            seal_id=pk,
            operator=request.user,
            remark=serializer.validated_data.get('remark', ''),
        )
        logger.info(f"User {request.user.username} froze seal {seal.seal_no}")
        return success_response(data={
            'operation': SealOperationSerializer(operation).data,
            'seal': SealSerializer(seal).data,
        }, message='冻结成功')


class SealUnfreezeView(APIView):
    """解冻封签"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = SealRemarkSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))

        seal, operation = services.unfreeze_seal(
            seal_id=pk,
            operator=request.user,
            remark=serializer.validated_data.get('remark', ''),
        )
        logger.info(f"User {request.user.username} unfroze seal {seal.seal_no}")
        return success_response(data={
            'operation': SealOperationSerializer(operation).data,
            'seal': SealSerializer(seal).data,
        }, message='解冻成功')


class SealLineageView(APIView):
    """谱系查询：返回任意封签的祖先、后代与发生过的操作"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        seal = _get_seal(pk)
        if seal is None:
            return error_response(message='封签不存在', code=404)

        lineage = services.get_lineage(seal)
        return success_response(data={
            'seal': SealSerializer(seal).data,
            'ancestors': [self._entry(item) for item in lineage['ancestors']],
            'descendants': [self._entry(item) for item in lineage['descendants']],
            'operations': SealOperationSerializer(lineage['operations'], many=True).data,
        })

    @staticmethod
    def _entry(item):
        operation = item['operation']
        return {
            'depth': item['depth'],
            'operation_no': operation.operation_no,
            'operation_type': operation.operation_type,
            'operation_type_display': operation.get_operation_type_display(),
            'seal': SealSerializer(item['seal']).data,
        }


class SealStateAtView(APIView):
    """历史时点解释：重演操作，说明某时点上封签的数量与状态"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        seal = _get_seal(pk)
        if seal is None:
            return error_response(message='封签不存在', code=404)

        at_param = request.query_params.get('at')
        if not at_param:
            return error_response(message='请提供 at 查询参数（ISO 8601 时间）')
        at = parse_datetime(at_param)
        if at is None:
            return error_response(message='时间格式无效，请使用 ISO 8601 格式')
        if timezone.is_naive(at):
            at = timezone.make_aware(at)

        result = services.explain_state_at(seal, at)
        data = {
            'seal_no': seal.seal_no,
            'at': at,
            'existed': result['existed'],
            'events': [self._event(event) for event in result['events']],
        }
        if result['existed']:
            data['quantity'] = str(result['quantity'])
            data['status'] = result['status']
            data['status_display'] = STATUS_DISPLAY.get(result['status'], '')
        else:
            data['quantity'] = None
            data['status'] = None
            data['status_display'] = None
            data['birth_time'] = result['birth_time']
        return success_response(data=data)

    @staticmethod
    def _event(event):
        return {
            'operation_no': event['operation_no'],
            'operation_type': event['operation_type'],
            'operation_type_display': event['operation_type_display'],
            'direction': event['direction'],
            'quantity': str(event['quantity']),
            'resulting_status': event['resulting_status'],
            'resulting_status_display': STATUS_DISPLAY.get(event['resulting_status'], ''),
            'resulting_quantity': str(event['resulting_quantity']),
            'operator': event['operator'],
            'time': event['time'],
        }


class SealOperationListView(APIView):
    """谱系操作台账"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = SealOperation.objects.prefetch_related(
            'nodes__seal', 'operator'
        ).order_by('-created_at', '-id')
        operation_type = request.query_params.get('operation_type')
        seal_no = request.query_params.get('seal_no')
        if operation_type:
            queryset = queryset.filter(operation_type=operation_type)
        if seal_no:
            queryset = queryset.filter(nodes__seal__seal_no=seal_no).distinct()

        total, operations, page, page_size = _paginate(request, queryset)
        return success_response(data={
            'list': SealOperationSerializer(operations, many=True).data,
            'total': total,
            'page': page,
            'page_size': page_size,
        })


class SealOperationDetailView(APIView):
    """谱系操作详情"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            operation = SealOperation.objects.prefetch_related(
                'nodes__seal', 'operator'
            ).get(pk=pk)
        except SealOperation.DoesNotExist:
            return error_response(message='谱系操作不存在', code=404)
        return success_response(data=SealOperationSerializer(operation).data)
