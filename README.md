Novel2Wiki
 
【v0.1 概念原型】
基于大模型流水线，将小说文本自动构建维基风格静态站点。优先锁定事实、后置文本润色，系统性抑制大模型幻觉。
 
An LLM pipeline for building Wikipedia-style static sites from novels. v0.1 concept prototype.
Fact-first & polish-later architecture to systematically reduce LLM hallucinations.
 
📖 项目介绍 | Introduction
 
中文
Novel2Wiki 是一套面向虚构叙事的结构化抽取与维基生成流水线。
不同于常规一键式 LLM 生成方案，本项目采用先结构化固化事实、后润色表达的双层架构：
 
1. 依据自研叙事 Schema，对小说全文进行实体、状态、事件、人物互动、时间线结构化抽取
2. 生成可溯源、强约束的机械初稿，保证所有内容绑定原文章节与引语
3. 两轮独立 AI 润色与抽样校验：仅优化措辞，不改动任何事实
 
本项目重点解决 AI 叙事创作普遍存在的脑补、冲突、溯源缺失问题，让小说衍生百科内容具备可校验、可复盘、可迭代的能力。
 
⚠️ 当前为 v0.1 早期原型
Schema 整体框架成型但仍较简陋，抽取存在漏识别、误判、细节缺失。
仓库内置样例为自动生成结果，不适合作为 Few-shot 标准答案。
仅用于技术展示与学术交流，不可用于生产环境。
 
English
Novel2Wiki is a structured extraction and wiki generation pipeline for fictional narratives.
 
Different from conventional one-shot LLM generators, this project adopts a strict fact-first, polish-later design:
 
1. Extract entities, states, events, interactions and timelines into structured JSON with custom narrative schema
2. Generate fully traceable mechanical drafts bound to original chapters and quotes
3. Apply two-stage AI polishing and sampling validation: only wording is modified, facts remain untouched
 
This project targets hallucination, logical conflict and untraceable generation in traditional novel-wiki solutions.
 
⚠️ v0.1 EARLY PROTOTYPE
The schema framework is functional but primitive.
Built-in demos are auto-generated data with imperfections, not for LLM ground-truth training.
For demonstration & research only, not production-ready.
 
✨ 核心特性 | Key Features
 
中文
 
- 自研叙事体系：四大实体表 + 非对称多视角互动表 + 章节时序时间线
- 事实锁定机制：所有词条内容可回溯原文段落、章节区间、原始引文
- 双层防幻觉校验：机器规则校验 + AI 抽样复核 + 叙事矛盾队列研判
- 增量式流水线：支持断点续跑、增量更新章节，无需全局重跑
- 输出完整静态 HTML 维基站点，本地可直接浏览，无需服务端部署
- 内置标准测试样例：《孔乙己》完整抽取结果、《红楼梦》前二十回抽取案例
 
English
 
- Custom narrative structure: 4 core entity tables + asymmetric multi-view interaction table + chapter-based timeline
- Full traceability: every statement links to original chapters and quotes
- Dual anti-hallucination validation: rule-based check + AI sampling review + conflict resolution queue
- Incremental pipeline: resumeable tasks, incremental updates without full reprocessing
- Output pure static HTML wiki, fully local and serverless
- Built-in test cases: Kong Yiji, Dream of the Red Chamber (Chapter 1–20)
 
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
Please retain the original license and copyright notice.# Novel2Wiki
Use LLMs to convert fiction novels into Wikipedia-style static sites. Early v0.1 concept prototype. Built around a custom narrative schema, with fact-first extraction, multi-perspective event recording and double-layer validation to reduce LLM hallucinations.（just an idea）
