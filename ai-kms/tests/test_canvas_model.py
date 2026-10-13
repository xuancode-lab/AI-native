"""画布文档模型：JSON Canvas 1.0 宽容解析 / 未知类型与字段透传 / 编辑面 / 落盘。"""
from __future__ import annotations

import json
import unittest

from tests.conftest import TempEnv

from core.canvas import (CanvasDocument, CanvasParseError, color_hex,
                         content_fp, extract_subgraph, grid_positions,
                         remap_copy, unique_attach_rel)
from core.storage.vault import Vault


# 模拟 Obsidian 写出的画布：group/link 节点、未知字段、全可选边字段
SAMPLE = json.dumps({
    "nodes": [
        {"id": "a1", "type": "file", "x": 0, "y": 0, "width": 240, "height": 120,
         "file": "技术/甲篇.md"},
        {"id": "a2", "type": "text", "x": 300, "y": 10, "width": 240, "height": 120,
         "text": "灵感 **加粗**", "color": "4"},
        {"id": "g1", "type": "group", "x": -50, "y": -50, "width": 600, "height": 300,
         "label": "区域一", "color": "1"},
        {"id": "l1", "type": "link", "x": 500, "y": 200, "width": 240, "height": 120,
         "url": "https://example.com", "foo": {"bar": [1, 2]}},
        {"id": "a3", "type": "file", "x": 600, "y": 0, "width": 240, "height": 120,
         "file": "学习/乙篇.md", "customField": "keepme", "style": {"x": 1}},
    ],
    "edges": [
        {"id": "e1", "fromNode": "a1", "fromSide": "right", "toNode": "a2",
         "toSide": "left", "toEnd": "arrow", "label": "相关", "color": "#ff0000",
         "unknownEdgeProp": 9},
        {"id": "e2", "fromNode": "g1", "toNode": "l1"},
    ],
}, ensure_ascii=False)


class TestRoundTrip(unittest.TestCase):
    def test_parsed_equal_after_load_dump(self):
        """互通铁律：loads→dumps 语义等价（未知类型/字段一个都不能丢）。"""
        doc, _ = CanvasDocument.loads("c.canvas", SAMPLE)
        self.assertEqual(json.loads(doc.dumps()), json.loads(SAMPLE))

    def test_dumps_idempotent(self):
        doc, _ = CanvasDocument.loads("c.canvas", SAMPLE)
        t1 = doc.dumps()
        doc2, _ = CanvasDocument.loads("c.canvas", t1)
        self.assertEqual(doc2.dumps(), t1)

    def test_unknown_types_and_extras_modeled(self):
        doc, _ = CanvasDocument.loads("c.canvas", SAMPLE)
        g1 = doc.node("g1")
        self.assertEqual((g1.type, g1.label), ("group", "区域一"))
        l1 = doc.node("l1")
        self.assertEqual(l1.url, "https://example.com")
        self.assertEqual(l1.extra, {"foo": {"bar": [1, 2]}})
        self.assertEqual(doc.node("a3").extra["customField"], "keepme")
        self.assertEqual(doc.edge("e1").extra, {"unknownEdgeProp": 9})

    def test_all_optionals_roundtrip(self):
        e1 = CanvasDocument.loads("c.canvas", SAMPLE)[0].edge("e1")
        self.assertEqual((e1.from_side, e1.to_side, e1.to_end, e1.label, e1.color),
                         ("right", "left", "arrow", "相关", "#ff0000"))
        self.assertIsNone(e1.from_end)

    def test_empty_and_missing_arrays(self):
        for raw in ('{"nodes":[],"edges":[]}', "{}"):
            doc, rep = CanvasDocument.loads("c.canvas", raw)
            self.assertTrue(doc.is_empty)
            self.assertFalse(doc.dirty)
            self.assertEqual(rep["skipped_nodes"], [])


