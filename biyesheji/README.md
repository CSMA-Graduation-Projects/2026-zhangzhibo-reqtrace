# 需求变更影响分析平台

基于 FastAPI 的需求变更管理系统。系统以需求文档为入口，完成文档解析、需求点提取、需求维护、变更记录、版本追溯、影响波及图展示和 AI 评估。

本项目为本科毕业设计项目，题目为：**基于多智能体协同的软件需求变更管理与可追溯平台设计与实现**。

## 功能

- 需求文档上传与解析
- 需求点自动提取与人工维护
- 需求新增、修改、删除记录
- 需求版本链追溯
- 影响波及图展示
- 追溯维护与追踪矩阵查看
- 文档级 AI 分析
- 人工基准维护与 AI 评估
- Precision、Recall、F1 指标计算
- 最新变更后文档导出

## 技术栈

| 类型 | 技术 |
| --- | --- |
| 后端框架 | FastAPI |
| ASGI 服务 | Uvicorn |
| 数据库 | MySQL |
| ORM | SQLAlchemy |
| 页面模板 | Jinja2 |
| 前端样式 | Bootstrap |
| AI 接口 | OpenAI 兼容接口 |
| 测试工具 | pytest |

## 项目结构

```text
biyesheji/
├── ai_service/
│   ├── api/
│   │   └── v1/                 # 接口路由
│   ├── core/                   # 配置读取
│   ├── db/                     # 数据库连接与表结构初始化
│   ├── models/                 # SQLAlchemy 数据模型
│   ├── services/               # 业务逻辑
│   ├── templates/              # Jinja2 页面模板
│   ├── uploaded_docs/          # 上传文档与导出文件目录
│   └── main.py                 # FastAPI 应用入口
├── tests/                      # 测试用例
├── .env.example                # 环境变量示例
├── .gitignore
├── pytest.ini
└── requirements.txt
```

## 环境要求

- Python 3.10+
- MySQL 8.0+
- Windows、macOS 或 Linux

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/your-username/your-repo-name.git
cd biyesheji
```

### 2. 创建虚拟环境

Windows：

```powershell
python -m venv .venv
.venv\Scripts\activate
```

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置环境变量

复制环境变量示例文件：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
copy .env.example .env
```

根据本地环境修改 `.env`：

```env
APP_NAME=需求变更影响分析平台
APP_ENV=dev

MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=123456
MYSQL_DB=trace_platform
MYSQL_CHARSET=utf8mb4

LLM_API_KEY=your_api_key_here
LLM_BASE_URL=
LLM_MODEL=gpt-4o-mini
LLM_TIMEOUT=25
```

### 5. 创建数据库

登录 MySQL 后执行：

```sql
CREATE DATABASE trace_platform DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

系统启动时会自动检查并初始化所需表结构。

### 6. 启动项目

```bash
python -m uvicorn ai_service.main:app --reload
```

启动后访问：

```text
http://127.0.0.1:8000/
```

## 页面入口

| 页面 | 地址 |
| --- | --- |
| 首页 | `/` |
| 需求管理 | `/ui/requirements` |
| 变更分析 | `/ui/change` |
| 追溯维护 | `/ui/suggest` |
| 影响波及图 | `/ui/impact-graph` |
| AI 评估 | `/ui/evaluation` |

## 主要接口

| 方法 | 地址 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/documents/upload` | 上传需求文档 |
| GET | `/api/v1/documents` | 查询文档列表 |
| GET | `/api/v1/documents/{document_id}` | 查询文档详情 |
| DELETE | `/api/v1/documents/{document_id}` | 删除文档及关联数据 |
| GET | `/api/v1/documents/{document_id}/requirements` | 查询文档下的需求点 |
| GET | `/api/v1/documents/{document_id}/ai-analysis` | 获取文档 AI 分析 |
| GET | `/api/v1/documents/{document_id}/changed-document/latest` | 导出最新变更后文档 |
| GET | `/api/v1/documents/graph/versions` | 查询需求版本图数据 |
| GET | `/api/v1/requirements/` | 查询需求列表 |
| POST | `/api/v1/requirements/` | 新增需求点 |
| PUT | `/api/v1/requirements/{req_code}` | 修改需求点 |
| DELETE | `/api/v1/requirements/{req_code}` | 删除需求点 |
| GET | `/api/v1/events` | 查询变更事件 |
| GET | `/api/v1/trace-matrix` | 查询追踪矩阵 |
| GET | `/api/v1/requirement-versions` | 查询需求版本记录 |
| GET | `/api/v1/evaluation/documents` | 查询可评估文档 |
| POST | `/api/v1/evaluation/documents/{document_id}/benchmark/from-ai` | 根据 AI 结果生成基准 |
| POST | `/api/v1/evaluation/documents/{document_id}/benchmark` | 保存人工基准 |
| POST | `/api/v1/evaluation/documents/{document_id}/run` | 执行文档级评估 |

## 测试

运行全部测试：

```bash
pytest -v
```

运行指定测试文件：

```bash
pytest tests/test_api.py -v
```

## Git 提交说明

以下文件不建议提交到仓库：

```text
.env
.venv/
__pycache__/
.pytest_cache/
ai_service/uploaded_docs/*
```

`.env.example` 可以提交，用于说明项目需要哪些配置项。

## 说明

- 上传文档建议使用 `.docx`、`.txt` 或 `.md` 格式。
- AI 功能依赖 `.env` 中的 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL` 配置。
- 如果 AI 接口不可用，部分需求提取流程会使用规则方式进行兜底处理。
- 导出的文档默认保存在 `ai_service/uploaded_docs/exports/` 目录下。

## License

仅用于毕业设计学习与演示。
