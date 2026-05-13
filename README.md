# 基于多智能体协同的软件需求变更管理与可追溯平台

## 项目简介

本项目是一个面向需求文档管理场景的软件需求变更管理与可追溯平台。系统以需求文档为入口，支持文档上传、文本解析、规则化需求点提取、需求点维护、变更事件记录、版本链追溯、影响波及图展示、文档导出以及 AI 评估报告生成等功能，用于辅助完成需求变更过程中的结构化管理、证据保存和结果验证。

项目采用 FastAPI 作为后端框架，使用 SQLAlchemy 操作 MySQL 数据库，前端页面基于 Jinja2 模板和 Bootstrap 实现，测试部分使用 pytest 完成基础页面和接口可用性验证。

## 主要功能

1. **需求文档管理**
   - 支持上传 `.docx`、`.txt`、`.md` 等需求文档。
   - 自动解析文档文本内容。
   - 记录上传文档信息、文档编号、原始文件名和解析状态。
   - 支持删除文档，并清理与该文档相关的需求、证据、版本和评估数据。

2. **需求点提取与维护**
   - 基于规则从需求文档中提取结构化需求点。
   - 支持按文档查看需求点列表。
   - 支持手动新增、修改、删除需求点。
   - 需求点统一使用 `R1`、`R2`、`R3` 等编号形式。

3. **来源证据保存**
   - 为需求点保存来源片段和来源位置。
   - 支持在追溯维护页面查看需求点与文档证据之间的对应关系。
   - 便于后续检查需求来源和版本变更依据。

4. **变更事件与版本链**
   - 需求新增、修改和删除时自动记录变更事件。
   - 保存需求变更前后的快照信息。
   - 支持按文档或需求编号查看需求版本链。

5. **追溯维护与影响波及图**
   - 提供轻量追踪矩阵，展示文档、需求、证据、版本和评估状态。
   - 基于 MySQL 中的文档、需求点、来源证据和版本记录生成影响波及图。
   - 支持按文档或单个需求查看版本传播关系。

6. **AI 分析与评估报告**
   - AI 不负责需求点抽取，需求点抽取由规则完成。
   - AI 用于文档整体分析和评估报告生成。
   - 支持建立文档级人工基准。
   - 支持计算 Precision、Recall、F1、TP、FP、FN 等指标。
   - 支持查看 AI 评估总结和差异明细。

7. **变更后文档导出**
   - 支持根据当前需求修改情况导出最新变更后的 Word 文档。
   - 导出文件保存在 `ai_service/uploaded_docs/exports/` 目录下。

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
| AI 调用 | OpenAI 兼容接口 |
| 测试工具 | pytest |

## 项目结构

```text
biyesheji/
├── ai_service/
│   ├── api/
│   │   └── v1/                    # API 路由
│   │       ├── documents.py        # 文档上传、删除、导出接口
│   │       ├── requirements.py     # 需求点增删改查接口
│   │       ├── events.py           # 变更事件接口
│   │       ├── graph.py            # 影响波及图接口
│   │       ├── trace_versions.py   # 追溯矩阵和版本链接口
│   │       └── evaluation.py       # AI 评估接口
│   ├── core/
│   │   └── config.py               # 配置读取
│   ├── db/
│   │   ├── base.py                 # SQLAlchemy Base
│   │   ├── schema.py               # 数据表初始化与补全
│   │   └── session.py              # 数据库连接会话
│   ├── models/                     # 数据模型
│   ├── services/                   # 业务服务
│   ├── static/                     # 静态资源
│   ├── templates/                  # 前端页面模板
│   ├── uploaded_docs/              # 上传文档与导出文件目录
│   └── main.py                     # FastAPI 应用入口
├── tests/                          # 测试用例
├── .env.example                    # 环境变量示例
├── .gitignore                      # Git 忽略规则
├── pytest.ini                      # pytest 配置
└── requirements.txt                # Python 依赖
```

## 环境要求

建议使用以下环境运行：

```text
Python 3.10+
MySQL 8.0+
Windows / Linux / macOS
```

