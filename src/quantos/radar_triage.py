from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb

from .research_radar import DiscoveryItem


_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
    "is", "it", "of", "on", "or", "that", "the", "this", "to", "using", "we",
    "with",
}


@dataclass(frozen=True)
class ResearchTheme:
    name: str
    keywords: tuple[str, ...]
    categories: tuple[str, ...] = ()


DEFAULT_THEMES = (
    ResearchTheme(
        "asset_pricing",
        (
            "asset pricing", "factor model", "factor investing", "expected returns",
            "cross-sectional returns", "return predictability", "anomaly", "momentum",
            "value premium", "quality factor",
        ),
        ("q-fin.PM", "q-fin.EC"),
    ),
    ResearchTheme(
        "portfolio_construction",
        (
            "portfolio optimization", "portfolio construction", "asset allocation",
            "covariance", "shrinkage", "risk parity", "black litterman",
            "robust optimization", "risk budgeting",
        ),
        ("q-fin.PM", "q-fin.RM"),
    ),
    ResearchTheme(
        "research_integrity",
        (
            "backtest overfitting", "data snooping", "multiple testing",
            "deflated sharpe", "survivorship bias", "look-ahead bias",
            "point-in-time", "replication", "publication bias",
        ),
        ("q-fin.ST",),
    ),
    ResearchTheme(
        "causal_methods",
        (
            "causal inference", "causal identification", "instrumental variable",
            "difference in differences", "treatment effect", "natural experiment",
        ),
        ("q-fin.EC", "econ.EM"),
    ),
    ResearchTheme(
        "market_microstructure",
        (
            "market microstructure", "order book", "order flow", "price impact",
            "execution", "liquidity", "transaction cost", "bid ask",
        ),
        ("q-fin.TR",),
    ),
    ResearchTheme(
        "machine_learning",
        (
            "machine learning", "transformer", "representation learning",
            "asset embedding", "embedding", "neural network", "deep learning",
            "reinforcement learning", "foundation model",
        ),
        ("q-fin.ST", "stat.ML", "cs.LG"),
    ),
    ResearchTheme(
        "alternative_data",
        (
            "alternative data", "satellite", "web traffic", "transaction data",
            "text data", "news sentiment", "job postings", "supply chain data",
        ),
        ("q-fin.ST",),
    ),
    ResearchTheme(
        "volatility_derivatives",
        (
            "implied volatility", "volatility surface", "option pricing",
            "derivatives pricing", "stochastic volatility", "volatility risk",
        ),
        ("q-fin.CP", "q-fin.PR"),
    ),
    ResearchTheme(
        "risk_regimes",
        (
            "regime change", "regime switching", "tail risk", "expected shortfall",
            "systemic risk", "stress test", "drawdown", "correlation breakdown",
        ),
        ("q-fin.RM",),
    ),
)


@dataclass(frozen=True)
class ThemeMatch:
    theme: str
    score: float
    keyword_hits: tuple[str, ...]
    category_hits: tuple[str, ...]


@dataclass(frozen=True)
class TriageResult:
    triage_id: str
    discovery_id: str
    profile_id: str
    triaged_at: datetime
    relevance: float
    lexical_novelty: float
    freshness: float
    attention_score: float
    attention_band: str
    theme_matches: tuple[ThemeMatch, ...]
    caveat: str


def make_profile_id(
    *,
    themes: tuple[ResearchTheme, ...],
    weights: tuple[float, float, float],
    freshness_half_life_days: float,
) -> str:
    payload = {
        "themes": [
            {
                "name": theme.name,
                "keywords": theme.keywords,
                "categories": theme.categories,
            }
            for theme in themes
        ],
        "weights": weights,
        "freshness_half_life_days": freshness_half_life_days,
    }
    material = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "triage-profile:" + hashlib.sha256(material).hexdigest()


def make_triage_id(
    *,
    discovery_id: str,
    profile_id: str,
    triaged_at: datetime,
) -> str:
    material = (
        discovery_id + "\n" + profile_id + "\n" + triaged_at.isoformat()
    ).encode()
    return "triage:" + hashlib.sha256(material).hexdigest()


