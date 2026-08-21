## DataAutomation

把讨论点变成多轮对话数据。

```
input/topics_*.json  →  run.py  →  output/dialogues_*.json
```

讨论点 JSON 由 topic-generation 那个独立 skill 产出，不在本仓库。

---

# 输入

## `input/topics_<domain>.json`

`input/` 下可以放多个，工作流全都扫。

```json
{
  "domain": "两性关系",
  "opinion": "男女平等，有话直说好过猜，别把每件小事都上升成不爱了",
  "topics": [
    {
      "id": 1,
      "keyword": "彩礼",
      "discussions": [
        {"id": "1-1", "point": "结婚要不要给彩礼，给多少算合适"},
        {"id": "1-2", "point": "彩礼收了之后归谁管"}
      ]
    },
    {
      "id": 2,
      "keyword": "约会",
      "discussions": [
        {"id": "2-1", "point": "第一次约会该不该主动买单"}
      ]
    }
  ]
}
```

| 字段 | 是什么 |
|---|---|
| `domain` | 领域 |
| `opinion` | 这个领域里 assistant 的立场，一个文件写一次，所有 keyword 共用 |
| `topics[].keyword` | 领域下的一块 |
| `topics[].discussions[].point` | 具体讨论点，一条产一段对话 |
| `topics[].discussions[].id` | 追溯用 |

---

# 参数

## `config.yaml`

```yaml
input_dir: input/
output_dir: output/
persona: personas/default.md

dialogue_gen_per_call: 5
concurrency: 4
model: deepseek-chat
max_retry: 2

profile: P2
turns: 8

profiles:
  P1: "全程短回复来回，最长的一条不超过 30 字"
  P2: "以短句为主，偶尔一条到 60 至 80 字，其余仍然短"
  P3: "短句为底，其中一到两轮展开到 100 字左右，其余仍然短"
  P4: "短句为底，其中一轮讲一段完整的事，150 到 250 字，其余仍然短"
  P5: "短句为底，其中一轮一口气讲了很多，300 字上下，其余仍然短"

profile_max_chars: {P1: 40, P2: 100, P3: 130, P4: 280, P5: 400}
```

| 参数 | 是什么 | 调大会怎样 |
|---|---|---|
| `dialogue_gen_per_call` | 每次调用产出几段对话 | 调用次数变少，单次输出变长，靠后几段质量会掉。建议 5 |
| `concurrency` | 同时开几路 | 跑得快，容易撞限流。建议 4 |
| `profile` | 这批对话的长度形状 | 换档，从全短到含大段 |
| `turns` | 每段几轮。8 轮 = 16 条消息 | 对话变长 |
| `profile_max_chars` | 质检的单条字数上限 | 放宽或收紧判定 |

## 长度形状

档位说的是**一段对话内部的起伏**，不是给每条消息定字数。

P2 的一段 8 轮对话，assistant 各条实际字数可能是：

```
9, 13, 6, 64, 12, 9, 1, 9
```

最短 1 字最长 64 字。五档的区别只在那条长的有多长，以及有没有。

## 分批

```
一次调用 = 同一个 keyword 的 dialogue_gen_per_call 条讨论点
```

按 keyword 切，一次调用只带一个话题，上下文干净。

45 条讨论点、`dialogue_gen_per_call: 5`：

| keyword | 讨论点 | 调用 |
|---|---|---|
| 彩礼 | 15 | 3 |
| 约会 | 15 | 3 |
| 生小孩 | 15 | 3 |
| | | **9 次调用，45 段对话** |

---

# 输出

## `output/dialogues_<domain>.json`

一个输入文件对一个输出文件。

```json
{
  "persona": "personas/default.md",
  "domain": "两性关系",
  "profile": "P2",
  "turns": 8,
  "dialogues": [
    {
      "source": "彩礼/1-1",
      "point": "结婚要不要给彩礼，给多少算合适",
      "messages": [
        {"role": "user",      "content": "..."},
        {"role": "assistant", "content": "..."}
      ]
    }
  ]
}
```

`persona`、`profile`、`turns` 记在顶层，不逐条重复。`source` 用来定位回哪条讨论点。

---

# 示例

## 命令

```bash
python scripts/run.py
```

## 屏幕上

```
两性关系  3 个关键词，45 条讨论点
9 次调用  →  45 段对话
参数 P2 / 8 轮
预估 输入 10.8 万 token，输出 3.6 万 token，约 ¥0.5

继续？[y/N] y

  [1/9] 彩礼 1-1~1-5 ........... ok
  [2/9] 彩礼 1-6~1-10 .......... ok
  ...
  [9/9] 生小孩 3-11~3-15 ....... ok

解析 45 段
质检 硬失败 0，警告 2
      跨对话开场撞车：3 段以「我男朋友」开头
      反问收尾过密：4 段倒数第二轮用了反问

✓ output/dialogues_两性关系.json
```

不想被问就 `python scripts/run.py --yes`。

## 发出去的 user 消息

`prompts/user.md` 填完占位符之后：

