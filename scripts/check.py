"""质检对话内容，判它像不像真人说话。

本文件按调用顺序定义六个函数：

- `Count_Han`：数一段文本有多少汉字，标点不算。判字数上限时用。
- `Count_Long_Clauses`：数一段文本里有几个长分句。判语气词上限时用。
- `Check_Message`：查单独一条消息，返回硬失败和警告两份清单。上面两个都被它调。
- `Hard_Problems_Of`：查一整段对话的硬失败。`parse.py` 落盘前和 `llm.py`
  判要不要重发都调它，三处用的是同一套标准。
- `Check_File`：查一个已经落盘的 JSON，除了逐条查，还做跨对话的统计。
- `Check_All`：总入口，扫 `output/` 下所有 `dialogues_*.json` 汇总打印。
- `_Clip_List`：清单太长就截断，只被 `Check_All` 调。

硬失败是必须清零的，命中就进不了 `dialogues_*.json`。警告交给人判断，不拦。

因为 `parse.py` 在落盘前已经拿 `Hard_Problems_Of` 挡过一道，在
`dialogues_*.json` 上跑 `Check_All` 硬失败应该恒为 0，非 0 就说明闸门漏了。

下面那几张词表是从 `references/words.md`（喂给模型的用词红线文档）手抄过来的，
两边各自能改，改了一边忘另一边就会出现「提示词里禁了但质检不拦」。
`scripts/test_wordlists.py` 专门核对这件事。

重查一遍已经落盘的 JSON，不花钱：

    python scripts/check.py
"""

import json
import re
import statistics
import sys
from collections import Counter

import ui
from config import ROOT, Load_Config


# ---------- 配置区：硬失败词表 ----------

# 网络烂梗、过度夸张、虚假共鸣、口语糟粕、拟声词、后缀句式、收尾套话，命中即废。
FORBIDDEN = [
    "赛博朋克", "赛博", "安利", "打开新世界大门", "给力", "神马", "刻进DNA",
    "硬核", "真是让人意想不到",
    "绝了", "简直了", "简直", "爽翻", "没谁了", "无聊到爆", "带劲", "没劲",
    "多香啊", "不香吗", "邪乎", "玄乎", "黑暗料理", "残影", "别担心",
    "交织", "仿佛", "琢磨", "这还没完呢", "试图", "让人惊叹",
    "谁说不是呢", "可不是嘛", "这就尴尬了", "唬住了", "哭笑不得",
    "屎尿屁", "心里咯噔", "哎呀妈呀", "这也太搞了", "搞笑", "奇葩",
    "嘤嘤嘤", "咕咕", "呜呜", "咚咚", "隐约记得",
    "到爆", "到窒息",
    "反正我是服了", "越想越离谱", "越想越不可思议", "光想想就觉得不可思议",
    "我真的会谢",
]

# 企业黑话里绝对禁用的那一档，用本义的场合也不放行。
JARGON = [
    "赋能", "抓手", "商业闭环", "价值闭环", "能力沉淀", "拉通", "底层逻辑",
    "顶层设计", "认知跃迁", "价值释放", "能力建设", "降本增效", "内容矩阵",
    "全链路", "组合拳", "打开想象空间", "结构性机会", "关键命题", "深层逻辑",
    "技术底座", "公共底座", "技术主权", "单点风险", "主脊柱", "材料锚点",
    "认知增量", "迭代闭环",
]

# 硬停词：一说这几个字后面就是总结陈词，聊天里不会这么讲话。
HARD_STOPS = ["说白了", "说穿了", "先说结论"]

# 模型路标：写文章才用的转折提示语，对话里出现一律是 AI 味。
ROAD_SIGNS = ["更微妙的是", "还有一层", "只说对了一半", "值得注意的是",
              "需要指出的是", "从某种意义上说"]

# AI 自称，一句都不许有。
AI_SELF = ["作为AI", "作为一个AI", "我是一个语言模型", "我是AI", "我没有情感",
           "作为人工智能"]

# 翻案腔的八种写法。这是 AI 味最重的句式，先抑后扬装深刻。
PIVOT = [
    r"不是[^，。！？]{1,30}[，,]\s*而是",
    r"并非[^，。！？]{1,30}[，,]\s*而是",
    r"不在于[^，。！？]{1,30}[，,]\s*而在于",
    r"与其说[^，。！？]{1,30}[，,]\s*(?:倒?不如|毋宁)",
    r"你以为[^，。！？]{1,30}[，,]\s*其实",
    r"看似[^，。！？]{1,30}[，,]\s*实则",
    r"表面上[^，。！？]{1,30}[，,]\s*(?:其实|实际)",
    r"[^，。！？]{0,20}不重要[，,]\s*重要的是",
]

