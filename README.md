## DataAutomation

把讨论点变成多轮对话数据。

```
input/topics_*.json  →  run.py  →  output/dialogues_*.json
                          │
                          ├── llm.py     拼 system 和 user，调模型
                          ├── parse.py   模型吐的纯文本 → JSON
                          ├── check.py   质检
                          ├── usage.py   数 token、查余额
                          ├── config.py  读配置
                          └── ui/        命令行长什么样
```

一条命令跑完。提示词怎么拼、请求怎么发，全在 `scripts/llm.py` 一个文件里，
那个文件只有这两件事。命令行的排版和进度条全在 `scripts/ui/`，业务脚本不拼
转义码、不数空格。

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

## api_key

三个地方都能放，后面的盖前面的：

| 放哪 | 进不进仓库 |
|---|---|
| `config.yaml` 里 `api_key: sk-...` | 进 |
| `secrets.yaml`（抄 `secrets.example.yaml`） | 不进，`.gitignore` 挡着 |
| 环境变量 `DEEPSEEK_API_KEY` | 不进 |

## `config.yaml`

```yaml
input_dir: input/
output_dir: output/
persona: personas/default.md

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
python scripts/run.py
```

就这一条。切批 → 调模型 → 解析 → 质检 → 落盘，一次跑完。
第一次跑之前先 `pip install -r requirements.txt`。

三个开关，还是这一条命令：

| 开关 | 干什么 |
|---|---|
| `--yes` | 不问「继续？」直接跑 |
| `--plan` | 只打印预估和余额，不生成 |
| `--preview` | 打印第一批拼好的 system 和 user，完全离线 |

出了问题才用得上的三个脚本，都不发请求、不花钱：

| | |
|---|---|
| `python scripts/parse.py` | raw 还在，重新解析一遍 |
| `python scripts/check.py` | 重查已经落盘的 JSON |
| `python scripts/usage.py` | 查一次余额 |

## 屏幕上

```
  DataAutomation · 讨论点 → 多轮对话

  ┌─ 输入 ────────────────────────────────────────────────────────────────
  │ 两性关系  3 个关键词 · 15 条讨论点
  │   彩礼    5 条  →  2 批
  │   约会    5 条  →  2 批
  │   生小孩  5 条  →  2 批
  └

  ┌─ 参数 ────────────────────────────────────────────────────────────────
  │ 模型      deepseek-v4-flash  思考 关
  │ 长度      P2  以短句为主，偶尔一条到 60 至 80 字，其余仍然短
  │ 轮数      4 到 8 轮  由讨论点自己定，不硬凑
  │ 并发      4 路  每批 4 段 · 不合格重发 2 次
  └

  ┌─ 每次调用喂进去多少 token ────────────────────────────────────────────
  │ system                6,449
  │   骨架 system.md        455
  │   人设                  232
  │   用词                1,649
  │   活人感              3,566
  │   事实边界              550
  │ user              432 - 468  随讨论点变
  │ 单次合计              6,917  最大的一批
  │
  │ tokenizer/deepseek.json 真数的
  └

  ┌─ 预估  6 次调用 → 15 段对话 ──────────────────────────────────────────
  │ 输入未命中    28,495
  │ 输入命中缓存  12,898
  │ 输出           4,000
  │
  │ 输入真数，输出按每条 25 字拍
  │ 余额        97.31 CNY
  └

  继续？[y/N] y

  生成  ████████████░░░░░░░░░░░░  3/6    ✓ 约会 第1批
  用量  输入未命中  20,700  命中缓存   6,400  输出   1,440  重发 1

  ┌─ 实际 ────────────────────────────────────────────────────────────────
  │ 输入未命中     3,500
  │ 输入命中缓存  44,800
  │ 输出           3,360
  │
  │ 余额        97.31 → 96.85 CNY（扣费有延迟，两个数一样是正常的）
  └

  ┌─ 解析 ────────────────────────────────────────────────────────────────
  │ dialogues_两性关系.json  15 段  4 轮 2 段 · 5 轮 4 段 · 7 轮 4 段 · 8 轮 5 段
  └

  ┌─ 质检 ────────────────────────────────────────────────────────────────
  │ 15 段对话    ✓ 全过    1 条警告
  │
  │ 警告  人判断，不拦
  │   [dialogues_两性关系] 跨对话开场撞车：3 段以「我男朋友」开头
  │
  │ 硬失败为 0，这批可以进训练集
  └

  ┌─ 落盘 ────────────────────────────────────────────────────────────────
  │ ✓ output/dialogues_两性关系.json
  └
```

