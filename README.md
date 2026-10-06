# AI-native KMS

> 原生自包含的个人知识管理系统 —— 让 AI 轻量地管理你的知识库

数据 **100% 本地**、**Markdown 原生**、可迁移、可离线。数据主权完全在你手里，不绑定任何平台。

---

## 核心信条

> **一次解析，原子沉淀，终身复用，判断不再重复读上下文。**

传统 AI 知识库的通病是"重字"——每次打标签、归类、去重、路由都要把整篇文档重新喂给 LLM，又贵又慢。

本系统把 AI 拆成两层，各司其职：

| 层 | 职责 | 做法 |
|---|---|---|
| **判断层**（轻） | 分类 / 归属 / 去重 / 路由 | 入库时做**一次**分类，结果落缓存；之后所有判断**只查缓存**，不碰原文 |
| **生成层**（重） | 答疑 / 润色 / 矛盾校验 | 只喂**检索命中的原子片段**（RAG），不读全文 |

素材进来的那一刻，就拆成原子片段（关键词 / 命题 / 标签 / 分类）持久化。此后人与 AI 的每一次操作都消费这些原子，原始文档只需读一次。

---

## 思想来源（融合五家精华，自研实现）

本项目**不引用、不绑定**任何现成工具，而是吸收各家的设计思想，用一套原生代码重新实现：

| 来源 | 吸收的思想 | 本项目的实现 |
|---|---|---|
| **Obsidian** | Markdown 原生存储、双向链接 | 自研 Vault 存储层 + `[[wikilink]]` 解析 |
| **LLMWiki** | 增量复利、去重、百科化沉淀 | 入库管线 + 内容指纹去重 + 原子沉淀 |
| **GBrain** | 知识图谱、语义关联、记忆权重 | 图谱引擎（节点/边/孤立点 + 共享关键词自动补链 + 使用权重） |
| **Claude** | 长文本推理、矛盾识别 | 生成层多模型（Claude / OpenAI 兼容 / Ollama / Mock 自动降级） |
| **jev** | 只判断、不重读上下文 | **分类路由中枢**：一次分类终身复用（规则集起步，可无缝换分类器模型） |

---

## 进度

- [x] **阶段 0** —— 骨架 + SQLite + Vault 挂载扫描
- [x] **阶段 1** —— 入库管线 + 分类路由（规则集）+ 原子抽取 + 去重 + watchdog 自动收录
- [x] **阶段 2** —— 知识图谱 + 语义/BM25 检索 + 力导向可视化 + IDE 风格 GUI
- [x] **阶段 3** —— AI 管家 + 生成层多模型 + 版本快照 + 人工确认开关
- [x] **阶段 4** —— 整合打磨 + 批量/重建 + E2E 全流程测试
- [x] **阶段 5** —— 全区域可操作：编辑/新建/重命名/删除/回滚/相关笔记跳转（21/21 通过）
- [x] **阶段 6 · 收录增强** —— 导入文件夹 + 树拖拽入库 + 知识节点簇 SVG 树图标 + 预览 `[[双链]]` 可点击渲染
- [x] **阶段 7 · AI 管家完善一期** —— FTS5 检索地基 + 图谱倒排/epoch 缓存 + 建议审阅队列 + 双链生成/分类校准（curator，纯规则离线可用）+ 挂载体系 + 设置面板（75/75 通过）
- [x] **阶段 8 · 资产开放一期** —— 结构导出（JSONL/图谱/全库 zip）+ MCP 只读服务（四工具，外部 AI 低成本调用知识库，108/108 通过）
- [x] **阶段 9 · 桌面打包** —— PyInstaller onedir 实测 exe（jieba 词典/图标入包）+ 数据位置三形态（便携 / KMS_DATA_ROOT / 降级平台目录）与设置面板迁移 + macOS/Linux 预埋（116/116 通过；双平台 CI 与签名待做）
- [x] **阶段 10 · 发现关联 + OpenAI 兼容** —— edge discovery 三档候选（虚线升实线 / 孤立救援 / 全库对，采纳即真双链）；生成层 OpenAIProvider（DeepSeek / Kimi / vLLM 等即插即用）；双链预览点击跳转修复；FlatCombo 下拉指示（133/133 通过）

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动桌面端
python main.py gui

