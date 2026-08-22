#!/usr/bin/env python3
"""质检 output/dialogues_*.json。

    python scripts/check.py       重查已有结果，不花钱

硬失败必须清零。警告交人判断。
词表和 references/words.md 必须保持一致，改一处就改另一处。
"""

import json
import re
import statistics
import sys
from collections import Counter

import ui
from config import ROOT, load

# ---------- 硬失败词表 ----------

FORBIDDEN = [
    # 网络烂梗
    "赛博朋克", "赛博", "安利", "打开新世界大门", "给力", "神马", "刻进DNA",
    "硬核", "真是让人意想不到",
    # 过度夸张、做作、书面化
    "绝了", "简直了", "简直", "爽翻", "没谁了", "无聊到爆", "带劲", "没劲",
    "多香啊", "不香吗", "邪乎", "玄乎", "黑暗料理", "残影", "别担心",
    "交织", "仿佛", "琢磨", "这还没完呢", "试图", "让人惊叹",
    # 虚假共鸣
    "谁说不是呢", "可不是嘛", "这就尴尬了", "唬住了", "哭笑不得",
    # 口语糟粕
    "屎尿屁", "心里咯噔", "哎呀妈呀", "这也太搞了", "搞笑", "奇葩",
    # 拟声词
    "嘤嘤嘤", "哈哈", "咕咕", "呜呜", "咚咚", "隐约记得",
    # 后缀句式
    "到爆", "到窒息",
    # 收尾套话
    "反正我是服了", "越想越离谱", "越想越不可思议", "光想想就觉得不可思议",
]

JARGON = [
    "赋能", "抓手", "商业闭环", "价值闭环", "能力沉淀", "拉通", "底层逻辑",
    "顶层设计", "认知跃迁", "价值释放", "能力建设", "降本增效", "内容矩阵",
    "全链路", "组合拳", "打开想象空间", "结构性机会", "关键命题", "深层逻辑",
    "技术底座", "公共底座", "技术主权", "单点风险", "主脊柱", "材料锚点",
    "认知增量", "迭代闭环",
]

HARD_STOPS = ["说白了", "说穿了", "先说结论"]

ROAD_SIGNS = ["更微妙的是", "还有一层", "只说对了一半", "值得注意的是",
              "需要指出的是", "从某种意义上说"]

AI_SELF = ["作为AI", "作为一个AI", "我是一个语言模型", "我是AI", "我没有情感",
           "作为人工智能"]

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

NOMINALIZE = [r"进行了[一二三四五六七八九十]?次?\S{1,6}",
              r"实现了\S{1,6}(?:增长|提升|优化)",
              r"完成了对\S{1,10}的",
              r"起到了\S{1,10}作用",
              r"具有\S{1,10}意义"]

# ---------- 警告词表 ----------

SINGLE_CHAR = ["怼", "呗", "贼", "逗", "懵", "炫", "噗", "喵", "亲"]

CONTEXT_JARGON = ["沉淀", "颗粒度", "对齐", "协同", "链路", "生态位", "心智",
                  "范式", "方法论", "核心变量", "打法", "想象空间", "闭环", "不丢"]

LYRIC = ["安放", "抵达", "微光", "褶皱", "丰盈", "滚烫", "轻盈", "赤裸",
         "剥开", "锋利", "坚硬", "柔软"]

PIVOT_SOFT = [r"我一直以为[^。！？]{1,40}(?:后来|才)",
              r"回头才发现", r"答案恰恰相反",
              r"大家都说[^。！？]{1,30}(?:可|但)真相"]

MODAL = "啊嘛呢咯"
CV_FLOOR = 0.40

# 模型不知道人名时会留个坑等人填，这种进了训练集就是教模型输出模板。
PLACEHOLDER = [
    (r"[XxＸ]{2,}|×{2,}", "占位符 XX"),
    (r"[【】\[\]{}]", "方括号或花括号"),
    (r"某某|张三|李四", "泛指占位"),
]



def han(s):
    return len(re.sub(r"[^一-鿿\w]", "", s))


def long_clauses(s):
    return sum(1 for c in re.split(r"[。！？!?]", s) if han(c) > 15)


def check_message(role, text, limit):
    """返回 (硬失败列表, 警告列表)"""
    hard, warn = [], []

    for w in FORBIDDEN:
        if w in text:
            hard.append(f"禁词「{w}」")
    for w in JARGON:
        if w in text:
            hard.append(f"黑话「{w}」")
    for w in HARD_STOPS + ROAD_SIGNS + AI_SELF:
        if w in text:
            hard.append(f"禁用表述「{w}」")
    for p in PIVOT:
        if re.search(p, text):
            hard.append("翻案腔")
            break
    for p in NOMINALIZE:
        if re.search(p, text):
            hard.append("名词化")
            break
    if "—" in text or "――" in text:
        hard.append("破折号")
    if re.search(r"[：:]", text) and not re.search(r"[：:]\s*[「“\"]", text):
        hard.append("提示性冒号")

    for pattern, name in PLACEHOLDER:
        if re.search(pattern, text):
            hard.append(name)
    n_modal = sum(text.count(c) for c in MODAL)
    cap = 2 if long_clauses(text) >= 2 else 1
    if n_modal > cap:
        hard.append(f"语气词 {n_modal} 个，上限 {cap}")

    if role == "assistant":
        if text.rstrip().endswith(("？", "?")):
            hard.append("问句结尾")
        if han(text) > limit:
            hard.append(f"{han(text)} 字，超过上限 {limit}")

    for w in SINGLE_CHAR:
        if w in text:
            warn.append(f"疑似禁词「{w}」")
    for w in CONTEXT_JARGON:
        if w in text:
            warn.append(f"语境黑话「{w}」")
    for w in LYRIC:
        if w in text:
            warn.append(f"抒情词「{w}」")
    for p in PIVOT_SOFT:
        if re.search(p, text):
            warn.append("疑似翻案腔变形")
            break
    if len(re.findall(r"挺.{1,4}的", text)) >= 2:
        warn.append("同句两处「挺X的」")

    return hard, warn


