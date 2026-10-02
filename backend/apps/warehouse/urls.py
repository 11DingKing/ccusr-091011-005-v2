"""
仓库管理URL配置
"""
from django.urls import path
from .views import (
    UnitListView, UnitDetailView, UnitBatchDeleteView, UnitAllView,
    CategoryListView, CategoryDetailView, CategoryBatchDeleteView, CategoryAllView,
    VarietyListView, VarietyDetailView, VarietyBatchDeleteView,
    VarietyTemplateView, VarietyImportView,
    DashboardView, GoodsListView, StockInListView, StockOutListView,
    WarningListView, ApprovalListView,
    SealListView, SealDetailView, SealSplitView, SealMergeView, SealRepackView,
    SealIssueView, SealFreezeView, SealUnfreezeView, SealLineageView,
    SealLineageByNoView, SealTimelineByNoView,
    SealTimelineView, SealOperationListView, SealConservationView,
)

urlpatterns = [
    # 仪表盘
    path('dashboard/', DashboardView.as_view(), name='dashboard'),

    # 单位管理
    path('units/', UnitListView.as_view(), name='unit-list'),
    path('units/all/', UnitAllView.as_view(), name='unit-all'),
    path('units/batch-delete/', UnitBatchDeleteView.as_view(), name='unit-batch-delete'),
    path('units/<int:pk>/', UnitDetailView.as_view(), name='unit-detail'),

    # 品类管理
    path('categories/', CategoryListView.as_view(), name='category-list'),
    path('categories/all/', CategoryAllView.as_view(), name='category-all'),
    path('categories/batch-delete/', CategoryBatchDeleteView.as_view(), name='category-batch-delete'),
    path('categories/<int:pk>/', CategoryDetailView.as_view(), name='category-detail'),

    # 品种管理
    path('varieties/', VarietyListView.as_view(), name='variety-list'),
    path('varieties/batch-delete/', VarietyBatchDeleteView.as_view(), name='variety-batch-delete'),
    path('varieties/template/', VarietyTemplateView.as_view(), name='variety-template'),
    path('varieties/import/', VarietyImportView.as_view(), name='variety-import'),
    path('varieties/<int:pk>/', VarietyDetailView.as_view(), name='variety-detail'),

    # 货物管理
    path('goods/', GoodsListView.as_view(), name='goods-list'),

    # 入库管理
    path('stock-in/', StockInListView.as_view(), name='stock-in-list'),

    # 出库管理
    path('stock-out/', StockOutListView.as_view(), name='stock-out-list'),

    # 预警管理
    path('warnings/', WarningListView.as_view(), name='warning-list'),

    # 审批管理
    path('approvals/', ApprovalListView.as_view(), name='approval-list'),

    # 封签谱系
    path('seals/', SealListView.as_view(), name='seal-list'),
    path('seals/operations/', SealOperationListView.as_view(), name='seal-operation-list'),
    path('seals/conservation/', SealConservationView.as_view(), name='seal-conservation'),
    path('seals/<int:pk>/', SealDetailView.as_view(), name='seal-detail'),
    path('seals/<int:pk>/lineage/', SealLineageView.as_view(), name='seal-lineage'),
    path('seals/<int:pk>/timeline/', SealTimelineView.as_view(), name='seal-timeline'),
    path('seals/by-no/<str:seal_no>/lineage/', SealLineageByNoView.as_view(), name='seal-lineage-by-no'),
    path('seals/by-no/<str:seal_no>/timeline/', SealTimelineByNoView.as_view(), name='seal-timeline-by-no'),
    path('seals/split/', SealSplitView.as_view(), name='seal-split'),
    path('seals/merge/', SealMergeView.as_view(), name='seal-merge'),
    path('seals/repack/', SealRepackView.as_view(), name='seal-repack'),
    path('seals/issue/', SealIssueView.as_view(), name='seal-issue'),
    path('seals/freeze/', SealFreezeView.as_view(), name='seal-freeze'),
    path('seals/unfreeze/', SealUnfreezeView.as_view(), name='seal-unfreeze'),
]
