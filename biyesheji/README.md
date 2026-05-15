# 软件需求变更管理与可追溯平台

## 项目简介

本项目是一个面向需求文档管理场景的软件需求变更管理与可追溯平台。系统以 **docx 需求文档** 为入口，围绕“文档上传—文本解析—规则需求提取—需求点维护—来源证据保存—版本链记录—需求变更追溯关系图展示—文档整体分析—AI评估报告—变更说明文档导出”形成完整处理流程。

项目后端采用 FastAPI，数据库采用 MySQL，数据访问使用 SQLAlchemy，前端页面基于 Jinja2、Bootstrap 和 JavaScript 实现，测试部分使用 pytest。系统中的需求点由规则方法提取，AI 不直接负责需求点提取，AI 主要用于文档整体分析和评估报告生成。

实现层面按 Watcher、Analyzer、Maintainer、Evaluation 四类职责组织流程：

| 职责名称 | 主要作用 |
| --- | --- |
| Watcher | 接收文档上传和需求操作，完成基础校验与流程触发 |
| Analyzer | 执行 docx 文本解析、规则需求提取和文档整体分析 |
| Maintainer | 维护来源证据、变更事件、需求版本和追溯关系图数据 |
| Evaluation | 维护人工基准，计算 TP、FP、FN、Precision、Recall、F1，并生成 AI 评估报告 |

当前系统的需求变更追溯关系图直接由 MySQL 中的文档、需求点、来源证据和版本链数据动态生成。

## 主要功能

### 1. 需求文档管理

- 仅支持上传 `.docx` 格式的需求文档。
- 上传后保存原始文件，解析 docx 正文、页眉和页脚文本。
- 记录文档编号、原始文件名、存储文件名、解析文本、提取结果和上传状态。
- 支持查看已上传文档列表和文档详情。
- 支持删除文档，并同步清理相关需求点、来源证据、变更事件、版本记录、人工基准和评估记录。

### 2. 规则需求提取与需求点维护

- 通过规则方法识别需求编号、标题、描述、来源片段和来源位置。
- 规则提取综合使用章节结构、表格内容、编号格式、项目符号、角色功能描述和需求关键词。
- 支持按文档查看需求点列表。
- 支持手动新增、修改、删除和查询需求点。
- 对新增、修改和删除操作自动生成变更事件和需求版本记录。

### 3. 来源证据保存

- 为每条需求保存来源片段 `source_excerpt` 和来源位置 `source_location`。
- 支持在需求管理和追溯维护页面查看需求来源。
- 当来源信息不完整时，系统会根据需求标题、描述和关键词从原文中补全相似片段。
- 来源证据用于后续人工核对、版本追溯和评估说明。

### 4. 变更事件与版本链

- 需求新增、修改和删除时自动记录变更事件。
- 保存变更前后的快照信息。
- 为需求生成版本号、变更类型、旧内容和新内容。
- 支持按文档 ID 或需求编号查看版本链和版本详情。
- 删除当前需求时保留历史版本记录，便于后续回溯。

### 5. 追溯维护与需求变更追溯关系图

- 追溯维护页面展示文档级追溯状态矩阵。
- 状态矩阵包括需求编号、标题、当前版本、版本数量、证据状态、基准状态和评估状态。
- 需求变更追溯关系图以文档节点和需求版本节点为主要展示对象。
- 文档节点表示上传需求文档，需求版本节点承载需求编号、标题、描述、版本号和变更类型等信息。
- 关系图主要展示文档到初始需求版本的提取关系，以及同一需求不同版本之间的变更链路。
- 支持按文档 ID 查看完整关系图，也支持按需求编号查看单条版本链。
- 关系图节点按需求编号自然排序，便于查看 R1、R2、R3 到 R10、R11 等连续编号。

### 6. AI 文档分析与 AI 评估报告

- AI 不负责最终需求点提取，需求点由规则方法生成。
- AI 用于对上传文档进行整体分析，帮助用户理解文档内容和需求特点。
- 支持以文档为单位维护人工确认基准。
- 支持将系统提取结果与人工基准进行对比。
- 自动计算 TP、FP、FN、Precision、Recall、F1。
- 生成评估摘要、差异明细和 AI 评估报告。
- 未配置模型接口或模型调用失败时，系统可使用本地兜底报告，保证页面可展示。

### 7. 变更说明文档导出

- 支持导出最新变更说明版 docx 文档。
- 导出时读取原始 docx 文档、当前需求、来源证据和人工变更记录。
- 在文档末尾追加“当前变更说明”。
- 导出文件默认保存到 `ai_service/uploaded_docs/exports/` 目录下。

## 技术栈

| 类型 | 技术 |
| --- | --- |
| 后端框架 | FastAPI |
| Web 服务器 | Uvicorn |
| ORM | SQLAlchemy |
| 数据库 | MySQL |
| 数据库驱动 | PyMySQL |
| 前端模板 | Jinja2 |
| 页面样式 | Bootstrap / CSS |
| 前端交互 | JavaScript |
| 文档解析与导出 | zipfile / docx XML 处理 |
| AI 调用 | OpenAI 兼容接口 |
| 自动化测试 | pytest |

