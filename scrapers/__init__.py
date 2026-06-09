# scrapers/__init__.py
from .steam import ScraperSteam
from .nintendo import ScraperNintendo
from .metacritic import ScraperMetacritic
from .hltb import ScraperHLTB
from .psn import ScraperPSN
from .amazon import ScraperAmazon

__all__ = [
    "ScraperSteam",
    "ScraperNintendo",
    "ScraperMetacritic",
    "ScraperHLTB",
    "ScraperPSN",
    "ScraperAmazon",
]
