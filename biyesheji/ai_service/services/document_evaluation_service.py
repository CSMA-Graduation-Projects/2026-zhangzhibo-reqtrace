# 评估服务：负责“系统提取结果”和“人工基准”的对比计算。
# 读取某份文档的 系统提取结果
# → 建立或保存人工确认基准
# → 将 系统提取结果和人工基准进行匹配
# → 统计 TP、FP、FN
# → 计算 Precision、Recall、F1
# → 生成评估摘要和 AI评估报告
# → 保存到 document_evaluation_records 表
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from ai_service.models.document_evaluation import DocumentEvaluationBenchmark, DocumentEvaluationRecord
from ai_service.models.requirement import Requirement
from ai_service.models.requirement_revision import RequirementRevision
from ai_service.models.uploaded_document import UploadedDocument
from ai_service.services.llm_client import chat_text

# 把 系统提取的JSON字符串转成字典
def _loads_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        # 数据库中 JSON 可能是字符串，先去掉空白再解析。
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}

# 保证返回的一定是列表
def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []

# 统一需求编号格式
def _normalize_req_code(code: str | None, index: int) -> str:
    raw = str(code or "").strip().upper()
    # 把 R-01、r_1 等写法统一成 R1，减少匹配误差。
    m = re.search(r"R\s*[-_]?\s*(\d+)", raw, flags=re.I)
    if m:
        return f"R{int(m.group(1))}"
    return raw or f"R{index}"

# 清洗文本，便于相似度比较
def _clean_text(text: str | None) -> str:
    text = str(text or "").strip().lower()
    text = re.sub(r"[\s\r\n\t]+", "", text)
    text = re.sub(r"[，。！？；：、,.!?;:()（）\[\]【】《》<>\-—_]+", "", text)
    return text

# 计算两个文本的相似度
def _similarity(a: str | None, b: str | None) -> float:
    aa = _clean_text(a)
    bb = _clean_text(b)
    if not aa and not bb:
        return 1.0
    if not aa or not bb:
        return 0.0
    # 完全相同直接满分；包含关系给较高分；否则使用 SequenceMatcher。
    if aa == bb:
        return 1.0
    if aa in bb or bb in aa:
        return 0.88
    return SequenceMatcher(None, aa, bb).ratio()

# 统一格式
def _item_from_dict(item: dict[str, Any], index: int) -> dict[str, Any]:
    code = _normalize_req_code(item.get("req_code") or item.get("benchmark_code"), index)
    title = str(item.get("title") or "").strip() or f"需求{index}"
    description = str(item.get("description") or "").strip() or title
    return {
        "req_code": code,
        "benchmark_code": code,
        "title": title,
        "description": description,
    }

# 根据文档 ID查询文档
def _document_or_404(db: Session, document_id: int) -> UploadedDocument:
    doc = db.execute(select(UploadedDocument).where(UploadedDocument.id == int(document_id))).scalar_one_or_none()
    if not doc:
        raise ValueError("文档不存在")
    return doc

# 从上传文档的 extracted_json 中读取 系统提取出的需求点
def get_ai_extracted_requirements(doc: UploadedDocument) -> list[dict[str, Any]]:
    # 系统提取结果保存在 uploaded_documents.extracted_json 中。
    # 系统提取结果存放在 uploaded_documents.extracted_json 中。
    data = _loads_dict(doc.extracted_json or "{}")
    rows = []
    seen: set[str] = set()
    # 将 系统提取项统一整理成评估需要的字段。
    for idx, item in enumerate(_safe_list(data.get("requirements")), start=1):
        if not isinstance(item, dict):
            continue
        row = _item_from_dict(item, idx)
        code = row["req_code"]
        # 同一文档内重复编号自动加后缀，避免评估时编号冲突。
        if code in seen:
            # 遇到重复编号时追加后缀，避免前端编辑和匹配时冲突。
            suffix = 2
            new_code = f"{code}-{suffix}"
            while new_code in seen:
                suffix += 1
                new_code = f"{code}-{suffix}"
            row["req_code"] = row["benchmark_code"] = new_code
        seen.add(row["req_code"])
        rows.append(row)
    return rows