class TestLenientParse(unittest.TestCase):
    def test_bad_json_raises(self):
        with self.assertRaises(CanvasParseError):
            CanvasDocument.loads("c.canvas", "{not json")
        with self.assertRaises(CanvasParseError):
            CanvasDocument.loads("c.canvas", "[]")          # 顶层必须是对象

    def test_skips_individual_bad_items_with_report(self):
        raw = json.dumps({
            "nodes": [
                {"id": "ok", "type": "text", "x": 0, "y": 0,
                 "width": 10, "height": 10, "text": "t"},
                {"id": "noid"},                                            # 缺 type/几何
                {"type": "file", "x": 0, "y": 0, "width": 1, "height": 1},  # 缺 id
                "string-item",                                              # 非对象
                {"id": "nofile", "type": "text", "x": 0, "y": 1,
                 "width": 2},                                                # 缺 height
            ],
            "edges": [
                {"id": "e_ok", "fromNode": "ok", "toNode": "ok"},
                {"id": "e_dangling", "fromNode": "ok"},                     # 缺 toNode
                {"fromNode": "a", "toNode": "b"},                            # 缺 id
            ],
        })
        doc, rep = CanvasDocument.loads("c.canvas", raw)
        self.assertEqual([n.id for n in doc.nodes], ["ok"])
        self.assertEqual(rep["skipped_nodes"], [1, 2, 3, 4])
        self.assertEqual([e.id for e in doc.edges], ["e_ok"])
        self.assertEqual(rep["skipped_edges"], [1, 2])

    def test_bool_not_number(self):
        raw = json.dumps({"nodes": [
            {"id": "n", "type": "text", "x": True, "y": 0,
             "width": 10, "height": 10}]})
        doc, rep = CanvasDocument.loads("c.canvas", raw)
        self.assertEqual(doc.nodes, [])
        self.assertEqual(rep["skipped_nodes"], [0])


class TestEditing(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        self.vault = self.env.vault

    def tearDown(self):
        self.env.teardown()

    def test_add_file_card_backslash_normalized(self):
        doc = CanvasDocument()
        n = doc.add_file_card(r"技术\甲篇.md", 10, 20)
        self.assertEqual(n.file, "技术/甲篇.md")
        self.assertEqual((n.width, n.height), (240.0, 120.0))
        self.assertTrue(doc.dirty)

    def test_add_edge_validation_and_json_omits_none(self):
        doc = CanvasDocument()
        a = doc.add_text_card("甲", 0, 0)
        b = doc.add_text_card("乙", 100, 0)
        with self.assertRaises(ValueError):
            doc.add_edge(a.id, a.id)                      # 自环
        with self.assertRaises(ValueError):
            doc.add_edge(a.id, "ghost")                   # 缺端点
        e = doc.add_edge(a.id, b.id, to_end="arrow")
        j = e.to_json()
        self.assertEqual(j, {"id": e.id, "fromNode": a.id, "toNode": b.id,
                             "toEnd": "arrow"})           # None 键不写

    def test_remove_node_cascades_edges(self):
        doc, _ = CanvasDocument.loads("c.canvas", SAMPLE)
        gone = doc.remove_node("a1")
        self.assertEqual([e.id for e in gone], ["e1"])
        self.assertTrue(all("a1" not in (e.from_node, e.to_node) for e in doc.edges))
        self.assertIsNone(doc.node("a1"))
        with self.assertRaises(ValueError):
            doc.remove_node("a1")

    def test_remove_edge_and_lookup_helpers(self):
        doc, _ = CanvasDocument.loads("c.canvas", SAMPLE)
        self.assertEqual(doc.edge("e2").from_node, "g1")
        self.assertIsNotNone(doc.remove_edge("e2"))
        self.assertIsNone(doc.remove_edge("e2"))
        self.assertEqual([e.id for e in doc.edges_of("a1")], ["e1"])

    def test_move_geometry_text_dirty_lifecycle(self):
        doc = CanvasDocument()
        a = doc.add_file_card("x/a.md", 0, 0)
        t = doc.add_text_card("旧", 100, 0)
        doc.dirty = False
        doc.move_nodes({"ghost": (1, 1)})                 # 全未知 id：不置脏
        self.assertFalse(doc.dirty)
        doc.move_nodes({a.id: (5, 7)})
        self.assertEqual((a.x, a.y), (5.0, 7.0))
        doc.set_geometry(a.id, width=300, height=150)
        self.assertEqual((a.width, a.height), (300.0, 150.0))
        doc.set_text(t.id, "新内容")
        self.assertEqual(t.text, "新内容")
        self.assertTrue(doc.dirty)
        with self.assertRaises(ValueError):
            doc.set_text(a.id, "笔记卡不可改")             # 正文属笔记工作区

    def test_save_open_roundtrip_on_disk(self):
        doc = CanvasDocument(rel_path="白板/测试.canvas")
        doc.add_file_card("技术/甲篇.md", 0, 0)
        doc.add_text_card("中文不转义 ✓", 300, 0)
        doc.remove_node(doc.nodes[0].id)
        doc.save(self.vault)
        self.assertFalse(doc.dirty)

        raw = (self.env.vault_path / "白板" / "测试.canvas").read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))  # UTF-8 无 BOM
        self.assertIn("中文不转义", raw.decode("utf-8"))     # ensure_ascii=False

        doc2, rep = CanvasDocument.open(self.vault, "白板/测试.canvas")
        self.assertEqual(rep["skipped_nodes"], [])
        self.assertFalse(doc2.dirty)
        self.assertEqual(len(doc2.nodes), 1)
        self.assertEqual(doc2.nodes[0].text, "中文不转义 ✓")

    def test_open_missing_file_is_fresh_empty_doc(self):
        doc, _ = CanvasDocument.open(self.vault, "不存在.canvas")
        self.assertTrue(doc.is_empty)
        self.assertFalse(doc.dirty)

    def test_new_document_resets_in_place(self):
        doc, _ = CanvasDocument.loads("c.canvas", SAMPLE)
        doc.new_document()
        self.assertTrue(doc.is_empty)
        self.assertTrue(doc.dirty)
        with self.assertRaises(ValueError):
            CanvasDocument().save(self.vault)             # 无路径拒绝保存


