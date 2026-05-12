from __future__ import annotations

import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ai_service.models.requirement import Requirement
from ai_service.models.requirement_evidence import RequirementEvidence
from ai_service.models.requirement_revision import RequirementRevision
from ai_service.models.uploaded_document import UploadedDocument
from ai_service.services.document_service import ensure_document_evidences

# 导出目录：继续放在 uploaded_docs/exports 下，避免和源文件混在一起。
UPLOAD_DIR = Path(__file__).resolve().parents[1] / "uploaded_docs"
EXPORT_DIR = UPLOAD_DIR / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML_NS = "http://www.w3.org/XML/1998/namespace"

ET.register_namespace("w", W_NS)
ET.register_namespace("r", R_NS)

NS = {"w": W_NS}


@dataclass
class ExportResult:
    path: Path
    filename: str
    replaced_count: int
    changed_requirement_count: int
    change_record_count: int


def _qn(name: str) -> str:
    prefix, tag = name.split(":", 1)
    if prefix == "w":
        return f"{{{W_NS}}}{tag}"
    if prefix == "r":
        return f"{{{R_NS}}}{tag}"
    raise ValueError(f"未知 XML 前缀：{prefix}")


def _safe_filename(name: str) -> str:
    value = (name or "document").strip().replace("\\", "_").replace("/", "_")
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_.-]+", "_", value)
    return value or "document"


def _plain(text: str | None) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", str(text or "")).strip()


def _clean_text(text: str | None) -> str:
    """整理快照文本，保留换行，但去掉多余空白。"""
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _parse_snapshot(text: str | None) -> dict[str, str]:
    """解析 requirement_revisions 中保存的“标题/描述”快照。"""
    raw = _clean_text(text)
    if not raw:
        return {"title": "", "description": "", "raw": ""}

    title = ""
    desc_lines: list[str] = []
    in_desc = False
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("标题："):
            title = line.replace("标题：", "", 1).strip()
            in_desc = False
        elif line.startswith("标题:"):
            title = line.replace("标题:", "", 1).strip()
            in_desc = False
        elif line.startswith("描述："):
            desc_lines.append(line.replace("描述：", "", 1).strip())
            in_desc = True
        elif line.startswith("描述:"):
            desc_lines.append(line.replace("描述:", "", 1).strip())
            in_desc = True
        elif in_desc:
            desc_lines.append(line)

    description = "\n".join(x for x in desc_lines if x).strip()
    if not title and not description:
        description = raw
    return {"title": title, "description": description, "raw": raw}


def _snapshot_display(snapshot: str | None) -> str:
    parsed = _parse_snapshot(snapshot)
    title = _clean_text(parsed.get("title"))
    desc = _clean_text(parsed.get("description"))
    raw = _clean_text(parsed.get("raw"))

    if title and desc:
        if title in desc:
            return desc
        return f"{title}\n{desc}"
    return desc or title or raw or "无"


def _snapshot_display_labeled(snapshot: str | None) -> list[str]:
    """把快照显示为页面字段口径：需求点名 + 需求详情。

    旧版本里直接把标题和描述合并输出，容易看成“标题被拆断”。
    这里统一拆成两个字段，和系统页面里的“需求点名/需求详情”一致。
    """
    parsed = _parse_snapshot(snapshot)
    title = _clean_text(parsed.get("title"))
    desc = _clean_text(parsed.get("description"))
    raw = _clean_text(parsed.get("raw"))

    # 兼容历史数据：如果快照没有“标题：/描述：”标签，但有两行以上，
    # 默认第一行是需求点名，后续内容是需求详情。
    if not title and not desc and raw and "\n" in raw:
        parts = [x.strip() for x in raw.split("\n") if x.strip()]
        if parts:
            title = parts[0]
            desc = "\n".join(parts[1:]).strip()

    lines: list[str] = []
    if title:
        lines.append(f"需求点名：{title}")
    if desc:
        lines.append(f"需求详情：{desc}")
    if not lines:
        lines.append(raw or "无")
    return lines


