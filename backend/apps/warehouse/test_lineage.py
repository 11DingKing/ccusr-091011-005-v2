"""封签谱系服务与接口测试。

覆盖审计要求：
* 拆分/合并/重新封装的祖先—后代谱系不可断裂；
* 数量守恒校验失败时整次操作回滚，无任何残留；
* 已领用、冻结、终结节点不得参与重组；
* 任意封签可查祖先、后代与全部操作；
* 可解释历史时点上的数量与状态；
* 台账与谱系边只增不改。
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.backends import generate_token
from apps.authentication.models import User
from apps.warehouse import lineage
from apps.warehouse.lineage import LineageError
from apps.warehouse.models import (
    Approval, Category, Goods, ImmutableRecordError, LineageEdge,
    OperationNode, Seal, SealOperation, Unit, Variety,
)
from django.test import TestCase


class LineageFixture(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("lineage-user", "testpass123", role="admin")
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {generate_token(self.user)}")
        self.unit = Unit.objects.create(name="件", created_by=self.user)
        self.category = Category.objects.create(name="受控器材", unit=self.unit, created_by=self.user)
        self.variety = Variety.objects.create(name="记录终端", category=self.category, created_by=self.user)
        self.goods = Goods.objects.create(
            variety=self.variety, name="执法记录终端", code="DEV-001",
            quantity=Decimal("100"),
        )
        self.other_goods = Goods.objects.create(
            variety=self.variety, name="其他物资", code="DEV-002",
            quantity=Decimal("10"),
        )

    def seal_root(self, qty="100", seal_no="ROOT-1", batch_no="B20261001"):
        node, _ = lineage.seal(
            goods=self.goods, quantity=Decimal(qty),
            seal_no=seal_no, batch_no=batch_no, operator=self.user,
        )
        return node


# ==================== 服务层：谱系与守恒 ====================

class LineageServiceTest(LineageFixture):
    def test_seal_creates_root_and_lineage_edge(self):
        root = self.seal_root()
        self.assertEqual(root.status, Seal.STATUS_IN_STOCK)
        edge = LineageEdge.objects.get(source__isnull=True, target=root)
        self.assertEqual(edge.quantity, Decimal("100.00"))
        op = SealOperation.objects.get(type=SealOperation.TYPE_SEAL)
        self.assertEqual(op.total_quantity, Decimal("100.00"))

    def test_split_preserves_full_lineage(self):
        root = self.seal_root()
        op, children = lineage.split(
            parent=root,
            outputs=[
                {'seal_no': 'S-1', 'quantity': Decimal('30')},
                {'seal_no': 'S-2', 'quantity': Decimal('70')},
            ],
            operator=self.user,
        )
        self.assertEqual(len(children), 2)
        root.refresh_from_db()
        self.assertEqual(root.status, Seal.STATUS_TERMINATED)
        # 父→子边精确记录数量来源，批次号默认继承
        edges = LineageEdge.objects.filter(operation=op).order_by('id')
        self.assertEqual([(e.source_id, e.target.seal_no, e.quantity) for e in edges],
                         [(root.id, 'S-1', Decimal('30.00')),
                          (root.id, 'S-2', Decimal('70.00'))])
        self.assertEqual(children[0].batch_no, 'B20261001')

        # 再拆 S-1，孙代仍能回溯到原始封签
        _, grandchildren = lineage.split(
            parent=children[0],
            outputs=[
                {'seal_no': 'S-1A', 'quantity': Decimal('10')},
                {'seal_no': 'S-1B', 'quantity': Decimal('20')},
            ],
            operator=self.user,
        )
        info = lineage.get_lineage(root)
        descendant_nos = {d['seal_no'] for d in info['descendants']}
        self.assertEqual(descendant_nos, {'S-1', 'S-2', 'S-1A', 'S-1B'})

        leaf_info = lineage.get_lineage(grandchildren[0])
        ancestor_nos = {a['seal_no'] for a in leaf_info['ancestors']}
        self.assertEqual(ancestor_nos, {'S-1', 'ROOT-1'})
        # 任意封签都能拿到发生过的操作
        op_types = {o['type'] for o in leaf_info['operations']}
        self.assertEqual(op_types, {SealOperation.TYPE_SEAL, SealOperation.TYPE_SPLIT})

    def test_split_rolls_back_when_quantity_not_conserved(self):
        root = self.seal_root()
        with self.assertRaises(LineageError):
            lineage.split(
                parent=root,
                outputs=[
                    {'seal_no': 'S-1', 'quantity': Decimal('30')},
                    {'seal_no': 'S-2', 'quantity': Decimal('60')},  # 合计 90 ≠ 100
                ],
                operator=self.user,
            )
        # 整次操作回滚：无新封签、无台账、无边，父封签仍在库
        self.assertEqual(Seal.objects.count(), 1)
        self.assertEqual(SealOperation.objects.count(), 1)  # 仅剩初始封装
        self.assertEqual(LineageEdge.objects.count(), 1)
        root.refresh_from_db()
        self.assertEqual(root.status, Seal.STATUS_IN_STOCK)

    def test_merge_conserves_and_requires_same_goods(self):
        a = self.seal_root("40", seal_no="M-1")
        b, _ = lineage.seal(goods=self.goods, quantity=Decimal("60"),
                            seal_no="M-2", operator=self.user)
        other, _ = lineage.seal(goods=self.other_goods, quantity=Decimal("5"),
                                seal_no="M-X", operator=self.user)

        # 跨货物合并不允许，且回滚
        with self.assertRaises(LineageError):
            lineage.merge(inputs=[a, other], output={'seal_no': 'BAD'},
                          operator=self.user)
        self.assertEqual(Seal.objects.filter(status=Seal.STATUS_TERMINATED).count(), 0)

        op, merged = lineage.merge(
            inputs=[a, b],
            output={'seal_no': 'M-OUT', 'quantity': Decimal('100')},
            operator=self.user,
        )
        self.assertEqual(merged.quantity, Decimal("100.00"))
        sources = LineageEdge.objects.filter(operation=op).values_list(
            'source__seal_no', flat=True
        )
        self.assertEqual(set(sources), {'M-1', 'M-2'})
        # 新封签的祖先包含两个来源
        ancestors = {x['seal_no'] for x in lineage.get_lineage(merged)['ancestors']}
        self.assertEqual(ancestors, {'M-1', 'M-2'})

    def test_merge_rejects_mismatched_output_quantity(self):
        a = self.seal_root("40", seal_no="MM-1")
        b, _ = lineage.seal(goods=self.goods, quantity=Decimal("60"),
                            seal_no="MM-2", operator=self.user)
        with self.assertRaises(LineageError):
            lineage.merge(
                inputs=[a, b],
                output={'seal_no': 'MM-OUT', 'quantity': Decimal('99')},
                operator=self.user,
            )
        self.assertFalse(Seal.objects.filter(seal_no='MM-OUT').exists())
        self.assertEqual(SealOperation.objects.filter(
            type=SealOperation.TYPE_MERGE).count(), 0)

    def test_repack_n_to_m_edges_balance_exactly(self):
        a = self.seal_root("33.33", seal_no="R-1")
        b, _ = lineage.seal(goods=self.goods, quantity=Decimal("66.67"),
                            seal_no="R-2", operator=self.user)
        op, children = lineage.repack(
            inputs=[a, b],
            outputs=[
                {'seal_no': 'R-O1', 'quantity': Decimal('50.00')},
                {'seal_no': 'R-O2', 'quantity': Decimal('25.50')},
                {'seal_no': 'R-O3', 'quantity': Decimal('24.50')},
            ],
            operator=self.user,
        )
        # 每个投入的出边合计等于其标称数量
        for src in (a, b):
            total = sum(e.quantity for e in op.edges.all() if e.source_id == src.pk)
            src.refresh_from_db()
            self.assertEqual(total, src.quantity)
        # 每个产出的入边合计等于其标称数量
        for child in children:
            total = sum(e.quantity for e in op.edges.all() if e.target_id == child.pk)
            self.assertEqual(total, child.quantity)

    def test_repack_rolls_back_on_imbalance(self):
        a = self.seal_root("10", seal_no="RR-1")
        b, _ = lineage.seal(goods=self.goods, quantity=Decimal("10"),
                            seal_no="RR-2", operator=self.user)
        with self.assertRaises(LineageError):
            lineage.repack(
                inputs=[a, b],
                outputs=[{'seal_no': 'RR-O', 'quantity': Decimal('19.99')}],
                operator=self.user,
            )
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.status, Seal.STATUS_IN_STOCK)
        self.assertEqual(b.status, Seal.STATUS_IN_STOCK)
        self.assertFalse(Seal.objects.filter(seal_no='RR-O').exists())


class LineageStateGuardTest(LineageFixture):
    def test_frozen_seal_cannot_recombine_or_issue(self):
        root = self.seal_root()
        lineage.freeze(seals=[root], operator=self.user)

        root.refresh_from_db()
        with self.assertRaises(LineageError):
            lineage.split(parent=root,
                          outputs=[{'seal_no': 'F-1', 'quantity': Decimal('50')},
                                   {'seal_no': 'F-2', 'quantity': Decimal('50')}],
                          operator=self.user)
        with self.assertRaises(LineageError):
            lineage.merge(inputs=[root], output={'seal_no': 'F-M'})
        with self.assertRaises(LineageError):
            lineage.repack(inputs=[root], outputs=[{'seal_no': 'F-R', 'quantity': Decimal('100')}])
        with self.assertRaises(LineageError):
            lineage.issue(seals=[root], receiver='张三', operator=self.user)
        # 冻结操作未产生任何谱系边，仅挂节点关联
        self.assertFalse(LineageEdge.objects.filter(
            operation__type=SealOperation.TYPE_FREEZE).exists())

        # 解冻后恢复正常
        lineage.unfreeze(seals=[root], operator=self.user)
        root.refresh_from_db()
        self.assertTrue(root.is_recombinable)

    def test_issued_and_terminated_seals_cannot_recombine(self):
        root = self.seal_root()
        _, children = lineage.split(
            parent=root,
            outputs=[{'seal_no': 'I-1', 'quantity': Decimal('40')},
                     {'seal_no': 'I-2', 'quantity': Decimal('60')}],
            operator=self.user,
        )
        lineage.issue(seals=[children[0]], receiver='李四', receiver_dept='一队',
                      operator=self.user)

        issued = Seal.objects.get(seal_no='I-1')
        terminated = Seal.objects.get(seal_no='ROOT-1')
        for bad in (issued, terminated):
            with self.assertRaises(LineageError):
                lineage.merge(inputs=[bad, children[1]],
                              output={'seal_no': 'X'}, operator=self.user)

        # 已领用封签再次领用同样拒绝
        with self.assertRaises(LineageError):
            lineage.issue(seals=[issued], receiver='王五', operator=self.user)

    def test_issue_requires_receiver(self):
        root = self.seal_root()
        with self.assertRaises(LineageError):
            lineage.issue(seals=[root], receiver='', operator=self.user)

    def test_invalid_quantity_rejected(self):
        with self.assertRaises(LineageError):
            lineage.seal(goods=self.goods, quantity=Decimal('0'), operator=self.user)
        with self.assertRaises(LineageError):
            lineage.seal(goods=self.goods, quantity='abc', operator=self.user)

    def test_missing_seal_rejected_without_creating_operation(self):
        root = self.seal_root()
        ghost = Seal(pk=999999, seal_no='GHOST', goods=self.goods,
                     quantity=Decimal('1'), status=Seal.STATUS_IN_STOCK)
        with self.assertRaises(LineageError):
            lineage.merge(inputs=[root, ghost],
                          output={'seal_no': 'G-OUT'}, operator=self.user)
        self.assertEqual(
            SealOperation.objects.filter(type=SealOperation.TYPE_MERGE).count(), 0
        )


class LineageTimelineTest(LineageFixture):
    def _build_chain(self):
        root = self.seal_root("100")
        _, children = lineage.split(
            parent=root,
            outputs=[{'seal_no': 'T-1', 'quantity': Decimal('60')},
                     {'seal_no': 'T-2', 'quantity': Decimal('40')}],
            operator=self.user,
        )
        return root, children

    def test_explain_before_seal(self):
        root = self.seal_root()
        result = lineage.explain_at(root, timezone.now() - timedelta(days=1))
        self.assertFalse(result['existed'])
        self.assertEqual(result['held_quantity'], '0.00')

    def test_explain_status_and_quantity_over_time(self):
        root, children = self._build_chain()

        # 拆分后：父已终结、在控为0；子在库、在控为标称数量
        now = timezone.now()
        root_view = lineage.explain_at(root, now)
        self.assertEqual(root_view['status'], Seal.STATUS_TERMINATED)
        self.assertEqual(root_view['held_quantity'], '0.00')
        self.assertEqual(root_view['cumulative_flow']['transferred_out'], '100.00')

        child_view = lineage.explain_at(children[0], now)
        self.assertEqual(child_view['status'], Seal.STATUS_IN_STOCK)
        self.assertEqual(child_view['held_quantity'], '60.00')

        # 冻结后时点：状态冻结、在控仍保留
        lineage.freeze(seals=[children[0]], operator=self.user)
        frozen_view = lineage.explain_at(children[0], timezone.now())
        self.assertEqual(frozen_view['status'], Seal.STATUS_FROZEN)
        self.assertEqual(frozen_view['held_quantity'], '60.00')
        self.assertIn('冻结', frozen_view['explanation'])

        # 解冻后领用：状态已领用、在控归零、流出60
        lineage.unfreeze(seals=[children[0]], operator=self.user)
        lineage.issue(seals=[children[0]], receiver='审计员', operator=self.user)
        issued_view = lineage.explain_at(children[0], timezone.now())
        self.assertEqual(issued_view['status'], Seal.STATUS_ISSUED)
        self.assertEqual(issued_view['held_quantity'], '0.00')
        self.assertEqual(issued_view['cumulative_flow']['transferred_out'], '60.00')


class ImmutabilityTest(LineageFixture):
    def test_operation_and_edge_and_node_are_append_only(self):
        root = self.seal_root()
        op = SealOperation.objects.get(type=SealOperation.TYPE_SEAL)
        edge = LineageEdge.objects.get(target=root)
        node = OperationNode.objects.get(seal=root)

        op.remark = '篡改'
        with self.assertRaises(ImmutableRecordError):
            op.save()
        with self.assertRaises(ImmutableRecordError):
            op.delete()

        edge.quantity = Decimal('999')
        with self.assertRaises(ImmutableRecordError):
            edge.save()
        with self.assertRaises(ImmutableRecordError):
            edge.delete()

        with self.assertRaises(ImmutableRecordError):
            node.delete()

    def test_seal_identity_fields_immutable_but_status_transitions(self):
        root = self.seal_root()
        root.quantity = Decimal('999')
        with self.assertRaises(ImmutableRecordError):
            root.save()
        root = Seal.objects.get(pk=root.pk)
        root.batch_no = '允许改批次备注'
        root.save()  # 批次号/状态等非身份字段允许流转更新


class ConservationAuditTest(LineageFixture):
    def test_verify_conservation_healthy_over_mixed_history(self):
        root = self.seal_root("100", seal_no="C-1")
        _, children = lineage.split(
            parent=root,
            outputs=[{'seal_no': 'C-2', 'quantity': Decimal('30')},
                     {'seal_no': 'C-3', 'quantity': Decimal('70')}],
            operator=self.user,
        )
        extra, _ = lineage.seal(goods=self.goods, quantity=Decimal('30'),
                                seal_no="C-4", operator=self.user)
        lineage.merge(inputs=[children[0], extra],
                      output={'seal_no': 'C-5', 'quantity': Decimal('60')},
                      operator=self.user)
        lineage.issue(seals=[children[1]], receiver='赵六', operator=self.user)
        report = lineage.verify_conservation()
        self.assertTrue(report['healthy'], report['problems'])


# ==================== API 端到端 ====================

class SealAPITest(LineageFixture):
    def test_full_flow_and_lineage_query(self):
        # 封装
        resp = self.client.post('/api/seals/', {
            'seal_no': 'API-1', 'goods': self.goods.id,
            'quantity': '100', 'batch_no': 'B-API',
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.json())
        seal_id = resp.json()['data']['seal']['id']

        # 守恒拆分
        resp = self.client.post('/api/seals/split/', {
            'seal_no': 'API-1',
            'outputs': [
                {'seal_no': 'API-1A', 'quantity': '60'},
                {'seal_no': 'API-1B', 'quantity': '40'},
            ],
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.json())
        self.assertEqual(len(resp.json()['data']['seals']), 2)

        # 谱系查询：祖先/后代/操作
        resp = self.client.get(f'/api/seals/{seal_id}/lineage/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['data']
        self.assertEqual({d['seal_no'] for d in data['descendants']},
                         {'API-1A', 'API-1B'})
        self.assertEqual(len(data['operations']), 2)

        # 时点解释
        resp = self.client.get(f'/api/seals/{seal_id}/timeline/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['data']['status'], Seal.STATUS_TERMINATED)

    def test_split_imbalance_api_rolls_back(self):
        self.client.post('/api/seals/', {
            'seal_no': 'API-RB', 'goods': self.goods.id, 'quantity': '100',
        }, format='json')
        before_seals = Seal.objects.count()
        resp = self.client.post('/api/seals/split/', {
            'seal_no': 'API-RB',
            'outputs': [
                {'seal_no': 'BAD-1', 'quantity': '50'},
                {'seal_no': 'BAD-2', 'quantity': '49'},
            ],
        }, format='json')
        body = resp.json()
        self.assertEqual(resp.status_code, 400)
        self.assertIn('守恒', body['message'])
        self.assertFalse(body['success'])
        self.assertEqual(Seal.objects.count(), before_seals)
        self.assertFalse(Seal.objects.filter(seal_no__in=['BAD-1', 'BAD-2']).exists())
        self.assertEqual(Seal.objects.get(seal_no='API-RB').status,
                         Seal.STATUS_IN_STOCK)

    def test_frozen_seal_rejected_at_api(self):
        self.client.post('/api/seals/', {
            'seal_no': 'API-F', 'goods': self.goods.id, 'quantity': '10',
        }, format='json')
        resp = self.client.post('/api/seals/freeze/', {'seal_nos': ['API-F']},
                                format='json')
        self.assertEqual(resp.status_code, 200)

        resp = self.client.post('/api/seals/issue/',
                                {'seal_nos': ['API-F'], 'receiver': '钱七'},
                                format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('重组', resp.json()['message'])
        self.assertEqual(Seal.objects.get(seal_no='API-F').status,
                         Seal.STATUS_FROZEN)

    def test_merge_and_issue_api(self):
        for no, qty in (('API-M1', '30'), ('API-M2', '70')):
            r = self.client.post('/api/seals/', {
                'seal_no': no, 'goods': self.goods.id, 'quantity': qty,
            }, format='json')
            self.assertEqual(r.status_code, 200)

        resp = self.client.post('/api/seals/merge/', {
            'seal_nos': ['API-M1', 'API-M2'],
            'output': {'seal_no': 'API-MOUT', 'quantity': '100'},
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.json())

        resp = self.client.post('/api/seals/issue/', {
            'seal_nos': ['API-MOUT'], 'receiver': '孙九', 'receiver_dept': '二队',
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Seal.objects.get(seal_no='API-MOUT').status,
                         Seal.STATUS_ISSUED)

    def test_repack_api_and_operation_list(self):
        self.client.post('/api/seals/', {
            'seal_no': 'API-RP', 'goods': self.goods.id, 'quantity': '50',
        }, format='json')
        resp = self.client.post('/api/seals/repack/', {
            'seal_nos': ['API-RP'],
            'outputs': [{'seal_no': 'API-RP1', 'quantity': '20'},
                        {'seal_no': 'API-RP2', 'quantity': '30'}],
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.json())

        resp = self.client.get('/api/seals/operations/')
        self.assertEqual(resp.status_code, 200)
        types_ = {item['type'] for item in resp.json()['data']['list']}
        self.assertIn(SealOperation.TYPE_SEAL, types_)
        self.assertIn(SealOperation.TYPE_REPACK, types_)

    def test_conservation_endpoint(self):
        self.client.post('/api/seals/', {
            'seal_no': 'API-AUD', 'goods': self.goods.id, 'quantity': '10',
        }, format='json')
        resp = self.client.get('/api/seals/conservation/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['data']['healthy'])

    def test_seal_endpoints_require_auth(self):
        self.assertEqual(APIClient().get('/api/seals/').status_code, 401)

    def test_duplicate_seal_no_rejected(self):
        payload = {'seal_no': 'DUP', 'goods': self.goods.id, 'quantity': '1'}
        self.assertEqual(self.client.post('/api/seals/', payload, format='json').status_code, 200)
        dup = self.client.post('/api/seals/', payload, format='json')
        self.assertEqual(dup.status_code, 400)

    def test_lineage_and_timeline_by_seal_no(self):
        self.client.post('/api/seals/', {
            'seal_no': 'NO-1', 'goods': self.goods.id, 'quantity': '100',
        }, format='json')
        r = self.client.post('/api/seals/split/', {
            'seal_no': 'NO-1',
            'outputs': [{'seal_no': 'NO-1A', 'quantity': '60'},
                        {'seal_no': 'NO-1B', 'quantity': '40'}],
        }, format='json')
        self.assertEqual(r.status_code, 200)

        r = self.client.get('/api/seals/by-no/NO-1A/lineage/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual({a['seal_no'] for a in r.json()['data']['ancestors']},
                         {'NO-1'})

        r = self.client.get('/api/seals/by-no/NO-1/timeline/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['data']['status'], Seal.STATUS_TERMINATED)

        r = self.client.get('/api/seals/by-no/UNKNOWN/lineage/')
        self.assertEqual(r.status_code, 404)

    def test_timeline_bad_at_param(self):
        self.client.post('/api/seals/', {
            'seal_no': 'NO-T', 'goods': self.goods.id, 'quantity': '1',
        }, format='json')
        seal_id = Seal.objects.get(seal_no='NO-T').id
        r = self.client.get(f'/api/seals/{seal_id}/timeline/?at=not-a-time')
        self.assertEqual(r.status_code, 400)
