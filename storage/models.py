"""SQLite 数据模型 — 使用 SQLAlchemy ORM"""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Text, Boolean, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship

import config

Base = declarative_base()
engine = create_engine(f"sqlite:///{config.DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


class Tweet(Base):
    __tablename__ = "tweets"

    id = Column(String, primary_key=True)                 # X 推文 ID
    text = Column(Text, nullable=False)                   # 推文原文
    created_at = Column(DateTime, nullable=False)         # 发布时间
    author_id = Column(String, nullable=False, index=True)
    like_count = Column(Integer, default=0)
    retweet_count = Column(Integer, default=0)
    reply_count = Column(Integer, default=0)
    quote_count = Column(Integer, default=0)
    lang = Column(String(10))
    fetched_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # 方便查询
    __table_args__ = (
        Index("idx_created_at", "created_at"),
    )


class TweetTicker(Base):
    """推文中提取的股票代码（中间表，用于解耦 NER 和情绪分析）"""
    __tablename__ = "tweet_tickers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tweet_id = Column(String, ForeignKey("tweets.id"), nullable=False)
    symbol = Column(String(20), nullable=False, index=True)   # 股票代码，如 AAPL
    name = Column(String(100))                                  # 中文/英文名称
    confidence = Column(Float, default=1.0)                     # 匹配置信度
    extracted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # 关联推文
    tweet = relationship("Tweet", backref="tickers")

    __table_args__ = (
        Index("idx_ticker_symbol", "symbol"),
        UniqueConstraint("tweet_id", "symbol", name="uq_tweet_ticker"),
    )


class StockMention(Base):
    __tablename__ = "stock_mentions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tweet_id = Column(String, ForeignKey("tweets.id"), nullable=False)
    symbol = Column(String(20), nullable=False, index=True)   # 股票代码，如 AAPL
    name = Column(String(100))                                  # 中文/英文名称
    sentiment_score = Column(Float)                             # -1.0 ~ +1.0
    sentiment_label = Column(String(10))                        # positive / neutral / negative
    llm_reason = Column(Text)                                   # AI 判断理由
    method = Column(String(20), default="finbert")              # finbert / llm_review / vader
    needs_llm_review = Column(Boolean, default=False)           # 是否需要 LLM 二审
    analyzed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # 关联推文（用于按时间排序过滤）
    tweet = relationship("Tweet", backref="stock_mentions")

    __table_args__ = (
        Index("idx_symbol_date", "symbol", "analyzed_at"),
        Index("idx_needs_review", "needs_llm_review"),
        UniqueConstraint("tweet_id", "symbol", name="uq_tweet_symbol"),
    )


class RebalanceEvent(Base):
    """持仓调仓记录 — 每次博主发推文导致分数变化时的仓位变动"""
    __tablename__ = "rebalance_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), nullable=False, index=True)   # 博主用户名
    date = Column(String(10), nullable=False, index=True)       # 调仓日期 YYYY-MM-DD
    symbol = Column(String(20), nullable=False)                 # 股票代码
    old_weight = Column(Float, default=0.0)                     # 调仓前权重
    new_weight = Column(Float, default=0.0)                     # 调仓后权重
    sentiment_score = Column(Float)                             # 触发调仓的情绪分
    price = Column(Float)                                       # 调仓时股价
    reason = Column(Text)                                       # 调仓理由
    portfolio_value = Column(Float)                             # 组合总价值
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_rebalance_user_date", "username", "date"),
    )


def init_db() -> None:
    """初始化数据库（建表）"""
    Base.metadata.create_all(bind=engine)


def get_session() -> Session:
    """获取裸 Session（调用方需自行 close）。"""
    return SessionLocal()


@contextmanager
def session_scope():
    """Session context manager — 自动 commit/rollback/close。

    用法:
        with session_scope() as session:
            session.add(obj)
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