# 3. 或者用 CLI
python main.py watch        # 监听 data/dropbox，丢素材自动入库
```

**入库三条路**（都走「清洗 → 分类 → 原子抽取 → 去重 → 落盘 → 双链/标签 → 索引」全流程，重复内容自动拦截）：

1. **丢 dropbox**：把 `.md` / `.txt` 丢进 `data/dropbox/`，watchdog 自动收录
2. **导入文件夹**：工具栏「导入文件夹」选目录 → 递归批量收录（隐藏/临时文件自动过滤，分批进度可视、可停止）
3. **拖拽入库**：直接把文件/文件夹（可多选混排）从资源管理器拖进左侧导航树

### CLI 命令

```bash
python main.py scan                 # 扫描 Vault 重建索引
python main.py ingest "一段素材"      # 直接收录一段文本
python main.py ingest path/file.md  # 收录一个文件
python main.py batch <dir>          # 批量收录目录下所有 .md/.txt
python main.py reindex              # 从 Vault 现有文件重建索引（DB 丢失后恢复）
python main.py fts-rebuild          # 全文检索索引对账重建（补漏/清幽灵）
python main.py export [dir]         # 导出结构层资产：JSONL+图谱+manifest（缺省 data/exports/<时间戳>）
python main.py export-full [dir]    # 全库打包 zip：原文 .md + 结构 + manifest（解压即用）
python main.py mcp                  # MCP 只读服务（stdio），外部 AI 客户端调用本知识库
python main.py ask "问题"            # AI 管家 RAG 问答
python main.py curator "dry wikilinks"       # 双链生成 dry-run 预扫描（只报数不写库）
python main.py curator "scan topic"          # 全库分类校准扫描（纯查缓存）→ 审阅队列
python main.py curator "list"                # 列出待处理建议
python main.py curator "apply 3"             # 采纳 #3 号建议（先快照后写）
python main.py curator "approve-high 0.9"    # 批量采纳置信度 ≥0.9 的建议
python main.py search 关键词         # 原子关键词检索
python main.py dump <rel_path>      # 查看某文件的判断 / 原子 / 双链
python main.py watch                # 监听 dropbox 自动入库
python main.py gui                  # 启动桌面端
```

---

## 桌面端（IDE 风格布局）

参考 Android Studio / VSCode 的多面板设计：所有面板都可折叠，且切换图标放在**常驻活动栏**里，折叠后图标始终可点。

```
┌────────────────────────────────────────────────────────┐
│ 顶部工具栏  收录 导入 | 笔记 图谱 AI | 监听 | 🔍 搜索 审阅 设置 │
├──┬──────────────────────────────┬──────────┬───────────┤
│📁│                              │          │           │
│  │        中间工作区              │ 笔记属性  │   📋      │
│  │   (笔记 / 图谱 / AI 可切换)     │ 路径/主题  │ (属性栏   │
│  │                              │ 原子片段  │  折叠)    │
│  │                              │ 相关笔记  │           │
│📝│                              │          │           │
├──┴──────────────────────────────┴──────────┴───────────┤
│   日志 / 版本历史 / 审阅队列（底栏，由左下 📝 图标切换）      │
└────────────────────────────────────────────────────────┘
```

| 位置 | 图标 | 作用 |
|---|---|---|
| 左活动栏**上** | 📁 `project` | 折叠 / 展开左侧文件树 |
| 左活动栏**下** | 📝 `logs` | 折叠 / 展开底部日志 / 版本历史 |
| 右活动栏**上** | 📋 `properties` | 折叠 / 展开右侧属性栏 |

**图标细节**：细线圆角 SVG，`currentColor` 动态着色——**未选中灰 `#5f6368`，选中蓝 `#1a73e8`**，背景只有一层极淡高亮，无硬边框（Android Studio 式的丝滑质感）。

- **树节点图标**不用 📁 / 📄 emoji，而是自绘**知识节点簇**：分类=中心环+三颗连线实心点（蓝），笔记=原子节点带连点（灰），呼应"图谱/原子"概念、摆脱资源管理器观感
- **下拉框**无原生黑色三角，FlatCombo 自绘细线 chevron，收起 `⌄` 展开 `⌃`

### 三个工作区

用顶部工具栏在中间区域切换：