def hard_of(dialogue, cfg, profile):
    """一段对话的硬失败清单。落盘前当闸门用，也给 llm 的重发判断用。

    只查单条消息级别的规则。跨对话的那些（开场撞车、反问过密）算不到
    单段头上，留在 check_all 里当警告。

    Args:
        dialogue (dict): 一段对话，要有 messages。
        cfg (dict): config.load() 的返回值。
        profile (str): 用哪一档的字数上限。

    Returns:
        list[str]: 硬失败，一条一行，形如「第2条  问句结尾」。空列表表示这段能用。
    """
    limit = cfg["profile_max_chars"][profile]
    out = []
    for i, m in enumerate(dialogue["messages"], 1):
        hard, _ = check_message(m["role"], m["content"], limit)
        out += [f"第{i}条  {x}" for x in hard]
    return out


def check_file(path, cfg):
    data = json.loads(path.read_text(encoding="utf-8"))
    limit = cfg["profile_max_chars"][data["profile"]]
    hard, warn = [], []
    openers, rhetorical = Counter(), 0

    for d in data["dialogues"]:
        tag = d["source"]
        msgs = d["messages"]

        for i, m in enumerate(msgs):
            h, w = check_message(m["role"], m["content"], limit)
            hard += [f"{tag} 第{i+1}条  {x}" for x in h]
            warn += [f"{tag} 第{i+1}条  {x}" for x in w]

        lens = [han(m["content"]) for m in msgs if m["role"] == "assistant"]
        if len(lens) > 2 and statistics.mean(lens):
            cv = statistics.pstdev(lens) / statistics.mean(lens)
            if cv < CV_FLOOR:
                warn.append(f"{tag}  各条长度太齐，变异系数 {cv:.2f}")

        if msgs:
            openers[msgs[0]["content"][:4]] += 1
        a = [m for m in msgs if m["role"] == "assistant"]
        if len(a) >= 2 and a[-2]["content"].rstrip().endswith(("？", "?")):
            rhetorical += 1

    for head, n in openers.items():
        if n >= 3:
            warn.append(f"跨对话开场撞车：{n} 段以「{head}」开头")
    total = len(data["dialogues"])
    if total and rhetorical / total > 0.3:
        warn.append(f"反问收尾过密：{rhetorical}/{total} 段倒数第二轮用了反问")

    return total, hard, warn


def check_all(cfg):
    """质检 output/ 下所有 dialogues_*.json，把结果印出来。

    Args:
        cfg (dict): config.load() 的返回值。

    Returns:
        int: 硬失败条数。0 表示这批数据可以用。
    """
    paths = sorted((ROOT / cfg["output_dir"]).glob("dialogues_*.json"))
    if not paths:
        ui.panel(ui.warn("质检"), ["output/ 下没有 dialogues_*.json"])
        return 0

    all_hard, all_warn, total = [], [], 0
    for path in paths:
        n, hard, warn = check_file(path, cfg)
        total += n
        all_hard += [f"[{path.stem}] {x}" for x in hard]
        all_warn += [f"[{path.stem}] {x}" for x in warn]

    verdict = ui.ok(f"{ui.OK} 全过") if not all_hard else ui.bad(f"{ui.BAD} {len(all_hard)} 条硬失败")
    rows = [f"{total} 段对话    {verdict}    {ui.warn(str(len(all_warn)) + ' 条警告')}"]

    if all_hard:
        rows += ["", ui.bad("硬失败")] + _clip(all_hard, 25)
    if all_warn:
        rows += ["", ui.warn("警告  人判断，不拦")] + _clip(all_warn, 12)
    if not all_hard:
        rows += ["", ui.dim("硬失败为 0，这批可以进训练集")]

    ui.panel("质检", rows)
    return len(all_hard)


def _clip(items, limit):
    """列表太长就截断，末尾补一句还剩多少条。

    Args:
        items (list[str]): 原始列表。
        limit (int): 最多显示几条。

    Returns:
        list[str]: 显示用的行。
    """
    shown = ["  " + x for x in items[:limit]]
    if len(items) > limit:
        shown.append(ui.dim(f"  另有 {len(items) - limit} 条"))
    return shown


if __name__ == "__main__":
    sys.exit(1 if check_all(load()) else 0)