def _current_text_labeled_lines(
    code: str,
    current_by_code: dict[str, Requirement],
    latest_by_code: dict[str, RequirementRevision],
) -> list[str]:
    """当前需求内容的字段化展示，作为快照缺失时的兜底。"""
    current = current_by_code.get(code)
    if current:
        title = _clean_text(current.title)
        desc = _clean_text(current.description)
        lines: list[str] = []
        if title:
            lines.append(f"需求点名：{title}")
        if desc:
            lines.append(f"需求详情：{desc}")
        return lines or [code]

    latest = latest_by_code.get(code)
    if latest:
        lines = _snapshot_display_labeled(latest.new_snapshot)
        return lines if lines != ["无"] else [_clean_text(latest.description) or _clean_text(latest.title) or "已变更"]
    return ["已变更"]


def _revision_time(rev: RequirementRevision) -> str:
    value = getattr(rev, "created_at", None)
    if not value:
        return ""
    try:
        return value.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)


def _is_manual_revision(rev: RequirementRevision) -> bool:
    """过滤掉文档初始导入记录，只保留用户后续新增/修改/删除等人工变更。"""
    change_type = (rev.change_type or "").strip()
    source_type = (rev.source_type or "").strip()

    ignored_change_types = {"文档导入", "导入", "当前需求"}
    if change_type in ignored_change_types:
        return False

    # 文档再次导入产生的自动更新不作为人工变更展示。
    if source_type == "document" and change_type in {"文档更新", "文档导入"}:
        return False

    return True


def _make_paragraph(text: str = "", *, bold: bool = False) -> ET.Element:
    p = ET.Element(_qn("w:p"))
    r = ET.SubElement(p, _qn("w:r"))
    if bold:
        r_pr = ET.SubElement(r, _qn("w:rPr"))
        ET.SubElement(r_pr, _qn("w:b"))
    t = ET.SubElement(r, _qn("w:t"))
    t.set(f"{{{XML_NS}}}space", "preserve")
    t.text = text
    return p


def _make_page_break_paragraph() -> ET.Element:
    p = ET.Element(_qn("w:p"))
    r = ET.SubElement(p, _qn("w:r"))
    br = ET.SubElement(r, _qn("w:br"))
    br.set(_qn("w:type"), "page")
    return p


def _append_paragraphs(root: ET.Element, lines: list[tuple[str, bool]]) -> None:
    body = root.find(".//w:body", NS)
    if body is None:
        return

    insert_at = len(body)
    if len(body) and body[-1].tag == _qn("w:sectPr"):
        insert_at = len(body) - 1

    for text, bold in lines:
        # 允许一条内容内部有换行，写入时拆成多个段落，避免 Word 中挤成一团。
        parts = str(text or "").split("\n") or [""]
        for index, part in enumerate(parts):
            body.insert(insert_at, _make_paragraph(part, bold=bold and index == 0))
            insert_at += 1


def _load_docx_xml(path: Path) -> tuple[dict[str, bytes], bytes]:
    with zipfile.ZipFile(path, "r") as zin:
        files = {name: zin.read(name) for name in zin.namelist()}
    if "word/document.xml" not in files:
        raise ValueError("该 docx 文件缺少 word/document.xml，无法导出文档")
    return files, files["word/document.xml"]


def _write_docx(files: dict[str, bytes], out_path: Path) -> None:
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in files.items():
            zout.writestr(name, data)


def _evidence_location(ev: RequirementEvidence | None) -> str:
    if not ev:
        return "文档相关片段"
    location = _plain(getattr(ev, "source_location", ""))
    excerpt = _plain(getattr(ev, "source_excerpt", ""))
    if location and excerpt:
        return f"{location}；来源片段：{excerpt[:120]}{'……' if len(excerpt) > 120 else ''}"
    if location:
        return location
    if excerpt:
        return f"来源片段：{excerpt[:160]}{'……' if len(excerpt) > 160 else ''}"
    return "文档相关片段"