| 工作区 | 能力 |
|---|---|
| **📝 笔记** | 双击文件树打开 → Markdown **预览 / 编辑**切换，**保存自动建版本快照并重建索引**（标题/指纹/原子/双链/全文索引）；`[[双链]]` 在预览里渲染为**可点击链接**（未解析目标标红，点击提示去新建；编辑器保持源码原样）；`＋新建` / `Ctrl+S` 保存 / `Ctrl+N` 新建；右侧同步显示属性 / 原子 / 相关笔记 |
| ** 图谱** | 力导向知识图谱：实线=双链、虚线=语义关联（共享≥2 关键词自动补链）、**红色描边=孤立点**、颜色=主题；点节点打开笔记并累加记忆权重；**拖动节点实时跟随** |
| **🤖 AI** | 7 种模式 + 挂载体系 + 审阅队列，见下 |

### 各区域可操作能力

| 区域 | 操作 |
|---|---|
| **中间笔记区** | 预览/编辑切换 · 保存（版本快照+索引重建）· 新建 · `Ctrl+S`/`Ctrl+N` |
| **左侧文件树** | 双击打开 · **右键菜单**：新建 / 📌 发送到 AI 管家 / 重命名（同步改 H1+文件名+全表路径）/ 删除（清理所有表记录+失效相关建议）· **接受资源管理器拖拽文件/文件夹入库** |
| **右侧属性栏** | 元信息 · 原子片段 · **相关笔记可双击跳转** |
| **底部版本历史** | 打开笔记自动加载快照列表 · **选中可回滚**（回滚前先快照，索引同步重建） |
| **底部审阅队列** | 双链/校准建议逐条采纳·编辑·跳过 · 阈值批量采纳 · 重读校准 · 清理已处理 |
| **底部日志** | 实时滚动 · **清空按钮** |

### 右侧属性栏（三个内容框）

双击左侧文件树或点图谱节点打开笔记时，右侧**标题 + 三个内容框**同步刷新（并把该笔记的记忆权重 +1）：

| 框 | 内容 | 数据来源 |
|---|---|---|
| **① 元信息** | `路径 / 主题 / 类型 / 优先级 / 使用次数 / 原子数` | `vault_files` 表（含记忆权重 `usage_count`） |
| **② 原子片段** | 最多 15 条，格式 `[keyword]` `[claim]` `[tag]` `[topic]` | `atoms` 表——入库时抽取，**判断层只查这里、不重读原文** |
| **③ 相关笔记** | 最多 5 条相关笔记标题 | `KnowledgeSearch.related()`——优先双链邻居，其次共享关键词（来自图谱） |

三个框对应三种视角：

- **① 它是什么** —— 分类归属 + 被使用了多少次
- **② 它有哪些碎片** —— AI 后续判断（分类 / 去重 / 补链 / 问答）的依据
- **③ 它跟谁有关** —— 图谱邻居关系，可跳转

其中 **②「原子片段」是整个系统信条的落地**：AI 之后做的一切判断都消费这一框背后的数据，而非回读原始文档。



### 🤖 AI 管家（7 种模式）

| 模式 | 说明 | 写库？ |
|---|---|---|
| **问答** | RAG：检索相关原子 → 喂 LLM 作答，严格基于知识库、不编造 | 否 |
| **润色笔记** | 生成润色稿预览 → 点「应用」写回 | 是（快照） |
| **矛盾检测** | 与语义相关笔记两两对比，列出冲突点（issue / severity / 建议） | 否 |
| **摘要** | 基于已缓存原子生成 3 句摘要（不重读原文） | 否 |
| **生成双链** | 扫描正文产出 `[[双链]]` 建议，两档置信度：标题精确提及 0.9 / 共享关键词 0.6；排除自身、已链目标、主题名、歧义重标题、代码围栏内提及 | 建议→审阅 |
| **分类校准** | **只查分类缓存**（`topic_hits` + 原子佐证加权重算），全程零读原文；证据不足项标 `needs_reread`，经 dry-run 报数并显式同意后才重读 | 建议→审阅 |
| **发现关联** | 全库关键词共现 + 标题相似度产"关联对"，三档：**虚线升实线**（共享≥2，图谱已画虚线的对，置信≥0.75 可批量）/ **孤立救援**（一端零连接，封顶 0.65 强制逐条人工）/ 全库对（需勾选）；采纳 = 在低连接侧笔记追加 `[[目标]]` 成真双链（快照可回滚，图谱虚线升实线） | 建议→审阅 |