# 查询某份文档已经保存的人工基准
def list_benchmarks(db: Session, document_id: int) -> list[dict[str, Any]]:
    # 查询该文档已经保存的人工基准。
    rows = db.execute(
        select(DocumentEvaluationBenchmark)
        .where(DocumentEvaluationBenchmark.document_id == int(document_id))
        .order_by(DocumentEvaluationBenchmark.id.asc())
    ).scalars().all()
    return [
        {
            "id": r.id,
            "document_id": r.document_id,
            "benchmark_code": r.benchmark_code,
            "req_code": r.benchmark_code,
            "title": r.title or "",
            "description": r.description or "",
            "created_at": str(r.created_at),
            "updated_at": str(r.updated_at),
        }
        for r in rows
    ]

# 获取某份文档最新的一条评估记录
def _latest_record(db: Session, document_id: int) -> dict[str, Any] | None:
    row = db.execute(
        select(DocumentEvaluationRecord)
        .where(DocumentEvaluationRecord.document_id == int(document_id))
        .order_by(desc(DocumentEvaluationRecord.id))
        .limit(1)
    ).scalar_one_or_none()
    if not row:
        return None
    return _record_to_dict(row)

# 把数据库里的评估记录对象转成前端可用的字典
def _record_to_dict(row: DocumentEvaluationRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "document_id": row.document_id,
        "precision": round(float(row.precision or 0), 2),
        "recall": round(float(row.recall or 0), 2),
        "f1": round(float(row.f1 or 0), 2),
        "tp_count": int(row.tp_count or 0),
        "fp_count": int(row.fp_count or 0),
        "fn_count": int(row.fn_count or 0),
        "ai_count": int(row.ai_count or 0),
        "benchmark_count": int(row.benchmark_count or 0),
        "matched": _safe_list(_loads_json(row.matched_json)),
        "false_positive": _safe_list(_loads_json(row.false_positive_json)),
        "false_negative": _safe_list(_loads_json(row.false_negative_json)),
        "ai_summary": row.ai_summary or "",
        "ai_report": row.ai_report or "",
        "created_at": str(row.created_at),
    }


def _loads_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value or "[]")
        except Exception:
            return []
    return []

# 返回 AI评估页面的文档列表
def list_evaluation_documents(db: Session) -> list[dict[str, Any]]:
    # 评估页面左侧文档列表按上传时间倒序展示。
    # 评估页面文档列表按上传时间倒序展示。
    docs = db.execute(select(UploadedDocument).order_by(desc(UploadedDocument.id))).scalars().all()
    out: list[dict[str, Any]] = []
    for doc in docs:
        # 同时统计 系统提取数量。
        # 系统提取结果是预测集合。
        ai_items = get_ai_extracted_requirements(doc)
        # 同时统计人工基准数量，用来判断这份文档能否直接评估。
        # 统计该文档人工基准数量。
        bench_count = db.execute(
            select(func.count())
            .select_from(DocumentEvaluationBenchmark)
            .where(DocumentEvaluationBenchmark.document_id == doc.id)
        ).scalar() or 0
        latest = _latest_record(db, doc.id)
        out.append({
            "id": doc.id,
            "doc_code": doc.doc_code,
            "original_filename": doc.original_filename,
            "status": doc.status,
            "ai_count": len(ai_items),
            "benchmark_count": int(bench_count),
            "has_benchmark": int(bench_count) > 0,
            "latest_record": latest,
            "created_at": str(doc.created_at),
        })
    return out

# 获取某份文档的评估详情
def get_document_evaluation_detail(db: Session, document_id: int) -> dict[str, Any]:
    doc = _document_or_404(db, document_id)
    data = _loads_dict(doc.extracted_json or "{}")
    ai_items = get_ai_extracted_requirements(doc)
    # 详情页需要展示最近一次评估记录。
    latest = _latest_record(db, document_id)
    # 评估详情会带上版本变更记录，报告里可以引用这些上下文。
    changes = _get_document_change_items(db, document_id)
    # 页面详情加载优先使用已保存的 AI 报告；没有报告时显示本地结构化兜底内容，避免页面打开时等待外部模型。
    # 旧记录没有报告时，用本地兜底报告补充展示。
    if latest and not latest.get("ai_report"):
        latest["ai_report"] = _fallback_ai_evaluation_report(doc, latest, changes)
    return {
        "document": {
            "id": doc.id,
            "doc_code": doc.doc_code,
            "original_filename": doc.original_filename,
            "status": doc.status,
            "summary": data.get("summary", ""),
            "extract_mode": data.get("extract_mode", ""),
            "created_at": str(doc.created_at),
        },
        "ai_requirements": ai_items,
        "benchmarks": list_benchmarks(db, document_id),
        "latest_record": latest,
        "ai_report": latest.get("ai_report") if latest else _fallback_ai_evaluation_report(doc, None, changes),
    }