class RadarTriageEngine:
    """Deterministic attention ranking for research review.

    This engine ranks what deserves human/research attention. It does not
    assess truth, replicability, economic value, or expected investment return.
    """

    CAVEAT = (
        "Attention score only: high relevance, novelty, or freshness does not "
        "establish correctness, replicability, causality, or investment value."
    )

    def __init__(
        self,
        *,
        themes: tuple[ResearchTheme, ...] = DEFAULT_THEMES,
        relevance_weight: float = 0.60,
        novelty_weight: float = 0.25,
        freshness_weight: float = 0.15,
        freshness_half_life_days: float = 30.0,
    ) -> None:
        weights = (relevance_weight, novelty_weight, freshness_weight)
        if any(weight < 0 for weight in weights):
            raise ValueError("triage weights cannot be negative")
        if not math.isclose(sum(weights), 1.0, abs_tol=1e-9):
            raise ValueError("triage weights must sum to 1")
        if freshness_half_life_days <= 0:
            raise ValueError("freshness_half_life_days must be positive")
        names = [theme.name for theme in themes]
        if len(set(names)) != len(names):
            raise ValueError("research theme names must be unique")
        self.themes = themes
        self.weights = weights
        self.freshness_half_life_days = freshness_half_life_days
        self.profile_id = make_profile_id(
            themes=themes,
            weights=weights,
            freshness_half_life_days=freshness_half_life_days,
        )

    def score(
        self,
        item: DiscoveryItem,
        *,
        triaged_at: datetime,
        prior_items: tuple[DiscoveryItem, ...] = (),
    ) -> TriageResult:
        if triaged_at.tzinfo is None:
            raise ValueError("triaged_at must be timezone-aware")
        if item.updated_at > triaged_at:
            raise ValueError("cannot triage a paper before its update timestamp")

        matches = tuple(
            match
            for theme in self.themes
            if (match := self._theme_match(item, theme)).score > 0
        )
        relevance = max((match.score for match in matches), default=0.0)
        novelty = self._novelty(item, prior_items)
        age_days = (triaged_at - item.updated_at).total_seconds() / 86400.0
        freshness = math.exp(
            -math.log(2.0) * age_days / self.freshness_half_life_days
        )
        freshness = min(1.0, max(0.0, freshness))
        attention = (
            self.weights[0] * relevance
            + self.weights[1] * novelty
            + self.weights[2] * freshness
        )
        if attention >= 0.75:
            band = "ATTENTION_HIGH"
        elif attention >= 0.50:
            band = "ATTENTION_REVIEW"
        else:
            band = "ATTENTION_LOW"

        triage_id = make_triage_id(
            discovery_id=item.discovery_id,
            profile_id=self.profile_id,
            triaged_at=triaged_at,
        )
        return TriageResult(
            triage_id=triage_id,
            discovery_id=item.discovery_id,
            profile_id=self.profile_id,
            triaged_at=triaged_at,
            relevance=relevance,
            lexical_novelty=novelty,
            freshness=freshness,
            attention_score=attention,
            attention_band=band,
            theme_matches=tuple(sorted(matches, key=lambda m: (-m.score, m.theme))),
            caveat=self.CAVEAT,
        )

    @staticmethod
    def _theme_match(item: DiscoveryItem, theme: ResearchTheme) -> ThemeMatch:
        title = item.title.lower()
        summary = item.summary.lower()
        keyword_hits: list[str] = []
        points = 0.0
        for keyword in theme.keywords:
            needle = keyword.lower()
            hit = False
            if needle in title:
                points += 2.0
                hit = True
            elif needle in summary:
                points += 1.0
                hit = True
            if hit:
                keyword_hits.append(keyword)
        category_hits = tuple(
            category
            for category in theme.categories
            if category in item.categories
        )
        points += float(len(category_hits))
        return ThemeMatch(
            theme=theme.name,
            score=min(1.0, points / 4.0),
            keyword_hits=tuple(keyword_hits),
            category_hits=category_hits,
        )

    @classmethod
    def _novelty(
        cls,
        item: DiscoveryItem,
        prior_items: tuple[DiscoveryItem, ...],
    ) -> float:
        current = cls._tokens(item.title + " " + item.summary)
        if not current:
            return 0.0
        similarities: list[float] = []
        for prior in prior_items:
            if prior.discovery_id == item.discovery_id:
                continue
            prior_tokens = cls._tokens(prior.title + " " + prior.summary)
            if not prior_tokens:
                continue
            union = current | prior_tokens
            similarity = len(current & prior_tokens) / len(union) if union else 0.0
            similarities.append(similarity)
        if not similarities:
            return 1.0
        return max(0.0, min(1.0, 1.0 - max(similarities)))

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {
            token
            for token in _TOKEN.findall(text.lower())
            if token not in _STOP and len(token) > 1
        }


class RadarTriageStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS radar_triage (
                triage_id VARCHAR PRIMARY KEY,
                discovery_id VARCHAR NOT NULL,
                profile_id VARCHAR NOT NULL,
                triaged_at TIMESTAMPTZ NOT NULL,
                relevance DOUBLE NOT NULL,
                lexical_novelty DOUBLE NOT NULL,
                freshness DOUBLE NOT NULL,
                attention_score DOUBLE NOT NULL,
                attention_band VARCHAR NOT NULL,
                theme_matches_json VARCHAR NOT NULL,
                caveat VARCHAR NOT NULL
            )
            """
        )

    def record(self, result: TriageResult) -> None:
        serialized = json.dumps(
            [
                {
                    "theme": match.theme,
                    "score": match.score,
                    "keyword_hits": match.keyword_hits,
                    "category_hits": match.category_hits,
                }
                for match in result.theme_matches
            ],
            sort_keys=True,
        )
        existing = self._con.execute(
            """
            SELECT discovery_id, profile_id, triaged_at, relevance,
                   lexical_novelty, freshness, attention_score,
                   attention_band, theme_matches_json, caveat
            FROM radar_triage
            WHERE triage_id = ?
            """,
            [result.triage_id],
        ).fetchone()
        new = (
            result.discovery_id,
            result.profile_id,
            result.triaged_at,
            result.relevance,
            result.lexical_novelty,
            result.freshness,
            result.attention_score,
            result.attention_band,
            serialized,
            result.caveat,
        )
        if existing is not None:
            old = (
                str(existing[0]), str(existing[1]), existing[2],
                float(existing[3]), float(existing[4]), float(existing[5]),
                float(existing[6]), str(existing[7]), str(existing[8]), str(existing[9]),
            )
            if old != new:
                raise ValueError("triage identity conflict")
            return
        self._con.execute(
            "INSERT INTO radar_triage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [result.triage_id, *new],
        )

    def close(self) -> None:
        self._con.close()