本项目开发测试时使用的是 Python 3.13 环境，其他 Python 3.10 以上版本通常也可以运行。

## 安装与运行

### 1. 克隆项目

```bash
git clone https://github.com/你的用户名/你的仓库名.git
cd 你的仓库名
```

### 2. 创建虚拟环境

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

### 4. 创建数据库

进入 MySQL 后创建数据库：

```sql
CREATE DATABASE trace_platform DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 5. 配置环境变量

复制 `.env.example` 为 `.env`：

Windows：

```bash
copy .env.example .env
```

Linux / macOS：

```bash
cp .env.example .env
```

然后根据本地环境修改 `.env`：

```env
APP_NAME=需求变更影响分析平台
APP_ENV=dev

MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=你的数据库密码
MYSQL_DB=trace_platform
MYSQL_CHARSET=utf8mb4

LLM_API_KEY=你的模型接口密钥
LLM_BASE_URL=你的模型接口地址
LLM_MODEL=你的模型名称
LLM_TIMEOUT=8
```

如果只测试文档上传、规则提取、需求维护、版本链和影响波及图，可以暂时不配置 AI 接口；如果要使用文档 AI 分析和 AI 评估报告，则需要配置 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL`。

### 6. 启动项目

```bash
python -m uvicorn ai_service.main:app --reload
```

启动成功后访问：

```text
http://127.0.0.1:8000/
```

常用页面：

```text
http://127.0.0.1:8000/ui/requirements      需求管理
http://127.0.0.1:8000/ui/change            变更分析
http://127.0.0.1:8000/ui/suggest           追溯维护
http://127.0.0.1:8000/ui/impact-graph      影响波及图
http://127.0.0.1:8000/ui/evaluation        AI评估
```

## 常用接口

| 功能 | 方法 | 地址 |
| --- | --- | --- |
| 上传需求文档 | POST | `/api/v1/documents/upload` |
| 获取文档列表 | GET | `/api/v1/documents` |
| 获取文档需求点 | GET | `/api/v1/documents/{document_id}/requirements` |
| 删除文档 | DELETE | `/api/v1/documents/{document_id}` |
| 导出最新变更后文档 | GET | `/api/v1/documents/{document_id}/changed-document/latest` |
| 获取需求列表 | GET | `/api/v1/requirements/` |
| 新增需求 | POST | `/api/v1/requirements/` |
| 修改需求 | PUT | `/api/v1/requirements/{req_code}` |
| 删除需求 | DELETE | `/api/v1/requirements/{req_code}` |
| 查看变更事件 | GET | `/api/v1/events` |
| 查看追踪矩阵 | GET | `/api/v1/trace-versions/trace-matrix` |
| 查看需求版本链 | GET | `/api/v1/trace-versions/requirement-versions` |
| 查看影响波及图 | GET | `/api/v1/graph/document-impact` |
| 查看评估文档 | GET | `/api/v1/evaluation/documents` |
| 运行 AI 评估 | POST | `/api/v1/evaluation/documents/{document_id}/run` |

## 测试

运行测试前请确认依赖已经安装完成：

```bash
pytest -v
```

也可以指定测试文件：

```bash
pytest tests/test_api.py -v
```

测试内容主要包括：

- FastAPI 应用是否可以正常导入。
- 首页是否可以访问。
- 需求管理页面是否可以访问。
- 变更分析页面是否可以访问。
- 追溯维护页面是否可以访问。
- AI 评估页面是否可以访问。

## 数据表说明

系统启动时会自动检查并创建或补全数据库表结构，主要数据表包括：

| 数据表 | 作用 |
| --- | --- |
| `uploaded_documents` | 上传文档信息表 |
| `requirements` | 需求点表 |
| `requirement_evidences` | 需求来源证据表 |
| `change_events` | 变更事件表 |
| `requirement_revisions` | 需求版本记录表 |
| `document_evaluation_benchmarks` | 文档级人工基准表 |
| `document_evaluation_records` | AI 评估记录表 |