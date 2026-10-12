Novel2Wiki

【v0.1 概念原型】
基于大模型流水线，将小说文本自动构建维基风格静态站点。优先锁定事实、后置文本润色，系统性抑制大模型幻觉。

An LLM pipeline for building Wikipedia-style static sites from novels. v0.1 concept prototype.
Fact-first & polish-later architecture to systematically reduce LLM hallucinations.

📦 下载 | Download

最新版本见 Releases 页：<https://github.com/bcosn/Novel2Wiki/releases/latest>

压缩包内含 `SKILL.md`、`scripts/`、`schema/`、`example1/`（两组走完全流程的示例）。
规范与流程也可以直接在仓库里读，不必下载：

- [schema/维基风格规范.md](schema/维基风格规范.md) —— 条目语言与站点界面的硬约束
- [SKILL.md](SKILL.md) —— 六步流程与迭代跑法

📖 项目介绍 | Introduction

中文
Novel2Wiki 是一套面向虚构叙事的结构化抽取与维基生成流水线。
不同于常规一键式 LLM 生成方案，本项目采用先结构化固化事实、后润色表达的双层架构：

1. 依据自研叙事 Schema，对小说全文进行实体、状态、事件、人物互动、时间线结构化抽取
2. 生成可溯源、强约束的机械初稿，保证所有内容绑定原文章节与引语
3. 两轮独立 AI 润色与抽样校验：仅优化措辞，不改动任何事实

本项目重点解决 AI 叙事创作普遍存在的脑补、冲突、溯源缺失问题，让小说衍生百科内容具备可校验、可复盘、可迭代的能力。

English
Novel2Wiki is a structured extraction and wiki generation pipeline for fictional narratives.

Different from conventional one-shot LLM generators, this project adopts a strict fact-first, polish-later design:

1. Extract entities, states, events, interactions and timelines into structured JSON with custom narrative schema
2. Generate fully traceable mechanical drafts bound to original chapters and quotes
3. Apply two-stage AI polishing and sampling validation: only wording is modified, facts remain untouched

This project targets hallucination, logical conflict and untraceable generation in traditional novel-wiki solutions.

⚠️ 当前为 v0.1 早期原型

Schema 整体框架成型但仍较简陋，抽取存在漏识别、误判、细节缺失。
仓库内置样例为自动生成结果，不适合作为 Few-shot 标准答案。
仅用于技术展示与学术交流，不可用于生产环境。

⚠️ v0.1 EARLY PROTOTYPE
The schema framework is functional but primitive.
Built-in demos are auto-generated data with imperfections, not for LLM ground-truth training.
For demonstration & research only, not production-ready.

📂 仓库结构 | Repository Layout

```
SKILL.md              六步流程、迭代跑法、目录说明
schema/维基风格规范.md  条目语言 + 站点界面，改稿改版式都照这份
assets/wiki.css       站点样式，渲染时复制到站点目录
scripts/              15 个脚本（解析、审查、抽取校验、渲染）
schema/               各步的字段规范
  schema_第二步.md     四表字段、层级规则
  schema_第三步.md     时间线字段、范围写法
  schema_第四步.md     追加规则、矛盾研判
  schema_第五步.md     条目字段、评价来源
  schema_互动.md       互动表（主体间互动）
  schema_动态属性.md   按作品动态添加属性
example1/             两组走完全流程的示例
  孔乙己_wiki/         10 条条目，多页站点
  红楼梦_wiki/         104 条条目，多页站点
  迭代示例.md          两轮 AI 抽样审批的完整实录
```

✨ 核心特性 | Key Features

中文

- 自研叙事体系：四大实体表 + 主体间互动表 + 章节时序时间线 + 按作品动态属性表
- 事实锁定机制：所有词条内容可回溯原文章节与原始引文，引文逐字回原文核验
- 双层防幻觉校验：机器规则校验 + AI 抽样复核两轮（两轮样本不重叠，验证修正是否推广开）
- 矛盾研判队列：四类性质判定（原文自身前后不一 / 前面抽错了 / 看似矛盾实为不同事 / 同一实体异写），修正类结论不自动应用，须人工复核
- 增量式流水线：支持断点续跑、增量更新章节，无需全局重跑
- 输出完整多页静态 HTML 维基站点（首页 + 每词条一页 + 共用样式），本地可直接浏览，无需服务端部署
- 内置标准测试样例：《孔乙己》10 条、《红楼梦》前二十回 104 条

English

- Custom narrative structure: 4 core entity tables + intersubjective interaction table + chapter-based timeline + per-work dynamic attributes
- Full traceability: every statement links to original chapters and quotes; quotes verified verbatim against the source
- Dual anti-hallucination validation: rule-based check + two rounds of AI sampling review (disjoint samples, to verify fixes generalize)
- Conflict adjudication queue: four verdict kinds (source self-contradiction / earlier extraction error / apparent conflict, actually different things / same entity, variant name); corrections are never auto-applied
- Incremental pipeline: resumeable tasks, incremental updates without full reprocessing
- Output pure static multi-page HTML wiki, fully local and serverless
- Built-in test cases: Kong Yiji (10 entries), Dream of the Red Chamber (Chapter 1–20, 104 entries)

🔍 已知局限 | Known Limitations

中文

- **字数阈值判据可以被绕过。** 用「与原文连续重合 N 字」当门槛，改稿方会靠插字、删虚字、换词把串压到阈值以下（实践中从 10 字绕到 6 字、又从 6 字绕到 5 字）。脚本只能提示线索，判断「这是不是原文改字」最终必须靠 AI 阅读。
- **抽取质量未经验证。** 抽取存在漏识别与误判，示例数据仅供流程演示。
- **示例规模有限。** 长篇只做到第 20 回，增量追更与剧透控制尚未实测。

English

- **Character-count thresholds are gameable.** Using "overlap of N consecutive characters with the source" as the gate invites padding, function-word deletion or synonym swaps to slip under it (in practice: from 10 down to 6, then to 5). Scripts can only surface leads; deciding whether a phrase is a rewrite of the source ultimately requires AI reading.
- **Extraction quality is unvalidated.** Missed entities and misjudgements exist; demo data is for pipeline illustration only.
- **Limited demo scope.** The long-form demo stops at Chapter 20; incremental updating and spoiler control remain untested.

📌 迭代规划 | Roadmap

中文
当前 v0.1 原型包含大量自研简易脚本，用于验证整套流程可行性。
后续版本将逐步工程化迭代：

- 替换自研解析模块为成熟专业文本清洗、电子书解析工具
- 接入标准化 NLP 抽取能力优化实体与事件识别精度
- 替换简易渲染模板为专业静态站点生成器

项目长期核心资产保持不变：叙事 Schema、多视角互动模型、矛盾研判规则、双层校验反幻觉流水线。

English
The current v0.1 prototype uses homemade scripts for verification purposes.
Future versions will replace temporary components with professional tools:

- Standard book parsing & text cleaning tools
- Optimized NLP extraction modules
- Mature static site generators for better wiki rendering

Core innovations will remain unchanged: custom narrative schema, asymmetric interaction modeling, conflict judging system, dual-layer anti-hallucination pipeline.

📄 License

This project is open-sourced under the MIT License.

Permissive, free for personal, academic and commercial use, modification and redistribution.
Please retain the original license and copyright notice.