# 名词化：把动作说成名词，公文腔的典型标志。
NOMINALIZE = [r"进行了[一二三四五六七八九十]?次?\S{1,6}",
              r"实现了\S{1,6}(?:增长|提升|优化)",
              r"完成了对\S{1,10}的",
              r"起到了\S{1,10}作用",
              r"具有\S{1,10}意义"]

# 半角标点。正文一律用全角，「.」不查，小数点会误报。
HALF_WIDTH = r"""["',;!?()]"""

# 「哈哈」恰好两个是敷衍，三个以上是真笑，放行。
FAKE_LAUGH = r"(?<!哈)哈哈(?!哈)"

# 一段里数笑用这个，三个哈起步才算一次笑。
REAL_LAUGH = r"哈{3,}"

# 一段对话里笑最多几次，超了或者两次同形都判废。
LAUGH_CAP = 2

# 骂人写「草」不写「操」。后面跟这几个字的是操作、操心、操场这类正常词，放行。
SWEAR = r"操(?![作心场练纵控盘守])"

# 模型不知道人名时会留个坑等人填，这种进了训练集就是教模型输出模板。
PLACEHOLDER = [
    (r"[XxＸ]{2,}|×{2,}", "占位符 XX"),
    (r"[【】\[\]{}]", "方括号或花括号"),
    (r"某某|张三|李四", "泛指占位"),
]

# 语气词，单条消息里最多出现几次由下面的规则算。
MODAL_CHARS = "啊嘛呢咯"


# ---------- 配置区：警告词表 ----------

# 单字疑似禁词。「亲」会在「相亲」「亲戚」上误报，所以只算警告不拦。
SINGLE_CHAR = ["怼", "呗", "贼", "逗", "懵", "炫", "噗", "喵", "亲"]

# 看语境的企业黑话，用本义时（化学沉淀、排版对齐）是正常的。
CONTEXT_JARGON = ["沉淀", "颗粒度", "对齐", "协同", "链路", "生态位", "心智",
                  "范式", "方法论", "核心变量", "打法", "想象空间", "闭环", "不丢"]

# 抒情词，写散文用的，聊天里出现就假。
LYRIC = ["安放", "抵达", "微光", "褶皱", "丰盈", "滚烫", "轻盈", "赤裸",
         "剥开", "锋利", "坚硬", "柔软"]

# 翻案腔的软变形，不如上面八条那么确凿，只提示。
PIVOT_SOFT = [r"我一直以为[^。！？]{1,40}(?:后来|才)",
              r"回头才发现", r"答案恰恰相反",
              r"大家都说[^。！？]{1,30}(?:可|但)真相"]

# 一段对话里 assistant 各条长度的变异系数低于这个值，说明长度太齐，不像真人。
CV_FLOOR = 0.40


def Count_Han(text: str) -> int:
    """
    数一段文本有多少个字，标点不算。

    判单条消息超没超字数上限时用。中文标点占位置但不占信息量，算进去会让
    上限判得偏松。

    Args:
        text: 任意文本。
    Returns:
        汉字和字母数字加起来的个数。
    """

    return len(re.sub(r"[^一-鿿\w]", "", text))


def Count_Long_Clauses(text: str) -> int:
    """
    数一段文本里有几个超过 15 字的分句。

    语气词的上限跟句子长短挂钩：一条短消息里塞两个语气词就腻，长消息里就还好。
    这个函数给上限判定提供依据。

    Args:
        text: 任意文本。
    Returns:
        长分句的个数。
    """

    return sum(1 for one_clause in re.split(r"[。！？!?]", text) if Count_Han(one_clause) > 15)


