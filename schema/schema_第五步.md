# 第五步：撰写百科条目 · v0.2fix

消费已有四表、时间线、互动与证据，按主体和主题组织内容。
使用 [维基风格规范](维基风格规范.md) 第一节；不加载历史审批实录作为当前流程。

## 范围与选择

短篇用全文，长篇用用户要求且已读完的范围。正式写作前先确定条目清单与材料分工。
优先主要人物、核心地点、关键物品和重要事件；提及一次的背景实体可保留在表中，
没有足够材料不必单独建页。用户要求全量时遵照用户要求。
中途样稿只用于校准语气，不先写全库再统一扩写。

## 字段

顶层 meta 使用 book、author、scope；电子书的 creator 对应这里的 author。

| 字段 | 说明 |
| --- | --- |
| id | 稳定标识，如 e-0001 |
| kind | character / place / object / event / race |
| name | 条目名；无名第一人称角色可用“叙述者”，原称记于说明 |
| aliases | 可确定的称呼类异名，不收“师兄”“校长”等跨实体泛称 |
| lead | 作品外第三人称，首句说明作品与身份 |
| lead_refs | 首段引用的 sources 编号，1 起 |
| sections | [{heading, body, refs}]，主题式节标题 |
| sources | [{chapter, cite, quote, note}]，一个引文对应一个可定位的来源 |
| reception | {in_work, user, external}；无资料留空 |
| reception_refs | 可选，{in_work: [来源编号]}，作品内评价的出处 |
| links | 其他条目的 id |
| stub | 材料不足时为 true，不能为了长度编造 |
| note | 可选，底本、范围等读者需要的说明，不写工序 |

事件可附 chapter、participants、place。所有 participants 必须是明确主体。
每个正文段的事实需要证据，refs 指向本节使用的 sources，不能借别节的引文通过检查。

sources 可选记录 speaker、perspective、certainty；
perspective 为 narrator / character / unknown，certainty 为 asserted / reported / inferred / dream。
无依据的归属不填，正文说明说话人不明。标记不是事实证明，仍需核对领起句和上下文。

## 内容原则

第一、第二人称只保留于直接引文。正文中的“我”转换为叙述者或角色名称。
人物指控、猜测、梦境与传闻需归属来源，不能改为客观事实。
代词在主体明确时可用，换主体或存在两个候选者时写姓名；不机械替换所有代词。
保留约数、否定、范围和不确定性，不为字面重合清零削字或换专名。
正常标点、时序连接词、必要摘要和名字重复不构成违规。

评价先使用已有用户材料，没有就留空；不要为每条找评价或自动联网。
材料归属与引用已明确后再写正文，发现问题只修受影响的句子。

## 校验与渲染

```powershell
python scripts/quote_source_check.py 书.entries.json --chapters 书.chapters.json
python scripts/check_voice.py 书.entries.json
python scripts/mos_check.py 书.entries.json
python scripts/render_wiki.py 书.entries.json -o 书_wiki --work 书 --unit 章
```

可选文风与重合检查只提供线索，不统一要求零命中。
独立 AI 审查按 [审查配置](审查配置.md) 的作品级参数执行；不为每批、每页、每个提示另起模型。
默认站点采用 Vector 2022 风格，信息框在正文内；参见风格规范第二节。
