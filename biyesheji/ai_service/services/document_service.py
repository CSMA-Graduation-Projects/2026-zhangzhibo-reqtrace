from __future__ import annotations

# 文档与需求管理的主业务服务。
# 主线：上传文档 -> 解析文本 -> 规则提取需求 -> 保存需求与证据 -> 记录变更和版本。

import hashlib
import json
import re
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ai_service.models.change_event import ChangeEvent
from ai_service.models.requirement import Requirement
from ai_service.models.requirement_revision import RequirementRevision
from ai_service.models.requirement_evidence import RequirementEvidence
from ai_service.models.uploaded_document import UploadedDocument
from ai_service.models.document_evaluation import DocumentEvaluationBenchmark, DocumentEvaluationRecord
from ai_service.services.llm_client import chat_text

# 上传文件统一保存目录，启动时如果目录不存在会自动创建。
# 上传文件统一放到 ai_service/uploaded_docs，便于和代码文件分开管理。
UPLOAD_DIR = Path(__file__).resolve().parents[1] / "uploaded_docs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# 生成上传文档的业务编号，格式包含时间戳和随机后缀。
def _now_code() -> str:
    return datetime.now().strftime("DOC%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]


# 清理上传文件名中的非法字符，避免路径穿越或保存异常。
def _safe_filename(name: str) -> str:
    name = (name or "document").strip().replace("\\", "_").replace("/", "_")
    name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_.-]+", "_", name)
    return name or "document"


# 保存用户上传的文件到 uploaded_docs 目录，并返回系统生成的文件名和路径。
def save_upload_file(filename: str, content: bytes) -> tuple[str, Path]:
    # 先清理原文件名，防止特殊字符影响保存路径。
    safe = _safe_filename(filename)
    # 保留文件扩展名，后续解析时需要根据扩展名判断类型。
    suffix = Path(safe).suffix or ".txt"
    stored = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}{suffix}"
    path = UPLOAD_DIR / stored
    # 文件内容按二进制写入，避免文本编码影响上传保存。
    path.write_bytes(content or b"")
    return stored, path


# 从 docx 文件内部 XML 中提取正文、页眉和页脚文本。
def _extract_docx_text(path: Path) -> str:
    try:
        # docx 本质是 zip 包，正文文本在内部 XML 文件中。
        with zipfile.ZipFile(path) as zf:
            # 除正文外，也读取页眉页脚，避免文档关键内容遗漏。
            names = ["word/document.xml"]
            names += [n for n in zf.namelist() if n.startswith("word/header") or n.startswith("word/footer")]
            paragraphs: list[str] = []
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            for name in names:
                if name not in zf.namelist():
                    continue
                # 解析 Word XML，再逐段提取 w:t 文本节点。
                root = ET.fromstring(zf.read(name))
                for p in root.findall(".//w:p", ns):
                    texts = [t.text or "" for t in p.findall(".//w:t", ns)]
                    line = "".join(texts).strip()
                    if line:
                        paragraphs.append(line)
            return "\n".join(paragraphs).strip()
    except Exception:
        return ""


# 根据文件类型提取文本内容，优先解析 docx，其他文件按常见编码读取。
def extract_text_from_file(path: Path, filename: str = "") -> str:
    suffix = (Path(filename or path.name).suffix or "").lower()
    # Word 文档优先走 docx 解析，解析失败再尝试普通文本读取。
    if suffix == ".docx":
        text = _extract_docx_text(path)
        if text:
            return text

    data = path.read_bytes()
    # 文本文档可能来自不同系统，因此按常见编码依次尝试。
    for enc in ("utf-8", "utf-8-sig", "gb18030", "gbk", "latin1"):
        try:
            text = data.decode(enc, errors="ignore")
            if text.strip():
                return text.strip()
        except Exception:
            continue
    return ""


# 规范化需求编号。
# 本系统当前统一使用 R1、R2、R3……作为入库编号。
# 原文中的 BR、NFR、UC 或章节信息保留在 source_location 中，便于回看来源。
def _normalize_req_code(code: str, index: int) -> str:
    try:
        no = int(index)
    except Exception:
        no = 1
    return f"R{max(no, 1)}"


def _clean_rule_lines(text: str) -> list[str]:
    """把 Word 解析出来的文本整理成可扫描的行列表。"""
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for raw in value.split("\n"):
        line = re.sub(r"\s+", " ", (raw or "").strip())
        if line:
            lines.append(line)
    return lines


def _drop_front_matter_and_toc(lines: list[str]) -> list[str]:
    """去掉封面和目录页，避免目录中的“2.2产品功能6”被误当正文标题。"""
    body_starts = [
        i for i, line in enumerate(lines)
        if re.fullmatch(r"1[.．、\s]*引言", line.strip())
    ]
    if len(body_starts) >= 2:
        return lines[body_starts[1]:]
    if body_starts:
        return lines[body_starts[0]:]
    # 有些文档目录里会把页码粘在标题后，例如“2.2产品功能6”；这类行不作为正文入口。
    for i, line in enumerate(lines):
        if re.fullmatch(r"1[.．、\s]*目的", line.strip()) or re.fullmatch(r"1[.．、\s]*概述", line.strip()):
            return lines[i:]
    return lines


def _is_heading_line(line: str) -> bool:
    value = (line or "").strip()
    if re.match(r"^表\s*\d+(?:\.\d+)?\s+.+", value):
        return True
    # 支持 1.引言、3.2.3功能描述、3 业务需求；不把“2）用户需求”这类列表项当标题。
    return bool(
        re.match(r"^\d+[.．]\s*.+", value)
        or re.match(r"^\d+(?:[.．]\d+)+\s*.+", value)
        or re.match(r"^\d+\s+.+", value)
    )


def _heading_level(line: str) -> int:
    m = re.match(r"^(\d+(?:\.\d+)*)", line or "")
    if not m:
        return 99
    return len(m.group(1).split("."))


def _next_block_end(lines: list[str], start: int, *, same_or_higher: bool = False) -> int:
    base_level = _heading_level(lines[start]) if start < len(lines) else 99
    for j in range(start + 1, len(lines)):
        if not _is_heading_line(lines[j]):
            continue
        if not same_or_higher or _heading_level(lines[j]) <= base_level:
            return j
    return len(lines)


def _find_line_index(lines: list[str], pattern: str, start: int = 0) -> int:
    reg = re.compile(pattern)
    for i in range(start, len(lines)):
        if reg.search(lines[i]):
            return i
    return -1


def _find_section_range(lines: list[str], start_pattern: str, end_patterns: list[str] | None = None) -> tuple[int, int] | None:
    start = _find_line_index(lines, start_pattern)
    if start < 0:
        return None
    end = len(lines)
    if end_patterns:
        for pat in end_patterns:
            idx = _find_line_index(lines, pat, start + 1)
            if idx >= 0:
                end = min(end, idx)
    else:
        end = _next_block_end(lines, start, same_or_higher=True)
    return start, end


def _field_value(block: list[str], label: str, stop_labels: set[str] | None = None) -> str:
    labels = stop_labels or {
        "需求编号", "需求名称", "需求描述", "优先级", "验收要点",
        "用例编号", "用例名称", "用例描述", "执行者", "参与者",
        "前置条件", "后置条件", "主事件流", "异常事件流", "业务规则",
        "编号", "业务需求", "业务价值", "类别", "验收标准", "测试功能", "测试项", "输入/操作", "检验点", "预期结果", "验收",
    }
    for i, line in enumerate(block):
        if line.strip() != label:
            continue
        values: list[str] = []
        for nxt in block[i + 1:]:
            nxt = nxt.strip()
            if not nxt:
                continue
            if nxt in labels:
                break
            if _is_heading_line(nxt):
                break
            values.append(nxt)
        return "\n".join(values).strip()
    return ""


