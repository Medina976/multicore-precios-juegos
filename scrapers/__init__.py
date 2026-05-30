# scrapers/__init__.py
from .steam import ScraperSteam
from .nintendo import ScraperNintendo
from .metacritic import ScraperMetacritic
from .hltb import ScraperHLTB

__all__ = ["ScraperSteam", "ScraperNintendo", "ScraperMetacritic", "ScraperHLTB"]
