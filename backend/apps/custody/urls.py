"""
封签谱系URL配置
"""
from django.urls import path

from .views import (
    SealDetailView, SealFreezeView, SealIssueView, SealLineageView,
    SealListView, SealMergeView, SealOperationDetailView, SealOperationListView,
    SealRepackView, SealSplitView, SealStateAtView, SealUnfreezeView,
)

urlpatterns = [
    # 封签登记与查询
    path('seals/', SealListView.as_view(), name='seal-list'),
    path('seals/split/', SealSplitView.as_view(), name='seal-split'),
    path('seals/merge/', SealMergeView.as_view(), name='seal-merge'),
    path('seals/repack/', SealRepackView.as_view(), name='seal-repack'),
    path('seals/<int:pk>/', SealDetailView.as_view(), name='seal-detail'),
    path('seals/<int:pk>/issue/', SealIssueView.as_view(), name='seal-issue'),
    path('seals/<int:pk>/freeze/', SealFreezeView.as_view(), name='seal-freeze'),
    path('seals/<int:pk>/unfreeze/', SealUnfreezeView.as_view(), name='seal-unfreeze'),
    path('seals/<int:pk>/lineage/', SealLineageView.as_view(), name='seal-lineage'),
    path('seals/<int:pk>/state-at/', SealStateAtView.as_view(), name='seal-state-at'),

    # 谱系操作台账
    path('seal-operations/', SealOperationListView.as_view(), name='seal-operation-list'),
    path('seal-operations/<int:pk>/', SealOperationDetailView.as_view(),
         name='seal-operation-detail'),
]