def _clean_requirement_title(title: str, fallback: str = "需求") -> str:
    """清理需求点名称，避免出现“xxx（说明”这类括号未闭合标题。"""
    value = re.sub(r"\s+", " ", str(title or "")).strip(" ：:。；;，,")
    if not value:
        return fallback

    # 标题经常来自“界面（可选择时间、类型，……）”这类长句。
    # 如果前面按逗号/分号截断后导致括号不完整，则删除未闭合括号及其后面的残留内容。
    bracket_pairs = [("（", "）"), ("(", ")"), ("【", "】"), ("[", "]")]
    for left, right in bracket_pairs:
        last_left = value.rfind(left)
        last_right = value.rfind(right)
        if last_left != -1 and last_left > last_right:
            value = value[:last_left].rstrip(" ：:。；;，,")

    # 如果标题整体仍然过长，优先去掉完整括号说明，保留前面的功能名称。
    if len(value) > 80:
        value = re.sub(r"（[^（）]{1,80}）", "", value)
        value = re.sub(r"\([^()]{1,80}\)", "", value)
        value = value.strip(" ：:。；;，,")

    return (value or fallback)[:80]


def _sentence_title(text: str, fallback: str = "需求") -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip(" ：:。；;，,")
    value = re.sub(r"^(系统|平台|软件|用户|读者|馆员|管理员|发布者|参与者|管理人员|普通读者|系统管理员)?(应当|应|需要|必须|能够|可以|支持|可|允许|提供)", "", value).strip(" ，,。；;：:")
    value = re.split(r"[，,。；;：:]", value, maxsplit=1)[0].strip()
    return _clean_requirement_title(value, fallback)


def _compact_detail(parts: list[str], max_chars: int = 1600) -> str:
    cleaned: list[str] = []
    for part in parts:
        value = re.sub(r"\n{3,}", "\n\n", str(part or "").strip())
        if value:
            cleaned.append(value)
    text = "\n".join(cleaned).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip("，。；、\n ") + "。"
    return text


def _normalize_key(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())[:260]


def _add_requirement(
    out: list[dict[str, str]],
    seen: set[str],
    *,
    title: str,
    description: str,
    source_excerpt: str,
    source_location: str,
) -> None:
    title = _clean_requirement_title(title, "需求")[:200]
    description = str(description or "").strip()
    source_excerpt = str(source_excerpt or description or title).strip()[:1200]
    source_location = str(source_location or "文档规则提取").strip()[:180]
    if not title or not description:
        return
    # 过滤过短、明显不是需求的内容。
    if len(title) < 2 or len(description) < 4:
        return
    noise_titles = {"项目", "内容", "说明", "状态", "版本", "作者", "日期", "目录", "待定项"}
    if title in noise_titles:
        return
    key = _normalize_key(title + description)
    if not key or key in seen:
        return
    seen.add(key)
    out.append({
        "req_code": f"R{len(out) + 1}",
        "title": title,
        "description": description,
        "source_excerpt": source_excerpt,
        "source_location": source_location,
    })


def _strip_bullet(line: str) -> str:
    return re.sub(r"^[·•●○\-—*\s]+", "", str(line or "")).strip()


def _is_bullet_line(line: str) -> bool:
    return bool(re.match(r"^[·•●○\-*]\s*\S+", line or ""))


def _is_role_heading(line: str) -> bool:
    return bool(re.match(r"^\d+\.\d+\.\d+\.\d+\s*.+$", line or "")) or line in {"参与者", "发布者", "管理员", "共有功能"}


def _extract_structured_requirement_blocks(lines: list[str], out: list[dict[str, str]], seen: set[str]) -> None:
    """提取字段式需求块，如“需求名称/需求描述”“用例名称/主事件流”等。"""
    for i, line in enumerate(lines):
        # 功能性需求块：标题行可能是“4.1 R1 用户登录与身份识别”，也可能只有“需求编号/需求名称/需求描述”。
        if re.match(r"^\d+\.\d+\s+R\s*\d+\s+.+", line, flags=re.I) or line == "需求编号":
            start = i
            if line == "需求编号":
                start = max(i - 1, 0)
            end = _next_block_end(lines, start)
            block = lines[start:end]
            title = _field_value(block, "需求名称")
            desc = _field_value(block, "需求描述")
            acceptance = _field_value(block, "验收要点")
            if title and desc:
                detail = _compact_detail([desc, f"验收要点：{acceptance}" if acceptance else ""])
                original_code = _field_value(block, "需求编号")
                location = line if line != "需求编号" else f"需求字段块 / 原编号：{original_code or '未标明'}"
                _add_requirement(out, seen, title=title, description=detail, source_excerpt=desc, source_location=location)

        # 用例规约表：按字段结构提取，一张表作为一条需求。
        if re.match(r"^表\s*\d+(?:\.\d+)?\s*.+用例规约$", line) or re.match(r"^\d+\.\d+\s+UC[-_]?\d+\s+.+", line, flags=re.I):
            end = _next_block_end(lines, i)
            block = lines[i:end]
            title = _field_value(block, "用例名称") or re.sub(r"^表\s*\d+(?:\.\d+)?\s*|用例规约$", "", line).strip()
            use_desc = _field_value(block, "用例描述")
            actor = _field_value(block, "执行者") or _field_value(block, "参与者")
            pre = _field_value(block, "前置条件")
            post = _field_value(block, "后置条件")
            main = _field_value(block, "主事件流")
            abnormal = _field_value(block, "异常事件流")
            rules = _field_value(block, "业务规则")
            if title and (use_desc or main or rules):
                detail = _compact_detail([
                    use_desc,
                    f"执行者：{actor}" if actor else "",
                    f"前置条件：{pre}" if pre else "",
                    f"后置条件：{post}" if post else "",
                    f"主事件流：{main}" if main else "",
                    f"异常事件流：{abnormal}" if abnormal else "",
                    f"业务规则：{rules}" if rules else "",
                ])
                _add_requirement(out, seen, title=title, description=detail, source_excerpt=use_desc or main or title, source_location=line)


def _extract_business_table(lines: list[str], out: list[dict[str, str]], seen: set[str]) -> None:
    """提取业务需求表：编号 / 业务需求 / 业务价值 / 优先级。"""
    rg = _find_section_range(lines, r"^3\s*业务需求|^\d+(?:\.\d+)*\s*业务需求", [r"^4\s*功能", r"^\d+(?:\.\d+)*\s*功能"])
    if not rg:
        return
    start, end = rg
    section = lines[start:end]
    # 支持 BR 编号表，也支持无编号的“业务需求/业务价值”表。
    for i, line in enumerate(section):
        if re.fullmatch(r"BR[-_]?\d+", line, flags=re.I):
            demand = section[i + 1].strip() if i + 1 < len(section) else ""
            value = section[i + 2].strip() if i + 2 < len(section) else ""
            if not demand or demand in {"业务需求", "业务价值", "优先级"}:
                continue
            desc = _compact_detail([demand, f"业务价值：{value}" if value and value not in {"高", "中", "低"} else ""])
            _add_requirement(out, seen, title=_sentence_title(demand, "业务需求"), description=desc, source_excerpt=demand, source_location=f"业务需求 / 原编号：{line}")


def _extract_product_function_table(lines: list[str], out: list[dict[str, str]], seen: set[str]) -> None:
    """提取“产品功能”表，适配没有编号但有功能/概述/用户三列表的真实文档。"""
    rg = _find_section_range(lines, r"^\d+(?:\.\d+)*\s*产品功能|产品的主要功能|主要功能有", [r"^\d+(?:\.\d+)*\s*用户特点", r"^\d+(?:\.\d+)*\s*约束", r"^3\."])
    if not rg:
        return
    start, end = rg
    section = lines[start:end]
    # 找表头“功能 / 概述 / 用户”之后的三元组。
    head = -1
    for i in range(len(section) - 2):
        if section[i] == "功能" and section[i + 1] == "概述" and section[i + 2] == "用户":
            head = i + 3
            break
    if head < 0:
        return
    i = head
    while i + 2 < len(section):
        title, desc, user = section[i], section[i + 1], section[i + 2]
        if _is_heading_line(title) or title in {"功能", "概述", "用户"}:
            break
        if len(title) <= 30 and len(desc) >= 6:
            detail = _compact_detail([desc, f"适用用户：{user}" if user and not _is_heading_line(user) else ""])
            _add_requirement(out, seen, title=title, description=detail, source_excerpt=desc, source_location="产品功能表")
        i += 3


