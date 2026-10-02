"""
封签谱系测试

覆盖审计关注的核心不变量：
- 拆分/合并/重新封装后谱系不断裂，可双向追溯；
- 数量守恒校验失败时整次操作回滚；
- 已领用、已冻结、已转化的节点不得参与不合法的重组；
- 任意封签可查询祖先、后代与发生过的操作；
- 可解释历史时点上的数量与状态；
- 谱系事实记录不可变。
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.backends import generate_token
from apps.authentication.models import User
from apps.warehouse.models import Category, Goods, Unit, Variety
from .exceptions import ImmutableLineageError
from .models import Seal, SealOperation, SealOperationNode


class CustodyFixture(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("custody-user", "testpass123", role="admin")
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {generate_token(self.user)}")
        self.unit = Unit.objects.create(name="箱", created_by=self.user)
        self.category = Category.objects.create(name="受控物资", unit=self.unit, created_by=self.user)
        self.variety = Variety.objects.create(name="封样试剂", category=self.category, created_by=self.user)
        self.goods = Goods.objects.create(
            variety=self.variety, name="标准封样试剂", code="REAGENT-001",
            quantity=Decimal("500"), warning_threshold=Decimal("10"),
        )
        self.other_goods = Goods.objects.create(
            variety=self.variety, name="对照封样试剂", code="REAGENT-002",
            quantity=Decimal("100"), warning_threshold=Decimal("10"),
        )

    def register(self, quantity, goods=None, seal_no=""):
        response = self.client.post("/api/seals/", {
            "goods": (goods or self.goods).id,
            "quantity": str(quantity),
            "seal_no": seal_no,
            "location": "A-01",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.json())
        return response.json()["data"]["seal"]


class SealRegisterTest(CustodyFixture):
    def test_register_creates_lineage_root(self):
        seal = self.register("100", seal_no="ROOT-001")
        self.assertEqual(seal["status"], "active")
        self.assertEqual(seal["quantity"], "100.00")
        self.assertEqual(seal["initial_quantity"], "100.00")

        operation = SealOperation.objects.get(operation_type="register")
        self.assertEqual(operation.output_quantity, Decimal("100"))
        edge = operation.nodes.get()
        self.assertEqual(edge.direction, "out")
        self.assertEqual(edge.seal_id, seal["id"])

        # 根节点没有祖先
        lineage = self.client.get(f"/api/seals/{seal['id']}/lineage/").json()["data"]
        self.assertEqual(lineage["ancestors"], [])
        self.assertEqual(lineage["descendants"], [])
        self.assertEqual(len(lineage["operations"]), 1)

    def test_register_rejects_invalid_quantity(self):
        for bad in ("0", "-5"):
            response = self.client.post("/api/seals/", {
                "goods": self.goods.id, "quantity": bad,
            }, format="json")
            self.assertEqual(response.status_code, 400)
        self.assertEqual(Seal.objects.count(), 0)

    def test_register_rejects_duplicate_seal_no_and_unknown_goods(self):
        self.register("10", seal_no="DUP-001")
        duplicate = self.client.post("/api/seals/", {
            "goods": self.goods.id, "quantity": "5", "seal_no": "DUP-001",
        }, format="json")
        unknown = self.client.post("/api/seals/", {
            "goods": 99999, "quantity": "5",
        }, format="json")
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(unknown.status_code, 400)


class SealSplitTest(CustodyFixture):
    def test_split_conserves_quantity_and_builds_lineage(self):
        parent = self.register("100", seal_no="P-100")
        response = self.client.post("/api/seals/split/", {
            "seal_id": parent["id"], "quantities": ["40", "60"],
        }, format="json")
        self.assertEqual(response.status_code, 200, response.json())
        children = response.json()["data"]["children"]
        self.assertEqual(len(children), 2)
        self.assertEqual(
            sorted(Decimal(c["quantity"]) for c in children),
            [Decimal("40.00"), Decimal("60.00")],
        )

        parent_seal = Seal.objects.get(pk=parent["id"])
        self.assertEqual(parent_seal.status, "consumed")
        self.assertEqual(parent_seal.quantity, Decimal("0"))

        operation = SealOperation.objects.get(operation_type="split")
        self.assertTrue(operation.conserved)
        self.assertEqual(operation.input_quantity, Decimal("100"))
        self.assertEqual(operation.output_quantity, Decimal("100"))
        self.assertEqual(operation.nodes.filter(direction="in").count(), 1)
        self.assertEqual(operation.nodes.filter(direction="out").count(), 2)

        # 子签反向追溯到父签
        child_lineage = self.client.get(
            f"/api/seals/{children[0]['id']}/lineage/"
        ).json()["data"]
        self.assertEqual(
            [a["seal"]["seal_no"] for a in child_lineage["ancestors"]], ["P-100"]
        )

    def test_split_quantity_mismatch_rolls_back_entirely(self):
        parent = self.register("100", seal_no="P-ROLL")
        response = self.client.post("/api/seals/split/", {
            "seal_id": parent["id"], "quantities": ["30", "60"],
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("数量守恒", response.json()["message"])

        # 整次回滚：父签原样、无操作单、无子签
        parent_seal = Seal.objects.get(pk=parent["id"])
        self.assertEqual(parent_seal.status, "active")
        self.assertEqual(parent_seal.quantity, Decimal("100"))
        self.assertEqual(Seal.objects.count(), 1)
        self.assertFalse(SealOperation.objects.filter(operation_type="split").exists())
        self.assertEqual(SealOperationNode.objects.count(), 1)  # 只剩登记边

    def test_split_requires_at_least_two_children(self):
        parent = self.register("100")
        response = self.client.post("/api/seals/split/", {
            "seal_id": parent["id"], "quantities": ["100"],
        }, format="json")
        self.assertEqual(response.status_code, 400)

    def test_split_rejects_zero_and_negative_child(self):
        parent = self.register("100")
        for quantities in (["0", "100"], ["-1", "101"]):
            response = self.client.post("/api/seals/split/", {
                "seal_id": parent["id"], "quantities": quantities,
            }, format="json")
            self.assertEqual(response.status_code, 400)

    def test_consumed_seal_cannot_be_split_again(self):
        parent = self.register("100")
        self.client.post("/api/seals/split/", {
            "seal_id": parent["id"], "quantities": ["40", "60"],
        }, format="json")
        again = self.client.post("/api/seals/split/", {
            "seal_id": parent["id"], "quantities": ["10", "90"],
        }, format="json")
        self.assertEqual(again.status_code, 400)
        self.assertIn("已转化", again.json()["message"])

    def test_split_children_inherit_goods_and_location(self):
        parent = self.register("100")
        response = self.client.post("/api/seals/split/", {
            "seal_id": parent["id"], "quantities": ["50", "50"],
        }, format="json")
        for child in response.json()["data"]["children"]:
            self.assertEqual(child["goods"], self.goods.id)
            self.assertEqual(child["location"], "A-01")


class SealMergeTest(CustodyFixture):
    def test_merge_conserves_quantity(self):
        a = self.register("10", seal_no="M-A")
        b = self.register("20", seal_no="M-B")
        c = self.register("30", seal_no="M-C")
        response = self.client.post("/api/seals/merge/", {
            "seal_ids": [a["id"], b["id"], c["id"]],
        }, format="json")
        self.assertEqual(response.status_code, 200, response.json())
        merged = response.json()["data"]["seal"]
        self.assertEqual(merged["quantity"], "60.00")

        for seal in (a, b, c):
            self.assertEqual(Seal.objects.get(pk=seal["id"]).status, "consumed")

        operation = SealOperation.objects.get(operation_type="merge")
        self.assertTrue(operation.conserved)
        self.assertEqual(operation.input_quantity, Decimal("60"))
        self.assertEqual(operation.output_quantity, Decimal("60"))

        # 新签可追溯到全部三个父签
        lineage = self.client.get(f"/api/seals/{merged['id']}/lineage/").json()["data"]
        self.assertEqual(
            {item["seal"]["seal_no"] for item in lineage["ancestors"]},
            {"M-A", "M-B", "M-C"},
        )

    def test_merge_rejects_mixed_goods(self):
        a = self.register("10")
        b = self.register("20", goods=self.other_goods)
        response = self.client.post("/api/seals/merge/", {
            "seal_ids": [a["id"], b["id"]],
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("同一货物", response.json()["message"])

    def test_merge_expected_quantity_mismatch_rolls_back(self):
        a = self.register("10")
        b = self.register("20")
        response = self.client.post("/api/seals/merge/", {
            "seal_ids": [a["id"], b["id"]], "expected_quantity": "999",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("数量守恒", response.json()["message"])
        # 整次回滚：投入封签仍在库
        for seal in (a, b):
            self.assertEqual(Seal.objects.get(pk=seal["id"]).status, "active")
        self.assertFalse(SealOperation.objects.filter(operation_type="merge").exists())

    def test_merge_rejects_duplicate_ids(self):
        a = self.register("10")
        response = self.client.post("/api/seals/merge/", {
            "seal_ids": [a["id"], a["id"]],
        }, format="json")
        self.assertEqual(response.status_code, 400)


class SealRepackTest(CustodyFixture):
    def test_repack_keeps_quantity_and_lineage(self):
        old = self.register("80", seal_no="OLD-80")
        response = self.client.post("/api/seals/repack/", {
            "seal_id": old["id"],
        }, format="json")
        self.assertEqual(response.status_code, 200, response.json())
        new = response.json()["data"]["seal"]
        self.assertEqual(new["quantity"], "80.00")
        self.assertNotEqual(new["seal_no"], "OLD-80")

        old_seal = Seal.objects.get(pk=old["id"])
        self.assertEqual(old_seal.status, "consumed")
        self.assertEqual(old_seal.quantity, Decimal("0"))

        lineage = self.client.get(f"/api/seals/{new['id']}/lineage/").json()["data"]
        self.assertEqual(
            [a["seal"]["seal_no"] for a in lineage["ancestors"]], ["OLD-80"]
        )

    def test_repack_quantity_mismatch_rolls_back(self):
        old = self.register("80")
        response = self.client.post("/api/seals/repack/", {
            "seal_id": old["id"], "expected_quantity": "79",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("数量守恒", response.json()["message"])
        self.assertEqual(Seal.objects.get(pk=old["id"]).status, "active")
        self.assertFalse(SealOperation.objects.filter(operation_type="repack").exists())


class SealStateGuardTest(CustodyFixture):
    """已领用/已冻结/已转化的节点不得参与不合法的重组"""

    def test_frozen_seal_cannot_reorganize(self):
        seal = self.register("50", seal_no="FRZ-50")
        other = self.register("50")
        self.client.post(f"/api/seals/{seal['id']}/freeze/", {}, format="json")
        self.assertEqual(Seal.objects.get(pk=seal["id"]).status, "frozen")

        for url, payload in (
            ("/api/seals/split/", {"seal_id": seal["id"], "quantities": ["20", "30"]}),
            ("/api/seals/merge/", {"seal_ids": [seal["id"], other["id"]]}),
            ("/api/seals/repack/", {"seal_id": seal["id"]}),
            (f"/api/seals/{seal['id']}/issue/", {"receiver": "张三"}),
        ):
            response = self.client.post(url, payload, format="json")
            self.assertEqual(response.status_code, 400, url)
            self.assertIn("冻结", response.json()["message"], url)

        # 解冻后恢复在库，可以正常拆分
        self.client.post(f"/api/seals/{seal['id']}/unfreeze/", {}, format="json")
        self.assertEqual(Seal.objects.get(pk=seal["id"]).status, "active")
        ok = self.client.post("/api/seals/split/", {
            "seal_id": seal["id"], "quantities": ["20", "30"],
        }, format="json")
        self.assertEqual(ok.status_code, 200)

    def test_issued_seal_cannot_reorganize(self):
        seal = self.register("60", seal_no="ISS-60")
        other = self.register("40")
        response = self.client.post(f"/api/seals/{seal['id']}/issue/", {
            "receiver": "李四", "receiver_dept": "稽查科",
        }, format="json")
        self.assertEqual(response.status_code, 200)

        issued = Seal.objects.get(pk=seal["id"])
        self.assertEqual(issued.status, "issued")
        self.assertEqual(issued.quantity, Decimal("0"))

        operation = SealOperation.objects.get(operation_type="issue")
        self.assertEqual(operation.receiver, "李四")
        self.assertEqual(operation.receiver_dept, "稽查科")
        self.assertEqual(operation.input_quantity, Decimal("60"))

        for url, payload in (
            ("/api/seals/split/", {"seal_id": seal["id"], "quantities": ["30", "30"]}),
            ("/api/seals/merge/", {"seal_ids": [seal["id"], other["id"]]}),
            ("/api/seals/repack/", {"seal_id": seal["id"]}),
            (f"/api/seals/{seal['id']}/freeze/", {}),
        ):
            response = self.client.post(url, payload, format="json")
            self.assertEqual(response.status_code, 400, url)
            self.assertIn("领用", response.json()["message"], url)

    def test_issue_requires_receiver(self):
        seal = self.register("10")
        response = self.client.post(f"/api/seals/{seal['id']}/issue/", {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_double_freeze_and_double_unfreeze_rejected(self):
        seal = self.register("10")
        self.client.post(f"/api/seals/{seal['id']}/freeze/", {}, format="json")
        again = self.client.post(f"/api/seals/{seal['id']}/freeze/", {}, format="json")
        self.assertEqual(again.status_code, 400)
        self.client.post(f"/api/seals/{seal['id']}/unfreeze/", {}, format="json")
        again = self.client.post(f"/api/seals/{seal['id']}/unfreeze/", {}, format="json")
        self.assertEqual(again.status_code, 400)


class SealLineageTest(CustodyFixture):
    """任意封签都可查询祖先、后代与发生过的操作"""

    def setUp(self):
        super().setUp()
        # A(100) 拆成 B(40)、C(60)；C 与 D(50) 合并成 E(110)；B 重新封装成 F(40)
        self.a = self.register("100", seal_no="A")
        response = self.client.post("/api/seals/split/", {
            "seal_id": self.a["id"], "quantities": ["40", "60"],
        }, format="json")
        children = response.json()["data"]["children"]
        self.b = next(c for c in children if c["quantity"] == "40.00")
        self.c = next(c for c in children if c["quantity"] == "60.00")
        self.d = self.register("50", seal_no="D")
        response = self.client.post("/api/seals/merge/", {
            "seal_ids": [self.c["id"], self.d["id"]],
        }, format="json")
        self.e = response.json()["data"]["seal"]
        response = self.client.post("/api/seals/repack/", {
            "seal_id": self.b["id"],
        }, format="json")
        self.f = response.json()["data"]["seal"]
        # 拆分/合并/重新封装产生的新签由系统编号，记录实际编号用于断言
        self.b_no = self.b["seal_no"]
        self.c_no = self.c["seal_no"]
        self.e_no = self.e["seal_no"]
        self.f_no = self.f["seal_no"]

    def test_ancestors_across_generations(self):
        lineage = self.client.get(f"/api/seals/{self.e['id']}/lineage/").json()["data"]
        by_no = {item["seal"]["seal_no"]: item["depth"] for item in lineage["ancestors"]}
        self.assertEqual(by_no.get(self.c_no), 1)
        self.assertEqual(by_no.get("D"), 1)
        self.assertEqual(by_no.get("A"), 2)
        # 每个祖先都标注了产生它的操作
        for item in lineage["ancestors"]:
            self.assertTrue(item["operation_no"])
            self.assertTrue(item["operation_type"])

    def test_descendants_across_generations(self):
        lineage = self.client.get(f"/api/seals/{self.a['id']}/lineage/").json()["data"]
        by_no = {item["seal"]["seal_no"]: item["depth"] for item in lineage["descendants"]}
        self.assertEqual(by_no.get(self.b_no), 1)
        self.assertEqual(by_no.get(self.c_no), 1)
        self.assertEqual(by_no.get(self.e_no), 2)
        self.assertEqual(by_no.get(self.f_no), 2)
        self.assertNotIn("D", by_no)

    def test_operations_cover_everything_that_happened(self):
        lineage = self.client.get(f"/api/seals/{self.c['id']}/lineage/").json()["data"]
        op_types = [op["operation_type"] for op in lineage["operations"]]
        self.assertEqual(op_types, ["split", "merge"])
        # 操作单携带完整的投入产出边
        merge_op = next(op for op in lineage["operations"] if op["operation_type"] == "merge")
        self.assertEqual(len(merge_op["nodes"]), 3)
        in_nodes = [n for n in merge_op["nodes"] if n["direction"] == "in"]
        self.assertEqual({n["seal_no"] for n in in_nodes}, {self.c_no, "D"})

    def test_root_and_leaf_boundaries(self):
        root = self.client.get(f"/api/seals/{self.a['id']}/lineage/").json()["data"]
        self.assertEqual(root["ancestors"], [])
        leaf = self.client.get(f"/api/seals/{self.f['id']}/lineage/").json()["data"]
        self.assertEqual(leaf["descendants"], [])


class SealStateAtTest(CustodyFixture):
    """解释历史时点上的数量与状态"""

    def setUp(self):
        super().setUp()
        self.base = timezone.now() - timedelta(days=1)
        self.seal = self.register("100", seal_no="HIST-100")
        self.client.post(f"/api/seals/{self.seal['id']}/freeze/", {}, format="json")
        self.client.post(f"/api/seals/{self.seal['id']}/unfreeze/", {}, format="json")
        response = self.client.post("/api/seals/split/", {
            "seal_id": self.seal["id"], "quantities": ["40", "60"],
        }, format="json")
        self.children = response.json()["data"]["children"]
        # 将四次操作锚定到确定的历史时点
        ops = list(SealOperation.objects.order_by("id"))
        for index, operation in enumerate(ops):
            SealOperation.objects.filter(pk=operation.pk).update(
                created_at=self.base + timedelta(hours=index)
            )

    def state_at(self, seal_id, at):
        response = self.client.get(
            f"/api/seals/{seal_id}/state-at/", {"at": at.isoformat()}
        )
        self.assertEqual(response.status_code, 200, response.json())
        return response.json()["data"]

    def test_before_registration_seal_did_not_exist(self):
        data = self.state_at(self.seal["id"], self.base - timedelta(seconds=1))
        self.assertFalse(data["existed"])
        self.assertIsNone(data["quantity"])
        self.assertIsNotNone(data["birth_time"])

    def test_state_replay_across_operations(self):
        # 登记后、冻结前：在库 100
        data = self.state_at(self.seal["id"], self.base + timedelta(minutes=30))
        self.assertTrue(data["existed"])
        self.assertEqual(data["status"], "active")
        self.assertEqual(data["quantity"], "100.00")
        # 冻结期间：已冻结，数量不变
        data = self.state_at(self.seal["id"], self.base + timedelta(hours=1, minutes=30))
        self.assertEqual(data["status"], "frozen")
        self.assertEqual(data["quantity"], "100.00")
        # 解冻后：恢复在库
        data = self.state_at(self.seal["id"], self.base + timedelta(hours=2, minutes=30))
        self.assertEqual(data["status"], "active")
        # 拆分后：父签已转化，数量清零
        data = self.state_at(self.seal["id"], self.base + timedelta(hours=4))
        self.assertEqual(data["status"], "consumed")
        self.assertEqual(data["quantity"], "0.00")

    def test_events_explain_every_transition(self):
        data = self.state_at(self.seal["id"], self.base + timedelta(hours=4))
        self.assertEqual(
            [event["operation_type"] for event in data["events"]],
            ["register", "freeze", "unfreeze", "split"],
        )
        for event in data["events"]:
            self.assertTrue(event["operation_no"])
            self.assertTrue(event["resulting_status_display"])

    def test_child_did_not_exist_before_split(self):
        child = self.children[0]
        before = self.state_at(child["id"], self.base + timedelta(hours=2))
        self.assertFalse(before["existed"])
        after = self.state_at(child["id"], self.base + timedelta(hours=4))
        self.assertTrue(after["existed"])
        self.assertEqual(after["status"], "active")
        self.assertEqual(after["quantity"], child["quantity"])

    def test_invalid_time_parameter_rejected(self):
        response = self.client.get(f"/api/seals/{self.seal['id']}/state-at/")
        self.assertEqual(response.status_code, 400)
        response = self.client.get(
            f"/api/seals/{self.seal['id']}/state-at/", {"at": "not-a-time"}
        )
        self.assertEqual(response.status_code, 400)


class SealImmutabilityTest(CustodyFixture):
    """谱系事实记录不可变、不可删除"""

    def test_operation_and_edge_are_immutable(self):
        self.register("10")
        operation = SealOperation.objects.get(operation_type="register")
        operation.remark = "篡改"
        with self.assertRaises(ImmutableLineageError):
            operation.save()
        with self.assertRaises(ImmutableLineageError):
            operation.delete()

        edge = SealOperationNode.objects.get()
        with self.assertRaises(ImmutableLineageError):
            edge.quantity = Decimal("999")
            edge.save()
        with self.assertRaises(ImmutableLineageError):
            edge.delete()

    def test_seal_cannot_be_deleted(self):
        seal_dict = self.register("10")
        seal = Seal.objects.get(pk=seal_dict["id"])
        with self.assertRaises(ImmutableLineageError):
            seal.delete()


class SealListAndOperationViewTest(CustodyFixture):
    def test_seal_list_filters(self):
        a = self.register("10", seal_no="LST-A")
        b = self.register("20", seal_no="LST-B")
        self.client.post(f"/api/seals/{b['id']}/freeze/", {}, format="json")

        response = self.client.get("/api/seals/", {"status": "frozen"})
        data = response.json()["data"]
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["list"][0]["seal_no"], "LST-B")

        response = self.client.get("/api/seals/", {"keyword": "LST-A"})
        self.assertEqual(response.json()["data"]["total"], 1)

        response = self.client.get("/api/seals/", {"goods": self.goods.id})
        self.assertEqual(response.json()["data"]["total"], 2)

    def test_operation_list_and_detail(self):
        a = self.register("10", seal_no="OP-A")
        self.client.post("/api/seals/split/", {
            "seal_id": a["id"], "quantities": ["4", "6"],
        }, format="json")

        response = self.client.get("/api/seal-operations/", {"operation_type": "split"})
        data = response.json()["data"]
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["list"][0]["operation_type"], "split")

        response = self.client.get("/api/seal-operations/", {"seal_no": "OP-A"})
        self.assertEqual(response.json()["data"]["total"], 2)  # 登记 + 拆分

        operation = SealOperation.objects.get(operation_type="split")
        detail = self.client.get(f"/api/seal-operations/{operation.id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.json()["data"]["nodes"]), 3)

    def test_seal_detail_and_missing_seal(self):
        seal = self.register("10")
        ok = self.client.get(f"/api/seals/{seal['id']}/")
        self.assertEqual(ok.status_code, 200)
        missing = self.client.get("/api/seals/99999/")
        self.assertEqual(missing.status_code, 404)
        missing_lineage = self.client.get("/api/seals/99999/lineage/")
        self.assertEqual(missing_lineage.status_code, 404)

    def test_requires_authentication(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.get("/api/seals/").status_code, 401)
        self.assertEqual(anonymous.post("/api/seals/split/", {}, format="json").status_code, 401)
        self.assertEqual(anonymous.get("/api/seal-operations/").status_code, 401)