底下那两行钉在屏幕上原地刷新，跑的过程中随时能看到已经烧了多少 token，
不用等跑完。用量是每批返回的官方 `usage` 累加的，重发那次也算进去。
重定向到文件时退化成一批一行，不带回车符。

所有对齐按显示宽度算，一个汉字两格，中英文混排不会歪。

## token 都花在哪

`system` 每批一字不差，第二次调用起走缓存。它那六千多 token 的构成：

| 块 | token | 来源 |
|---|---|---|
| 骨架 | 455 | `prompts/system.md` 去掉四个占位符剩下的：任务说明、衔接、硬禁令速查、交稿前自查 |
| 人设 | 232 | `personas/default.md` |
| 用词 | 1,649 | `references/words.md` |
| 活人感 | 3,566 | `references/alive-dialogue.md` |
| 事实边界 | 550 | `references/knowledge-honesty.md` |
| **合计** | **6,449** | |

数字是 `tokenizer/deepseek.json` 真跑一遍分词出来的，不是按字数估的。
这份 tokenizer 是 DeepSeek-V3 的官方词表，仓库里带着，不联网。
它数的是纯文本，接口返回的 `prompt_tokens` 还要加上几个 chat 模板的固定 token。

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
├── secrets.example.yaml          # 抄成 secrets.yaml 填 api_key，后者不进仓库
├── requirements.txt
│
├── input/                        # 讨论点 JSON
│   └── topics_两性关系.json
│
├── prompts/                      # 所有提示词，py 里一个字都没有
│   ├── system.md                 # 任务说明 + 四个占位符 + 硬禁令速查 + 交稿前自查
│   │                             #        {persona} {words} {alive_dialogue} {knowledge_honesty}
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
├── tokenizer/
│   └── deepseek.json             # DeepSeek-V3 官方词表，本地数 token 用
│
├── scripts/
│   ├── run.py                    # 唯一入口：分批、并发、落盘、打印
│   ├── llm.py                    # 只有两件事：拼提示词、调模型
│   ├── parse.py                  # 被 run.py 调用，也能单独重新解析已有 raw
│   ├── check.py                  # 被 run.py 调用，也能单独重查已有 json
│   ├── usage.py                  # 数 token、汇总官方 usage、查余额
│   ├── config.py                 # 读 config.yaml + secrets.yaml + 环境变量
│   └── ui/                       # 命令行长什么样，全在这个包里
│       ├── theme.py              # 宽度、线条、颜色
│       ├── blocks.py             # 面板、键值行、表格、按显示宽度对齐
│       └── progress.py           # 屏幕底部原地刷新的进度条和用量
│
└── output/
    ├── raw/                      # 解析成功后删
    └── dialogues_两性关系.json