class TestColorHex(unittest.TestCase):
    def test_palette_hex_invalid(self):
        self.assertEqual(color_hex("1"), "#e93147")
        self.assertEqual(color_hex("#abcdef"), "#abcdef")
        self.assertIsNone(color_hex("9"))
        self.assertIsNone(color_hex(None))
        self.assertIsNone(color_hex(""))


class TestSetProps(unittest.TestCase):
    def setUp(self):
        self.doc = CanvasDocument()
        self.file = self.doc.add_file_card("a.md", 0, 0)
        self.text = self.doc.add_text_card("旧", 300, 0)

    def test_whitelist_and_old_snapshot(self):
        self.doc.dirty = False
        old = self.doc.set_node_props([self.file.id], {"color": "4"})
        self.assertEqual(old, {self.file.id: {"color": None}})
        self.assertEqual(self.file.color, "4")
        self.assertTrue(self.doc.dirty)
        self.doc.set_node_props([self.file.id], old[self.file.id])
        self.assertIsNone(self.file.color)                 # 撤销回放=写回旧值

    def test_bad_keys_and_unknown_id(self):
        with self.assertRaises(ValueError):
            self.doc.set_node_props([self.file.id], {"x": 1})   # x 不在白名单
        with self.assertRaises(ValueError):
            self.doc.set_node_props(["ghost"], {"color": "1"})

    def test_text_only_for_text_cards(self):
        with self.assertRaises(ValueError):
            self.doc.set_node_props([self.file.id], {"text": "越权"})
        old = self.doc.set_node_props([self.text.id], {"text": "新"})
        self.assertEqual(old[self.text.id]["text"], "旧")
        self.assertEqual(self.text.text, "新")

    def test_edge_props_and_toggle(self):
        e = self.doc.add_edge(self.file.id, self.text.id, to_end="arrow")
        old = self.doc.set_edge_props([e.id], {"from_end": "arrow", "to_end": None,
                                               "label": "备注"})
        self.assertEqual(old[e.id]["to_end"], "arrow")
        self.assertEqual((e.from_end, e.to_end, e.label), ("arrow", None, "备注"))
        with self.assertRaises(ValueError):
            self.doc.set_edge_props([e.id], {"fromNode": "x"})   # spec 键名≠属性名