def _requirement_title(
    code: str,
    current_by_code: dict[str, Requirement],
    latest_by_code: dict[str, RequirementRevision],
    revs: list[RequirementRevision],
) -> str:
    current = current_by_code.get(code)
    if current and _plain(current.title):
        return _plain(current.title)
    latest = latest_by_code.get(code)
    if latest and _plain(latest.title):
        return _plain(latest.title)
    for rev in reversed(revs):
        if _plain(rev.title):
            return _plain(rev.title)
        parsed = _parse_snapshot(rev.new_snapshot or rev.old_snapshot)
        if _plain(parsed.get("title")):
            return _plain(parsed.get("title"))
    return code


def _current_text(code: str, current_by_code: dict[str, Requirement], latest_by_code: dict[str, RequirementRevision]) -> str:
    current = current_by_code.get(code)
    if current:
        title = _clean_text(current.title)
        desc = _clean_text(current.description)
        if title and desc:
            if title in desc:
                return desc
            return f"{title}\n{desc}"
        return desc or title or code
    latest = latest_by_code.get(code)
    if latest:
        return _snapshot_display(latest.new_snapshot) or _clean_text(latest.description) or _clean_text(latest.title) or "已变更"
    return "已变更"


def _build_change_summary_lines(
    *,
    doc: UploadedDocument,
    manual_revs: list[RequirementRevision],
    current_by_code: dict[str, Requirement],
    latest_by_code: dict[str, RequirementRevision],
    evidence_by_code: dict[str, RequirementEvidence],
) -> list[tuple[str, bool]]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    grouped: dict[str, list[RequirementRevision]] = {}
    for rev in manual_revs:
        if not rev.req_code:
            continue
        grouped.setdefault(rev.req_code, []).append(rev)

    for revs in grouped.values():
        revs.sort(key=lambda r: (getattr(r, "created_at", None) or datetime.min, getattr(r, "id", 0) or 0))

    lines: list[tuple[str, bool]] = [
        ("当前变更说明", True),
        (f"来源文档：{doc.original_filename or doc.doc_code}", False),
        (f"导出时间：{now}", False),
        ("说明：本文档正文内容保持原样，系统仅在文档末尾汇总已发生变更的需求点；未发生变更的需求点不在此处列出。", False),
        ("", False),
    ]

    if not grouped:
        lines.append(("当前文档暂无人工变更记录。", False))
        return lines

    total_records = sum(len(v) for v in grouped.values())
    lines.append((f"本次导出共涉及 {len(grouped)} 个已变更需求点，累计 {total_records} 次变更。", False))
    lines.append(("", False))

    for index, code in enumerate(sorted(grouped.keys(), key=lambda x: (re.sub(r"\d+", lambda m: m.group(0).zfill(6), x))), start=1):
        revs = grouped[code]
        title = _requirement_title(code, current_by_code, latest_by_code, revs)
        location = _evidence_location(evidence_by_code.get(code))

        lines.append((f"{index}. {code} {title}", True))
        lines.append((f"大概位置：{location}", False))
        lines.append((f"变更次数：{len(revs)} 次", False))

        previous_new_lines: list[str] = []
        for change_index, rev in enumerate(revs, start=1):
            change_type = _plain(rev.change_type) or "变更"
            created_at = _revision_time(rev)
            old_lines = _snapshot_display_labeled(rev.old_snapshot)
            new_lines = _snapshot_display_labeled(rev.new_snapshot)

            # 有些历史记录 old_snapshot 可能为空，第 2 次以后可用上一次新内容补足“原来是什么”。
            if old_lines == ["无"] and previous_new_lines:
                old_lines = previous_new_lines
            if not old_lines:
                old_lines = ["无"]
            if not new_lines or new_lines == ["无"]:
                new_lines = _current_text_labeled_lines(code, current_by_code, latest_by_code)

            time_part = f"，时间：{created_at}" if created_at else ""
            lines.append((f"第{change_index}次变更（{change_type}{time_part}）", True))
            lines.append(("原来是：", False))
            for line in old_lines:
                lines.append((line, False))
            lines.append(("更改为：", False))
            for line in new_lines:
                lines.append((line, False))
            previous_new_lines = new_lines

        lines.append(("", False))

    return lines