def Check_Message(role: str, text: str, limit: int) -> tuple:
    """
    查单独一条消息，挑出所有毛病。

    毛病分两档：硬失败是必须清零的，命中就整段进不了训练集；警告交给人判断，
    不拦。问句结尾和字数上限只查 assistant，user 用问号结尾是正常的，
    长度档位也只约束 assistant。

    Args:
        role: "user" 或 "assistant"。
        text: 这条消息的正文。
        limit: assistant 单条消息的字数上限，从 `profile_max_chars` 取。
    Returns:
        `(硬失败清单, 警告清单)` 两个字符串列表。
    """

    hard_problems = []
    warn_problems = []

    for one_word in FORBIDDEN:
        if one_word in text:
            hard_problems.append(f"禁词「{one_word}」")

    for one_word in JARGON:
        if one_word in text:
            hard_problems.append(f"黑话「{one_word}」")

    for one_word in HARD_STOPS + ROAD_SIGNS + AI_SELF:
        if one_word in text:
            hard_problems.append(f"禁用表述「{one_word}」")

    # 八种写法命中任意一种就够定性了，报一条就行，不用逐条列
    for one_pattern in PIVOT:
        if re.search(one_pattern, text):
            hard_problems.append("翻案腔")
            break

    for one_pattern in NOMINALIZE:
        if re.search(one_pattern, text):
            hard_problems.append("名词化")
            break

    if "—" in text or "――" in text:
        hard_problems.append("破折号")

    # 冒号后面跟引号是在引原话，那是正常的；不跟引号就是「建议：」这种提示性用法
    if re.search(r"[：:]", text) and not re.search(r"[：:]\s*[「“\"]", text):
        hard_problems.append("提示性冒号")

    for one_pattern, problem_name in PLACEHOLDER:
        if re.search(one_pattern, text):
            hard_problems.append(problem_name)

    if re.search(HALF_WIDTH, text):
        hard_problems.append("半角标点，改成全角")

    if re.search(FAKE_LAUGH, text):
        hard_problems.append("「哈哈」两个字，要笑就多打几个")

    if re.search(SWEAR, text):
        hard_problems.append("「操」改成「草」")

    # 短消息里最多一个语气词，含两个以上长分句的消息可以放到两个
    modal_count = sum(text.count(one_char) for one_char in MODAL_CHARS)
    modal_cap = 2 if Count_Long_Clauses(text) >= 2 else 1
    if modal_count > modal_cap:
        hard_problems.append(f"语气词 {modal_count} 个，上限 {modal_cap}")

    if role == "assistant":

        # 每段都用反问收尾是 AI 最典型的毛病，把球硬踢回给用户
        if text.rstrip().endswith(("？", "?")):
            hard_problems.append("问句结尾")

        if Count_Han(text) > limit:
            hard_problems.append(f"{Count_Han(text)} 字，超过上限 {limit}")

    for one_word in SINGLE_CHAR:
        if one_word in text:
            warn_problems.append(f"疑似禁词「{one_word}」")

    for one_word in CONTEXT_JARGON:
        if one_word in text:
            warn_problems.append(f"语境黑话「{one_word}」")

    for one_word in LYRIC:
        if one_word in text:
            warn_problems.append(f"抒情词「{one_word}」")

    for one_pattern in PIVOT_SOFT:
        if re.search(one_pattern, text):
            warn_problems.append("疑似翻案腔变形")
            break

    if len(re.findall(r"挺.{1,4}的", text)) >= 2:
        warn_problems.append("同句两处「挺X的」")

    return hard_problems, warn_problems


def Hard_Problems_Of(dialogue: dict, cfg: dict, profile: str) -> list:
    """
    查一整段对话的硬失败。

    这是整条流水线唯一的内容闸门：`parse.py` 落盘前调它决定这段进 dialogues
    还是 rejected，`llm.py` 也调它决定要不要让模型重写，两处标准完全一致。

    先逐条过 `Check_Message`，再补一条整段级别的：一段里笑最多两次，
    两次不许同形。跨对话的那些统计（开场撞车、反问过密）算不到单段头上，
    留在 `Check_All` 里当警告。

    Args:
        dialogue: 一段对话，要有 messages 这个键。
        cfg: `config.Load_Config` 的返回值，用到 profile_max_chars。
        profile: 用哪一档的字数上限，例如 "P2"。
    Returns:
        硬失败清单，形如 `["第2条  问句结尾"]`。空列表表示这段能用。
    """

    char_limit = cfg["profile_max_chars"][profile]
    problems = []

    for message_index, one_message in enumerate(dialogue["messages"], 1):
        hard_problems, _ = Check_Message(one_message["role"], one_message["content"], char_limit)
        problems += [f"第{message_index}条  {one}" for one in hard_problems]

    # 把整段所有消息里的笑都捞出来，判密度和重复
    laugh_list = [
        one_laugh for one_message in dialogue["messages"]
        for one_laugh in re.findall(REAL_LAUGH, one_message["content"])
    ]

    if len(laugh_list) > LAUGH_CAP:
        problems.append(f"整段笑了 {len(laugh_list)} 次，上限 {LAUGH_CAP}")

    elif len(laugh_list) == 2 and laugh_list[0] == laugh_list[1]:
        problems.append(f"两次笑同形，都是「{laugh_list[0]}」")

    return problems


