# Novel2Wiki · v0.3

给 AI 使用的小说百科生成 skill。先抽取人物、地点、物品、互动与状态变化，
再用作品外的第三人称写百科条目，输出可在本地打开的静态 Wiki。

An AI skill for turning fiction into source-backed encyclopedia entries and local static wiki pages.

## v0.3：内核鲁棒性与成本控制

- SQLite 保存正式数据、不可变版本、稳定记录 ID、幂等批次和可恢复任务；JSON 保留为交换与导出格式。
- lxml 解析现有 XML/HTML，charset-normalizer 辅助编码探测；格式转换后端保持原样。
- 子代理默认关闭；任务按路径、hash 和范围派发，结果直接落盘，由程序提交和合并。
- 图片与书评独立导入、关联、更新、删除，数据库渲染入口支持离线展示，重生成不覆盖用户内容。
- 三份网上作品的 800 字节选用于回归，不是模型训练数据；新测试包含并行提交与强制退出恢复。
- 专用 DSH headless overlay 精简插件与工具，固定可复用上下文，提供请求审计和用量去重统计。
- 流程修补：prepare-draft 提供有界证据包；程序复制引文并建立编号，finalize 返回局部修复包、校验完整输出缓存，resume 恢复已提交稿件。
- 继续修补：单次读取规范与证据；局部更新按ID合并，误删旧条目会被拦截，结构及跨批次引用错误集中报告。compact-v1模板为实验选项，真实对照未证实省耗，默认仍使用原lead格式。

新增可选[条目规划](docs/entry-planning.md)：先按原文判断重要性、去向与主题，再写正文；种族和设定可建公共页，有据关系连接人物、事件与类别，不预设属性名、品阶或成员。

当前开发版通过180项程序测试；整体模型省耗与语义质量尚未验收通过。接口与恢复功能通过不代表事实和风格完全正确。
本轮调试修复时间范围泄漏、未发布页计划恢复、特殊标题链接、锚点碰撞及同名预检，见[调试日志](docs/v0.3-debug-log.md)。
写作提示词已做两轮跨作品小样本工程与匿名分项核对，新候选仍有事实错误，默认保留legacy；quality-v2为显式实验选项，见[提示词与质量验收](docs/prompt-quality.md)。

先运行 `python -m pip install -r requirements-lock.txt`。
用法：[工作区说明](docs/v0.3-workspace.md) · [看板](docs/v0.3-board.md) · [开发记录](docs/development-notes.md) · [验证结果](docs/v0.3-validation.md)。
显式有据的关系链接已支持；自动关系建议留到 v0.3fix。小模型留到 v0.5；格式后端升级和客户端分发留到 v1.0。
条目规划两轮同篇短文试验均增加费用且仍有覆盖、标题与归属问题，保留显式选项，见[完整结果](docs/benchmarks/2026-10-15-entry-planning/README.md)。

## 演示视频

https://github.com/user-attachments/assets/3091fdab-414f-4c4f-b55b-f4f779e9d1e8

