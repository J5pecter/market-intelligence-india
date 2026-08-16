"""Import every model so `Base.metadata` is complete for create_all/alembic."""

from app.db.base import Base  # noqa: F401
from app.models.derivatives import (FuturesSnapshot, OptionChainSnapshot,  # noqa: F401
                                    OptionGreeks, OptionSnapshot)
from app.models.fundamental import (CompanyProfile, CorporateAction,  # noqa: F401
                                    EarningsEvent, FinancialStatement,
                                    Fundamental, Shareholding)
from app.models.instrument import (Exchange, IndexConstituent,  # noqa: F401
                                   Instrument, InstrumentSyncRun, MarketHoliday)
from app.models.ipo import (Ipo, IpoAnalysis, IpoFinancials,  # noqa: F401
                            IpoGmpHistory, IpoRiskFactor, IpoSubscription)
from app.models.market import (FlowSnapshot, HistoricalPrice,  # noqa: F401
                               IndexSnapshot, Quote, SectorPerformance,
                               TechnicalIndicatorSnapshot)
from app.models.news import NewsArticle, NewsPriceReaction, NewsScore  # noqa: F401
from app.models.research import (Catalyst, ResearchCall,  # noqa: F401
                                 ResearchCallPerformance, ResearchCallVersion,
                                 ResearchCitation, ResearchDocument,
                                 ResearchReport, ResearchSource, Signal)
from app.models.system import (AuditLog, ComplianceDocument,  # noqa: F401
                               DataProviderStatus, JobRunLog,
                               ScannerDefinition)
from app.models.user import ApiCredential, Role, User  # noqa: F401
from app.models.user_data import (Alert, AlertEvent, Backtest,  # noqa: F401
                                  BacktestTrade, PaperPosition,
                                  PortfolioHolding, PortfolioTransaction,
                                  Watchlist, WatchlistItem)

__all__ = ["Base"]