class TestDiskFp(unittest.TestCase):
    def test_content_fp_full_text_sensitive(self):
        """对照钉死：content_fp 对缩进/空白敏感，make_dedup_key 会漏检——不可混用。"""
        a = '{"nodes": [], "edges": []}'
        b = '{"nodes":[],"edges":[]}'
        self.assertNotEqual(content_fp(a), content_fp(b))
        self.assertEqual(Vault.make_dedup_key(a), Vault.make_dedup_key(b))  # 对照
        self.assertIsNone(content_fp(""))

    def test_loads_records_baseline_and_stale_flow(self):
        doc, _ = CanvasDocument.loads("c.canvas", '{"nodes": [], "edges": []}')
        base = doc.disk_fp
        self.assertIsNotNone(base)
        self.assertFalse(doc.stale_on_disk(base))
        self.assertTrue(doc.stale_on_disk(content_fp("外部改过")))
        self.assertTrue(doc.stale_on_disk(None))          # 盘上文件被删

    def test_finalize_save_and_new_sentinel(self):
        doc = CanvasDocument(rel_path="c.canvas")
        self.assertIsNone(doc.disk_fp)                    # 新文档：盘上无此文件
        text = doc.dumps()
        doc.add_text_card("x", 0, 0)
        self.assertTrue(doc.dirty)
        doc.finalize_save(text)
        self.assertFalse(doc.dirty)
        self.assertEqual(doc.disk_fp, content_fp(text))


class TestSubgraph(unittest.TestCase):
    def test_edges_only_between_selected(self):
        doc = CanvasDocument()
        a, b, c = (doc.add_text_card("甲", 0, 0), doc.add_text_card("乙", 100, 0),
                   doc.add_text_card("丙", 200, 0))
        e_ab = doc.add_edge(a.id, b.id)
        e_bc = doc.add_edge(b.id, c.id)
        nodes, edges = extract_subgraph(doc, [a.id, b.id])
        self.assertEqual({n.id for n in nodes}, {a.id, b.id})
        self.assertEqual([e.id for e in edges], [e_ab.id])   # e_bc 一端在外不取

    def test_remap_new_ids_and_endpoints(self):
        doc = CanvasDocument()
        a = doc.add_text_card("甲", 10, 10)
        b = doc.add_text_card("乙", 100, 100)
        e = doc.add_edge(a.id, b.id, to_end="arrow")
        nodes, edges = extract_subgraph(doc, [a.id, b.id])
        remap_copy(nodes, edges, offset=(24.0, 24.0))
        new_ids = {n.id for n in nodes}
        self.assertEqual(new_ids & {a.id, b.id}, set())      # id 全换
        self.assertEqual({n.id for n in nodes} & {e.from_node, e.to_node}, set())
        self.assertIn(edges[0].from_node, new_ids)           # 端点重映射到新卡
        self.assertIn(edges[0].to_node, new_ids)
        self.assertEqual((nodes[0].x, nodes[0].y), (34.0, 34.0))
        self.assertEqual(edges[0].to_end, "arrow")           # 其余字段原样

    def test_copy_isolated_from_source_doc(self):
        doc = CanvasDocument()
        t = doc.add_text_card("原", 0, 0)
        nodes, _ = extract_subgraph(doc, [t.id])
        nodes[0].text = "改了副本"
        remap_copy(nodes, [])
        self.assertEqual(t.text, "原")                      # 原 doc 不受污染
        self.assertNotEqual(nodes[0].id, t.id)