**笔记选择不靠下拉**：下拉降级为"最近 20 篇"快捷入口；主路径是输入框打 `@` 弹搜索挂载浮层（↑↓ 选择）、树/右键「📌 发送到 AI 管家」、挂载 chips（上限 3，× 取消）。问答时挂载笔记的原子**置顶进上下文**。一万篇规模同样可用。

**审阅队列**（底栏第三个 tab，带待处理角标）：策展建议全部落 `suggestions` 表逐条人工审——采纳 / 编辑后采纳 / 跳过；支持**按置信度阈值批量采纳**（逐条 tick、可随时取消、失败条留队列）；扫描后笔记内容改过（指纹不符）建议自动作废，绝不盲写。

**安全机制**：一切写回**强制先做版本快照**（底部"版本历史"可回滚）；审阅队列的人工采纳本身就是确认，不受 `HUMAN_CONFIRM_WRITES` 二次弹窗干扰。

**设置面板（⚙ 工具栏）**：生成后端模式（auto / Claude / **OpenAI 兼容** / 本地 Ollama / 离线 Mock）、API key、模型名——写回 `.env` 并**保存即生效**，无需手改文件、无需重启；含"重建搜索索引"自救入口（jieba 词表漂移后用）。无密钥时全链路离线可跑：双链生成、分类校准本就是规则引擎产物，问答也能返回真实检索片段。

---

## 架构

```
                    ┌────────────────────────────────┐
 输入渠道            │      IDE 风格桌面端 (PySide6)      │
 剪贴板 / 文件 /      │   左树 · 中工作区 · 右属性 · 底栏      │
 导入文件夹 / 拖拽 /   │   顶工具栏 · 常驻活动栏(可折叠)       │
  :watchdog 监听     │   (日志 · 版本历史 · 审阅队列)        │
                    └────────────────┬───────────────┘
                                     ▼
                    ┌────────────────────────────────┐
   入库────────────────────────────────────────────┐  │
      清洗 ──► 分类路由(判断,不重读) ──► 原子抽取 + 缓存      │  │
              │  topic/type/priority               │  │
              ▼                                    │  │
        增量合并 / 去重 / 补全 (LLMWiki 思想)          │  │
              │                                    │  │
              ▼                                    │  │
   图谱引擎(GBrain 思想) ◄── 双链+标签 ──► 原生 MD 落盘    │  │
   (节点/边/孤立点/记忆权重)                            │  │
                    └────────────────────────────────┘  │
                                     ▼                  │
               ┌─────────────────────┴──────────────┐  │
               ▼                                    ▼  │
        本地 Vault (.md)        SQLite (原子/图谱/分类/日志/快照/建议队列)
               │                          + notes_fts 全文索引(FTS5)
               │  RAG 检索（只取原子，不读全文）
               ▼
  策展 curator：双链生成/分类校准 ──► 审阅队列(人工采纳) ──► 快照写回
               ▼
        生成层多模型：mode 驱动 Claude / OpenAI兼容 / Ollama / Mock
```

**存储双轨**：Markdown Vault（知识本体，人可读可迁移）+ SQLite（图谱、原子索引、分类缓存、版本快照、日志、**建议队列 suggestions**、**全文索引 notes_fts/FTS5**）。

**检索与图谱（万篇规模的底）**：入库即分词写入 `notes_fts`，查询走 FTS5 bm25（3000 篇实测 p95 ≈ 23ms）；图谱语义边由 O(N²) 双循环改为**关键词倒排 + hub 桶封顶**（冷构建 0.15s），并按 `store.epoch` 写路径自动失效缓存——搜索/相关推荐/图谱共享同一份缓存。编辑、改名、回滚、AI 写回统一经 `pipeline.reindex_note` 重建元信息/原子/双链/全文索引。

---

## 目录结构