## 项目结构

```text
biyesheji/
├── ai_service/
│   ├── api/
│   │   └── v1/
│   │       ├── documents.py        # 文档上传、详情、删除、导出接口
│   │       ├── requirements.py     # 需求点新增、修改、删除、查询接口
│   │       ├── events.py           # 变更事件接口
│   │       ├── graph.py            # 需求变更追溯关系图接口
│   │       ├── trace_versions.py   # 追溯状态矩阵和版本链接口
│   │       └── evaluation.py       # 人工基准与 AI 评估接口
│   ├── core/
│   │   └── config.py               # 环境变量与配置读取
│   ├── db/
│   │   ├── base.py                 # SQLAlchemy Base
│   │   ├── schema.py               # 数据表初始化与字段补全
│   │   └── session.py              # 数据库连接会话
│   ├── models/                     # 数据模型
│   ├── services/
│   │   ├── document_service.py     # 文档解析、规则提取、需求导入、版本图构造
│   │   ├── document_export_service.py
│   │   ├── document_evaluation_service.py
│   │   └── llm_client.py
│   ├── static/                     # 静态资源
│   ├── templates/                  # 前端页面模板
│   ├── uploaded_docs/              # 上传文档和导出文件目录
│   └── main.py                     # FastAPI 应用入口
├── tests/
│   └── test_api.py                 # 自动化测试
├── .env.example                    # 环境变量示例
├── .gitignore
├── pytest.ini
├── railway.json                    # Railway 部署配置
├── runtime.txt                     # Railway Python 版本配置
└── requirements.txt                # Python 依赖
```

## 环境要求

建议环境如下：

```text
Python 3.11+
MySQL 8.0+
Windows / Linux / macOS
```

本地开发可使用 Python 3.13，Railway 部署环境可使用 `runtime.txt` 中指定的 Python 3.11。

## 本地安装与运行

### 1. 克隆项目

```bash
git clone https://github.com/17783802849/biyesheji.git
cd biyesheji
```

如果你已经在本地项目目录中，可以直接进入项目根目录：

```bash
cd D:\python3.13.0\biyesheji
```

### 2. 创建并激活虚拟环境

Windows：

```bash
python -m venv .venv
.venv\Scripts\activate
```

Linux / macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 创建 MySQL 数据库

进入 MySQL 后执行：

```sql
CREATE DATABASE trace_platform DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 5. 配置环境变量

复制 `.env.example` 为 `.env`。

Windows：

```bash
copy .env.example .env
```

Linux / macOS：

```bash
cp .env.example .env
```

根据本地数据库和 AI 接口情况修改 `.env`：

```env
APP_NAME=软件需求变更管理与可追溯平台
APP_ENV=dev

MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=你的数据库密码
MYSQL_DB=trace_platform
MYSQL_CHARSET=utf8mb4

LLM_API_KEY=你的模型接口密钥
LLM_BASE_URL=你的模型接口地址
LLM_MODEL=gpt-4o-mini
LLM_TIMEOUT=25

OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=
```

说明：

- 如果只测试文档上传、规则提取、需求维护、版本链、需求变更追溯关系图和文档导出，可以暂时不配置 AI 接口。
- 如果需要使用文档整体 AI 分析和 AI 评估报告，需配置 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL`。
- 系统支持 OpenAI 兼容接口，`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL` 可作为备用变量名。

### 6. 启动项目

```bash
python -m uvicorn ai_service.main:app --reload
```

启动后访问：

```text
http://127.0.0.1:8000/
```

常用页面：

```text
http://127.0.0.1:8000/                     首页总览
http://127.0.0.1:8000/ui/requirements      需求管理
http://127.0.0.1:8000/ui/change            变更分析
http://127.0.0.1:8000/ui/suggest           追溯维护
http://127.0.0.1:8000/ui/impact-graph      需求变更追溯关系图
http://127.0.0.1:8000/ui/evaluation        AI评估
http://127.0.0.1:8000/docs                 API文档
```

## Railway 部署说明

项目中已包含 `railway.json`，部署启动命令为：

```bash
uvicorn ai_service.main:app --host 0.0.0.0 --port $PORT
```

Railway 部署时需要配置以下变量：

```env
MYSQL_HOST=你的线上 MySQL 地址
MYSQL_PORT=3306
MYSQL_USER=你的用户名
MYSQL_PASSWORD=你的密码
MYSQL_DB=trace_platform
MYSQL_CHARSET=utf8mb4

LLM_API_KEY=你的模型接口密钥
LLM_BASE_URL=你的模型接口地址
LLM_MODEL=gpt-4o-mini
LLM_TIMEOUT=25
```

部署建议：

1. Railway 服务连接 GitHub 仓库。
2. 分支选择 `main`。
3. Root Directory 保持仓库根目录，除非代码放在二级目录中。
4. 推送到 GitHub 后，Railway 会自动重新部署。
5. 部署成功后进入线上页面检查需求管理、追溯维护、需求变更追溯关系图和 AI 评估页面是否可访问。

如果线上页面没有更新，可以在本地执行：