[观看演示视频](https://github.com/user-attachments/assets/3091fdab-414f-4c4f-b55b-f4f779e9d1e8)（57 秒，720p） · [下载 MP4](docs/novel2wiki-demo.mp4?raw=true)

## 本版修复

- **界面**：默认采用 Vector 2022 风格。左侧可点击目录、正文内信息框、右侧工具与字号/宽度控制；
  手机目录默认收起，参考资料点击编号展开。
- **语言**：统一第三人称，明确叙述者、人物言语、传闻和推测的归属。无名叙述者不猜填性别。
  正常代词、标点、专名与事实短语不以重复或字面重合清零为验收门槛。
- **成本**：独立审查改成作品级参数，跨步骤、批次和续跑累计；审查包限制样本和上下文，
  使用台账防止重复领取。正式条目在目标阅读范围完成后集中写，不反复全库扩写。
- **校验**：修复待复核事项被覆盖、跨来源拼接引文误通过、空示例误通过；
  检查节级 refs、所标原文章节、时间线收口、页面文件与锚点。
- **回归**：包含自动测试和 GitHub Actions；《孔乙己》《红楼梦》多页示例已重渲染。

本版仍是概念原型。脚本通过可以证明结构与字面对应，不代表转述意义全部正确；
示例不是事实标准答案；已完成一次前三十章的版本对照，抽取准确率与整本小说费用尚未做基准测量。

## 对照试验报告

[某小说前三十章：v0.2 与 v0.2fix 对照报告](docs/benchmarks/2026-10-14-v0.2-v0.2fix/README.md)（2026-10-14），包含桌面与手机截图、匿名摘要数据和测试方法。

本次统一单价后的估算费用降低 83.23%，千字正文成本降低 64.45%；正文同时缩短 52.83%，
语义抽查仍发现主语错配和无依据扩写。结果仅适用于这次单次试验，不代表等质节省或通用降幅。

## 使用

把本目录作为 skill 交给 AI，入口是 [SKILL.md](SKILL.md)。
需要 Python 3.10+、lxml 和 charset-normalizer，SQLite 使用标准库。Calibre 仅为旧版 doc 与部分 azw3 的可选依赖。

流程与标准按需读取，不要每次加载全部文件：

- [SKILL.md](SKILL.md)：流程、参数、停止条件
- [百科语言与界面](schema/维基风格规范.md)：第三人称、归属、现代书面语、Vector 布局
- [审查配置](schema/审查配置.md)：省钱/平衡/严格模式与台账操作
- schema/：四表、互动、时间线、动态属性、追加与条目字段
- example1/：公版作品流程示例
- example1/迭代示例.md：旧版本返工实录，不是本版固定审批流程

### 审查参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| mode | balanced | economy / balanced / strict |
| max_reviews | 0 / 1 / 2 | 对应模式的独立审查调用总上限，可显式覆盖 |
| sample_size | 4 | 每次最多抽多少条 |
| context_chars | 12000 | 序列化审查包字符上限，含指令、条目和引文 |

```powershell
# 省钱：只由当前写作 AI 自查，不另起审查
python scripts/review_plan.py data/书.entries.json --mode economy --state data/书.review-state.json -o data/书.review-packet.json

# 平衡：最多一次独立抽样审查
python scripts/review_plan.py data/书.entries.json --mode balanced --sample-size 3 --context-chars 8000 --state data/书.review-state.json -o data/书.review-packet.json

# 严格：发现内容问题才使用第二次，总上限为两次
python scripts/review_plan.py data/书.entries.json --mode strict --max-reviews 2 --state data/书.review-state.json -o data/书.review-packet.json
```

生成包之后先用 --claim 领取，再调用审查 AI，完成后 --record-result 记录结果。
同一作品始终使用同一个 state；样本未变时复用 pending，已领取但未记录时拒绝新包。
脚本本身不调用模型，不测量实际人民币支出。次数与字符限制需要执行 skill 的 AI 遵守，
不是服务端计费硬限额。不要删除或重建台账来绕过限制。

### 渲染与检查

```powershell
python scripts/quote_source_check.py data/书.entries.json --chapters out/书.chapters.json
python scripts/check_voice.py data/书.entries.json
python scripts/mos_check.py data/书.entries.json
python scripts/render_wiki.py data/书.entries.json -o 书_wiki --work 书 --unit 章
```

双击 书_wiki/index.html 即可浏览；不加载外部字体、图片、脚本或 API。
讨论、编辑、历史仅为静态站点的不可用标签。目录、参考资料、外观选项可用。

```powershell
python -m unittest discover -s tests -v
python scripts/verify_example.py example1 --book 孔乙己
python scripts/verify_example.py example1 --book 红楼梦
```

可选的 check_style / check_duplication / original_overlap_check 提供线索，
不因普通措辞重合或必要主语重复要求全库重写。

## 打包

仓库的 novel2wiki.zip 与源码版本一致，包含 skill、脚本、规范、样例和测试；
不含用户书籍、DSH session、私人测试输入、日志或运行台账。
GitHub 的源码 ZIP 也可使用，入口同样是根目录 SKILL.md。

## 已知限制

- 事实归属和转述含义不能仅由字面检查证明；economy 模式保留当前写作 AI 的自查责任。
- 多人物与代词共现只是歧义线索，不按正则强行判错；无名角色与专名仍需结合上下文。
- PDF 排版恢复、旧电子书压缩格式与章节切分仍存在启发式局限。
- 旧示例仅演示流程，尚未逐条重写为本版的最佳百科语言。
- 审查设置控制独立审批调用，不能限制执行平台的底层模型步骤、思考长度或工具调用费用。
- 没有整本小说在相同模型、范围与价格下的对照运行，不承诺固定费用或节省比例。

MIT License。