```
ai-kms/
├─ app/                      # 桌面端 (PySide6)
│  ├─ main_window.py         # IDE 风格主窗口（活动栏 / 工作区 / 折叠）
│  ├─ ui/graph.py            # 力导向知识图谱可视化 (QGraphicsView)
│  └─ icons/                 # 扁平 SVG 图标（currentColor 着色）
├─ core/
│  ├─ ingest/                # 收录管线
│  │  ├─ pipeline.py         # 清洗→分类→原子→去重→落盘 编排 + reindex_note + FTS 对账重建
│  │  ├─ cleaner.py          # Markdown 清洗 / 去冗余
│  │  └─ watcher.py          # watchdog 监听自动收录
│  ├─ classify/              # 判断层（jev 思想）
│  │  ├─ router.py           # 判断中枢：一次分类终身复用
│  │  ├─ rules.py            # 规则集分类器（MVP 默认）
│  │  └─ providers.py        # 分类器接口 + 多模型预留
│  ├─ atoms/                 # 原子抽取 + 只查缓存索引（精确倒排补链推荐）
│  ├─ graph/                 # GBrain 思想
│  │  ├─ engine.py           # 节点/边/孤立点/记忆权重（倒排语义边 + epoch 缓存）
│  │  └─ search.py           # FTS5/BM25 检索 + 相关推荐（预留语义向量）
│  ├─ curator.py             # ★知识策展人：双链生成 / 分类校准 / 建议应用（纯规则离线可用）
│  ├─ export.py              # ★结构导出：JSONL/graph.json/manifest + 全库 zip（确定性排序）
│  ├─ mcp_server.py          # ★MCP 只读服务：KmsTools 纯函数 + MCPServer 薄壳（stdio·ro 直连）
│  ├─ generative/            # 生成层（多模型）
│  │  ├─ provider.py         # mode 驱动：auto/claude/openai/ollama/mock，保存即生效
│  │  └─ tasks.py            # 摘要 / QA(RAG+挂载置顶) / 润色 / 矛盾检测
│  ├─ aipilot/               # AI 管家
│  │  ├─ manager.py          # 对外能力：ask / polish / apply_change
│  │  └─ snapshot.py         # 版本快照 + 回滚 + 人工确认
│  └─ storage/
│     ├─ vault.py            # 原生 MD Vault + 双链/标签解析
│     ├─ fts.py              # ★分词单一事实源（索引/查询共用，jieba 预分词）
│     └─ sqlite_db.py        # SQLite 8 表 + FTS5 虚表 + suggestions 队列 + epoch
├─ config/settings.py        # 路径 / 模型 / 开关 + .env 写回
├─ tests/                    # 108 例：管线/导入/FTS/建议队列/图谱/检索/策展×2/设置/只读/导出/MCP
├─ data/                     # 运行时（vault / kms.db / dropbox）
├─ main.py                   # CLI + GUI 入口
├─ requirements.txt
└─ README.md
```

---

## 核心模块速查

| 模块 | 职责 |
|---|---|
| `core/ingest/pipeline.py` | 收录编排：清洗 → 分类 → 原子 → 去重 → 落盘 |
| `core/classify/router.py` | **判断中枢**，一次分类终身复用（缓存命中即返回） |
| `core/classify/providers.py` | 分类器接口，预留"只判断"分类器模型 / 多模型 |
| `core/atoms/extractor.py` | 关键词 / 命题 / 标签原子抽取 |
| `core/atoms/index.py` | 只查缓存的原子索引（去重、补链、检索复用；关键词精确倒排） |
| `core/graph/engine.py` | 图谱引擎：双链 + 语义补链（倒排建边）+ 孤立点 + 记忆权重 + epoch 缓存 |
| `core/graph/search.py` | FTS5/BM25 检索 + 相关推荐（fts 不可用时自动降级旧路径） |
| `core/curator.py` | **知识策展人**：双链生成、分类校准（纯缓存）、建议应用（快照+重索引） |
| `core/export.py` | 结构导出：派生资产 JSONL + graph.json + manifest，全库打包 zip |
| `core/mcp_server.py` | MCP 只读服务：search/atoms/classify_hint/graph_neighbors 四工具，ro 直连主库 |
| `core/storage/fts.py` | 分词单一事实源——索引与查询必须同词表（jieba 预分词 → FTS5 unicode61） |
| `core/generative/provider.py` | 生成层多模型，Claude → OpenAI 兼容 → Ollama → Mock 降级 |
| `core/generative/tasks.py` | 摘要 / RAG 问答 / 润色 / 矛盾检测 |
| `core/aipilot/manager.py` | AI 管家对外能力 |
| `core/aipilot/snapshot.py` | 版本快照 + 回滚 + 人工确认开关 |
| `core/storage/vault.py` | 原生 Markdown Vault，双链 / 标签解析 |
| `core/storage/sqlite_db.py` | SQLite：文件/原子/双链/分类/快照/日志/分类体系/建议队列 + FTS5 + 记忆权重 + epoch |

