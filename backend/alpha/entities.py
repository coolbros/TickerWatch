"""Financial Entity Recognition, Disambiguation, and Instrument Resolution."""

from enum import Enum
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from backend.alpha.instruments import (
    InstrumentMappingRecord,
    RelevanceTier,
    get_instrument_master,
)


class EntityType(str, Enum):
    COMPANY = "COMPANY"
    SUBSIDIARY = "SUBSIDIARY"
    REGULATOR = "REGULATOR"
    EXECUTIVE = "EXECUTIVE"
    MACRO_ENTITY = "MACRO_ENTITY"
    SECTOR = "SECTOR"


class ResolutionState(str, Enum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"


class EntityDefinition(BaseModel):
    """Canonical metadata and relationship mapping for a known financial entity."""

    entity_id: str = Field(..., description="Unique key e.g. RELIANCE, INFY, RBI")
    canonical_name: str
    entity_type: EntityType
    exchange_tickers: Dict[str, str] = Field(default_factory=dict, description="e.g. {'NSE': 'RELIANCE', 'BSE': '500325'}")
    parent_entity_id: Optional[str] = None
    aliases: List[str] = Field(default_factory=list)
    industry_sector: Optional[str] = None
    market_cap_tier: str = "LARGE"  # MEGA, LARGE, MID, SMALL, MACRO
    executives: List[str] = Field(default_factory=list)
    direct_instruments: List[str] = Field(default_factory=list)


class ResolvedEntity(BaseModel):
    """Output of entity disambiguation and instrument resolution."""

    entity_id: Optional[str] = None
    canonical_name: Optional[str] = None
    entity_type: Optional[EntityType] = None
    matched_text: str
    resolution_state: ResolutionState
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence_span: Optional[Tuple[int, int]] = None
    parent_entity_id: Optional[str] = None
    candidate_entities: List[str] = Field(default_factory=list)
    instruments: List[InstrumentMappingRecord] = Field(default_factory=list)


# Words that collide with short or standard English vocabulary
TICKER_COLLISION_STOPWORDS: Set[str] = {
    "A", "AN", "AND", "ARE", "AS", "AT", "BE", "BY", "CAN", "DO", "FOR",
    "GO", "HE", "IF", "IN", "IS", "IT", "ME", "MY", "NO", "NOT", "OF",
    "ON", "OR", "SO", "TO", "UP", "US", "WE", "ALL", "NEW", "NOW", "ONE",
    "OUT", "SEE", "TWO", "WAY", "WHO", "BOY", "DAY", "GET", "HAS", "HIM",
    "HIS", "HOW", "MAN", "OLD", "OUR", "SAY", "SHE", "TOO", "USE", "WAR"
}

# Contextual keywords that validate a ticker mention in text
FINANCIAL_CONTEXT_KEYWORDS: Set[str] = {
    "shares", "share", "stock", "stocks", "quarter", "results", "profit",
    "revenue", "dividend", "q1", "q2", "q3", "q4", "ltd", "limited",
    "corp", "inc", "pat", "ebitda", "capex", "board", "order", "nse", "bse"
}


class FinancialEntityResolver:
    """
    Context-aware financial entity recognizer and disambiguator.
    Prevents false-positive ticker collisions, resolves subsidiaries to parents,
    and maps resolved entities to canonical instruments.
    """

    def __init__(self, confidence_threshold: float = 0.6) -> None:
        self.confidence_threshold = confidence_threshold
        self.instrument_master = get_instrument_master()
        self._entities: Dict[str, EntityDefinition] = {}
        self._alias_index: Dict[str, List[str]] = {}  # lowercase alias -> list of entity_ids
        self._ticker_index: Dict[str, List[str]] = {}  # uppercase ticker -> list of entity_ids
        self._load_knowledge_base()

    def register_entity(self, entity: EntityDefinition) -> None:
        """Register an entity in the knowledge base."""
        self._entities[entity.entity_id] = entity

        # Index aliases (lowercase)
        all_aliases = [entity.canonical_name.lower()] + [a.lower() for a in entity.aliases]
        for alias in set(all_aliases):
            if alias not in self._alias_index:
                self._alias_index[alias] = []
            if entity.entity_id not in self._alias_index[alias]:
                self._alias_index[alias].append(entity.entity_id)

        # Index tickers (uppercase)
        for ex, ticker in entity.exchange_tickers.items():
            t_upper = ticker.upper()
            if t_upper not in self._ticker_index:
                self._ticker_index[t_upper] = []
            if entity.entity_id not in self._ticker_index[t_upper]:
                self._ticker_index[t_upper].append(entity.entity_id)

    def _load_knowledge_base(self) -> None:
        """Populate initial canonical financial entities across Indian and US markets."""
        entities = [
            # 1. Reliance Industries & Subsidiaries
            EntityDefinition(
                entity_id="RELIANCE",
                canonical_name="Reliance Industries Limited",
                entity_type=EntityType.COMPANY,
                exchange_tickers={"NSE": "RELIANCE", "BSE": "500325"},
                aliases=["Reliance", "RIL", "Reliance Industries"],
                industry_sector="Energy & Conglomerate",
                market_cap_tier="MEGA",
                executives=["Mukesh Ambani", "Akash Ambani", "Isha Ambani", "Anant Ambani"],
                direct_instruments=["NSE:RELIANCE:EQ", "NSE:RELIANCE_FUT:CURRENT"],
            ),
            EntityDefinition(
                entity_id="JIO",
                canonical_name="Jio Platforms Limited",
                entity_type=EntityType.SUBSIDIARY,
                parent_entity_id="RELIANCE",
                aliases=["Jio", "Reliance Jio", "Jio Infocomm"],
                industry_sector="Telecommunications & Tech",
                direct_instruments=["NSE:RELIANCE:EQ", "NSE:RELIANCE_FUT:CURRENT"],
            ),
            EntityDefinition(
                entity_id="RELIANCE_RETAIL",
                canonical_name="Reliance Retail Ventures Limited",
                entity_type=EntityType.SUBSIDIARY,
                parent_entity_id="RELIANCE",
                aliases=["Reliance Retail"],
                industry_sector="Retail",
                direct_instruments=["NSE:RELIANCE:EQ", "NSE:RELIANCE_FUT:CURRENT"],
            ),

            # 2. Infosys
            EntityDefinition(
                entity_id="INFY",
                canonical_name="Infosys Limited",
                entity_type=EntityType.COMPANY,
                exchange_tickers={"NSE": "INFY", "BSE": "500209", "NYSE": "INFY"},
                aliases=["Infosys"],
                industry_sector="Information Technology",
                market_cap_tier="LARGE",
                executives=["Salil Parekh", "Nandan Nilekani"],
                direct_instruments=["NSE:INFY:EQ"],
            ),

            # 3. Tata Consultancy Services & Tata Motors
            EntityDefinition(
                entity_id="TCS",
                canonical_name="Tata Consultancy Services Limited",
                entity_type=EntityType.COMPANY,
                exchange_tickers={"NSE": "TCS", "BSE": "532540"},
                aliases=["Tata Consultancy Services"],
                industry_sector="Information Technology",
                market_cap_tier="MEGA",
                executives=["K Krithivasan", "N Chandrasekaran"],
                direct_instruments=["NSE:TCS:EQ"],
            ),
            EntityDefinition(
                entity_id="TATAMOTORS",
                canonical_name="Tata Motors Limited",
                entity_type=EntityType.COMPANY,
                exchange_tickers={"NSE": "TATAMOTORS", "BSE": "500570"},
                aliases=["Tata Motors", "TaMo", "JLR", "Jaguar Land Rover"],
                industry_sector="Automotive",
                market_cap_tier="LARGE",
            ),

            # 4. Banking & Financials
            EntityDefinition(
                entity_id="HDFCBANK",
                canonical_name="HDFC Bank Limited",
                entity_type=EntityType.COMPANY,
                exchange_tickers={"NSE": "HDFCBANK", "BSE": "500180", "NYSE": "HDB"},
                aliases=["HDFC Bank", "HDFC"],
                industry_sector="Banking & Financial Services",
                market_cap_tier="MEGA",
                executives=["Sashidhar Jagdishan"],
                direct_instruments=["NSE:HDFCBANK:EQ"],
            ),
            EntityDefinition(
                entity_id="ICICIBANK",
                canonical_name="ICICI Bank Limited",
                entity_type=EntityType.COMPANY,
                exchange_tickers={"NSE": "ICICIBANK", "BSE": "532174", "NYSE": "IBN"},
                aliases=["ICICI Bank", "ICICI"],
                industry_sector="Banking & Financial Services",
                market_cap_tier="LARGE",
                executives=["Sandeep Bakhshi"], direct_instruments=["NSE:ICICIBANK:EQ"],
            ),
            EntityDefinition(
                entity_id="SBIN",
                canonical_name="State Bank of India",
                entity_type=EntityType.COMPANY,
                exchange_tickers={"NSE": "SBIN", "BSE": "500112"},
                aliases=["State Bank of India", "SBI"],
                industry_sector="Public Sector Banking",
                market_cap_tier="LARGE",
            ),
            EntityDefinition(
                entity_id="LT",
                canonical_name="Larsen & Toubro Limited",
                entity_type=EntityType.COMPANY,
                exchange_tickers={"NSE": "LT", "BSE": "500510"},
                aliases=["Larsen & Toubro", "Larsen and Toubro", "L&T", "L and T"],
                industry_sector="Infrastructure & Engineering",
                market_cap_tier="LARGE",
                executives=["S N Subrahmanyan", "A M Naik"],
                direct_instruments=["NSE:LT:EQ"],
            ),
            EntityDefinition(
                entity_id="BHARTIARTL",
                canonical_name="Bharti Airtel Limited",
                entity_type=EntityType.COMPANY,
                exchange_tickers={"NSE": "BHARTIARTL", "BSE": "532454"},
                aliases=["Bharti Airtel", "Airtel"],
                industry_sector="Telecommunications",
                market_cap_tier="LARGE",
                executives=["Sunil Bharti Mittal", "Gopal Vittal"], direct_instruments=["NSE:BHARTIARTL:EQ"],
            ),
            EntityDefinition(
                entity_id="ITC",
                canonical_name="ITC Limited",
                entity_type=EntityType.COMPANY,
                exchange_tickers={"NSE": "ITC", "BSE": "500875"},
                aliases=["ITC", "ITC Ltd"],
                industry_sector="FMCG & Conglomerate",
                market_cap_tier="LARGE",
                executives=["Sanjiv Puri"], direct_instruments=["NSE:ITC:EQ"],
            ),
            EntityDefinition(
                entity_id="KOTAKBANK",
                canonical_name="Kotak Mahindra Bank Limited",
                entity_type=EntityType.COMPANY,
                exchange_tickers={"NSE": "KOTAKBANK", "BSE": "500247"},
                aliases=["Kotak Mahindra Bank", "Kotak Bank", "Kotak"],
                industry_sector="Banking & Financial Services",
                market_cap_tier="LARGE",
                executives=["Uday Kotak", "Ashok Vaswani"], direct_instruments=["NSE:KOTAKBANK:EQ"],
            ),

            # 5. Regulators & Macroeconomic Bodies
            EntityDefinition(
                entity_id="RBI",
                canonical_name="Reserve Bank of India",
                entity_type=EntityType.REGULATOR,
                aliases=["Reserve Bank of India", "RBI", "Monetary Policy Committee", "MPC"],
                industry_sector="Central Banking",
                market_cap_tier="MACRO",
                direct_instruments=["NSE:NIFTY_FUT:CURRENT", "NSE:BANKNIFTY_FUT:CURRENT"],
            ),
            EntityDefinition(
                entity_id="SEBI",
                canonical_name="Securities and Exchange Board of India",
                entity_type=EntityType.REGULATOR,
                aliases=["Securities and Exchange Board of India", "SEBI"],
                industry_sector="Securities Regulation",
                market_cap_tier="MACRO",
                direct_instruments=["NSE:NIFTY_FUT:CURRENT"],
            ),
            EntityDefinition(
                entity_id="FINMIN",
                canonical_name="Ministry of Finance (India)",
                entity_type=EntityType.MACRO_ENTITY,
                aliases=["Ministry of Finance", "Finance Ministry", "FinMin", "Government of India"],
                industry_sector="Fiscal Authority",
                market_cap_tier="MACRO",
                direct_instruments=["NSE:NIFTY_FUT:CURRENT"],
            ),

            # 6. Global US Mega-Caps (SEC Baseline)
            EntityDefinition(
                entity_id="AAPL",
                canonical_name="Apple Inc.",
                entity_type=EntityType.COMPANY,
                exchange_tickers={"NASDAQ": "AAPL"},
                aliases=["Apple", "Apple Inc"],
                industry_sector="Technology",
                executives=["Tim Cook"], direct_instruments=["US:AAPL:EQ"],
            ),
            EntityDefinition(
                entity_id="AMZN",
                canonical_name="Amazon.com Inc.",
                entity_type=EntityType.COMPANY,
                exchange_tickers={"NASDAQ": "AMZN"},
                aliases=["Amazon", "Amazon.com", "AWS"],
                industry_sector="Consumer Discretionary & Cloud",
                executives=["Andy Jassy", "Jeff Bezos"], direct_instruments=["US:AMZN:EQ"],
            ),
            EntityDefinition(
                entity_id="MSFT",
                canonical_name="Microsoft Corporation",
                entity_type=EntityType.COMPANY,
                exchange_tickers={"NASDAQ": "MSFT"},
                aliases=["Microsoft", "Microsoft Corp"],
                industry_sector="Technology & Software",
                executives=["Satya Nadella"], direct_instruments=["US:MSFT:EQ"],
            ),
        ]

        for ent in entities:
            self.register_entity(ent)

    def resolve_text(self, text: str) -> List[ResolvedEntity]:
        """
        Scan and disambiguate entity mentions in an article headline and body text.
        Returns:
            List of ResolvedEntity objects.
        """
        if not text or not text.strip():
            return []

        resolved_list: List[ResolvedEntity] = []
        text_lower = text.lower()
        words = re.findall(r"\b[A-Za-z0-9_.-]+\b", text)
        words_upper = [w.upper() for w in words]

        has_financial_context = any(w.lower() in FINANCIAL_CONTEXT_KEYWORDS for w in words)
        seen_entity_ids: Set[str] = set()

        # 1. Alias Dictionary Multi-Word Matching (Longest-match first)
        sorted_aliases = sorted(self._alias_index.keys(), key=lambda x: len(x), reverse=True)
        for alias in sorted_aliases:
            # Word boundary check for alias
            pattern = r"\b" + re.escape(alias) + r"\b"
            match = re.search(pattern, text_lower)
            if match:
                candidates = self._alias_index[alias]
                span = (match.start(), match.end())
                matched_raw = text[match.start() : match.end()]

                if len(candidates) == 1:
                    e_id = candidates[0]
                    if e_id not in seen_entity_ids:
                        ent_def = self._entities[e_id]
                        mappings = self._derive_instruments(ent_def)
                        resolved_list.append(
                            ResolvedEntity(
                                entity_id=e_id,
                                canonical_name=ent_def.canonical_name,
                                entity_type=ent_def.entity_type,
                                matched_text=matched_raw,
                                resolution_state=ResolutionState.RESOLVED,
                                confidence=0.95,
                                evidence_span=span,
                                parent_entity_id=ent_def.parent_entity_id,
                                instruments=mappings,
                            )
                        )
                        seen_entity_ids.add(e_id)
                elif len(candidates) > 1:
                    # Ambiguous mention
                    resolved_list.append(
                        ResolvedEntity(
                            matched_text=matched_raw,
                            resolution_state=ResolutionState.AMBIGUOUS,
                            confidence=0.50,
                            evidence_span=span,
                            candidate_entities=candidates,
                        )
                    )

        # 2. Exact Ticker Matching with Stopword & Collision Guard
        for word in words_upper:
            if word in self._ticker_index:
                # If ticker is an ordinary English stopword, require explicit financial context
                if word in TICKER_COLLISION_STOPWORDS and not has_financial_context:
                    continue

                candidates = self._ticker_index[word]
                if len(candidates) == 1:
                    e_id = candidates[0]
                    if e_id not in seen_entity_ids:
                        ent_def = self._entities[e_id]
                        mappings = self._derive_instruments(ent_def)
                        resolved_list.append(
                            ResolvedEntity(
                                entity_id=e_id,
                                canonical_name=ent_def.canonical_name,
                                entity_type=ent_def.entity_type,
                                matched_text=word,
                                resolution_state=ResolutionState.RESOLVED,
                                confidence=0.90 if word not in TICKER_COLLISION_STOPWORDS else 0.70,
                                parent_entity_id=ent_def.parent_entity_id,
                                instruments=mappings,
                            )
                        )
                        seen_entity_ids.add(e_id)

        # 3. Executive Name Co-occurrence Matching
        for e_id, ent_def in self._entities.items():
            if e_id in seen_entity_ids:
                continue
            for exec_name in ent_def.executives:
                if exec_name.lower() in text_lower:
                    mappings = self._derive_instruments(ent_def)
                    resolved_list.append(
                        ResolvedEntity(
                            entity_id=e_id,
                            canonical_name=ent_def.canonical_name,
                            entity_type=ent_def.entity_type,
                            matched_text=exec_name,
                            resolution_state=ResolutionState.RESOLVED,
                            confidence=0.85,
                            parent_entity_id=ent_def.parent_entity_id,
                            instruments=mappings,
                        )
                    )
                    seen_entity_ids.add(e_id)
                    break

        # 4. If nothing resolved, return explicit UNRESOLVED record
        if not resolved_list:
            resolved_list.append(
                ResolvedEntity(
                    matched_text=text[:50],
                    resolution_state=ResolutionState.UNRESOLVED,
                    confidence=0.0,
                    candidate_entities=[],
                    instruments=[],
                )
            )

        return resolved_list

    def _derive_instruments(self, ent_def: EntityDefinition) -> List[InstrumentMappingRecord]:
        """Derive instrument mappings using Phase 2 relevance taxonomy."""
        # Check direct instruments registered on entity
        mappings: List[InstrumentMappingRecord] = []
        target_entity_id = ent_def.parent_entity_id or ent_def.entity_id

        # Query InstrumentMaster for canonical mappings
        event_family = "MACRO" if ent_def.entity_type in (EntityType.REGULATOR, EntityType.MACRO_ENTITY) else "GENERAL"
        resolved_mappings = self.instrument_master.resolve_event_mapping(
            entity_id=target_entity_id,
            event_family=event_family,
        )
        seen_keys: Set[Tuple[str, str]] = set()
        if ent_def.entity_type not in (EntityType.REGULATOR, EntityType.MACRO_ENTITY):
            for inst_id in ent_def.direct_instruments:
                rec = InstrumentMappingRecord(
                    instrument_id=inst_id,
                    relevance=RelevanceTier.DIRECT,
                    weight=1.0,
                    notes=f"Direct instrument ({target_entity_id})",
                )
                key = (rec.instrument_id, rec.relevance.value)
                if key not in seen_keys:
                    mappings.append(rec)
                    seen_keys.add(key)
        for rm in resolved_mappings:
            key = (rm.instrument_id, rm.relevance.value)
            if key not in seen_keys:
                mappings.append(rm)
                seen_keys.add(key)
        return mappings


_entity_resolver: Optional[FinancialEntityResolver] = None


def get_entity_resolver() -> FinancialEntityResolver:
    """Return shared FinancialEntityResolver singleton."""
    global _entity_resolver
    if _entity_resolver is None:
        _entity_resolver = FinancialEntityResolver()
    return _entity_resolver