class TestGroup(unittest.TestCase):
    def setUp(self):
        self.doc = CanvasDocument()
        self.a = self.doc.add_file_card("a.md", 0, 0)        # 240×120 默认
        self.b = self.doc.add_text_card("乙", 400, 300)
        self.c = self.doc.add_text_card("丙", 2000, 2000)

    def test_create_group_bbox_and_pad(self):
        g = self.doc.create_group([self.a.id, self.b.id], label="区域")
        self.assertEqual(g.type, "group")
        self.assertEqual(g.label, "区域")
        # 包围盒 x[-120,520] y[-60,360] + pad24 → x[-144,544] y[-84,384]
        self.assertAlmostEqual(g.x, (-144 + 544) / 2)
        self.assertAlmostEqual(g.y, (-84 + 384) / 2)
        self.assertAlmostEqual(g.width, 688.0)
        self.assertAlmostEqual(g.height, 468.0)
        self.assertTrue(self.doc.dirty)

    def test_members_geometric_center_inside(self):
        g = self.doc.create_group([self.a.id, self.b.id])
        self.assertEqual(set(self.doc.group_members(g.id)), {self.a.id, self.b.id})
        self.b.x, self.b.y = 2000, 2000                       # 拖出组
        self.assertEqual(self.doc.group_members(g.id), [self.a.id])
        self.c.x, self.c.y = g.x + g.width / 2, g.y            # 中心恰在边界=内
        self.assertIn(self.c.id, self.doc.group_members(g.id))

    def test_create_validation(self):
        with self.assertRaises(ValueError):
            self.doc.create_group([self.a.id])                 # <2
        with self.assertRaises(ValueError):
            self.doc.create_group([self.a.id, "ghost"])

    def test_delete_group_only_removes_box(self):
        g = self.doc.create_group([self.a.id, self.b.id])
        gone = self.doc.remove_node(g.id)
        self.assertEqual(gone, [])                             # 组无级联边
        self.assertIsNotNone(self.doc.node(self.a.id))
        self.assertEqual(self.doc.group_members(g.id), [])     # 组没了成员查询=空

    def test_group_of_groups_rejected(self):
        g = self.doc.create_group([self.a.id, self.b.id])
        with self.assertRaises(ValueError):
            self.doc.create_group([self.a.id, g.id])

    def test_unknown_group_type_passthrough(self):
        """Obsidian 写的 group 节点：建模 + members 判定 + 存盘透传不破。"""
        raw = '{"nodes":[{"id":"a","type":"file","x":0,"y":0,"width":240,"height":120,' \
              '"file":"a.md"},{"id":"g","type":"group","x":0,"y":0,"width":400,' \
              '"height":300,"label":"组"}],"edges":[]}'
        doc, rep = CanvasDocument.loads("c.canvas", raw)
        self.assertEqual(rep["skipped_nodes"], [])
        self.assertEqual(doc.group_members("g"), ["a"])
        self.assertEqual(json.loads(doc.dumps())["nodes"][1]["label"], "组")


class TestAttachNaming(unittest.TestCase):
    def test_unique_naming(self):
        existing = {"附件/图.png"}
        self.assertEqual(unique_attach_rel("图.png", existing), "附件/图-1.png")
        self.assertEqual(unique_attach_rel("图.png", existing | {"附件/图-1.png"}),
                         "附件/图-2.png")
        self.assertEqual(unique_attach_rel("新图.JPG", set()), "附件/新图.jpg")
        self.assertEqual(unique_attach_rel("", set()), "附件/file")


class TestGridPositions(unittest.TestCase):
    def test_square_grid(self):
        pts = grid_positions(5, (0, 0))
        self.assertEqual(len(pts), 5)
        self.assertEqual(pts[0], (0.0, 0.0))
        self.assertEqual(pts[1], (280.0, 0.0))                # cols=3（ceil√5）
        self.assertEqual(pts[3], (0.0, 160.0))
        self.assertEqual(grid_positions(0, (0, 0)), [])
        self.assertEqual(grid_positions(2, (10, 10), cols=2)[1], (290.0, 10.0))


if __name__ == "__main__":
    unittest.main()