# 根据 系统提取结果自动初始化人工基准
def create_benchmark_from_ai(db: Session, document_id: int, overwrite: bool = False) -> dict[str, Any]:
    doc = _document_or_404(db, document_id)
    # 如果已有人工基准，默认不覆盖，避免误删人工修改结果。
    # 已有基准时默认保留，避免覆盖人工修改。
    existing_count = db.execute(
        select(func.count()).select_from(DocumentEvaluationBenchmark).where(DocumentEvaluationBenchmark.document_id == int(document_id))
    ).scalar() or 0
    if existing_count and not overwrite:
        return get_document_evaluation_detail(db, document_id)

    # overwrite=True 时才允许删除旧基准并重新生成。
    if overwrite:
        # 覆盖基准时先删旧数据，再用 系统提取结果重新生成。
        # 保存人工基准采用先清空再重写，保证与页面编辑结果一致。
        db.execute(delete(DocumentEvaluationBenchmark).where(DocumentEvaluationBenchmark.document_id == int(document_id)))

    # 这里用 系统提取结果初始化基准，后续仍可由人工编辑修正。
    ai_items = get_ai_extracted_requirements(doc)
    for idx, item in enumerate(ai_items, start=1):
        db.add(DocumentEvaluationBenchmark(
            document_id=int(document_id),
            benchmark_code=item.get("req_code") or f"R{idx}",
            title=item.get("title") or f"需求{idx}",
            description=item.get("description") or item.get("title") or "",
        ))
    db.commit()
    return get_document_evaluation_detail(db, document_id)

# 保存用户编辑后的人工基准
def save_benchmark_items(db: Session, document_id: int, items: list[dict[str, Any]]) -> dict[str, Any]:
    _document_or_404(db, document_id)
    # 保存人工基准时采用“先删后写”，保证数据库与页面编辑结果一致。
    db.execute(delete(DocumentEvaluationBenchmark).where(DocumentEvaluationBenchmark.document_id == int(document_id)))
    seen: set[str] = set()
    for idx, raw in enumerate(items or [], start=1):
        if not isinstance(raw, dict):
            continue
        row = _item_from_dict(raw, idx)
        code = row["benchmark_code"]
        if code in seen:
            suffix = 2
            new_code = f"{code}-{suffix}"
            while new_code in seen:
                suffix += 1
                new_code = f"{code}-{suffix}"
            code = new_code
        seen.add(code)
        db.add(DocumentEvaluationBenchmark(
            document_id=int(document_id),
            benchmark_code=code,
            title=row["title"],
            description=row["description"],
        ))
    db.commit()
    return get_document_evaluation_detail(db, document_id)

# 计算一条 系统提取需求和一条人工基准之间的匹配分数
def _match_score(ai: dict[str, Any], bench: dict[str, Any]) -> float:
    # 编号、标题、描述共同决定匹配分数；编号一致时权重更高。
    ai_code = _normalize_req_code(ai.get("req_code"), 0)
    bench_code = _normalize_req_code(bench.get("benchmark_code") or bench.get("req_code"), 0)
    # 编号相同时认为编号匹配，否则主要依赖标题和描述相似度。
    code_score = 1.0 if ai_code and bench_code and ai_code == bench_code else 0.0
    title_score = _similarity(ai.get("title"), bench.get("title"))
    desc_score = _similarity(ai.get("description"), bench.get("description"))
    if code_score:
        # 编号可信时，编号、标题、描述分别按权重参与总分。
        return 0.35 * code_score + 0.35 * title_score + 0.30 * desc_score
    # 编号不一致时，更依赖描述内容判断是否同一需求。
    return 0.45 * title_score + 0.55 * desc_score