def export_latest_changed_document(db: Session, document_id: int) -> ExportResult:
    """导出某份上传文档的最新变更说明版 docx。

    当前简化规则：
    1. 不替换原文正文；
    2. 不在正文中插入需求详情；
    3. 只在文档末尾追加“当前变更说明”；
    4. 只列出发生过人工新增、修改、删除的需求点；
    5. 每个需求点按时间顺序列出第 1 次、第 2 次、第 3 次……变更内容。
    """
    document_id = int(document_id)
    doc = db.get(UploadedDocument, document_id)
    if not doc:
        raise ValueError("文档不存在")

    source_path = Path(str(doc.file_path or ""))
    if not source_path.exists() or not source_path.is_file():
        raise ValueError("原始上传文件不存在，无法导出文档")
    if source_path.suffix.lower() != ".docx":
        raise ValueError("当前功能仅支持导出 docx 原格式文档，请使用 docx 需求文档测试")

    ensure_document_evidences(db, document_id)

    current_reqs = db.execute(
        select(Requirement)
        .where(Requirement.document_id == document_id)
        .order_by(Requirement.id.asc())
    ).scalars().all()
    current_by_code = {req.req_code: req for req in current_reqs if req.req_code}

    evidences = db.execute(
        select(RequirementEvidence)
        .where(RequirementEvidence.document_id == document_id)
        .order_by(RequirementEvidence.id.asc())
    ).scalars().all()
    evidence_by_code = {ev.req_code: ev for ev in evidences if ev.req_code}

    known_codes = sorted(set(current_by_code) | set(evidence_by_code))
    rev_stmt = select(RequirementRevision).where(RequirementRevision.document_id == document_id)
    if known_codes:
        rev_stmt = rev_stmt.where(or_(RequirementRevision.document_id == document_id, RequirementRevision.req_code.in_(known_codes)))
    revisions = db.execute(
        rev_stmt.order_by(RequirementRevision.created_at.asc(), RequirementRevision.id.asc())
    ).scalars().all()

    latest_by_code: dict[str, RequirementRevision] = {}
    for rev in revisions:
        if rev.req_code:
            latest_by_code[rev.req_code] = rev

    manual_revs = [rev for rev in revisions if _is_manual_revision(rev)]

    files, document_xml = _load_docx_xml(source_path)
    root = ET.fromstring(document_xml)

    # 只追加末尾变更说明，不修改原文正文。
    body = root.find(".//w:body", NS)
    if body is not None:
        insert_at = len(body)
        if len(body) and body[-1].tag == _qn("w:sectPr"):
            insert_at = len(body) - 1
        body.insert(insert_at, _make_page_break_paragraph())

    append_lines = _build_change_summary_lines(
        doc=doc,
        manual_revs=manual_revs,
        current_by_code=current_by_code,
        latest_by_code=latest_by_code,
        evidence_by_code=evidence_by_code,
    )
    _append_paragraphs(root, append_lines)

    files["word/document.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    stem = Path(doc.original_filename or doc.doc_code or f"document_{document_id}").stem
    export_name = f"{_safe_filename(stem)}_最新变更说明_{datetime.now().strftime('%Y%m%d%H%M%S')}.docx"
    out_path = EXPORT_DIR / f"{uuid.uuid4().hex}_{export_name}"
    _write_docx(files, out_path)

    changed_codes = {rev.req_code for rev in manual_revs if rev.req_code}
    return ExportResult(
        path=out_path,
        filename=export_name,
        replaced_count=0,
        changed_requirement_count=len(changed_codes),
        change_record_count=len(manual_revs),
    )
