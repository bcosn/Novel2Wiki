# 条目规划：先判断，再组织与写作

八表保存抽取事实，条目计划保存编辑取舍。两者分开：一条属性记录不自动等于一个页面，旧importance和姓名出现次数也不等于最终重要性。

## 判断依据

结合当前已读范围，判断对象是否有独立身份、关键作用、值得共同解释的定义、层级或成员关系。主角可以展开更多有据主题，配角和零散物件可以短写或并入主题；一次出现但决定事件走向的对象也可能重要。材料不足时保留待判断，不用频次阈值代替判断。

种族表中的项目先是候选。动态属性可能是等级体系，也可能只是地址、称号或一次状态记录。程序没有“境界、血统、品阶”等关键词名单、等级值或等级顺序；由当前执行写作的模型根据原文作有理由、有引用的判断，不另起分类子代理。

共同设定优先考虑一张总览页。各等级只有在材料支持独立介绍时才拆页。种族用race，其他有据设定用concept；同一词在不同语境不自动视为同一类。保留历史记录的章节和先后，不把所有出现过的人都列为当前成员，也不从A/B、数字或level字段推断高低。

程序负责保留去向、绑定来源和检查结构，不能证明判断或转述在语义上正确。

## 去向与深度

| 去向 | 用途 |
| --- | --- |
| standalone | 独立页，须先确定有据主题 |
| stub | 简短独立页，可只有导语 |
| merge | 将内容放入一个明确的独立目标主题，主体不变 |
| table_only | 保留抽取记录，暂不建页 |
| review | 判断依据不足，保留未决 |

初始清单保留所有基础实体和已有页。已有页优先保留；表中main/major暂列独立页，minor暂列短页，mention暂留表；未知为review，不能当作mention。种族即使level=0也不会自动忽略。所有默认值仍需证据判断；新增事件、设定和所有显式改动须有短理由。

深度expanded/standard/brief分别最多6/4/1个主题，这是上限，不是配额。不规定最低字数；没有主题材料就选stub，不造空小节。每个详细事实只在一个主题展开，导语保留必要概括。

merge只表示内容去向，不是身份等同。次要人物不能变成另一人物的别名或假重定向；真正异名仍由aliases表示。现有页不会因新计划自动删掉，需明确完整替换并提供当前hash。

## 有界判断包

```powershell
python scripts/workspace.py --db data/novel2wiki.sqlite --work 书 plan-entries work/entry-plan.json
python scripts/workspace.py --db data/novel2wiki.sqlite --work 书 inspect-plan work/entry-plan.json --offset 0 --limit 6
python scripts/workspace.py --db data/novel2wiki.sqlite --work 书 prepare-plan work/plan-context --plan work/entry-plan.json --candidate <ID>
# 同一接口可选当前小范围的动态属性
python scripts/workspace.py --db data/novel2wiki.sqlite --work 书 prepare-plan work/attribute-context --plan work/entry-plan.json --table custom_attributes --chapter 1
```

完整计划只保存在文件里。inspect-plan最多12行、6000字符；prepare-plan最多6个候选、12000字符，含规范、相关事实与冻结原文。只读返回的context。姓名命中是候选定位，不是事实证明。

超限缩小候选、表或章节。单章太长可明确重复传`--evidence-id <ID>`选择段落，包会声明partial_selection及省略数，不能据此声称完成全章。候选在包外的定位只报告省略数量，不改变原计划。表仍过大就缩小实际处理范围，不自动截断。

执行者将判断累计写到decisions.json；每次重新plan-entries时都带上前几批决策，不能只写最新一批而覆盖旧结论。程序不调用模型、不创建代理；判断与写作由同一工作流程执行。

## 决策文件

以下是虚构格式示例，标题与分类必须按作品证据确定：

```json
{
  "items": [
    {
      "kind": "concept",
      "name": "弦序",
      "reason": "原文明确定义共同标记方式，适合一张总览页。",
      "importance": "major",
      "disposition": "standalone",
      "depth": "standard",
      "evidence_ids": ["c1p2"],
      "topics": [
        {"id": "marks", "heading": "标记名称", "focus": "只写原文两种称呼，不猜等级。", "evidence_ids": ["c1p2"]}
      ]
    }
  ]
}
```