# 根据评估指标生成一句评估摘要
def _make_summary(tp: int, fp: int, fn: int, precision: float, recall: float, f1: float) -> str:
    if tp == 0 and fp == 0 and fn == 0:
        return "当前文档暂无可评估需求点，建议先上传并提取需求文档。"
    if fp == 0 and fn == 0:
        return "系统提取结果与人工确认基准完全一致，需求点覆盖完整，未发现明显误抽或漏抽。"
    parts = [f"本次评估匹配成功{tp}条"]
    if fp:
        parts.append(f"系统多提{fp}条")
    if fn:
        parts.append(f"系统漏提{fn}条")
    parts.append(f"Precision为{precision:.2f}%，Recall为{recall:.2f}%，F1为{f1:.2f}%。")
    if fp or fn:
        parts.append("建议根据差异明细对人工基准或提取规则进行复核。")
    return "，".join(parts).replace("。，", "。")


# 找出某份文档关联的所有需求编号
def _doc_requirement_codes(db: Session, document_id: int) -> set[str]:
    # 汇总当前需求表和历史版本表中的需求编号。
    codes: set[str] = set()
    req_codes = db.execute(
        select(Requirement.req_code).where(Requirement.document_id == int(document_id))
    ).scalars().all()
    codes.update(str(x).strip() for x in req_codes if str(x or "").strip())

    rev_codes = db.execute(
        select(RequirementRevision.req_code).where(RequirementRevision.document_id == int(document_id))
    ).scalars().all()
    codes.update(str(x).strip() for x in rev_codes if str(x or "").strip())
    return codes

# 查询某份文档相关的需求版本变更记录
def _get_document_change_items(db: Session, document_id: int) -> list[dict[str, Any]]:
    codes = _doc_requirement_codes(db, document_id)
    stmt = (
        select(RequirementRevision)
        .order_by(RequirementRevision.req_code.asc(), RequirementRevision.version_no.asc(), RequirementRevision.id.asc())
    )
    if codes:
        stmt = stmt.where(RequirementRevision.req_code.in_(sorted(codes)))
    else:
        stmt = stmt.where(RequirementRevision.document_id == int(document_id))
    rows = db.execute(stmt).scalars().all()
    items: list[dict[str, Any]] = []
    for r in rows:
        items.append({
            "req_code": r.req_code or "",
            "version_no": int(r.version_no or 1),
            "title": r.title or "",
            "change_type": r.change_type or "",
            "source_type": r.source_type or "",
            "old_snapshot": r.old_snapshot or "",
            "new_snapshot": r.new_snapshot or "",
            "created_at": str(r.created_at),
        })
    return items

# 把变更记录压缩成简短文本
def _change_summary_text(changes: list[dict[str, Any]], limit: int = 10) -> str:
    if not changes:
        return "当前文档暂无需求版本变更记录。"
    lines: list[str] = []
    # 报告中只展示前几条变更，避免内容过长。
    for item in changes[:limit]:
        old_text = re.sub(r"\s+", " ", item.get("old_snapshot") or "无")
        new_text = re.sub(r"\s+", " ", item.get("new_snapshot") or "")
        lines.append(
            f"{item.get('req_code')} v{item.get('version_no')} [{item.get('change_type')}]："
            f"{old_text[:100]} -> {new_text[:140]}"
        )
    return "\n".join(lines)

# 整理 AI 生成报告的格式
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

# 限制 AI报告长度
def _limit_generated_text(text: str, max_chars: int) -> str:
    value = _format_structured_ai_text(text)
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip("，。；、\n ") + "。"