```

---

# 文件介绍

按下面的顺序从上往下读一遍，就知道每个文件干什么。
每一条只用到前面已经出现过的东西。

跑起来只要两个第三方包，都在 `requirements.txt` 里：`PyYAML` 读配置，
`tokenizers` 数 token。后者没装也能跑，token 数退化成按字数估。

代码的阅读顺序和运行顺序有一处不同：`parse.py` 排在 `llm.py` 前面。因为 `llm.py` 判断模型输出合不合格，用的就是 `parse.py` 那套解析和校验，先看 `parse.py` 才看得懂 `llm.py` 的重发条件。

---

### S1 输入与素材

这一节全是数据文件，一行代码都没有。模型最终读到的每一个字都出自这里。

`input/topics_<domain>.json` 是整条流水线的入口，结构三层：

```json
{
  "domain": "两性关系",
  "opinion": "男女平等，有话直说好过猜，别把每件小事都上升成不爱了",
  "topics": [
    {"id": 1, "keyword": "彩礼", "discussions": [
      {"id": "1-1", "point": "结婚要不要给彩礼，给多少算合适"}
    ]}
  ]
}
```

`opinion` 是 assistant 在这个领域的立场，一个文件写一次，底下所有 keyword 共用。一条 `discussion` 产一段对话。这个文件由 topic-generation 那个独立 skill 产出，不在本仓库。

按序读八个文件：

1. `config.yaml`：所有参数。分五块：路径、接口（`api_key`、`base_url`、`model`、`thinking`、`timeout`）、调用（`dialogue_gen_per_call` 每批几段、`concurrency` 并发几路、`max_retry` 重发几次）、形状（`profile` 用哪档长度、`min_turns` 到 `max_turns` 轮数区间）、以及 `profiles` 五档长度形状的原文和 `profile_max_chars` 质检的单条字数上限。

   `profiles` 里那五句话原样填进提示词。模型数不清字数，所以长度给的是一句形状描述：

   ```yaml
   P2: "以短句为主，偶尔一条到 60 至 80 字，其余仍然短"
   ```

   每档末尾都有「其余仍然短」，防止模型把整段拉成一样长。

2. `personas/default.md`：assistant 演的是谁。八段，每段一件事：定位成朋友、有看法但说完就停不说教、不会的直接说不会、会走神会跑题、有情绪、对方难受时先接住、不主动追问收尾。不写长度，长度归 `user.md` 的 `{answer_length}` 管，两处都写会打架。

3. `references/words.md`：用词红线。四部分，前三部分命中即不合格：禁词（网络烂梗、过度夸张、虚假共鸣、口语糟粕、拟声词、后缀句式、收尾套话）、企业黑话（绝对禁用 27 个 + 看语境 14 个）、AI 句式（翻案腔、名词化、模型路标、硬停词、抒情词、三连排比、标点）。第四部分是可选词库，其中「语气词每条最多一个」和「同句不许两次挺X的」两条密度限制是硬的。

4. `references/alive-dialogue.md`：活人感，全篇最长的一份。六节：消息长度要拉开差距、对话怎么推进（user 说话的三个来源、钩子要点名具体事物、首轮接球、user 中间轮不许纯捧哏）、话怎么说（回答前有停顿、说完附一句感受、把自己放进去、说完就停）、情绪场景、不许出现的说法（问句结尾、因果解释收尾、旁白腔、空洞接球、文案空话、类比后升华、收尾重复自己）、不知道的时候怎么退场。

5. `references/knowledge-honesty.md`：事实边界。开头就写明「对话里出现可查证的说法时执行这一节」，是有条件触发的。三条硬规则（不确定不写成确定、专有名词要么写对要么绕开、不许倒推因果）、三档说法、用户说的事不许扩写、争议内容不许当定论。

6. `prompts/system.md`：system 的骨架。开头一段任务说明讲清三件事：写多轮对话、user 和 assistant 两边都由模型写、下一条消息管具体参数。再一句挡跑偏的，说后面几节的例子只示范怎么说话，题材跟要写的内容无关。然后四个占位符 `{persona}` `{words}` `{alive_dialogue}` `{knowledge_honesty}` 按顺序把上面四份填进来。最后两张清单：硬禁令速查 12 条，交稿前自查 5 条。

   四份 reference 各自的内部标题都比文件标题低一级，所以拼完整条 system 只有六个顶层小节，每份的子标题都收在自己那节底下。

7. `prompts/user.md`：user 的骨架，九个占位符 `{domain}` `{keyword}` `{opinion}` `{n}` `{points}` `{first_id}` `{min_turns}` `{max_turns}` `{answer_length}`。内容顺序是：这一批哪些讨论点 → 讨论点只交代这段聊什么，user 的第一句台词由模型自己编 → 轮数区间和四条注水禁令 → 长度形状 → 输出格式。

   输出格式这段是整条流水线的契约：

   ```text
   === 1-1
   U 用户说的话
   A 你回的话
   ```

   `{first_id}` 填的是这一批第一条讨论点的真实编号。写死成「`=== 讨论点id`」时模型会照抄成「`=== 讨论点 3-1`」，整批解析失败。

8. `prompts/retry.md`：输出不合格时追加在 user 末尾的那段，两个占位符 `{problems}` 毛病清单和 `{min_turns}` `{max_turns}` 轮数区间。

---

### S2 底件

主线三个文件都依赖的东西：读配置、命令行排版、数 token。

按序读五个文件：

9. `scripts/config.py`：一个 `load()`。三层叠加，后面盖前面：`config.yaml` 读全部参数，`secrets.yaml` 存在就叠上去（`.gitignore` 挡着不进仓库），环境变量 `DEEPSEEK_API_KEY` 优先级最高。`llm.py` 不读配置，`cfg` 一律由调用方传进去。

    仓库里给了一份 `secrets.example.yaml`，抄一份改名成 `secrets.yaml` 填进自己的 key 即可。

10. `scripts/ui/theme.py`：宽度 72 格、缩进两格、框线和方块字符、状态标记 `✓ ✗`、七个色号。`color_on()` 决定要不要上色：设了 `NO_COLOR` 不上、输出重定向到文件时不上（日志里不该有转义码）、Windows 上先用 ctypes 开 `ENABLE_VIRTUAL_TERMINAL_PROCESSING`，开不了就当没有颜色。不上色时 `CODES` 里全是空串，调用处不用写 if。

11. `scripts/ui/blocks.py`：静态排版。核心是 `width()`。一个汉字在等宽终端里占两格，用 `len()` 对齐中文会歪，所以宽度按 `unicodedata.east_asian_width` 算，顺带把 ANSI 转义码剥掉不计宽。`pad()` 按显示宽度补齐，`table()` 先量每列最宽再对齐，`panel()` 画一个上边框带标题、下边框只有一个角的框，`kv()` 排「标签 值 灰色备注」一行。

12. `scripts/ui/progress.py`：动态那部分。`bar()` 是纯函数，只画方块串。`Live` 占住屏幕底部固定几行，每次 `update()` 先把光标上移 `height` 行再整块重画，所以跑的时候屏幕不往下滚。非终端时退化成一步一行，只留第一行，并且剥掉转义码。

13. `scripts/usage.py`：token 和钱，两条原则：每次请求的用量一律用响应体里官方的 `usage` 对象，账户余额一律查官方 `GET /user/balance`。本地不维护价目表。

    - `count()` 用 `tokenizer/deepseek.json` 真跑一遍分词。`tokenizers` 没装或词表文件不在，就退回按字符数除以 1.5 估，`exact()` 告诉调用方现在是哪种，屏幕上会写明。数出来的是纯文本 token，不含 chat 模板那几个固定 token，所以比接口返回的 `prompt_tokens` 略少几个。
    - `merge()` 把若干次响应的 `usage` 加成一个。不挑字段，数值型的逐项相加，嵌套的 `prompt_tokens_details` 和 `completion_tokens_details` 摊平到顶层。`reasoning_tokens` 就是从这里来的，思考开着时它占输出的大头，手挑字段会漏掉它。
    - `estimate()` 跑之前拍一份用量。输入是现成文本，真数；输出还不存在，按每条消息 25 字拍。system 每次调用一字不差，只有最先发出去的那几路 cache miss，所以按 `concurrency` 路算 miss、其余算命中。
    - `fmt()` `line()` `rows()` 三种排法：面板里的多行、屏幕底部常驻的一行、表格行。
    - `balance()` 查余额，任何异常都返回 `None`，查不到余额不该让整趟跑挂掉。`fmt_balance()` 把跑前跑后两个数排成一行，并注明两个数一样是正常的，DeepSeek 扣费有几分钟延迟。

---

### S3 主线

解析、拼提示词调模型、质检。三个文件都能单独跑，不依赖 `run.py`。

14. `scripts/parse.py`：把模型吐的纯文本变成 JSON，同时提供整条流水线唯一的一套结构校验。

    - `parse_raw()` 按 `===` 切段，读 `U ` / `A ` 前缀定角色。两处容错：行首的列表符号和序号先用 `STRIP` 剥掉；模型手滑把一条消息断成两行时，没有前缀的那行接到上一条后面，不新起一条。
    - `validate()` 三件事：消息数是偶数（最后一条必须是 A）、轮数落在 `[min_turns, max_turns]` 区间里、角色严格交替且没有空消息。
    - `load_points()` 从 `input/` 建一张 `{领域: {讨论点id: (关键词, 讨论点原文)}}` 的表，落盘时把讨论点原文写回每段对话，方便日后追溯。
    - `parse_all()` 扫 `output/raw/*.txt` 全部解析。文件名是 `{domain}_{keyword}_{batch}.txt`，从右边切两刀取领域名，这样领域名自己带下划线也不会切错。任何一段校验不过就整趟不落盘、raw 全部保留，改完重跑；全部通过才写 `dialogues_<domain>.json` 并删掉 raw。

      输出 JSON 顶层记 `persona` `domain` `profile` `min_turns` `max_turns`，每段自己的 `turns` 逐条记，因为轮数每段都不一样。

15. `scripts/llm.py`：拼提示词 + 调模型。这个文件不读配置、不数 token、不落盘。

    - `_fill()` 读 `prompts/` 下的骨架换占位符。用 `str.replace` 不用 `str.format`，因为 references 里有 JSON 示例自带大括号，走 format 会被当占位符炸掉。换之前先核对骨架里的占位符和传进来的键完全一致，多一个少一个都当场报错。宁可炸，也别拿一段带着 `{keyword}` 的提示词去调模型。
    - `build_system()` 拼 system，结果跟具体讨论点无关，开跑前拼一次就够。`system_parts()` 把它拆成骨架和四份 reference，供屏幕上统计每块占多少 token；骨架那块是把四个占位符全填空串之后剩下的内容。
    - `build_user()` 拼这一批的 user，`build_retry()` 拼重发时追加的那段。
    - `chat()` 发一次请求。直接打 `urllib`，不用 openai SDK，发出去的每个字段都在 `payload` 那八行里明文摆着。`thinking` 是 DeepSeek v4 的开关，v4-flash 默认开思考。四类异常全部包成 `RuntimeError` 带上人能读的原因。
    - `find_problems()` 判这次输出合不合格，用的就是上面 `parse.parse_raw` 和 `parse.validate`。标准跟落盘时一模一样，所以报 ok 的批次 `parse.py` 一定收得下，不会出现「跑完说成功、解析时全灭」。查四件事：讨论点漏没漏、有没有多出不属于这批的、有没有同一条写两遍、每段轮数和角色顺序对不对。内容写得好不好一概不管，那是 `check.py` 的事。
    - `generate()` 一批的完整流程：`chat` → `find_problems` → 不合格就把毛病清单接在原始 user 后面重发，最多 `max_retry` 次。每次重发都从原始 user 重新接，不把上一轮的清单叠上去。返回每次请求的官方 `usage` 原样收集，包括没跑成的那几次。

16. `scripts/check.py`：质检落盘后的 JSON，判的是内容好不好，跟结构无关。词表从 `references/words.md` 抄过来，改一处得改另一处，文件开头写着这条。

    硬失败（必须清零）：禁词、企业黑话、硬停词、模型路标、AI 自称、翻案腔 8 条正则、名词化 5 条正则、破折号、提示性冒号、语气词超限（单条最多一个，含两个以上长句时可到两个）、assistant 问句结尾、单条超过 `profile_max_chars`。

    警告（交人判断，不拦）：单字疑似禁词（`亲` 会在「相亲」「亲戚」上误报，所以只算警告）、看语境的黑话、抒情词、疑似翻案腔变形、同句两处「挺X的」、一段之内 assistant 各条长度太齐（变异系数低于 0.40）、跨对话开场撞车（三段以上同样的四字开头）、反问收尾过密（超过三成的段落倒数第二轮用反问）。

    `han()` 数汉字时排除标点，`check_message()` 判单条，`check_file()` 判一个文件并做跨对话的统计，`check_all()` 汇总打印并返回硬失败条数当退出码。

---

### S4 入口

17. `scripts/run.py`：唯一入口，四个开关：不带参数打印预估后问一句、`--yes` 不问直接跑、`--plan` 只打印预估和余额、`--preview` 打印第一批实际会发出去的 system 和 user 且完全离线。

    - `check_config()` 开跑前把 `config.yaml` 里会炸的值一次性全挑出来：没有 api_key、`profile` 拼错、`profile_max_chars` 里缺这一档、每批 0 段、并发 0 路、轮数区间倒挂。能挑的一次挑完全部列出来再退，省得改一条跑一次。
    - `build_tasks()` 切批。扫 `input/` 下所有 JSON，按 keyword 切成一次调用 `dialogue_gen_per_call` 条讨论点。一次调用只带一个 keyword，上下文干净。`opinion` 空着的文件整个跳过，屏幕上会说明跳了哪个。
    - 四张开跑前的面板：`panel_input` 每个领域几个关键词多少讨论点切成几批、`panel_params` 模型和形状和并发、`panel_tokens` 一次调用喂进去多少 token 并按 system 的五块拆开、`panel_estimate` 这一趟的预估用量加当前余额。
    - `generate_all()` 并发跑。线程池按 `concurrency` 开，屏幕底部两行常驻：`_line_progress` 画进度条和刚跑完的那一批，`_line_usage` 画累计 token 和重发次数，每收到一批就重画一次，跑的过程中随时看得到烧了多少。
    - `run_one()` 跑一批：调 `llm.generate`，成功才把原文写进 `output/raw/`。失败的批次 raw 不落盘，脏数据进不了下一步。
    - 跑完 `panel_result` 打实际用量和余额变化加失败清单，然后依次调 `parse.parse_all` 和 `check.check_all`，最后 `panel_落盘` 打产物路径。

---

### 完整运行逻辑

`input/` 下的讨论点 JSON 按 keyword 切成批，每批 `dialogue_gen_per_call` 条讨论点算一次调用。开跑前先拼好 system（人设 + 三份 reference 填进 `system.md` 的骨架），用本地 tokenizer 数一遍各块多少 token，连同预估用量和账户余额一起打在屏幕上等确认。

确认后按 `concurrency` 路并发。每一路：拼这批的 user、发请求、拿 `parse` 那套校验判合不合格、不合格就把毛病清单接在原始 user 后面重发最多 `max_retry` 次，合格才把原文写进 `output/raw/`。屏幕底部两行实时刷进度和累计 token。

全部跑完，`parse_all` 把 raw 按 `=== / U / A` 拆开组装成 `dialogues_<domain>.json`，全部解析成功才落盘并删 raw。`check_all` 拿词表和句式规则过一遍，硬失败清零才算这批能进训练集。

省钱主线是 DeepSeek 的前缀缓存：system 每次调用逐字节相同，六千多 token 只有最先发出去的那几路真算钱，其余按 cache hit 计价便宜三十倍。所以一次调用只带一个 keyword，不把多个领域揉进一次请求。

失败一律往安全方向失败：轮数出界的批次 raw 不落盘，任何一段解析不过整趟不落盘，硬失败非零时 `check.py` 退出码是 1。宁可少产几段，不让残数据混进训练集。