```powershell
python scripts/workspace.py --db data/novel2wiki.sqlite --work 书 plan-entries work/entry-plan.json --decisions work/decisions.json
```

新页ID由作品、kind和name生成，已有同身份页复用ID。计划可保留跨类型同名候选；发布标题须消歧，最终稿仍禁止两个完全相同name。不要为了换标题偷偷改已有ID的name/kind。

独立页主题含id/heading/focus/evidence_ids。若明确给topics但没给条目级evidence_ids，后者收窄为主题引用并集；显式条目证据优先。短页及并入对象应明确选本批证据，不把全书姓名命中带进写作包。

新增页第一次生成ID后，可在同一累计决策文件加入关系，再次plan-entries：

```json
{
  "source": "人物ID",
  "target": "设定ID",
  "label": "标记记录",
  "evidence_ids": ["c1p3", "c2p1"]
}
```

以上对象放在decisions.relations数组中。每条关系须有本范围原文，目标为独立页；不同方向分别判断，程序不自动推出反向关系。人物→事件的参与关系也使用同一接口，不额外复制整个事件经过。labels说明原文关系，不能把历史所属写成当前所属。
不改关系时省略relations，重建计划会继承正式条目的关系；显式空数组表示清空，显式列表表示完整替换，须带上仍需保留的其它关系。来源hash缺失或已变化的旧关系不会默默绑定新段落，需显式复核。
定稿时，已验证计划以`_entry_plan`元数据文档与正文同事务保存到SQLite，默认重建恢复尚未发布的事件/设定及其判断。不是新增事实表，也不把原文复制进条目元数据；来源变更不复用旧判断。发布过又明确删除的页，不作为“尚未发布”复活旧关系。
旧工作区若只有外部计划，可用`plan-entries work/recovered-plan.json --base-plan work/entry-plan.json`恢复；原计划必须对应当前来源。恢复后下一次带--plan定稿才保存新元数据。新计划的同名独立标题或保留旧页的同名冲突，会在写作准备前拒绝，先消歧再调用模型。

关系参与plan_hash。定稿由程序写入relations并复制来源；渲染为带引用的“关系：目标条目”。目标尚未发布时保留关系数据，不产生死链接，待目标发布自动显示。追加公共页无需重写原人物正文。

## 写作与一次收尾

```powershell
python scripts/workspace.py --db data/novel2wiki.sqlite --work 书 prepare-draft work/draft-context --plan work/entry-plan.json --plan-entry <ID> --prompt-profile quality-v2
python scripts/workspace.py --db data/novel2wiki.sqlite --work 书 finalize work/entries-b01.json --plan work/entry-plan.json --directory . --report work/finalize-report.json
```

每包1–3页，只带所选页的导语、主题、并入对象和关系需要的证据。只读draft-context.json；按其中draft_template填正文，不重打quote。计划正文每节含topic_id/body/refs，标题由程序补齐；空正文、错主题、跨主题借引用、遗漏合并主体和把合并主体当alias都会拒绝。

保留meta.evidence_hash与meta.entry_plan_hash。计划绑定全部章节与八表版本，来源变化先重建计划；不能去掉--plan绕过已规划稿的检查。旧无计划流程仍兼容，但没有计划验收结论。局部修订继续用--update和--expected-hash，保留其余页；单独无计划更新会撤销全局计划状态，不能沿用旧验收标记。

completed/reused只说明当前正式稿的机械检查和渲染。entry_plan另外返回missing_page_count、unresolved_count、unverified_page_count与retained_outside_plan_count；仍有缺页、待判断或未按当前计划核验的旧页时，不报告整书完成。resume不需要原计划文件，相同来源下保留已存覆盖状态；无法确认时明确unavailable。

## 验证范围

程序回归覆盖分类待判断、任意属性名、显式关系、历史状态、分批链接、来源过期、主题结构、旧页保护与上下文边界。虚构小样本证明页面/链接的机制；不用于证明《龙族》等具体作品设定正确。

真实短文试验分别记录事实覆盖、错误、主次分配、主题重复和全部调用成本，含规划与失败。一次小样本不能证明普遍省耗或全书语义质量提升；默认legacy写作提示词保持不变，quality-v2仍为显式实验项。