---

## 配置

```bash
# 复制模板并按需填密钥
cp .env.example .env
```

| 变量 | 说明 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API（生成层，推荐） |
| `CLAUDE_MODEL` | 模型 id |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | 本地 Ollama（可选替代，model 填了才启用） |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` | **OpenAI 兼容接口**：DeepSeek、Kimi、智谱、vLLM、one-api 网关等；base_url 留空=官方，model 填了才启用，本地网关 key 可空 |
| `KMS_PROVIDER_MODE` | `auto`（默认）/ `claude` / `openai` / `ollama` / `mock` |
| `CLASSIFY_API_KEY` / `URL` | 预留：外部"只判断"分类器 |

**判断层（分类路由）无需任何密钥**——内置规则集离线运行；**生成双链 / 分类校准本就是规则引擎产物，Mock 下也出真实可用建议**。生成层无密钥时自动降级 MockProvider，界面与全流程照常可用。

> 这些变量**不必手改 `.env`**：桌面端「⚙ 设置」面板填完保存即写回 `.env` 并即时生效。

`config/settings.py` 可调：Vault 路径、主题词表、`WATCHER_ENABLED`、`HUMAN_CONFIRM_WRITES`（AI 写回是否需确认）、`GENERATIVE.disabled`。

---

## 资产开放：导出 与 外部 AI 调用（MCP）

> "一次解析、原子沉淀、终身复用"——沉淀出的资产也要**搬得走、被调用**。

**结构导出**（GUI 工具栏「导出」或 CLI，两种模式）：

- `structure`：`manifest.json` + `structure/{files,atoms,links,classifications,categories,suggestions}.jsonl` + `graph.json`——AI 读出来的派生数据（原子/分类/双链/图谱/审阅队列）全量导出；
- `full`：再捆绑 vault 全部 `.md` 原文打成单个 zip（内部布局与目录模式逐字一致，解压即用）；
- 约定：path 一律 vault 相对 POSIX；自增 id 不进资产；排序确定——同一库两次导出逐字一致；`mcp`/`mcp.log` 之外零新依赖。

**MCP 只读服务**（`python main.py mcp`，stdio）——外部 AI（Claude Code / Cursor / 任意 MCP 客户端）把知识库当便宜工具调：查询只命中缓存原子、**不重读原文**，返回体小、不烧 API：

| 工具 | 职责 |
|---|---|
| `kms.search` | FTS5/BM25 检索（未灌词自动降级旧路径），返回 path/title/topic/score/backlinks/neighbors |
| `kms.atoms` | 某笔记的缓存原子按 kind 分组（keyword/claim/tag/topic + 权重） |
| `kms.classify_hint` | 分类缓存查询——**纯缓存，绝不触发任何 AI 分类/API 调用** |
| `kms.graph_neighbors` | 图谱直接邻居（双向，含对方 title/topic） |

**接入**——把示例中的 `<安装路径>` 换成你机器上 `ai-kms` 目录的实际位置。MCP 客户端独立启动服务进程、不继承任何工作目录，因此**必须写绝对路径**：

```bash
# Claude Code 一行接入
claude mcp add kms -- python "<安装路径>/ai-kms/main.py" mcp
```

```text
路径示例
  Windows : C:/Users/<你>/ai-kms/main.py
  macOS   : /Users/<你>/ai-kms/main.py        （command 建议用 python3）
  Linux   : /home/<你>/ai-kms/main.py         （command 建议用 python3）
```

项目级 `.mcp.json`（或 Cursor 的 `~/.cursor/mcp.json`）同构：

```json
{ "mcpServers": { "kms": {
    "command": "python",
    "args": ["<安装路径>/ai-kms/main.py", "mcp"] } } }
