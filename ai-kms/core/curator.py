"""知识策展人（AI 管家·判断类模式）：双链生成 / 分类校准 / 发现关联 / 建议应用。

设计约束（用户拍板）：
  - 纯规则驱动，Mock/无密钥下即产出真实可用的建议；LLM 增强只走 enhancer 接口，一期 None。
  - 分类校准只查缓存（classifications.verdict + atoms 关键词），**零 read_note**；
    缓存证据不足者标 needs_reread，由显式动作 reread_recalibrate 经用户同意后重读。
  - 建议全部落 suggestions 表走人工审阅队列；应用一律经 SnapshotManager 先快照后写。
  - 双链/关联的 target 一律用文件 stem（=graph title_index 首键），不用 H1，避免双标题源分歧。
  - 发现关联采纳 = 写进笔记成真链接（复用 wikilink 段追加路径，reindex 后图谱自然升实线）。

suggestion payload schema（唯一定义处）：
  kind="wikilink":
    { source: "title_mention"|"shared_keywords",   # 置信度 0.9 / 0.6 两档
      target_title, target_path,
      content_fp:   apply 前校验（扫描后内容被改则建议作废）,
      insert_at:    首次出现偏移 | null(=追加"## 相关"段),
      anchor:       insert_at 处应等于 target_title 的定位串,
      safe:         false ⇒ insert_at 必 null（提及落在代码围栏/已有[[..]]内）,
      shared_keywords: [..]                        # 仅 shared_keywords 档 }
  kind="topic":
    { now_topic, new_topic, topic_hits, extra_hits, margin,
      needs_reread: bool, evidence: "cache"|"cache+atoms"|"reread" }
  kind="edge"（发现关联；payload 结构兼容 wikilink 的 section 模式，复用 _apply_wikilink）:
    { source: "edge_discovery", target_title(=对面 stem), target_path,
      content_fp(被改侧), insert_at: null, anchor: null, safe: false,
      shared_keywords: [..], title_shared: int,
      tier: "dashed"(虚线升实线)|"rescue"(孤立救援)|"library"(全库单词对),
      deg_from, deg_to }                           # pair 两端在图中的度数
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from itertools import combinations

from core.classify.rules import TOPIC_RULES
from core.graph.engine import GraphEngine
from core.storage.fts import tokenize
from core.storage.vault import Vault, slugify
from core.aipilot.snapshot import SnapshotManager

MAX_SUGG_PER_NOTE = 5          # 单篇一次扫描的建议上限
MAX_EDGE_PER_NODE = 3          # 发现关联：每节点最多建议数（两端都计数）
MAX_EDGE_PAIRS = 500           # 发现关联：单次扫描全局建议上限
_EDGE_HUB_CAP = 60             # 关键词波及文件数超此值视为 hub 词，不计入 shared（对齐 engine）


class Curator:
    def __init__(self, store, vault, pipe, enhancer=None):
        self.store = store
        self.vault = vault
        self.pipe = pipe
        self.snapshots = SnapshotManager(store, vault)
        self.enhancer = enhancer          # LLM 增强接口（一期 None）
        self._ctx: "_ScanCtx | None" = None
        self._ctx_epoch = -1

    # ================= scope =================
    def resolve_scope(self, scope: dict) -> list[str]:
        """{"type":"note","paths":[..]} / {"type":"topic","topic":X} / {"type":"all"}"""
        t = scope.get("type", "all")
        if t == "note":
            return [p for p in scope.get("paths", []) if self.store.get_file(p)]
        if t == "topic":
            return [f["path"] for f in self.store.all_files() if f["topic"] == scope["topic"]]
        return [f["path"] for f in self.store.all_files()]

    # ================= 扫描预处理（共享上下文，epoch 缓存） =================
    def _scan_ctx(self) -> "_ScanCtx":
        if self._ctx is None or self._ctx_epoch != self.store.epoch:
            self._ctx = _ScanCtx(self.store)
            self._ctx_epoch = self.store.epoch
        return self._ctx

    # ================= 双链生成 =================
    def wikilinks_dry_run(self, scope: dict) -> dict:
        paths = self.resolve_scope(scope)
        return {"will_scan": len(paths), "note": "逐篇生成建议进审阅队列，可中途停止"}

    def wikilinks_scan(self, paths: list[str], limit: int = 20) -> dict:
        """处理 paths 前 limit 篇，建议落库。返回进度（队列由调用方持有，GUI tick 分批）。"""
        batch, rest = paths[:limit], paths[limit:]
        ctx = self._scan_ctx()
        added = 0
        for rel in batch:
            try:
                rows = self.wikilinks_for(rel, ctx)
            except Exception as e:
                self.store.log("error", "curator", f"wikilinks {rel}: {e}")
                continue
            if rows:
                added += self.store.add_suggestions(rows)
        return {"scanned": len(batch), "added": added, "remaining": len(rest)}

    def wikilinks_for(self, rel: str, ctx: "_ScanCtx") -> list[dict]:
        """单篇 → 建议 dict 列表（纯函数，不落库）。"""
        content = self.vault.read_note(rel)
        if not content:
            return []
        fp = Vault.make_dedup_key(content)
        existing = {l["to_target"] for l in self.store.links_for(rel)}
        my_stem = _stem(rel)
        suggested = {rel}
        out: list[dict] = []

        # a) 标题提及（conf 0.9）：倒排粗筛 + 原文精验
        toks = set(tokenize(content))
        for title, path in ctx.title_of.items():
            if (path in suggested or title == my_stem      # 排除自身（按 stem 也排）
                    or title in ctx.ambiguous or title in ctx.topic_names
                    or title in existing or path in existing):
                continue
            if not ctx.title_tokens[title] <= toks:        # 粗筛：标题 token 全出现
                continue
            pos = content.find(title)
            if pos < 0:                                     # 精验：子串并不连续出现
                continue
            safe = not _in_protected(content, pos, len(title))
            suggested.add(path)
            out.append(_wikilink_row(rel, title, path, fp, pos if safe else None,
                                     title, safe, "title_mention", 0.9))

        # b) 共享关键词（conf 0.6）：无正文锚点，永远"## 相关"段模式
        my_kws = [a["value"] for a in self.store.atoms_for(rel) if a["kind"] == "keyword"]
        share: Counter = Counter()
        share_kw: dict = {}
        for kw in my_kws:
            for p in ctx.kw_to_paths.get(kw, ()):
                if p in suggested or p == rel:
                    continue
                title = ctx.title_of_path.get(p, "")
                if title and (title in existing or p in existing):
                    continue
                share[p] += 1
                share_kw.setdefault(p, []).append(kw)
        for p, cnt in share.most_common(3):
            if cnt < 2 or len(out) >= MAX_SUGG_PER_NOTE:
                continue
            title = ctx.title_of_path.get(p) or _stem(p)
            suggested.add(p)
            out.append(_wikilink_row(rel, title, p, fp, None, title, False,
                                     "shared_keywords", 0.6,
                                     shared_keywords=share_kw[p][:6]))
        return out[:MAX_SUGG_PER_NOTE]

    # ================= 分类校准 =================
    def _topic_map(self) -> dict[str, list[str]]:
        merged = {k: list(v) for k, v in TOPIC_RULES.items()}
        for k, v in self.store.custom_topic_rules().items():
            merged.setdefault(k, []).extend(v)
        return merged

    def _classification_map(self) -> dict[str, dict]:
        """path → 最新分类行（一条分组 SQL 替代逐篇查询）。"""
        latest = {r["file_path"]: r["id"] for r in self.store.conn.execute(
            "SELECT file_path, MAX(id) id FROM classifications GROUP BY file_path")}
        if not latest:
            return {}
        rows = self.store.conn.execute(
            "SELECT * FROM classifications WHERE id IN (%s)"
            % ",".join("?" * len(latest)), list(latest.values())).fetchall()
        return {r["file_path"]: dict(r) for r in rows}

    def _scan_prepare(self):
        topic_map = self._topic_map()
        cmap = self._classification_map()
        kws_by_file: dict[str, list] = {}
        for r in self.store.conn.execute(
                "SELECT file_path, value FROM atoms WHERE kind='keyword'"):
            kws_by_file.setdefault(r["file_path"], []).append(r["value"])
        return topic_map, cmap, kws_by_file

    def recalibrate_dry_run(self, scope: dict) -> dict:
        """与 scan 同判定路径（含打平态），只数不落库。"""
        paths = self.resolve_scope(scope)
        topic_map, cmap, kws = self._scan_prepare()
        need = 0
        for p in paths:
            row = self.store.get_file(p)
            if row is None:
                continue
            s = self.recalibrate_for(p, row["topic"], topic_map, cmap.get(p),
                                     kws.get(p, []))
            if s and s["payload"].get("needs_reread"):
                need += 1
        return {"will_scan": len(paths), "cache_ok": len(paths) - need,
                "needs_reread": need}

    def recalibrate_scan(self, paths: list[str], limit: int = 20) -> dict:
        """纯缓存路径：全程不读笔记文件（测试断言 mtime 不变）。"""
        batch, rest = paths[:limit], paths[limit:]
        topic_map, cmap, kws_by_file = self._scan_prepare()
        added = 0
        for rel in batch:
            row = self.store.get_file(rel)
            if row is None:
                continue
            s = self.recalibrate_for(rel, row["topic"], topic_map,
                                     cmap.get(rel), kws_by_file.get(rel, []))
            if s:
                self.store.add_suggestions([s])
                added += 1
        return {"scanned": len(batch), "added": added, "remaining": len(rest)}

    def recalibrate_for(self, rel: str, now_topic: str, topic_map: dict,
                        cls_row: dict | None, keywords: list[str]) -> dict | None:
        hits = _topic_hits(cls_row)
        extra: Counter = Counter()
        for topic, tks in topic_map.items():
            for v in keywords:
                if any(k == v or (len(k) >= 2 and k in v) for k in tks):
                    extra[topic] += 1
        scores = {t: hits.get(t, 0) + 0.5 * extra.get(t, 0) for t in topic_map}
        ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
        (best, b_score), (second, s_score) = ranked[0], ranked[1] if len(ranked) > 1 else ("", 0)
        no_evidence = (not cls_row) or not any(hits.values())
        payload = {"now_topic": now_topic, "new_topic": best,
                   "topic_hits": hits, "extra_hits": dict(extra)}
        if no_evidence or b_score <= 0 or b_score == s_score:
            # 证据不足/打平：占位建议行（conf 0），等显式"重读校准"处理
            payload.update(needs_reread=True, margin=0.0, evidence="cache")
            return {"kind": "topic", "file_path": rel,
                    "target": best or now_topic, "confidence": 0.0,
                    "payload": payload}
        if best == now_topic:
            return None
        payload.update(needs_reread=False,
                       margin=round((b_score - s_score) / b_score, 3),
                       evidence="cache+atoms" if extra.get(best) else "cache")
        return {"kind": "topic", "file_path": rel, "target": best,
                "confidence": min(1.0, (b_score - s_score) / b_score),
                "payload": payload}

    def reread_recalibrate(self, rels: list[str]) -> dict:
        """显式重读（调用方必须已 dry-run 报数并获用户同意）。绕过缓存重新判断。"""
        changed = 0
        for rel in rels:
            row = self.store.get_file(rel)
            if row is None:
                continue
            content = self.vault.read_note(rel)
            if not content:
                continue
            cls = self.pipe.router.classify(content, row["title"], rel_path="")
            self.store.save_classification(rel, cls)
            self.store.mark_obsolete(file_path=rel)     # 旧 needs_reread 占位行作废
            if cls["topic"] != row["topic"]:
                hits = cls.get("verdict", {}).get("topic_hits", {})
                ranked = sorted(hits.items(), key=lambda x: -x[1])
                b = ranked[0][1] if ranked else 1
                s = ranked[1][1] if len(ranked) > 1 else 0
                self.store.add_suggestions([{
                    "kind": "topic", "file_path": rel, "target": cls["topic"],
                    "confidence": (b - s) / b if b else 0.5,
                    "payload": {"now_topic": row["topic"], "new_topic": cls["topic"],
                                "topic_hits": dict(ranked), "extra_hits": {},
                                "margin": (b - s) / b if b else 0.5,
                                "needs_reread": False, "evidence": "reread"}}])
                changed += 1
        return {"done": len(rels), "changed": changed}

    # ================= 发现关联（edge discovery） =================
    def edges_dry_run(self, scope: dict, all_pairs: bool = False) -> dict:
        """预扫描报数：纯算不落库、不读笔记文件。"""
        pairs = self.edge_candidates(scope, all_pairs)
        g = GraphEngine(self.store).build()
        in_scope = set(self.resolve_scope(scope))
        iso = sum(1 for p in g["isolated"] if p in in_scope)
        return {"will_pairs": len(pairs), "isolated": iso,
                "pending": self.store.count_pending("edge")}

    def edge_candidates(self, scope: dict, all_pairs: bool = False) -> list[dict]:
        """生成→门槛→评分→排序→cap 的纯函数。pair 规则见模块 docstring。

        默认产出：tier1 dashed（shared≥2，即图谱已有虚线、可升实线）与
        tier2 rescue（一端孤立、须有标题交集或同主题背书）；
        all_pairs=True 时追加 tier2b library（两端非孤立、仅共享 1 词）。
        """
        paths = set(self.resolve_scope(scope))
        ctx = self._scan_ctx()
        if not paths:
            return []
        g = GraphEngine(self.store).build()
        deg: Counter = Counter()
        link_pairs: set = set()
        for e in g["edges"]:
            a, b = g["nodes"][e["source"]]["id"], g["nodes"][e["target"]]["id"]
            deg[a] += 1
            deg[b] += 1
            if e["kind"] == "link":
                link_pairs.add(frozenset((a, b)))
        topic_of = {f["path"]: f["topic"] for f in self.store.all_files()}
        # 候选期排除（双向）：applied 任何类型；pending 仅 wikilink（同对已被内容级建议覆盖，
        # 不再出 edge）。pending edge 不在此排除——留给 commit 期防反向、同向走
        # add_suggestions 的 DO UPDATE 刷新语义（重扫让置信度保持新鲜）。
        sugg_pairs = set()
        for r in self.store.conn.execute(
                "SELECT kind, file_path, target, status FROM suggestions"
                " WHERE status IN ('pending','applied')"):
            q = ctx.title_of.get(r["target"])
            if q is None:
                continue
            if r["status"] == "applied" or r["kind"] != "edge":
                sugg_pairs.add(frozenset((r["file_path"], q)))
        # 批量取 scope 内关键词原子，查 ctx 倒排聚 pair（hub 词整桶跳过）
        kws_by_file: dict[str, list] = {}
        for r in self.store.conn.execute(
                "SELECT file_path, value FROM atoms WHERE kind='keyword'"):
            if r["file_path"] in paths:
                kws_by_file.setdefault(r["file_path"], []).append(r["value"])
        shared_kw: dict = {}
        for rel, kws in kws_by_file.items():
            for kw in kws:
                peers = ctx.kw_to_paths.get(kw, ())
                if len(peers) > _EDGE_HUB_CAP:
                    continue
                for p in peers:
                    if p != rel:
                        shared_kw.setdefault(frozenset((rel, p)), set()).add(kw)
        out = []
        for k, kws in shared_kw.items():
            a, b = sorted(k)
            sa, sb = ctx.title_of_path.get(a, ""), ctx.title_of_path.get(b, "")
            if not sa or not sb:
                continue
            if sa in ctx.ambiguous or sb in ctx.ambiguous \
                    or sa in ctx.topic_names or sb in ctx.topic_names:
                continue
            if k in link_pairs or k in sugg_pairs:
                continue
            shared = len(kws)
            ts = len(ctx.title_tokens.get(sa, set()) & ctx.title_tokens.get(sb, set()))
            same_topic = topic_of.get(a) == topic_of.get(b)
            min_deg = min(deg.get(a, 0), deg.get(b, 0))
            if shared >= 2:
                tier = "dashed"
                conf = min(0.90, 0.55 + 0.10 * shared + 0.15 * ts)
            elif min_deg == 0 or all_pairs:
                if shared == 1 and ts == 0 and not same_topic:
                    continue          # 单词无背书：噪音，宁缺勿滥
                tier = "rescue" if min_deg == 0 else "library"
                conf = min(0.65, 0.25 + 0.15 * shared + 0.20 * ts)
            else:
                continue
            out.append({"a": a, "b": b, "shared": shared, "kws": sorted(kws)[:6],
                        "ts": ts, "tier": tier, "conf": round(conf, 3),
                        "min_deg": min_deg,
                        "deg_a": deg.get(a, 0), "deg_b": deg.get(b, 0)})
        out.sort(key=lambda d: (d["min_deg"], -d["conf"],
                                -(d["shared"] + 2 * d["ts"]), d["a"], d["b"]))
        seen: Counter = Counter()
        res = []
        for d in out:
            if seen[d["a"]] >= MAX_EDGE_PER_NODE or seen[d["b"]] >= MAX_EDGE_PER_NODE:
                continue
            seen[d["a"]] += 1
            seen[d["b"]] += 1
            res.append(d)
            if len(res) >= MAX_EDGE_PAIRS:
                break
        return res

    def edges_scan(self, pairs: list[dict], limit: int = 20) -> dict:
        """提交批：处理前 limit 个 pair 落 suggestions。契约对齐 wikilinks_scan。

        候选列表是开扫时快照；epoch 变化靠 commit 期三道防线兜底：
        ① 已有任一向实线 ② pending 同对（含反向翻转） ③ 被改侧 pending≥cap。
        """
        batch, rest = pairs[:limit], pairs[limit:]
        ctx = self._scan_ctx()
        pend = self.store.conn.execute(
            "SELECT file_path, target FROM suggestions"
            " WHERE kind='edge' AND status='pending'").fetchall()
        # pair → 该对中已作为被改侧出现的 pending 行集合：同向放行（刷新），反向跳过
        pend_pairs: dict = {}
        pend_cnt = Counter()
        for r in pend:
            q = ctx.title_of.get(r["target"])
            if q:
                pend_pairs.setdefault(frozenset((r["file_path"], q)), set()) \
                    .add(r["file_path"])
            pend_cnt[r["file_path"]] += 1
        linked = set()
        for r in self.store.conn.execute("SELECT from_path, to_target FROM links"):
            p = ctx.title_of.get(r["to_target"])
            if p:
                linked.add(frozenset((r["from_path"], p)))
        rows = []
        fp_cache: dict = {}
        for d in batch:
            a, b = d["a"], d["b"]
            mod, other = (a, b) if (d["deg_a"], a) <= (d["deg_b"], b) else (b, a)
            stem_o = ctx.title_of_path.get(other, _stem(other))
            k = frozenset((a, b))
            mods = pend_pairs.get(k)
            if k in linked:
                continue
            if mods and mod not in mods:
                continue          # 反向 pending 已有 → 跳过；同向放行走刷新
            if mods is None and pend_cnt.get(mod, 0) >= MAX_EDGE_PER_NODE:
                continue          # 被改侧 cap 已满不产新行（同向刷新不受限）
            content = self.vault.read_note(mod)
            if not content:
                continue                       # 被改侧已删
            fp = fp_cache.get(mod)
            if fp is None:
                fp = fp_cache[mod] = Vault.make_dedup_key(content)
            rows.append({"kind": "edge", "file_path": mod, "target": stem_o,
                         "confidence": d["conf"],
                         "payload": {"source": "edge_discovery",
                                     "target_title": stem_o, "target_path": other,
                                     "content_fp": fp, "insert_at": None,
                                     "anchor": None, "safe": False,
                                     "shared_keywords": d["kws"],
                                     "title_shared": d["ts"], "tier": d["tier"],
                                     "deg_from": d["deg_a"], "deg_to": d["deg_b"]}})
            pend_pairs.setdefault(k, set()).add(mod)
            pend_cnt[mod] += 1                 # 批内自防
        added = self.store.add_suggestions(rows) if rows else 0
        return {"scanned": len(batch), "added": added, "remaining": len(rest)}

    # ================= 应用（写回） =================
    def apply_suggestion(self, sid: int, target_override: str | None = None) -> dict:
        """采纳一条建议。失败不写库、行保持 pending。

        注：这里恒 require_confirm=False——进入审阅队列并点采纳本身就是人工确认，
        不再受 HUMAN_CONFIRM_WRITES 全局开关二次拦截。
        """
        row = self.store.get_suggestion(sid)
        if row is None or row["status"] != "pending":
            return {"ok": False, "reason": "not_pending"}
        try:
            if row["kind"] == "wikilink":
                return self._apply_wikilink(row, target_override)
            if row["kind"] == "edge":
                # 发现关联：payload 兼容 wikilink 段模式，写进笔记成真双链
                return self._apply_wikilink(row, target_override, reason="关联确认")
            if row["kind"] == "topic":
                return self._apply_topic(row, target_override)
        except Exception as e:
            self.store.log("error", "curator", f"apply #{sid}: {e}")
            return {"ok": False, "reason": f"error: {e}"}
        return {"ok": False, "reason": "unknown_kind"}

    def _apply_wikilink(self, row: dict, override: str | None,
                        reason: str = "AI 双链") -> dict:
        rel, pl = row["file_path"], row["payload"]
        target = (override or pl.get("target_title") or row["target"]).strip()
        content = self.vault.read_note(rel)
        if not content or Vault.make_dedup_key(content) != pl.get("content_fp"):
            self.store.resolve_suggestion(row["id"], "obsolete")
            return {"ok": False, "reason": "stale"}
        pos = pl.get("insert_at")
        anchor = pl.get("anchor") or target
        if pos is not None and content[pos:pos + len(anchor)] == anchor:
            new_content = content[:pos] + f"[[{target}]]" + content[pos + len(anchor):]
        else:
            new_content = _append_related(content, target)
        res = self.snapshots.write(rel, new_content, reason, require_confirm=False)
        self.pipe.reindex_note(rel, new_content)
        self.store.resolve_suggestion(row["id"], "applied")
        return {"ok": True, "status": "applied", "snapshot_id": res.get("snapshot_id")}

    def _apply_topic(self, row: dict, override: str | None) -> dict:
        rel = row["file_path"]
        f = self.store.get_file(rel)
        if f is None:
            self.store.resolve_suggestion(row["id"], "obsolete")
            return {"ok": False, "reason": "missing"}
        now_topic = f["topic"]
        new_topic = (override or row["target"]).strip()
        if not new_topic or new_topic == now_topic:
            self.store.resolve_suggestion(row["id"], "skipped")
            return {"ok": False, "reason": "no_change"}
        # 移动文件：目录=slugify(topic)，文件名 stem 不变（to_target 耦合不受影响）
        base = os.path.basename(rel)
        new_dir = slugify(new_topic)
        new_rel = f"{new_dir}/{base}"
        n = 2
        while (self.vault.vault_path / new_rel).exists() and new_rel != rel:
            new_rel = f"{new_dir}/{base[:-3] if base.endswith('.md') else base}-{n}.md"
            n += 1
        (self.vault.vault_path / new_dir).mkdir(parents=True, exist_ok=True)  # 新分类目录
        os.rename(self.vault.vault_path / rel, self.vault.vault_path / new_rel)
        self.store.rename_path(rel, new_rel)             # 含 fts/suggestions/links 级联
        self.store.upsert_file(new_rel, f["title"], new_topic, f["type"],
                               f["priority"], f["dedup_key"], f["mtime"] or 0)
        self.store.delete_link(new_rel, now_topic)       # 主题软链跟随
        self.store.add_link(new_rel, new_topic)
        self.store.remove_atoms_kind(new_rel, "topic")
        self.store.add_atoms(new_rel, [{"kind": "topic", "value": new_topic, "weight": 1.0}])
        self.store.save_classification(new_rel, {
            "topic": new_topic, "type": f["type"], "priority": f["priority"],
            "verdict": {"source": "curator_recalibration", "applied_from": row["id"],
                        "from_topic": now_topic}})
        self.store.resolve_suggestion(row["id"], "applied")
        return {"ok": True, "status": "applied", "new_rel": new_rel}


# ================= 内部工具 =================
def _stem(rel: str) -> str:
    return os.path.splitext(os.path.basename(rel))[0]


def _topic_hits(cls_row: dict | None) -> dict:
    if not cls_row:
        return {}
    try:
        v = json.loads(cls_row.get("verdict") or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    hits = v.get("topic_hits") or {}
    return {k: float(n) for k, n in hits.items() if isinstance(n, (int, float))}


_FENCE_RE = re.compile(r"```.*?```|`[^`\n]+`", re.S)
_WIKI_RE = re.compile(r"\[\[[^\[\]]*\]\]")


def _in_protected(content: str, pos: int, length: int) -> bool:
    """提及落点是否已在代码围栏/已有 [[..]] 内（防重复包裹/破坏代码）。"""
    for rx in (_FENCE_RE, _WIKI_RE):
        for m in rx.finditer(content):
            if m.start() <= pos and pos + length <= m.end():
                return True
    return False


def _append_related(content: str, target: str) -> str:
    line = f"- [[{target}]]\n"
    idx = content.find("## 相关")
    if idx >= 0:
        nxt = content.find("\n## ", idx + 5)
        end = nxt if nxt >= 0 else len(content)
        body = content[idx:end].rstrip("\n")
        return content[:idx] + body + "\n" + line + content[end:]
    return content.rstrip() + f"\n\n## 相关\n{line}"


def _wikilink_row(rel, title, path, fp, insert_at, anchor, safe, source, conf,
                  shared_keywords=None) -> dict:
    pl = {"source": source, "target_title": title, "target_path": path,
          "content_fp": fp, "insert_at": insert_at, "anchor": anchor, "safe": safe}
    if shared_keywords:
        pl["shared_keywords"] = shared_keywords
    return {"kind": "wikilink", "file_path": rel, "target": _stem(path),
            "confidence": conf, "payload": pl}


class _ScanCtx:
    """全库扫描共享预处理（epoch 失效）。10k×10k substring 不可行，靠它降规模：
    标题→token 集倒排粗筛，候选再原文精验；关键词→路径倒排直接喂 b 档。"""

    def __init__(self, store):
        self.title_of: dict[str, str] = {}         # stem → path（target 一律用 stem）
        self.title_of_path: dict[str, str] = {}    # path → stem
        self.ambiguous: set[str] = set()           # 重复 stem：宁缺勿误链
        self.kw_to_paths: dict[str, list[str]] = {}
        for f in store.all_files():
            stem = _stem(f["path"])
            if stem in self.title_of and self.title_of[stem] != f["path"]:
                self.ambiguous.add(stem)
            self.title_of[stem] = f["path"]
            self.title_of_path[f["path"]] = stem
        self.title_tokens = {t: set(tokenize(t)) for t in self.title_of}
        for r in store.conn.execute(
                "SELECT file_path, value FROM atoms WHERE kind='keyword'"):
            self.kw_to_paths.setdefault(r["value"], []).append(r["file_path"])
        # 主题名不作双链目标（pipeline 已把 topic 写成软链，再建议 [[主题]] 是噪音）
        topics = {f["topic"] for f in store.all_files()} | set(TOPIC_RULES) \
            | {c["name"] for c in store.all_categories()}
        self.topic_names = topics