# 生成本地兜底评估报告
def _fallback_ai_evaluation_report(doc: UploadedDocument, record: dict[str, Any] | None, changes: list[dict[str, Any]]) -> str:
    # 没有评估记录时，生成提示型报告。
    if not record:
        text = (
            "一、评估对象\n"
            f"当前文档为《{doc.original_filename or doc.doc_code}》，系统尚未生成AI评估记录。\n\n"
            "二、评估状态\n"
            "请先根据系统提取结果建立人工确认基准，再运行AI评估。系统将计算Precision、Recall和F1，并展示差异明细。\n\n"
            "三、变更情况\n"
            f"{_change_summary_text(changes, 5)}"
        )
        return _limit_generated_text(text, 520)

    fp = record.get("false_positive") or []
    fn = record.get("false_negative") or []
    metrics = (
        f"Precision={record.get('precision', 0):.2f}%，"
        f"Recall={record.get('recall', 0):.2f}%，"
        f"F1={record.get('f1', 0):.2f}%"
    )
    diff_text = (
        f"匹配成功 {record.get('tp_count', 0)} 条，系统多提 {record.get('fp_count', 0)} 条，"
        f"系统漏提 {record.get('fn_count', 0)} 条。"
    )
    fp_text = "系统多提项：无。" if not fp else "系统多提项包括：" + "、".join(str(x.get("title") or x.get("req_code") or "") for x in fp[:5]) + "。"
    fn_text = "系统漏提项：无。" if not fn else "系统漏提项包括：" + "、".join(str(x.get("title") or x.get("benchmark_code") or "") for x in fn[:5]) + "。"

    text = (
        "一、评估对象与数据来源\n"
        f"本次评估对象为文档《{doc.original_filename or doc.doc_code}》。系统以系统首次提取需求点为待评估结果，以人工确认基准为标准答案。\n\n"
        "二、指标结果\n"
        f"本次结果为：{metrics}。{diff_text}{record.get('ai_summary') or ''}\n\n"
        "三、差异与变更总结\n"
        f"{fp_text}{fn_text} 文档版本记录显示：{_change_summary_text(changes, 5)}\n\n"
        "四、综合结论\n"
        "该报告用于说明系统需求提取结果的准确性和完整性，并辅助用户复核文档需求的后续变更情况。"
    )
    return _limit_generated_text(text, 900)

# 调用大模型生成AI评估报告
def _generate_ai_evaluation_report(db: Session, document_id: int, record: dict[str, Any] | None = None) -> str:
    doc = _document_or_404(db, document_id)
    changes = _get_document_change_items(db, document_id)
    # 先生成兜底报告，模型异常时直接返回，不影响评估流程。
    # 先准备兜底报告，模型不可用时直接使用。
    fallback = _fallback_ai_evaluation_report(doc, record, changes)
    if not record:
        return fallback

    matched_titles = []
    for x in (record.get("matched") or [])[:5]:
        ai = x.get("ai") or {}
        bm = x.get("benchmark") or {}
        matched_titles.append(f"{ai.get('req_code') or ''} {ai.get('title') or ''} ↔ {bm.get('benchmark_code') or ''} {bm.get('title') or ''}")
    fp_titles = [f"{x.get('req_code') or ''} {x.get('title') or ''}" for x in (record.get("false_positive") or [])[:5]]
    fn_titles = [f"{x.get('benchmark_code') or x.get('req_code') or ''} {x.get('title') or ''}" for x in (record.get("false_negative") or [])[:5]]

    # 将指标、差异项和版本记录放入提示词，让报告有数据依据。
    # 把指标、差异和变更记录放入提示词，让 AI 报告有依据。
    prompt = f"""
请生成一份结构清晰的中文 AI 评估报告，直接用于毕业设计系统页面展示。
要求：
1. 包含四个部分：一、评估对象与数据来源；二、Precision/Recall/F1指标总结；三、匹配与差异分析；四、文档变更总结与建议。
2. 每个部分标题单独占一行；每个小点也必须单独占一行。
3. 不要把多个小点挤在同一行，例如不要写成“1.……2.……3.……”，应该一点一行 。
4. 篇幅控制在 600—800 字左右，句子短一些，不要长篇展开，也不要在报告中出现任何字数统计说明。
5. 说明系统首次提取结果是待评估结果，人工确认基准是标准答案。
6. 必须包含该文档的变更总结，但只总结关键变更。
7. 不要编造文档之外的内容；当前系统只基于上传需求文档。
8. 报告结尾只写结论和建议，不要添加任何字数统计或括号说明。

文档名称：{doc.original_filename or doc.doc_code}
文档ID：{doc.id}
系统提取数量：{record.get('ai_count')}
人工基准数量：{record.get('benchmark_count')}
Precision：{record.get('precision')}%
Recall：{record.get('recall')}%
F1：{record.get('f1')}%
TP：{record.get('tp_count')}，FP：{record.get('fp_count')}，FN：{record.get('fn_count')}
系统评估摘要：{record.get('ai_summary') or ''}
匹配成功：
{chr(10).join(matched_titles) or '无'}
系统多提：
{chr(10).join(fp_titles) or '无'}
系统漏提：
{chr(10).join(fn_titles) or '无'}
文档变更记录：
{_change_summary_text(changes, 6)}
""".strip()
    try:
        # 评估报告是自然语言文本，因此使用 chat_text。
        text = chat_text(
            "你是软件需求文档评估专家，负责生成可信、结构清晰的AI评估报告。请保证标题清楚，每个编号小点单独一行，不要把多个小点写在同一行，也不要输出任何字数统计说明。",
            prompt,
            temperature=0.2,
            max_tokens=720,
        ).strip()
        text = _limit_generated_text(text, 1000)
        return text or fallback
    except Exception:
        return fallback

