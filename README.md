## DataAutomation

把讨论点变成多轮对话数据。

```
input/topics_*.json  →  run.py  →  output/dialogues_*.json
                          │
                          ├── llm.py     拼 system 和 user，调模型
                          ├── parse.py   模型吐的纯文本 → JSON
                          ├── check.py   质检
                          ├── usage.py   token 和余额
                          └── config.py  读 config.yaml
```

一条命令跑完。提示词怎么拼、请求怎么发，全在 `scripts/llm.py` 一个文件里，
那个文件只有这两件事，别的都不在里面。

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

api_key: sk-...
base_url: https://api.deepseek.com
model: deepseek-v4-flash
thinking: false
timeout: 600

dialogue_gen_per_call: 4
concurrency: 4
max_retry: 2

profile: P2
min_turns: 4
max_turns: 8

profiles:
  P1: "全程短回复来回，最长的一条不超过 30 字"
  P2: "以短句为主，偶尔一条到 60 至 80 字，其余仍然短"
  P3: "短句为底，其中一到两轮展开到 100 字左右，其余仍然短"
  P4: "短句为底，其中一轮讲一段完整的事，150 到 250 字，其余仍然短"
  P5: "短句为底，其中一轮一口气讲了很多，300 字上下，其余仍然短"

profile_max_chars: {P1: 40, P2: 100, P3: 130, P4: 280, P5: 400}
```

| 参数 | 是什么 | 调了会怎样 |
|---|---|---|
| `api_key` | DeepSeek 的 key | 没写直接退出 |
| `base_url` | 接口地址 | 走 OpenAI 兼容格式，换别家也能用 |
| `model` | `deepseek-v4-flash` / `deepseek-v4-pro` | pro 贵三倍 |
| `thinking` | 开不开思考 | 开着输出 token 多十倍，轮数更稳。v4-flash 默认是开，这里默认关 |
| `timeout` | 单次请求超时，秒 | 思考开着一次要一两分钟，别设太短 |
| `dialogue_gen_per_call` | 每次调用产出几段对话 | 调用次数变少，单次输出变长，靠后几段容易变短变糊。重发时整批重来，一批越大重发越贵 |
| `concurrency` | 同时开几路 | 跑得快，容易撞限流 |
| `max_retry` | 格式坏了重发几次 | 重发一次的钱等于跑一次 |
| `profile` | 这批对话的长度形状 | 换档，从全短到含大段 |
| `min_turns` / `max_turns` | 每段几轮的上下限 | 区间内由讨论点自己决定，卡死成一个数会逼模型注水 |
| `profile_max_chars` | 质检的单条字数上限 | 放宽或收紧判定 |

## 轮数

`min_turns` 到 `max_turns` 是区间，具体几轮由讨论点本身决定，`prompts/user.md`
里明确禁止为了凑数注水、为了收短硬砍。15 条讨论点跑出来的实际分布：

| 轮数 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|
| 段数 | 3 | 7 | 4 | 1 | 0 |

落在区间外的段整批重发，重发 `max_retry` 次还不行就跳过，raw 不落盘。
`dialogues_*.json` 里每一段都带自己的 `turns`。

早先把轮数卡死成一个数（`turns: 8`）时，模型经常只写四五轮就收尾，
`finish_reason` 是 `stop` 不是截断，重发也只是一轮轮往上挪：

| 配置 | 每段实际拿到几条消息（当时要 16 条） |
|---|---|
| 思考关，一次 5 段 | 12 / 11 / 10 / 12 / 12 |
| 思考开，一次 5 段 | 10 / 10 / 10 / 10 / 10 |
| 思考关，一次 2 段 | 14 / 14 |
| 思考开，一次 2 段 | 16 / 16 |

改成区间之后，思考关、一次 2 段，9 批全过，15 条讨论点落地 15 段。

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

按 keyword 切，一次调用只带一个话题，上下文干净。system 每次调用一字不差，
第二次起走 DeepSeek 的 prompt cache，按 cache hit 计价，便宜 30 倍。

token 数不自己数，直接汇总每次响应里官方的 `usage` 对象，字段名照抄，
`reasoning_tokens` 也在里面。花费不自己算，跑前跑后各查一次官方的
`GET /user/balance`。DeepSeek 扣费有几分钟延迟，跑完那一刻两个余额常常一样。

45 条讨论点、`dialogue_gen_per_call: 4`：

| keyword | 讨论点 | 调用 |
|---|---|---|
| 彩礼 | 15 | 4 |
| 约会 | 15 | 4 |
| 生小孩 | 15 | 4 |
| | | **12 次调用，45 段对话** |

调用次数本身不收费，DeepSeek 只按 token 计价。多切一刀的代价是 system 多发一遍，
但那部分走 cache hit，一万字的 system 命中缓存约 7000 token，一次不到 0.001 元。

---

# 输出

## `output/dialogues_<domain>.json`

一个输入文件对一个输出文件。

```json
{
  "persona": "personas/default.md",
  "domain": "两性关系",
  "profile": "P2",
  "min_turns": 4,
  "max_turns": 8,
  "dialogues": [
    {
      "source": "彩礼/1-1",
      "point": "结婚要不要给彩礼，给多少算合适",
      "turns": 8,
      "messages": [
        {"role": "user",      "content": "..."},
        {"role": "assistant", "content": "..."}
      ]
    }
  ]
}
```

`persona`、`profile`、`min_turns`、`max_turns` 记在顶层，不逐条重复。
每段自己的 `turns` 逐条记，因为它是变的。`source` 用来定位回哪条讨论点。

---

# 示例

## 命令

```bash
python scripts/run.py            打印预估，等确认后跑
python scripts/run.py --yes      不问直接跑
python scripts/run.py --plan     只打印预估，不发请求
python scripts/run.py --preview  打印第一批拼好的 system 和 user，不发请求
```

## 屏幕上

下面是记录下来的一次真实运行，当时 `dialogue_gen_per_call` 还是 2，所以是 9 次调用。

```
两性关系  3 个关键词，15 条讨论点
  彩礼           5 条讨论点  →  3 次调用
  约会           5 条讨论点  →  3 次调用
  生小孩          5 条讨论点  →  3 次调用