```

**只读纪律**：MCP 进程以 `mode=ro` 直连主库——GUI 开着也能同时读写互不阻塞（WAL）；本期不开放任何写接口，AI 写回仍只走库内"快照 + 审阅队列"。调用审计在 `data/logs/mcp.log`。注意：graph 缓存存活于 MCP 进程内，主库有大量更新后重启该进程即可看到最新图谱形态（其余查询实时读已提交快照）。

---

## 测试

```bash
python -m pytest tests -q            # 108 例
```

| 测试文件 | 覆盖面 |
|---|---|
| `test_pipeline.py` | 清洗 / 分类 / 原子 / 入库 / 去重 / 监听 |
| `test_e2e.py` | 全流程 + 快照回滚 |
| `test_import.py` | 导入枚举白名单 / 路径归一 / 幂等 |
| `test_fts.py` / `test_suggestions.py` | 全文索引级联·对账·降级 / 建议队列 CRUD |
| `test_graph_engine.py` / `test_search_router.py` | 倒排建边等价·桶封顶·epoch 缓存 / 检索结构·分类器修复 |
| `test_curator_wikilinks.py` / `test_curator_topic.py` | 双链生成两档·围栏保护·stale 防护 / 纯缓存校准·**扫描零读盘断言** |
| `test_settings_env.py` | .env 保注释改写 / Provider mode 矩阵 |
| `test_readonly_store.py` / `test_export.py` / `test_mcp_tools.py` | 只读直连·写守卫·裸 SQL 契约 / 导出口径·容错·zip 布局 / MCP 四工具 + 服务薄壳冒烟 |

当前状态：**108/108 通过**。3000 篇规模实测：检索 p95 ≈ 23ms、图谱冷构建 0.15s、全库分类校准（纯缓存）约 7s。

---

## 技术栈

| 层 | 选型 |
|---|---|
| 界面 | PySide6 (Qt6)，自包含桌面 |
| 存储 | Markdown Vault + SQLite (WAL) + FTS5 全文索引 + suggestions 审阅队列 |
| 判断层 | 规则集 + 分类器接口（预留模型替换）+ curator 纯规则策展 |
| 生成层 | Claude API / OpenAI 兼容（DeepSeek·Kimi·vLLM 等）/ 本地 Ollama / Mock，mode 驱动、保存即生效 |
| 检索 | FTS5 bm25（jieba 预分词，索引/查询同源）+ 图谱倒排建边 + epoch 缓存（向量语义检索为**条件触发增强**，见「发布节奏」） |
| 监听 | watchdog |
| 依赖 | 仅 `PySide6 · watchdog · requests · python-dotenv · jieba`（`mcp` 仅 MCP 服务命令需要） |

---

## 打包发布（exe）

入口 `kms_entry.py`（双击直进 GUI），构建脚本 `build.py` 已内置依赖裁剪与数据文件收集：

```bash
pip install pyinstaller          # 或 pip install nuitka
python build.py pyinstaller      # 日常/内测：onedir，几分钟出包
python build.py nuitka           # 商用发布：编译成 C（启动快、源码不再是可拆字节码）
python build.py nuitka --onefile # Nuitka 单文件 exe
```

**策略**：开发期用 PyInstaller onedir（钩子成熟、构建快）；正式商用发布切 Nuitka（真编译，代码保护强一个量级）。PySide6 打包后 80~170MB 属正常。

**macOS/Linux 预埋（已完成，代码级跨平台就绪）**：
- 平台标准数据目录：exe 不可写时 Win→`%LOCALAPPDATA%`、Mac→`~/Library/Application Support`（天然避开 iCloud 同步 × SQLite-WAL 冲突）、Linux→XDG
- 快捷键用 `QKeySequence` 标准键，Mac 自动 ⌘S/⌘N/⌘W；提示文案随平台显示
- 无边框自绘标题栏仅 Windows 启用；Mac/Linux 用原生标题栏（流量灯归系统）
- 品牌图标单一 SVG 源：`python tools/make_icons.py` 一键产出 PNG 全尺寸 + `.ico`（Windows）+ `.iconset`（Mac 上再跑 `iconutil` 得 `.icns`）


**数据位置三形态**（`settings` 路径体系，自动决策 + 显式覆盖）：

| 形态 | 条件 | 用户文件落点 |
|---|---|---|
| 便携（默认） | exe 所在目录**可写** | `<exe同级>\data\`——整个文件夹拷走即迁移 |
| 受保护安装 | exe 不可写（如 Program Files） | 自动降级 `%LOCALAPPDATA%\AI-Native KMS\data\`，避开 UAC VirtualStore 黑洞 |
| 自定义 | `.env` 写 `KMS_DATA_ROOT=D:/MyKnowledge`（或设置面板"更改位置…"） | 任意目录；`KMS_VAULT_PATH` 可让知识文件单独放（如"文档"），与索引分离 |

设置面板"更改位置…"会写入 `.env` 并可选**迁移 vault/dropbox**（索引不迁，重启后启动对账自动重建），重启生效。`.env` 也随之落在可写基地（`APP_HOME`）。

**已实测**（PyInstaller 6.22 / Python 3.12 / Windows 10）：
- 产物 `dist/AI-Native-KMS/`（173MB，含 `AI-Native-KMS.exe`），jieba 词典、SVG 图标均在包内
- 双击 exe 正常启动，`data/`（kms.db + vault + dropbox + logs + snapshots）生成在 exe 同级 ✓
- GUI 链路自动排除 `mcp/pydantic/WebEngine/Qml` 等 22 个重型模块
- 回归：源码态 108/108 测试不受影响

---

## 发布节奏（版本视角）

> 已完成阶段（0-10）的逐条记录见顶部「[进度](#进度)」，此处只维护未来向。

- [ ] **v1.0 = 当前（✅ 发现关联已落地）** —— 收录闭环 + 图谱检索（FTS5/倒排/epoch 缓存）+ AI 管家（7 模式 / 挂载 / 审阅队列）+ 资产开放（结构导出 + MCP 只读）。133 例测试全绿、万篇规模实测达标。
- [ ] **v1.1** —— 批量润色整理（灵魂依赖真实 LLM，未接密钥前不放，避免空壳承诺）、挂载拖拽（交互补全）
- [ ] **随时可做（不占版本位）** —— 真实 LLM 接入与调优：设置面板已就绪，密钥即插即用；接入后重点验证润色 / 矛盾检测 / 摘要的真实效果，效果数据反过来决定 v1.1 的取舍
- [ ] **后续** —— 知识复盘 Agent、多 Vault 同步、外部改 vault 文件的双向同步
- [ ] **（条件触发·不排期）向量语义检索** —— 满足以下任一条件前不做，届时再立项评估：
  ① 日常使用中出现明确的**同义词/近义检索 miss**（FTS5+关键词倒排"该找到的没找到"，且无法靠补分类关键词解决）；
  ② 发现关联上线后，词法召回在数百篇规模下**孤立点补边明显不足**。
  届时的实现约束：作为 FTS 之外的**第二路召回、不替换词法检索**（保留建议的可解释性，配合审阅队列）；优先 sqlite-vec + 本地轻量嵌入模型，依赖增量最小；模型换代等于全量重嵌入，须按"一次解析终身复用"原则做嵌入版本化管理。

---

## 许可

本项目以 **GNU AGPL-3.0-or-later** 发布（官方全文见仓库根目录 [`LICENSE`](LICENSE)）。

- **免费与自由**：本地个人使用、学习、修改、自托管均无任何限制——这正是"数据不锁定"的代码层兑现。
- **Copyleft 约束**：分发本项目（含打包分发二进制），或修改后通过网络对外提供服务，都必须以 AGPL 同步开源完整对应源码（AGPL §13）。
- **商业双授权**：桌面本体永久免费开源；Sync / 多设备 / 团队等增值能力，或需要闭源嵌入、行业定制交付的场景，可另行洽谈商业授权（联系方式：见仓库主页）。作者保留全部版权，双授权不设 CLA 门槛。

**开源核心边界**：后续付费增值模块会在目录/仓库层面物理隔离——**删掉付费部分，开源部分仍是完整可用的系统**；开源主干不会倒退成功能残缺的试用版。

**第三方致谢**（依赖均为宽松许可，与 AGPL 兼容）：

| 依赖 | 许可 |
|---|---|
| PySide6 (Qt6) | LGPL-3.0 / GPL-2.0 / GPL-3.0 三重许可（pip wheels 动态链接，按 LGPL 合规） |
| watchdog / requests | Apache-2.0 |
| python-dotenv | BSD |
| jieba / mcp（官方 SDK） | MIT |

> 思想来源（Obsidian / LLMWiki / GBrain / Claude / jev）仅为设计理念借鉴，代码库不含其任何实现或接口绑定。