# 执行一次 AI评估
def run_document_evaluation(db: Session, document_id: int) -> dict[str, Any]:
    doc = _document_or_404(db, document_id)
    # 评估时，系统提取结果作为预测集合，人工基准作为标准答案。
    ai_items = get_ai_extracted_requirements(doc)
    # 人工基准是标准答案集合。
    bench_items = list_benchmarks(db, document_id)
    if not bench_items:
        raise ValueError("请先建立人工确认基准，再运行AI评估")

    # unmatched_bench 保存尚未被 系统提取结果命中的人工基准索引。
    # 保存尚未被匹配到的人工基准索引。
    unmatched_bench = set(range(len(bench_items)))
    matched: list[dict[str, Any]] = []
    false_positive: list[dict[str, Any]] = []

    # 每条 系统提取需求只匹配一个最相近的人工基准，避免重复命中。
    # 每条 系统提取需求都寻找最相似的一条人工基准。
    for ai in ai_items:
        best_idx: int | None = None
        best_score = 0.0
        for idx in unmatched_bench:
            score = _match_score(ai, bench_items[idx])
            if score > best_score:
                best_score = score
                best_idx = idx
        # 达到阈值 0.62，认为匹配成功，计入 TP。
        if best_idx is not None and best_score >= 0.62:
            # 分数达到阈值才算匹配成功，否则作为 系统多提项。
            bench = bench_items[best_idx]
            unmatched_bench.remove(best_idx)
            matched.append({
                "ai": ai,
                "benchmark": bench,
                "score": round(best_score, 4),
            })
        else:
            # 找不到匹配基准，说明 系统多提或误抽，计入 FP。
            false_positive.append(ai)

    # 剩下没有被命中的人工基准，就是 系统漏提项。
    # 剩余没有匹配到的人工基准，就是 系统漏提的 FN。
    false_negative = [bench_items[idx] for idx in sorted(unmatched_bench)]

    tp = len(matched)
    fp = len(false_positive)
    fn = len(false_negative)
    ai_count = len(ai_items)
    benchmark_count = len(bench_items)

    # Precision 看“抽出来的有多少是对的”；Recall 看“应该抽的有多少被抽到”。
    # Precision 表示 系统提取出的需求中有多少是正确的。
    precision = (tp / (tp + fp) * 100) if (tp + fp) else 0.0
    # Recall 表示应抽需求中有多少被系统提取到了。
    recall = (tp / (tp + fn) * 100) if (tp + fn) else 0.0
    # F1 综合精确率和召回率。
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    summary = _make_summary(tp, fp, fn, precision, recall, f1)

    # 将指标和差异明细保存下来，前端可直接展示历史评估结果。
    # 把指标和差异明细保存为一条评估记录。
    record = DocumentEvaluationRecord(
        document_id=doc.id,
        precision=precision,
        recall=recall,
        f1=f1,
        tp_count=tp,
        fp_count=fp,
        fn_count=fn,
        ai_count=ai_count,
        benchmark_count=benchmark_count,
        matched_json=json.dumps(matched, ensure_ascii=False),
        false_positive_json=json.dumps(false_positive, ensure_ascii=False),
        false_negative_json=json.dumps(false_negative, ensure_ascii=False),
        ai_summary=summary,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    record_dict = _record_to_dict(record)
    # 运行评估时调用已接入的大模型生成真正的 AI 评估报告，并保存到评估记录中；
    # 如果模型服务不可用，_generate_ai_evaluation_report 会自动返回本地兜底报告
    # 指标保存后再生成评估报告，报告可以引用本次记录。
    record_dict["ai_report"] = _generate_ai_evaluation_report(db, doc.id, record_dict)
    record.ai_report = record_dict["ai_report"]
    db.add(record)
    db.commit()
    return {
        "document": {
            "id": doc.id,
            "doc_code": doc.doc_code,
            "original_filename": doc.original_filename,
        },
        "record": record_dict,
        "ai_report": record_dict["ai_report"],
    }