9 次调用  →  15 段对话
参数 deepseek-v4-flash  P2  4-8 轮  思考关
预估 输入未命中 3.1 万  输入命中缓存 3.5 万  输出 0.4 万

继续？[y/N] y

  [1/9] 两性关系/约会 第1批                  ok
  [2/9] 两性关系/彩礼 第3批                  ok
  ...
  [9/9] 两性关系/生小孩 第1批                 ok

实际 输入未命中 0.4 万  输入命中缓存 6.2 万  输出 0.4 万
余额 92.69 → 92.28 CNY（扣费有延迟，两个数一样是正常的）

解析 15 段  →  dialogues_两性关系.json

质检 15 段对话
  硬失败 11
    [dialogues_两性关系] 彩礼/1-3 第6条  提示性冒号
    [dialogues_两性关系] 生小孩/3-3 第2条  问句结尾
    ...
  警告 9
    [dialogues_两性关系] 彩礼/1-2  各条长度太齐，变异系数 0.14
    ...

✓ output/dialogues_两性关系.json
```

「预估」按字符数拍，「实际」是官方 `usage` 的汇总。没跑成的批次 raw 不落盘，
下次重跑只会重跑它们。单独查余额：`python scripts/usage.py`。

## 发出去的 system

`prompts/system.md` 填完四个占位符，一万字上下，每次调用一字不差。
`python scripts/run.py --preview` 原样打出来。

## 发出去的 user

`prompts/user.md` 填完占位符之后：

```
领域：两性关系
话题：彩礼

你在这个领域的立场：
男女平等，有话直说好过猜，别把每件小事都上升成不爱了

下面 2 个问题，每个写一段对话。

1-1  结婚要不要给彩礼，给多少算合适
1-2  彩礼收了之后归谁管

每段 4 到 8 轮。一轮是 U 一行、A 一行，交替，最后一行是 A。

轮数按这个讨论点本身该聊多久来定：三两句能说完的就 4 轮收，需要来回掰扯的就往 8 轮走。同一批里的几段不许一样长。

严禁为了凑够轮数注水。以下都算注水，出现即废稿：

- 把上一轮的意思换个说法再说一遍
- 「嗯」「好的」「明白了」这类空附和单独占一轮
- 明知故问，问一个前面已经答过的东西
- 结尾多加一轮客套

严禁为了收短硬砍。话没说完就「行吧我知道了」「那我再想想」草草收场，也是废稿。

这批对话的长度形状：以短句为主，偶尔一条到 60 至 80 字，其余仍然短

长的那条落在不同轮次，不许每段都卡在同一个位置。其中允许有一到两段完全没有长回复，全程短句来回。

内容撑不满就写短，不许为凑字数加废话。

输出格式：

=== 1-1
U 用户说的话
A 你回的话
U 用户说的话
A 你回的话