def _extract_external_interface_bullets(lines: list[str], out: list[dict[str, str]], seen: set[str]) -> None:
    """提取外部接口需求中的界面、软件接口等项目符号。"""
    sections = [
        (r"^\d+\.\d+\.\d+\s*用户界面", [r"^\d+\.\d+\.\d+\s*硬件接口", r"^\d+\.\d+\.\d+\s*软件接口"]),
        (r"^\d+\.\d+\.\d+\s*软件接口", [r"^\d+\.\d+\.\d+\s*通信接口", r"^\d+\.\d+\s*功能需求"]),
        (r"^\d+\.\d+\.\d+\s*通信接口", [r"^\d+\.\d+\s*功能需求"]),
    ]
    for start_pat, end_pats in sections:
        rg = _find_section_range(lines, start_pat, end_pats)
        if not rg:
            continue
        start, end = rg
        source = lines[start]
        for line in lines[start + 1:end]:
            value = _strip_bullet(line)
            if not value or value.startswith("待定项") or value in {"参与者", "发布者", "管理员"}:
                continue
            # 用户界面只提取明显的界面入口；软件接口提取接口动作。
            if _is_bullet_line(line) or any(k in value for k in ["界面", "接口", "发布", "查看", "导出", "申请", "查找", "报名", "审核", "注销", "修改"]):
                title = _sentence_title(value, value[:20])
                _add_requirement(out, seen, title=title, description=value, source_excerpt=value, source_location=source)


def _extract_detailed_role_functions(lines: list[str], out: list[dict[str, str]], seen: set[str]) -> None:
    """提取“功能描述（详细）”中的参与者/发布者/管理员/共有功能块。"""
    rg = _find_section_range(lines, r"^\d+\.\d+\.\d+\s*功能描述（?详细）?", [r"^\d+\.\d+\.\d+\s*用户场景", r"^\d+\.\d+\s*性能需求"])
    if not rg:
        return
    start, end = rg
    role = "功能描述"
    i = start + 1
    while i < end:
        line = lines[i]
        # 识别角色小节。
        role_match = re.match(r"^\d+\.\d+\.\d+\.\d+\s*(.+)$", line)
        if role_match:
            role = role_match.group(1).strip()
            i += 1
            continue
        if line in {"参与者", "发布者", "管理员", "共有功能"}:
            role = line
            i += 1
            continue
        if _is_bullet_line(line):
            title = _strip_bullet(line)
            # 收集到下一个项目符号、角色小节或大章节为止。
            desc_lines: list[str] = []
            j = i + 1
            while j < end:
                nxt = lines[j]
                if _is_bullet_line(nxt) or _is_role_heading(nxt) or re.match(r"^\d+\.\d+\.\d+\s+", nxt):
                    break
                desc_lines.append(nxt)
                j += 1
            desc = _compact_detail(desc_lines, max_chars=1800) or f"{role}可使用{title}功能。"
            _add_requirement(out, seen, title=title, description=desc, source_excerpt=desc[:800], source_location=f"功能描述（详细）/{role}")
            i = j
            continue
        i += 1


def _extract_nfr_table_rows(lines: list[str], out: list[dict[str, str]], seen: set[str]) -> None:
    """全局提取 NFR-xx 表格行，避免非功能需求章节标题不统一时漏提。"""
    for i, line in enumerate(lines):
        if not re.fullmatch(r"NFR[-_]?\d+", line, flags=re.I):
            continue
        category = lines[i + 1].strip() if i + 1 < len(lines) else "非功能"
        desc = lines[i + 2].strip() if i + 2 < len(lines) else ""
        standard = lines[i + 3].strip() if i + 3 < len(lines) else ""
        if not desc or category in {"类别", "需求描述", "验收标准"}:
            continue
        detail = _compact_detail([desc, f"验收标准：{standard}" if standard and not re.fullmatch(r"NFR[-_]?\d+", standard, flags=re.I) else ""])
        _add_requirement(out, seen, title=f"{category}需求" if not category.endswith("需求") else category, description=detail, source_excerpt=desc, source_location=f"非功能性需求 / 原编号：{line}")


def _extract_nonfunctional_sections(lines: list[str], out: list[dict[str, str]], seen: set[str]) -> None:
    """提取性能、移植性、稳定性、时间特性、适应性和其他非功能需求。"""
    rg = _find_section_range(lines, r"^\d+\.\d+\s*性能需求|^\d+\s*非功能性需求", [r"^4\.?\s*验收", r"^5\.?\s*其他需求", r"^\d+\s*验收"])
    if not rg:
        return
    start, end = rg
    i = start + 1
    while i < end:
        line = lines[i]
        if re.match(r"^\d+\.\d+\.\d+\s*.+", line):
            title = re.sub(r"^\d+\.\d+\.\d+\s*", "", line).strip()
            j = i + 1
            desc_lines: list[str] = []
            while j < end and not re.match(r"^\d+\.\d+\.\d+\s*.+", lines[j]):
                # 跳过纯表头，但保留实际约束内容。
                if lines[j] not in {"字段", "精度", "备注", "表格", "导出", "格式"}:
                    desc_lines.append(lines[j])
                j += 1
            desc = _compact_detail(desc_lines, max_chars=2000)
            if desc:
                _add_requirement(out, seen, title=f"{title}要求", description=desc, source_excerpt=desc[:800], source_location=line)
            i = j
            continue
        # NFR 表形式：NFR-01 / 类别 / 描述 / 验收标准。
        if re.fullmatch(r"NFR[-_]?\d+", line, flags=re.I):
            category = lines[i + 1].strip() if i + 1 < end else "非功能"
            desc = lines[i + 2].strip() if i + 2 < end else ""
            standard = lines[i + 3].strip() if i + 3 < end else ""
            if desc:
                detail = _compact_detail([desc, f"验收标准：{standard}" if standard else ""])
                _add_requirement(out, seen, title=f"{category}需求", description=detail, source_excerpt=desc, source_location=f"非功能性需求 / 原编号：{line}")
            i += 4
            continue
        i += 1


def _extract_acceptance_feature_groups(lines: list[str], out: list[dict[str, str]], seen: set[str]) -> None:
    """从验收验证标准中提取高层测试功能，避免把按钮级步骤全部拆成需求。"""
    rg = _find_section_range(lines, r"^4\.?\s*验收|^\d+\.?\s*验收验证标准", [r"^5\.?\s*其他需求", r"^\d+\.?\s*其他需求"])
    if not rg:
        return
    start, end = rg
    # 只提取“xxx功能”这类高层验收项，跳过输入框、标题栏、按钮级细节。
    skip = {"输入框", "主界面操作", "标题栏元素", "页面响应及跳转", "界面跳转", "界面响应", "下拉选择菜单", "数字匹配", "信息匹配"}
    for i in range(start + 1, end):
        line = lines[i]
        if not line.endswith("功能") or line in skip or len(line) > 30:
            continue
        # 找到后面若干行作为说明。
        desc_lines: list[str] = []
        for nxt in lines[i + 1:min(i + 8, end)]:
            if nxt.endswith("功能") and len(nxt) <= 30:
                break
            if nxt not in skip and nxt not in {"测试功能", "测试项", "输入/操作", "检验点", "预期结果", "验收"}:
                desc_lines.append(nxt)
        desc = _compact_detail(desc_lines, max_chars=800) or f"系统应满足{line}相关验收要求。"
        _add_requirement(out, seen, title=f"{line}验收", description=desc, source_excerpt=desc[:800], source_location="验收验证标准")