def Check_File(path, cfg: dict) -> tuple:
    """
    查一个已经落盘的 dialogues JSON。

    除了逐条查单消息，还做两项跨对话的统计：多段对话用同样的四个字开头说明
    模型在套模板，倒数第二轮大量用反问说明它在硬凑互动。这两项算不到单段头上，
    所以只在这里算，而且只当警告。

    Args:
        path: dialogues JSON 的路径。
        cfg: `config.Load_Config` 的返回值。
    Returns:
        `(这个文件里几段对话, 硬失败清单, 警告清单)`。
    """

    file_data = json.loads(path.read_text(encoding = "utf-8"))
    char_limit = cfg["profile_max_chars"][file_data["profile"]]

    hard_problems = []
    warn_problems = []
    opener_counter = Counter()
    rhetorical_count = 0

    for one_dialogue in file_data["dialogues"]:
        source_tag = one_dialogue["source"]
        message_list = one_dialogue["messages"]

        for message_index, one_message in enumerate(message_list):
            hard_list, warn_list = Check_Message(
                one_message["role"], one_message["content"], char_limit)
            hard_problems += [f"{source_tag} 第{message_index + 1}条  {one}" for one in hard_list]
            warn_problems += [f"{source_tag} 第{message_index + 1}条  {one}" for one in warn_list]

        # 变异系数是标准差除以均值，衡量这一段里 assistant 各条长度差得开不开
        assistant_lengths = [
            Count_Han(one_message["content"]) for one_message in message_list
            if one_message["role"] == "assistant"
        ]
        if len(assistant_lengths) > 2 and statistics.mean(assistant_lengths):
            length_cv = statistics.pstdev(assistant_lengths) / statistics.mean(assistant_lengths)
            if length_cv < CV_FLOOR:
                warn_problems.append(f"{source_tag}  各条长度太齐，变异系数 {length_cv:.2f}")

        # 攒开场白的前四个字，多段撞同一个开头说明在套模板
        if message_list:
            opener_counter[message_list[0]["content"][:4]] += 1

        # 倒数第二条 assistant 用问号结尾，就是那种「你觉得呢」式的硬互动
        assistant_messages = [one for one in message_list if one["role"] == "assistant"]
        if len(assistant_messages) >= 2 and assistant_messages[-2]["content"].rstrip().endswith(("？", "?")):
            rhetorical_count += 1

    for head_text, head_count in opener_counter.items():
        if head_count >= 3:
            warn_problems.append(f"跨对话开场撞车：{head_count} 段以「{head_text}」开头")

    total_count = len(file_data["dialogues"])
    if total_count and rhetorical_count / total_count > 0.3:
        warn_problems.append(f"反问收尾过密：{rhetorical_count}/{total_count} 段倒数第二轮用了反问")

    return total_count, hard_problems, warn_problems


def Check_All(cfg: dict) -> int:
    """
    质检 `output/` 下所有 dialogues JSON，把结果印成一张面板。

    Args:
        cfg: `config.Load_Config` 的返回值。
    Returns:
        硬失败条数。0 表示这批数据可以进训练集，非 0 说明落盘前那道闸门漏了。
    """

    json_paths = sorted((ROOT / cfg["output_dir"]).glob("dialogues_*.json"))
    if not json_paths:
        ui.Panel(ui.Warn("质检"), ["output/ 下没有 dialogues_*.json"])
        return 0

    all_hard = []
    all_warn = []
    total_count = 0

    for one_path in json_paths:
        file_count, hard_list, warn_list = Check_File(one_path, cfg)
        total_count += file_count
        all_hard += [f"[{one_path.stem}] {one}" for one in hard_list]
        all_warn += [f"[{one_path.stem}] {one}" for one in warn_list]

    verdict_text = ui.Ok(f"{ui.MARK_OK} 全过") if not all_hard \
        else ui.Bad(f"{ui.MARK_BAD} {len(all_hard)} 条硬失败")
    panel_rows = [f"{total_count} 段对话    {verdict_text}    {ui.Warn(str(len(all_warn)) + ' 条警告')}"]

    if all_hard:
        panel_rows += ["", ui.Bad("硬失败")] + _Clip_List(all_hard, 25)

    if all_warn:
        panel_rows += ["", ui.Warn("警告  人判断，不拦")] + _Clip_List(all_warn, 12)

    if not all_hard:
        panel_rows += ["", ui.Dim("硬失败为 0，这批可以进训练集")]

    ui.Panel("质检", panel_rows)
    return len(all_hard)


def _Clip_List(items: list, limit: int) -> list:
    """
    清单太长就截断，末尾补一句还剩多少条。

    面板上印几百条谁也不会看，详细的在 rejected JSON 里。

    Args:
        items: 原始清单。
        limit: 最多显示几条。
    Returns:
        缩进两格的显示行。
    """

    shown_lines = ["  " + one for one in items[:limit]]
    if len(items) > limit:
        shown_lines.append(ui.Dim(f"  另有 {len(items) - limit} 条"))

    return shown_lines


if __name__ == "__main__":
    sys.exit(1 if Check_All(Load_Config()) else 0)
