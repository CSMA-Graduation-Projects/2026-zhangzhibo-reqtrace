# 给接口提供数据库连接，用完后自动关闭
from ai_service.db.session import SessionLocal

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