def _extract_keyword_sentences_in_requirement_sections(lines: list[str], out: list[dict[str, str]], seen: set[str]) -> None:
    """在需求相关章节内用关键词补充提取普通散写句。"""
    allow = False
    section = "需求章节"
    allow_heads = ["需求", "功能", "接口", "用例", "性能", "产品功能"]
    deny_heads = ["目的", "范围", "定义", "引用", "综述", "总体描述", "产品描述", "用户特点", "约束", "假设", "依赖", "用户场景", "背景", "修改历史", "目录", "其他需求", "附录", "优先级"]
    keywords = ["系统应", "系统需要", "系统必须", "应支持", "应提供", "应记录", "应允许", "必须", "能够", "可以", "支持", "提供", "记录", "导出", "审核", "发布", "报名", "签到", "查询", "统计", "维护", "修改", "删除"]
    for line_no, line in enumerate(lines, start=1):
        if _is_heading_line(line):
            allow = any(k in line for k in allow_heads) and not any(k in line for k in deny_heads)
            section = line
            continue
        if not allow or len(line) < 10 or len(line) > 260:
            continue
        if any(k in line for k in deny_heads) or _is_bullet_line(line) or re.match(r"^[a-zA-Z][.．]", line) or "用户需求、迫切" in line:
            continue
        prev_line = lines[line_no - 2] if line_no >= 2 else ""
        next_line = lines[line_no] if line_no < len(lines) else ""
        # 已有编号表格中的业务价值、验收标准等不再拆成单独需求。
        if next_line in {"高", "中", "低"} or re.fullmatch(r"(?:BR|NFR|UC)[-_]?\d+", prev_line, flags=re.I) or re.fullmatch(r"(?:BR|NFR|UC)[-_]?\d+", next_line, flags=re.I):
            continue
        if not any(k in line for k in keywords):
            continue
        # 排除已经被结构化块覆盖的短片段。
        if any(line[:28] in req.get("description", "") or line[:28] in req.get("source_excerpt", "") for req in out):
            continue
        _add_requirement(out, seen, title=_sentence_title(line, "需求"), description=line, source_excerpt=line, source_location=f"{section} / 第{line_no}行")


# 规则方法从文档文本中提取需求点。
# 当前策略不依赖 AI，也不要求文档必须有编号；按“章节 + 表格 + 项目符号 + 角色功能块 + 需求关键词”综合提取。
def fallback_extract_requirements(text: str) -> dict[str, Any]:
    lines = _drop_front_matter_and_toc(_clean_rule_lines(text))
    requirements: list[dict[str, str]] = []
    seen: set[str] = set()

    # 先处理结构较强的内容，再补充无编号散写内容。
    _extract_structured_requirement_blocks(lines, requirements, seen)
    _extract_business_table(lines, requirements, seen)
    _extract_product_function_table(lines, requirements, seen)
    _extract_external_interface_bullets(lines, requirements, seen)
    _extract_detailed_role_functions(lines, requirements, seen)
    _extract_nfr_table_rows(lines, requirements, seen)
    _extract_nonfunctional_sections(lines, requirements, seen)
    _extract_acceptance_feature_groups(lines, requirements, seen)
    _extract_keyword_sentences_in_requirement_sections(lines, requirements, seen)

    relations = infer_relations(requirements)
    return {
        "requirements": requirements,
        "relations": relations,
        "extract_mode": "rule-section-table-bullet-role",
        "summary": f"按规则共提取 {len(requirements)} 条需求点，来源包括功能描述、产品功能、接口需求、非功能需求和验收条目等内容。",
    }


# 需求点提取不调用 AI；AI 仅保留文档整体分析功能。
def ai_extract_requirements(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {"requirements": [], "relations": [], "extract_mode": "empty", "summary": "文档为空，未提取需求。"}
    return fallback_extract_requirements(text)


# 根据需求标题和描述中的共同关键词，推断需求之间的轻量相关关系。
def infer_relations(requirements: list[dict[str, str]]) -> list[dict[str, str]]:
    relations: list[dict[str, str]] = []
    keyword_re = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_]{2,}")
    # 先为每条需求建立关键词集合，后面通过共同关键词判断相关性。
    req_keywords: dict[str, set[str]] = {}
    stop = {"系统", "需求", "功能", "用户", "可以", "需要", "进行", "支持", "实现", "管理"}
    for req in requirements:
        text = f"{req.get('title','')} {req.get('description','')}"
        words = {w for w in keyword_re.findall(text) if w not in stop and len(w) >= 2}
        req_keywords[req.get("req_code", "")] = words

    # 两两比较需求关键词，有交集就生成一条“相关”关系。
    for i, a in enumerate(requirements):
        for b in requirements[i + 1:]:
            ac = a.get("req_code", "")
            bc = b.get("req_code", "")
            inter = sorted((req_keywords.get(ac) or set()) & (req_keywords.get(bc) or set()))
            if inter[:2]:
                relations.append({
                    "source": ac,
                    "target": bc,
                    "relation_type": "相关",
                    "evidence": "共同关键词：" + "、".join(inter[:4]),
                })
    return relations[:30]


# 从标题和描述中提取关键词，用于证据定位和关系推断。
def _keyword_tokens(*parts: str) -> list[str]:
    raw = " ".join(str(p or "") for p in parts)
    words = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]{2,}", raw)
    stop = {"系统", "需求", "功能", "用户", "可以", "需要", "进行", "支持", "实现", "管理", "普通", "相关", "信息", "页面"}
    seen: set[str] = set()
    result: list[str] = []
    for w in words:
        w = w.strip()
        if not w or w in stop or w in seen:
            continue
        seen.add(w)
        result.append(w)
    return result[:12]


# 当提取结果未返回来源证据时，根据关键词从原文中猜测最相关的证据片段。
def _guess_source_from_text(text: str, title: str, description: str, index: int = 1) -> tuple[str, str]:
    source = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [x.strip() for x in source.split("\n") if x.strip()]
    # 根据标题和描述提取关键词，用来在原文中定位证据片段。
    tokens = _keyword_tokens(title, description)
    if not lines:
        return (description or title or "")[:500], f"文档导入第{index}条需求"

    best_line = ""
    best_score = -1
    best_no = 1
    # 对文档每一行打分，命中关键词越多，越可能是来源位置。
    for no, line in enumerate(lines, start=1):
        if len(line) < 4:
            continue
        score = 0
        for token in tokens:
            if token and token in line:
                score += 2 if token in (title or "") else 1
        if title and title[:8] and title[:8] in line:
            score += 5
        if description and description[:12] and description[:12] in line:
            score += 4
        if score > best_score:
            best_score = score
            best_line = line
            best_no = no

    # 找到匹配行时，返回该行文本和行号。
    if best_score > 0 and best_line:
        return best_line[:800], f"第{best_no}行"

    pos = min(max(index - 1, 0), len(lines) - 1)
    return lines[pos][:800], f"文档第{pos + 1}个片段"


# 从 系统提取项中提取来源片段和来源位置，缺失时自动回退到原文匹配。
def _item_source_fields(item: dict[str, Any], doc_text: str, title: str, description: str, index: int) -> tuple[str, str]:
    excerpt = str(
        item.get("source_excerpt")
        or item.get("evidence_text")
        or item.get("evidence")
        or item.get("原文片段")
        or ""
    ).strip()[:1200]
    location = str(
        item.get("source_location")
        or item.get("source_label")
        or item.get("source")
        or item.get("来源位置")
        or ""
    ).strip()[:120]
    # 如果提取结果没有来源片段，就从文档正文中自动补充。
    if not excerpt:
        excerpt, guessed_location = _guess_source_from_text(doc_text, title, description, index)
        location = location or guessed_location
    if not location:
        location = f"文档导入第{index}条需求"
    return excerpt, location