`=== ` 后面原样抄上面列出的编号，不写「讨论点」三个字，不加别的字。

一条消息一行，不换行。段与段之间空一行。不写序号，不写引号，不写任何解释。
```

## 重发时追加的

轮数出界或者漏了讨论点，`prompts/retry.md` 接在 user 末尾重发：

```
---

上一次输出不合格：

2-1: 3 轮，要 4 到 8 轮
2-2: 3 轮，要 4 到 8 轮

重新输出这一批的全部讨论点，每段 4 到 8 轮，每段最后一条必须是 A。格式不变。轮数不够就把没聊透的地方接着聊，不许拿空话补位。
```

每次重发都从原始 user 重新接，不把上一轮的清单叠上去。

## 模型返回

落在 `output/raw/两性关系_彩礼_1.txt`：

```
=== 1-1
U 结婚彩礼这事，你们那边一般给多少
A 看地方吧，我这边普通人家十万上下
U 十万也不算少了吧
A 嗯，差不多是行情价
U 那要是女方非要三十万呢
A 我会先问一句，这钱是带回小家还是留给娘家，方向完全不一样
U 有区别吗，不都是给出去的钱
A 区别大了，带回小家等于左手倒右手
U 那你觉得给多少算合适
A 量力而行吧，家里拿得出就多给点
U 我见过有人为彩礼借了一屁股债
A 图个面子呗，最后日子还是自己过的
U 所以你是不支持给彩礼那种
A 也不是，给可以，但别让这个数变成负担
U 有道理
A 不然一开始就埋了根刺

=== 1-2
U 彩礼收了之后一般谁管
A 你想怎么拿
（同样 8 轮）
```

`=== ` 分段，`U ` / `A ` 定角色。解析成功后 `output/raw/` 删掉。

## 组装后

```json
{
  "persona": "personas/default.md",
  "domain": "两性关系",
  "profile": "P2",
  "min_turns": 4,
  "max_turns": 8,
  "dialogues": [
    {
      "source": "彩礼/1-1",
      "point": "结婚要不要给彩礼，给多少算合适",
      "turns": 8,
      "messages": [
        {"role": "user",      "content": "结婚彩礼这事，你们那边一般给多少"},
        {"role": "assistant", "content": "看地方吧，我这边普通人家十万上下"},
        {"role": "user",      "content": "十万也不算少了吧"},
        {"role": "assistant", "content": "嗯，差不多是行情价"},
        {"role": "user",      "content": "那要是女方非要三十万呢"},
        {"role": "assistant", "content": "我会先问一句，这钱是带回小家还是留给娘家，方向完全不一样"},
        {"role": "user",      "content": "有区别吗，不都是给出去的钱"},
        {"role": "assistant", "content": "区别大了，带回小家等于左手倒右手"},
        {"role": "user",      "content": "那你觉得给多少算合适"},
        {"role": "assistant", "content": "量力而行吧，家里拿得出就多给点"},
        {"role": "user",      "content": "我见过有人为彩礼借了一屁股债"},
        {"role": "assistant", "content": "图个面子呗，最后日子还是自己过的"},
        {"role": "user",      "content": "所以你是不支持给彩礼那种"},
        {"role": "assistant", "content": "也不是，给可以，但别让这个数变成负担"},
        {"role": "user",      "content": "有道理"},
        {"role": "assistant", "content": "不然一开始就埋了根刺"}
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
│   ├── user.md                   # 占位符 {domain} {keyword} {opinion} {n} {points}
│   │                             #        {first_id} {min_turns} {max_turns} {answer_length}
│   └── retry.md                  # 占位符 {problems} {min_turns} {max_turns}
│
├── personas/
│   └── default.md
│
├── references/                   # 填进 system.md
│   ├── words.md                  # 禁词、企业黑话、AI 句式、推荐词
│   ├── alive-dialogue.md         # 活人感
│   └── knowledge-honesty.md      # 涉及事实时的边界
│
├── scripts/
│   ├── run.py                    # 唯一入口：分批、并发、落盘、打印
│   ├── llm.py                    # 只有两件事：拼提示词、调模型
│   ├── parse.py                  # 被 run.py 调用，也能单独重新解析已有 raw
│   ├── check.py                  # 被 run.py 调用，也能单独重查已有 json
│   ├── usage.py                  # 汇总官方 usage、查余额，也能单独查
│   └── config.py                 # 读 config.yaml
│
└── output/
    ├── raw/                      # 解析成功后删
    └── dialogues_两性关系.json
```