```bash
git status
git add .
git commit -m "Update project files"
git push
```

确认 GitHub 最新提交后，在 Railway 的 Deployments 中等待最新部署成功，并对浏览器页面执行强制刷新。

## 常用接口

| 功能 | 方法 | 地址 |
| --- | --- | --- |
| 上传需求文档 | POST | `/api/v1/documents/upload` |
| 获取文档列表 | GET | `/api/v1/documents` |
| 获取文档详情 | GET | `/api/v1/documents/{document_id}` |
| 获取文档需求点 | GET | `/api/v1/documents/{document_id}/requirements` |
| 文档 AI 分析 | GET | `/api/v1/documents/{document_id}/ai-analysis` |
| 删除文档 | DELETE | `/api/v1/documents/{document_id}` |
| 导出最新变更说明文档 | GET | `/api/v1/documents/{document_id}/changed-document/latest` |
| 获取需求列表 | GET | `/api/v1/requirements/` |
| 新增需求 | POST | `/api/v1/requirements/` |
| 修改需求 | PUT | `/api/v1/requirements/{req_code}` |
| 删除需求 | DELETE | `/api/v1/requirements/{req_code}` |
| 查看变更事件 | GET | `/api/v1/events` |
| 查看追溯状态矩阵 | GET | `/api/v1/trace-versions/trace-matrix` |
| 查看需求版本链 | GET | `/api/v1/trace-versions/requirement-versions` |
| 查看需求变更追溯关系图 | GET | `/api/v1/graph/document-impact` |
| 查看评估文档 | GET | `/api/v1/evaluation/documents` |
| 保存人工基准 | POST | `/api/v1/evaluation/documents/{document_id}/benchmark` |
| 运行 AI 评估 | POST | `/api/v1/evaluation/documents/{document_id}/run` |

## 测试说明

运行全部测试：

```bash
pytest
```

查看详细测试过程：

```bash
pytest tests/test_api.py -v
```

当前 `tests/test_api.py` 包含 3 个综合测试用例：

| 测试函数 | 覆盖内容 |
| --- | --- |
| `test_app_and_basic_pages_ok` | FastAPI 应用初始化、首页、需求管理页、变更分析页、追溯维护页、关系图页、AI评估页 |
| `test_docx_text_parsing_and_rule_requirement_extraction` | docx 文本解析、非 docx 文件拒绝解析、规则需求提取 |
| `test_document_upload_crud_versions_evaluation_and_export` | 文档上传、需求导入、需求新增、修改、删除、版本记录、关系图接口、人工基准、指标计算、AI评估报告、文档导出 |

测试运行时使用临时 SQLite 数据库，不依赖本地 MySQL 业务数据。测试重点是验证核心流程能否自动化运行，页面截图、性能耗时和人工指标实验仍需结合论文中的页面操作结果说明。

## 数据表说明

系统启动时会自动检查并创建或补全数据库表结构。主要数据表如下：

| 数据表 | 作用 |
| --- | --- |
| `uploaded_documents` | 上传文档信息和解析文本 |
| `requirements` | 当前需求点 |
| `requirement_evidences` | 需求来源证据 |
| `change_events` | 需求变更事件 |
| `requirement_revisions` | 需求版本记录 |
| `document_evaluation_benchmarks` | 文档级人工确认基准 |
| `document_evaluation_records` | 文档级评估记录和 AI 评估报告 |

## 系统演示流程

1. 进入需求管理页面。
2. 上传一份 `.docx` 需求文档。
3. 查看文档列表和系统规则提取出的需求点。
4. 打开某条需求的来源证据，说明需求来自原始文档。
5. 新增或修改一条需求，观察系统生成变更事件和版本记录。
6. 进入追溯维护页面，按文档 ID 或需求编号查看版本链。
7. 进入需求变更追溯关系图页面，查看文档节点和需求版本节点之间的关系。
8. 进入 AI 评估页面，保存人工基准并运行评估。
9. 查看 TP、FP、FN、Precision、Recall、F1 和 AI 评估报告。
10. 返回需求管理页面，导出最新变更说明 docx 文档。

## 上传文件与导出文件说明

- 上传的原始文档保存在 `ai_service/uploaded_docs/`。
- 导出的最新变更说明文档保存在 `ai_service/uploaded_docs/exports/`。
- 正式归档时建议保留少量演示文档，避免上传目录中存在过多无说明的临时运行文件。
- 若线上部署需要直接展示完整流程，建议预先上传一份演示 docx 文档，并准备对应人工基准与评估结果。

## 注意事项

1. 当前系统仅支持上传 `.docx` 文档。
2. 需求点由规则方法提取，AI 只用于文档整体分析和评估报告生成。
3. 需求变更追溯关系图由 MySQL 业务数据动态生成。
4. 若 AI 接口未配置，核心的文档上传、规则提取、需求维护、版本链、关系图和文档导出仍可运行。
5. 数据库连接错误时，请优先检查 `.env` 中 MySQL 配置和数据库是否已创建。
6. Railway 部署后若页面未更新，请确认本地修改已经 commit、push，并等待最新部署完成后强制刷新浏览器。