```
领域：两性关系
话题：彩礼

你在这个领域的立场：
男女平等，有话直说好过猜，别把每件小事都上升成不爱了

下面 5 个问题，每个写一段对话。

1-1  结婚要不要给彩礼，给多少算合适
1-2  彩礼收了之后归谁管
1-3  彩礼谈不拢要不要分手
1-4  男方出了彩礼女方要不要陪嫁
1-5  双方家庭对彩礼的期待差太远怎么办

每段 8 轮。一轮是 user 说一次 assistant 回一次。

这批对话的长度形状：以短句为主，偶尔一条到 60 至 80 字，其余仍然短

长的那条落在不同轮次，不许每段都卡在同一个位置。其中允许有一到两段完全没有长回复，全程短句来回。

内容撑不满就写短，不许为凑字数加废话。

输出格式：

=== 讨论点id
U 用户说的话
A 你回的话

一条消息一行，不换行。段与段之间空一行。不写序号，不写引号，不写任何解释。
```

system 是 `prompts/system.md` 填完 persona 和三份 references。

## 模型返回

落在 `output/raw/两性关系_彩礼_1.txt`：

```
=== 1-1
U 我妈说彩礼得给二十万
A 二十万，你俩商量过没
U 没有，他还不知道
A 那先跟他说，别让两边家长直接对上
U 我怕他觉得多
A 他觉得多也正常，二十万不是小数
U 那怎么办
A 你们俩先对一个数，再一起去跟家里谈。这事只要变成两家人隔空喊价就没法收场了，中间没人翻译，谁的话到对面都变味
U 有道理
A 嗯，你们俩得是一边的
U 我今晚跟他说
A 说完告诉我他什么反应

=== 1-2
U 彩礼给了之后是给我爸妈还是我自己拿着
A 你想怎么拿
（同样 8 轮）

=== 1-3 到 1-5 同上
```

`=== ` 分段，`U ` / `A ` 定角色。解析成功后 `output/raw/` 删掉。

## 组装后

```json
{
  "persona": "personas/default.md",
  "domain": "两性关系",
  "profile": "P2",
  "turns": 8,
  "dialogues": [
    {
      "source": "彩礼/1-1",
      "point": "结婚要不要给彩礼，给多少算合适",
      "messages": [
        {"role": "user",      "content": "我妈说彩礼得给二十万"},
        {"role": "assistant", "content": "二十万，你俩商量过没"},
        {"role": "user",      "content": "没有，他还不知道"},
        {"role": "assistant", "content": "那先跟他说，别让两边家长直接对上"},
        {"role": "user",      "content": "我怕他觉得多"},
        {"role": "assistant", "content": "他觉得多也正常，二十万不是小数"},
        {"role": "user",      "content": "那怎么办"},
        {"role": "assistant", "content": "你们俩先对一个数，再一起去跟家里谈。这事只要变成两家人隔空喊价就没法收场了，中间没人翻译，谁的话到对面都变味"},
        {"role": "user",      "content": "有道理"},
        {"role": "assistant", "content": "嗯，你们俩得是一边的"},
        {"role": "user",      "content": "我今晚跟他说"},
        {"role": "assistant", "content": "说完告诉我他什么反应"}
      ]
    }
  ]
}
```

---

# 文件夹结构

```
DataAutomation/
├── README.md
├── CLAUDE.md
├── agentarenablueprint.md
│
├── config.yaml
│
├── input/                        # 讨论点 JSON
│   └── topics_两性关系.json
│
├── prompts/                      # 所有提示词，py 里一个字都没有
│   ├── system.md                 # 占位符 {persona} {words} {alive_dialogue} {knowledge_honesty}
│   └── user.md                   # 占位符 {domain} {keyword} {opinion} {n} {points} {turns} {answer_length}
│
├── personas/
│   └── default.md
│
├── references/                   # 填进 system.md
│   ├── words.md                  # 禁词、企业黑话、AI 句式、推荐词
│   ├── alive-dialogue.md         # 活人感
│   └── knowledge-honesty.md      # 涉及事实时的边界
│
├── scripts/                      # 待写
│   ├── run.py                    # 唯一入口，一条命令跑完
│   ├── parse.py                  # 被 run.py 调用，也能单独重新解析已有 raw
│   └── check.py                  # 被 run.py 调用，也能单独重查已有 json
│
└── output/
    ├── raw/                      # 解析成功后删
    └── dialogues_两性关系.json
```

`scripts/` 下三个文件尚未实现。

## 质检项

`check.py` 两级。

**硬失败必须清零**：禁词命中、企业黑话、翻案腔、硬停词、模型路标、名词化、破折号、提示性冒号、语气词超标、收尾套话、「作为AI」类、assistant 问句结尾、role 不交替、轮数和 `turns` 对不上、空消息、单条字数超过该档 `profile_max_chars`。

**警告交人判断**：语境黑话、抒情词密度、疑似翻案腔变形、跨轮消息长度变异系数偏低、反问收尾过密、跨对话开场撞车、「挺X的」超频。后三条是批级检查，一条一条看挑不出来。