# 安全读取文档记录中的 extracted_json 字段，解析失败时返回空字典。
def _load_extracted_json(doc: UploadedDocument) -> dict[str, Any]:
    try:
        data = json.loads(doc.extracted_json or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# 把需求点列表压缩成简短文本，供 AI文档分析提示词使用。
def _requirement_brief(requirements: list[dict[str, Any]], limit: int = 8) -> str:
    lines: list[str] = []
    for item in requirements[:limit]:
        code = str(item.get("req_code") or item.get("benchmark_code") or "").strip()
        title = str(item.get("title") or "").strip()
        desc = re.sub(r"\s+", " ", str(item.get("description") or "").strip())
        lines.append(f"{code} {title}：{desc[:160]}")
    return "\n".join(lines)


# 整理 AI 生成文本的格式，使标题和编号条目更适合页面展示。
def _format_structured_ai_text(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"(?<!^)(?<!\n)([一二三四五六七八九十]+、)", r"\n\n\1", value)
    value = re.sub(r"(?<!^)(?<!\n)(第[一二三四五六七八九十]+部分[:：])", r"\n\n\1", value)
    # 将 1.2.3. 或 1.内容2.内容 这类连续编号整理为“一行一个点”。
    value = re.sub(r"(?<![\dA-Za-z])(?<!\n)([1-9]\d*)[\.、．]\s*(?=[\u4e00-\u9fffA-Za-z])", r"\n\1. ", value)
    value = re.sub(r"([；;。])\s*(?=([1-9]\d*)[\.、．]\s*)", r"\1\n", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


# 限制 AI 生成内容长度，避免页面展示过长或响应过慢。
def _limit_generated_text(text: str, max_chars: int) -> str:
    value = _format_structured_ai_text(text)
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip("，。；、\n ") + "。"


# 当 AI文档分析不可用时，基于本地数据生成文档分析兜底内容。
def _fallback_document_ai_analysis(doc: UploadedDocument, extracted: dict[str, Any], requirements: list[dict[str, Any]]) -> str:
    summary = str(extracted.get("summary") or "").strip()
    req_titles = [str(r.get("title") or r.get("req_code") or "需求点").strip() for r in requirements if isinstance(r, dict)]
    req_count = len(requirements)
    core = "、".join(req_titles[:6]) if req_titles else "暂无明确需求点"
    text = (
        f"一、文档概况\n"
        f"该文档《{doc.original_filename or doc.doc_code}》共识别出 {req_count} 个需求点，主要用于描述系统的功能范围、操作流程和业务约束。"
        f"{('文档摘要为：' + summary[:120]) if summary else ''}\n\n"
        f"二、核心内容\n"
        f"文档重点涉及：{core}。这些内容构成后续需求维护、版本追溯、波及图展示和AI评估的数据基础。\n\n"
        f"三、维护建议\n"
        f"建议后续维护时重点检查需求编号、标题和描述是否清晰一致；需求发生调整后，应及时查看追溯版本和影响波及图，保证变更过程可回溯。"
    )
    return _limit_generated_text(text, 420)


# 生成或读取某份上传文档的 AI文档分析结果，并缓存到 extracted_json。
def get_document_ai_analysis(db: Session, document_id: int, force: bool = False) -> dict[str, Any]:
    doc = db.execute(select(UploadedDocument).where(UploadedDocument.id == int(document_id))).scalar_one_or_none()
    if not doc:
        raise ValueError("文档不存在")

    extracted = _load_extracted_json(doc)
    # 文档分析结果会缓存到 extracted_json，非强制刷新时直接复用。
    # 文档分析结果缓存在 extracted_json 中，避免每次打开页面都调用模型。
    cached = str(extracted.get("document_ai_analysis") or "").strip()
    # 非强制刷新时直接返回缓存结果。
    if cached and not force:
        return {
            "document_id": doc.id,
            "doc_code": doc.doc_code,
            "original_filename": doc.original_filename,
            "analysis": cached,
            "generated_by": extracted.get("document_ai_analysis_generated_by", "ai-cache"),
        }

    # 优先读取数据库中的需求点，保证分析基于最新维护结果。
    requirements = list_document_requirements(db, int(document_id))
    if not requirements:
        requirements = extracted.get("requirements", []) or []

    # 先准备本地兜底文本，AI 调用失败时页面仍能显示内容。
    fallback = _fallback_document_ai_analysis(doc, extracted, requirements)
    prompt = f"""
请根据上传的需求文档和系统提取出的需求点，生成一份结构清晰的中文 AI文档分析。
要求：
1. 不要写代码，不要写表格。
2. 可以使用“一、文档概况”“二、核心需求内容”“三、后续维护建议”等简短标题。
3. 每个标题单独占一行；每个小点也必须单独占一行。
4. 不要把多个小点挤在同一行，例如不要写成“1.……2.……3.……”，应该一点一行。
5. 总长度控制在 500—700 字以内，语言清楚通顺，避免空泛套话。

文档名称：{doc.original_filename or doc.doc_code}
文档原始摘要：{extracted.get('summary') or ''}
提取出的需求点数量：{len(requirements)}
提取出的需求点：
{_requirement_brief(requirements, 6)}
文档正文片段：
{(doc.text_content or '')[:1800]}
""".strip()
    try:
        # 文档分析属于自然语言生成，所以调用 chat_text。
        analysis = chat_text(
            "你是软件需求分析助手，负责生成结构清晰的中文总结。请保证标题清楚，每个编号小点单独一行，不要把多个小点写在同一行。",
            prompt,
            temperature=0.2,
            max_tokens=760,
        ).strip()
        analysis = _limit_generated_text(analysis, 900)
        generated_by = "ai"
        if not analysis:
            analysis = fallback
            generated_by = "rule-fallback"
    except Exception:
        analysis = fallback
        generated_by = "rule-fallback"

    # 将分析结果写回文档 JSON 字段，避免每次打开页面都重新生成。
    # 把分析结果写回 JSON 字段，作为下一次页面展示的缓存。
    extracted["document_ai_analysis"] = analysis
    extracted["document_ai_analysis_generated_by"] = generated_by
    doc.extracted_json = json.dumps(extracted, ensure_ascii=False)
    # 所有需求、事件、版本和证据写完后统一提交事务。
    db.commit()
    return {
        "document_id": doc.id,
        "doc_code": doc.doc_code,
        "original_filename": doc.original_filename,
        "analysis": analysis,
        "generated_by": generated_by,
    }


# 新增或更新某条需求对应的来源证据记录。
def upsert_requirement_evidence(
        db: Session,
        *,
        document_id: int | None,
        req_code: str,
        source_excerpt: str = "",
        source_location: str = "",
        evidence_type: str = "document",
) -> None:
    """保存每条需求的文档证据来源。"""
    if not document_id or not req_code:
        return
    req_code = (req_code or "").strip()
    # 同一文档同一需求只保留一条证据记录，有则更新、无则新增。
    # 先查询是否已有同文档同需求的证据记录。
    existing = db.execute(
        select(RequirementEvidence)
        .where(RequirementEvidence.document_id == int(document_id))
        .where(RequirementEvidence.req_code == req_code)
    ).scalar_one_or_none()
    excerpt = (source_excerpt or "").strip()[:2000]
    location = (source_location or "").strip()[:120]
    # 已存在则更新，不重复插入证据。
    if existing:
        if excerpt:
            existing.source_excerpt = excerpt
        if location:
            existing.source_location = location
        existing.evidence_type = evidence_type or existing.evidence_type
        return
    db.add(RequirementEvidence(
        document_id=int(document_id),
        req_code=req_code,
        source_excerpt=excerpt,
        source_location=location,
        evidence_type=evidence_type or "document",
    ))

# 为文档导入需求生成安全的 req_code，避免不同文档之间编号冲突。
def _document_scoped_req_code(db: Session, document_id: int, source_req_code: str) -> str:
    code = (source_req_code or "").strip().upper() or "R1"
    existing = db.execute(select(Requirement).where(Requirement.req_code == code)).scalar_one_or_none()
    if not existing or getattr(existing, "document_id", None) in (None, 0, document_id):
        return code
    # 不同文档都出现 R1 时，加文档前缀形成唯一编号。
    candidate = f"D{document_id}-{code}"
    counter = 2
    while db.execute(select(Requirement).where(Requirement.req_code == candidate)).scalar_one_or_none():
        candidate = f"D{document_id}-{code}-{counter}"
        counter += 1
    return candidate

# 把需求标题和描述整理成快照文本，用于变更事件和版本记录。
def _snapshot(title: str, description: str) -> str:
    return f"标题：{(title or '').strip()}\n描述：{(description or '').strip()}"


# 查询某条需求当前最大版本号，并计算下一版本号。
def _next_version_no(db: Session, req_code: str) -> int:
    # 版本号按 req_code 查询最大值后递增。
    latest = db.execute(
        select(func.max(RequirementRevision.version_no)).where(RequirementRevision.req_code == req_code)
    ).scalar()
    return int(latest or 0) + 1


# 记录一次需求版本变更，生成 requirement_revisions 版本链数据。
def record_requirement_revision(
        db: Session,
        *,
        req_code: str,
        title: str,
        description: str,
        change_type: str,
        source_type: str = "manual",
        document_id: int | None = None,
        event_id: int | None = None,
        old_snapshot: str = "",
        relation_json: str | dict | list | None = None,
) -> RequirementRevision:
    req_code = (req_code or "").strip()
    # relation_json 可能是字符串、字典或列表，这里统一转成可存储文本。
    rel_text = ""
    if relation_json:
        if isinstance(relation_json, str):
            rel_text = relation_json
        else:
            rel_text = json.dumps(relation_json, ensure_ascii=False)
    rev = RequirementRevision(
        req_code=req_code,
        version_no=_next_version_no(db, req_code),
        title=(title or "").strip(),
        description=(description or "").strip(),
        change_type=(change_type or "变更").strip(),
        source_type=source_type,
        document_id=document_id,
        event_id=event_id,
        old_snapshot=old_snapshot or "",
        new_snapshot=_snapshot(title, description),
        relation_json=rel_text,
    )
    db.add(rev)
    return rev


# 将文档提取结果导入数据库，写入需求、证据、变更事件和版本记录。
def import_requirements_from_document(db: Session, *, doc: UploadedDocument, extracted: dict[str, Any]) -> dict[str, Any]:
    # extracted 是 AI/规则提取后的临时结果，下面逐条写入业务表。
    reqs = extracted.get("requirements", []) or []
    relations = extracted.get("relations", []) or []
    imported: list[dict[str, Any]] = []

    for idx, item in enumerate(reqs, start=1):
        if not isinstance(item, dict):
            continue
        # 先规范化 提取结果返回的编号，再处理跨文档编号冲突。
        source_req_code = _normalize_req_code(str(item.get("req_code", "")), idx)
        req_code = _document_scoped_req_code(db, doc.id, source_req_code)
        title = str(item.get("title", "") or "").strip()[:200] or f"需求{idx}"
        description = str(item.get("description", "") or "").strip() or title

        # 如果需求已经存在则更新，否则新建需求。
        # 已有需求则更新当前内容；没有则新增一条需求。
        existing = db.execute(select(Requirement).where(Requirement.req_code == req_code)).scalar_one_or_none()
        if existing:
            old = _snapshot(existing.title or "", existing.description or "")
            existing.title = title
            existing.description = description
            existing.document_id = doc.id
            change_type = "文档更新"
        else:
            old = ""
            existing = Requirement(req_code=req_code, title=title, description=description, document_id=doc.id)
            db.add(existing)
            change_type = "文档导入"

        db.flush()
        # 每次导入或更新都生成变更事件，后续版本链依赖这个事件。
        # 变更事件记录本次导入或更新的前后文本。
        event = ChangeEvent(req_code=req_code, change_type=change_type, old_text=old, new_text=_snapshot(title, description))
        db.add(event)
        db.flush()
        # 同步写入版本表，保存本次需求的快照。
        # 每次导入/更新都同步生成版本记录。
        rev = record_requirement_revision(
            db,
            req_code=req_code,
            title=title,
            description=description,
            change_type=change_type,
            source_type="document",
            document_id=doc.id,
            event_id=event.id,
            old_snapshot=old,
            relation_json=relations,
        )
        db.flush()
        # 来源证据优先取 提取结果返回值，缺失时从原文中匹配。
        # 需求入库后立即保存来源证据，保证可追溯。
        source_excerpt, source_location = _item_source_fields(item, doc.text_content or "", title, description, idx)
        upsert_requirement_evidence(
            db,
            document_id=doc.id,
            req_code=req_code,
            source_excerpt=source_excerpt,
            source_location=source_location,
            evidence_type="document-ai" if (item.get("source_excerpt") or item.get("evidence_text") or item.get("source_label")) else "document-rule",
        )
        db.flush()
        imported.append({
            "req_code": req_code,
            "source_req_code": source_req_code,
            "title": title,
            "change_type": change_type,
            "event_id": event.id,
            "revision_id": rev.id,
            "version_no": rev.version_no,
            "document_id": doc.id,
        })

    db.commit()
    return {
        "document_id": doc.id,
        "doc_code": doc.doc_code,
        "imported_count": len(imported),
        "requirements": imported,
        "relations_count": len(relations),
        "extract_mode": extracted.get("extract_mode", "unknown"),
        "summary": extracted.get("summary", ""),
    }


# 创建上传文档数据库记录，保存解析文本和 需求提取 JSON。
def create_uploaded_document_record(
        db: Session,
        *,
        original_filename: str,
        stored_filename: str,
        content_type: str,
        file_path: str,
        text_content: str,
        extracted: dict[str, Any],
) -> UploadedDocument:
    base = f"{original_filename}-{datetime.now().isoformat()}".encode("utf-8", errors="ignore")
    doc_code = _now_code()
    if not doc_code:
        doc_code = "DOC-" + hashlib.md5(base).hexdigest()[:12]
    # 创建上传文档记录，保存文件信息、正文和提取结果。
    doc = UploadedDocument(
        doc_code=doc_code,
        original_filename=original_filename or "document",
        stored_filename=stored_filename,
        content_type=content_type or "application/octet-stream",
        file_path=file_path,
        text_content=text_content or "",
        extracted_json=json.dumps(extracted, ensure_ascii=False),
        status="已解析" if extracted.get("requirements") else "未提取到需求",
    )
    db.add(doc)
    db.flush()
    return doc


# 查询上传文档列表，并统计每份文档的需求数量、关系数量和摘要信息。
def list_documents(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(select(UploadedDocument).order_by(UploadedDocument.id.desc())).scalars().all()
    out = []
    for d in rows:
        try:
            extracted = json.loads(d.extracted_json or "{}")
        except Exception:
            extracted = {}
        # 统计该文档当前需求数量。
        req_count = db.execute(
            select(func.count()).select_from(Requirement).where(Requirement.document_id == d.id)
        ).scalar() or 0
        # 如果当前需求表没有记录，则从版本表统计历史需求数量。
        if not req_count:
            req_count = db.execute(
                select(func.count(func.distinct(RequirementRevision.req_code)))
                .select_from(RequirementRevision)
                .where(RequirementRevision.document_id == d.id)
            ).scalar() or 0
        out.append({
            "id": d.id,
            "doc_code": d.doc_code,
            "original_filename": d.original_filename,
            "content_type": d.content_type,
            "status": d.status,
            "requirements_count": int(req_count) if req_count else len(extracted.get("requirements", []) or []),
            "relations_count": len(extracted.get("relations", []) or []),
            "extract_mode": extracted.get("extract_mode", ""),
            "summary": extracted.get("summary", ""),
            "created_at": str(d.created_at),
        })
    return out


# 删除上传文档及其关联的需求、证据、版本、评估记录和上传文件。
def delete_uploaded_document(db: Session, document_id: int) -> dict[str, Any]:
    document_id = int(document_id)
    doc = db.get(UploadedDocument, document_id)
    if not doc:
        raise ValueError("文档不存在或已被删除")

    # 先收集该文档关联的需求编号和事件编号，后面按这些编号级联删除。
    # 删除前先收集该文档涉及的所有需求编号。
    req_codes: set[str] = set()
    req_codes.update(
        str(x) for x in db.execute(
            select(Requirement.req_code).where(Requirement.document_id == document_id)
        ).scalars().all() if x
    )
    req_codes.update(
        str(x) for x in db.execute(
            select(RequirementRevision.req_code).where(RequirementRevision.document_id == document_id)
        ).scalars().all() if x
    )
    # 通过版本记录找到关联变更事件，后续一起删除。
    event_ids = [
        int(x) for x in db.execute(
            select(RequirementRevision.event_id)
            .where(RequirementRevision.document_id == document_id)
            .where(RequirementRevision.event_id.is_not(None))
        ).scalars().all() if x
    ]


    # 删除服务器上的物理文件，避免数据库删了但文件还残留。
    file_deleted = False
    file_path = str(doc.file_path or "").strip()
    # 尝试删除服务器上的物理文件。
    if file_path:
        try:
            path = Path(file_path)
            if path.exists() and path.is_file():
                path.unlink()
                file_deleted = True
        except Exception:
            file_deleted = False

    # 按依赖顺序删除证据、评估、事件、版本和需求，避免残留脏数据。
    # 下面开始删除 MySQL 中与该文档相关的业务数据。
    db.execute(delete(RequirementEvidence).where(RequirementEvidence.document_id == document_id))
    db.execute(delete(DocumentEvaluationBenchmark).where(DocumentEvaluationBenchmark.document_id == document_id))
    db.execute(delete(DocumentEvaluationRecord).where(DocumentEvaluationRecord.document_id == document_id))

    if event_ids:
        db.execute(delete(ChangeEvent).where(ChangeEvent.id.in_(event_ids)))
    if req_codes:
        db.execute(delete(ChangeEvent).where(ChangeEvent.req_code.in_(sorted(req_codes))))

    db.execute(delete(RequirementRevision).where(RequirementRevision.document_id == document_id))
    if req_codes:
        db.execute(
            delete(RequirementRevision)
            .where(RequirementRevision.req_code.in_(sorted(req_codes)))
            .where(RequirementRevision.document_id.is_(None))
        )
        db.execute(delete(Requirement).where(Requirement.req_code.in_(sorted(req_codes))))
    db.execute(delete(Requirement).where(Requirement.document_id == document_id))

    filename = doc.original_filename or doc.doc_code or f"文档{document_id}"
    # 最后删除文档主记录。
    db.delete(doc)
    db.commit()

    return {
        "ok": True,
        "document_id": document_id,
        "document_name": filename,
        "deleted_requirements": len(req_codes),
        "deleted_change_events": len(set(event_ids)),
        "file_deleted": file_deleted,
        "message": f"文档《{filename}》及其需求、版本、证据和评估记录已删除。",
    }


# 根据历史版本记录修复缺失或未关联 document_id 的需求行。
def _repair_document_requirements_from_revisions(db: Session, document_id: int) -> None:
    revisions = db.execute(
        select(RequirementRevision)
        .where(RequirementRevision.document_id == document_id)
        .order_by(RequirementRevision.req_code.asc(), RequirementRevision.version_no.asc(), RequirementRevision.id.asc())
    ).scalars().all()

    # 没有版本记录时，用当前 requirements 表临时生成 v1 节点。
    if not revisions:
        return

    # 同一需求可能有多个版本，这里取最新版本用于修复当前需求表。
    latest: dict[str, RequirementRevision] = {}
    # 每条版本记录会转换成前端图谱中的一个版本节点。
    for rev in revisions:
        latest[rev.req_code] = rev

    changed = False
    for code, rev in latest.items():
        if (rev.change_type or "") == "删除":
            continue
        req = db.execute(select(Requirement).where(Requirement.req_code == code)).scalar_one_or_none()
        if req:
            if getattr(req, "document_id", None) in (None, 0):
                req.document_id = document_id
                changed = True
            continue

        db.add(Requirement(
            req_code=code,
            title=rev.title or code,
            description=rev.description or "",
            document_id=document_id,
        ))
        changed = True

    if changed:
        db.commit()


# 为已有文档补全缺失的需求来源证据，并同步更新图谱。
def ensure_document_evidences(db: Session, document_id: int) -> int:
    doc = db.get(UploadedDocument, int(document_id))
    if not doc:
        return 0
    _repair_document_requirements_from_revisions(db, int(document_id))

    reqs = db.execute(
        select(Requirement).where(Requirement.document_id == int(document_id)).order_by(Requirement.id.asc())
    ).scalars().all()
    if not reqs:
        return 0

    existing = db.execute(
        select(RequirementEvidence).where(RequirementEvidence.document_id == int(document_id))
    ).scalars().all()
    # 先把已有证据按 req_code 做索引，方便判断哪些缺失。
    existing_by_code = {e.req_code: e for e in existing if e.req_code}

    extracted = _load_extracted_json(doc)
    extracted_reqs = extracted.get("requirements", []) if isinstance(extracted.get("requirements", []), list) else []
    # 提取结果按编号和标题建立索引，用于补全证据。
    by_code: dict[str, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    for idx, item in enumerate(extracted_reqs, start=1):
        if not isinstance(item, dict):
            continue
        code = _normalize_req_code(str(item.get("req_code", "")), idx)
        title = str(item.get("title") or "").strip()
        if code:
            by_code[code] = item
        if title:
            by_title[title] = item

    added = 0
    for idx, req in enumerate(reqs, start=1):
        ev = existing_by_code.get(req.req_code)
        if ev and (ev.source_excerpt or ev.source_location):
            continue
        item = by_code.get(req.req_code) or by_title.get(req.title or "") or {}
        source_excerpt, source_location = _item_source_fields(
            item,
            doc.text_content or "",
            req.title or req.req_code,
            req.description or "",
            idx,
            )
        upsert_requirement_evidence(
            db,
            document_id=int(document_id),
            req_code=req.req_code,
            source_excerpt=source_excerpt,
            source_location=source_location,
            evidence_type="document-ai" if item and (item.get("source_excerpt") or item.get("evidence_text") or item.get("source_label")) else "document-rule",
        )
        added += 1

    if added:
        db.commit()
        return added


# 查询某份文档下的需求点列表，并附带来源证据片段。
def list_document_requirements(db: Session, document_id: int) -> list[dict[str, Any]]:
    _repair_document_requirements_from_revisions(db, document_id)
    ensure_document_evidences(db, document_id)

    # 查询当前文档下的需求点，供前端列表展示。
    rows = db.execute(
        select(Requirement)
        .where(Requirement.document_id == document_id)
        .order_by(Requirement.id.asc())
    ).scalars().all()
    evidences = db.execute(
        select(RequirementEvidence).where(RequirementEvidence.document_id == int(document_id))
    ).scalars().all()
    # 证据按 req_code 关联到需求列表中返回。
    evidence_by_code = {e.req_code: e for e in evidences}
    return [
        {
            "id": r.id,
            "req_code": r.req_code,
            "title": r.title,
            "description": r.description,
            "document_id": r.document_id,
            "source_location": (evidence_by_code.get(r.req_code).source_location if evidence_by_code.get(r.req_code) else "") or "",
            "source_excerpt": (evidence_by_code.get(r.req_code).source_excerpt if evidence_by_code.get(r.req_code) else "") or "",
        }
        for r in rows
    ]


# 从 JSON 字符串中解析需求关系列表。
def _parse_relations(text: str) -> list[dict[str, Any]]:
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        return data.get("relations", []) or []
    return []



# 根据需求编号推断其所属文档 ID，用于按需求编号查询图谱。
def _infer_document_ids_for_focus(db: Session, focus_req_code: str) -> set[int]:
    focus_req_code = (focus_req_code or "").strip()
    if not focus_req_code:
        return set()

    ids: set[int] = set()
    rev_doc_ids = db.execute(
        select(RequirementRevision.document_id)
        .where(RequirementRevision.req_code == focus_req_code)
        .where(RequirementRevision.document_id.is_not(None))
    ).scalars().all()
    ids.update(int(x) for x in rev_doc_ids if x)

    req_doc_ids = db.execute(
        select(Requirement.document_id)
        .where(Requirement.req_code == focus_req_code)
        .where(Requirement.document_id.is_not(None))
    ).scalars().all()
    ids.update(int(x) for x in req_doc_ids if x)
    return ids



# 基于 MySQL 中的文档、需求和版本记录构建前端影响波及图数据。
def build_requirement_version_graph(db: Session, req_code: str | None = None, document_id: int | None = None) -> dict[str, Any]:
    # 该函数直接基于 MySQL 数据生成前端影响波及图。
    focus_req_code = (req_code or "").strip()
    document_id = int(document_id) if document_id else None

    # 搜索规则：
    # 1）输入文档ID：展示该文档的完整图谱；
    # 2）只输入需求编号 R1/R2：展示该需求自己的单链版本图；
    # 这样既满足“文档检索看全局”，也满足“需求检索看单链”。
    # 只传需求编号时展示单链；传文档ID时展示完整文档图谱。
    req_single_chain = bool(focus_req_code and not document_id)
    inferred_doc_ids: set[int] = set()
    if req_single_chain:
        inferred_doc_ids = _infer_document_ids_for_focus(db, focus_req_code)

    # 文档 ID 搜索时不能只筛 requirement_revisions.document_id。
    # 部分版本记录可能缺少 document_id，但 req_code 仍属于该文档；因此先找出该文档下的需求编号，
    # 再按 req_code 拉取这些需求的完整版本链，避免搜索文档 ID 后图谱缺版本。
    doc_req_codes: set[str] = set()
    doc_id_by_req_code: dict[str, int] = {}
    # 按文档查图谱时，先找该文档下所有需求编号，再拉完整版本链。
    # 文档查询时先找出该文档下所有 req_code，避免版本 document_id 缺失造成漏查。
    if document_id:
        req_rows = db.execute(
            select(Requirement.req_code, Requirement.document_id)
            .where(Requirement.document_id == document_id)
        ).all()
        rev_rows = db.execute(
            select(RequirementRevision.req_code, RequirementRevision.document_id)
            .where(RequirementRevision.document_id == document_id)
        ).all()
        for code, doc_id in [*req_rows, *rev_rows]:
            code = (code or "").strip()
            if code:
                doc_req_codes.add(code)
                if doc_id:
                    doc_id_by_req_code[code] = int(doc_id)

    stmt = select(RequirementRevision).order_by(
        RequirementRevision.version_no.asc(),
        RequirementRevision.req_code.asc(),
        RequirementRevision.id.asc(),
    )
    if document_id:
        if doc_req_codes:
            stmt = stmt.where(RequirementRevision.req_code.in_(sorted(doc_req_codes)))
        else:
            stmt = stmt.where(RequirementRevision.document_id == document_id)
    elif req_single_chain:
        stmt = stmt.where(RequirementRevision.req_code == focus_req_code)
    elif inferred_doc_ids:
        stmt = stmt.where(RequirementRevision.document_id.in_(sorted(inferred_doc_ids)))
    revisions = db.execute(stmt).scalars().all()

    # 如果没有版本记录，就用当前需求临时构造 v1 节点，保证图谱不空白。
    if not revisions:
        req_stmt = select(Requirement).order_by(Requirement.req_code.asc())
        if document_id:
            req_stmt = req_stmt.where(Requirement.document_id == document_id)
        elif req_single_chain:
            req_stmt = req_stmt.where(Requirement.req_code == focus_req_code)
        elif inferred_doc_ids:
            req_stmt = req_stmt.where(Requirement.document_id.in_(sorted(inferred_doc_ids)))
        reqs = db.execute(req_stmt).scalars().all()
        revisions = [
            RequirementRevision(
                id=0,
                req_code=r.req_code,
                version_no=1,
                title=r.title,
                description=r.description,
                change_type="当前需求",
                source_type="manual",
                document_id=getattr(r, "document_id", None),
                event_id=None,
                old_snapshot="",
                new_snapshot=_snapshot(r.title, r.description),
                relation_json="",
            )
            for r in reqs
        ]

    doc_ids: set[int] = set()
    for rev in revisions:
        effective_doc_id = getattr(rev, "document_id", None) or doc_id_by_req_code.get(rev.req_code)
        if effective_doc_id:
            doc_ids.add(int(effective_doc_id))
    if document_id:
        doc_ids.add(document_id)
    if inferred_doc_ids:
        doc_ids.update(inferred_doc_ids)

    documents = []
    if doc_ids:
        documents = db.execute(
            select(UploadedDocument)
            .where(UploadedDocument.id.in_(sorted(doc_ids)))
            .order_by(UploadedDocument.id.asc())
        ).scalars().all()

    doc_by_id = {int(d.id): d for d in documents}

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    columns: dict[int, dict[str, Any]] = {}
    latest_by_req: dict[str, list[dict[str, Any]]] = {}

    # 第 0 列固定放上传文档节点，后面各列放需求版本节点。
    # 图谱第 0 列是上传文档节点。
    if documents:
        columns[0] = {"version_no": 0, "title": "上传需求文档", "count": len(documents)}
        for d in documents:
            try:
                extracted = json.loads(d.extracted_json or "{}")
            except Exception:
                extracted = {}
            nodes.append({
                "id": f"doc-{d.id}",
                "req_code": d.doc_code,
                "version_no": 0,
                "label": d.original_filename or d.doc_code,
                "title": d.original_filename or d.doc_code,
                "description": extracted.get("summary") or (d.text_content or "")[:160],
                "change_type": d.status or "已解析",
                "source_type": "uploaded_document",
                "document_id": d.id,
                "event_id": None,
                "column": 0,
                "type": "DOCUMENT",
                "focused": False,
            })

    for rev in revisions:
        ver = int(rev.version_no or 1)
        effective_doc_id = getattr(rev, "document_id", None) or doc_id_by_req_code.get(rev.req_code)
        col_title = "初始提取需求" if ver == 1 else f"第{ver - 1}次变更后的需求点"
        columns.setdefault(ver, {"version_no": ver, "title": col_title, "count": 0})
        columns[ver]["count"] += 1
        node_id = f"rev-{getattr(rev, 'id', 0) or rev.req_code + '-' + str(ver)}"
        node = {
            "id": node_id,
            "req_code": rev.req_code,
            "version_no": ver,
            "label": f"{rev.req_code} v{ver}",
            "title": rev.title,
            "description": rev.description,
            "change_type": rev.change_type,
            "source_type": rev.source_type,
            "document_id": effective_doc_id,
            "event_id": rev.event_id,
            "column": ver,
            "type": "REQ_VERSION",
            "focused": False,
        }
        evidence = db.execute(
            select(RequirementEvidence)
            .where(RequirementEvidence.document_id == int(effective_doc_id))
            .where(RequirementEvidence.req_code == rev.req_code)
        ).scalar_one_or_none() if effective_doc_id else None
        if evidence:
            node["source_location"] = evidence.source_location or ""
            node["source_excerpt"] = evidence.source_excerpt or ""
        nodes.append(node)
        latest_by_req.setdefault(rev.req_code, []).append(node)

        # 初始版本节点和文档节点之间建立“提取”边。
        if ver == 1 and effective_doc_id and int(effective_doc_id) in doc_by_id:
            edges.append({
                "source": f"doc-{int(effective_doc_id)}",
                "target": node_id,
                "type": "DOC_TO_REQUIREMENT",
                "label": "提取",
                "reason": f"从文档《{doc_by_id[int(effective_doc_id)].original_filename}》中提取出需求 {rev.req_code}",
                "focused": False,
            })

    # 同一 req_code 的不同版本按顺序连成变更链。
    # 同一需求的多个版本按版本号连接成变更链。
    for code, items in latest_by_req.items():
        items.sort(key=lambda x: (x["version_no"], x["id"]))
        for a, b in zip(items, items[1:]):
            edges.append({
                "source": a["id"],
                "target": b["id"],
                "type": "CHANGE_CHAIN",
                "label": b.get("change_type") or "变更",
                "reason": f"{code} 从 v{a['version_no']} 变更为 v{b['version_no']}",
                "focused": False,
            })

    if req_single_chain:
        focus_msg = f" 当前按需求编号 {focus_req_code} 展示单链版本图谱。"
    elif focus_req_code:
        focus_msg = " 当前按文档 ID 展示完整需求版本图谱。"
    else:
        focus_msg = ""
    result = {
        "mode": "document_requirement_versions",
        "req_code": focus_req_code or "",
        "focus_req_code": focus_req_code or "",
        "document_id": document_id,
        "columns": [columns[k] for k in sorted(columns)],
        "nodes": nodes,
        "edges": edges,
        "summary": f"当前图谱基于上传需求文档和需求变更版本生成，共 {len(nodes)} 个节点、{len(edges)} 条关系线。{focus_msg}",
        "notes": [
            "最前面一列展示上传的需求文档节点，文档节点通过“提取”关系连接到从该文档中提取出的初始需求点。",
            "后续列展示需求被修改后的版本节点，同一 req_code 的版本之间使用变更链路连接。",
            "输入文档ID时展示该文档完整图谱；只输入需求编号时展示该需求自己的单链版本图。",
            "不同需求点之间不再绘制关系线，避免图中出现交叉线和误导性连接。",
        ],
    }
    return result
