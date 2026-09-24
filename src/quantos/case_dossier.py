from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum

from .claim_evidence_graph import ClaimEvidenceGraph
from .claims import ClaimStore
from .professional_reviews import dossier_fingerprint
from .reasoning_dossier import EvidenceDossier, EvidenceDossierBuilder
from .research_case import ResearchCase, ResearchCaseStore
from .scenarios import ScenarioSet, ScenarioSetStore


class CaseEvidenceRole(str, Enum):
    SUPPORTING = "SUPPORTING"
    LIMITING = "LIMITING"
    CONTRADICTING = "CONTRADICTING"


@dataclass(frozen=True)
class CaseClaimDossier:
    role: CaseEvidenceRole
    claim_id: str
    dossier_fingerprint: str
    dossier: EvidenceDossier


@dataclass(frozen=True)
class CaseDossier:
    case: ResearchCase
    scenario_set: ScenarioSet
    claim_dossiers: tuple[CaseClaimDossier, ...]
    case_dossier_fingerprint: str
    caveat: str


class CaseDossierBuilder:
    CAVEAT = (
        "Case dossier fingerprint binds one immutable research case, one exact "
        "scenario set and the current evidence dossiers for every cited claim. "
        "It is a review target, not a capital authorization."
    )

    def build(
        self,
        *,
        case_id: str,
        scenario_set_id: str,
        cases: ResearchCaseStore,
        scenarios: ScenarioSetStore,
        claims: ClaimStore,
        evidence_graph: ClaimEvidenceGraph,
    ) -> CaseDossier:
        case = cases.get(case_id)
        if case is None:
            raise KeyError(case_id)

        scenario_set = scenarios.get(scenario_set_id)
        if scenario_set is None:
            raise KeyError(scenario_set_id)
        if scenario_set.case_id != case.case_id:
            raise ValueError(
                "scenario set does not belong to the requested research case"
            )

        builder = EvidenceDossierBuilder()
        claim_dossiers: list[CaseClaimDossier] = []
        for role, claim_ids in (
            (CaseEvidenceRole.SUPPORTING, case.supporting_claim_ids),
            (CaseEvidenceRole.LIMITING, case.limiting_claim_ids),
            (CaseEvidenceRole.CONTRADICTING, case.contradicting_claim_ids),
        ):
            for claim_id in claim_ids:
                dossier = builder.build(
                    claim_id,
                    claims=claims,
                    evidence_graph=evidence_graph,
                )
                claim_dossiers.append(
                    CaseClaimDossier(
                        role=role,
                        claim_id=claim_id,
                        dossier_fingerprint=dossier_fingerprint(dossier),
                        dossier=dossier,
                    )
                )

        claim_dossiers.sort(key=lambda item: (item.role.value, item.claim_id))
        frozen_claim_dossiers = tuple(claim_dossiers)
        fingerprint = self._fingerprint(
            case_id=case.case_id,
            scenario_set_id=scenario_set.scenario_set_id,
            claim_dossiers=frozen_claim_dossiers,
        )
        return CaseDossier(
            case=case,
            scenario_set=scenario_set,
            claim_dossiers=frozen_claim_dossiers,
            case_dossier_fingerprint=fingerprint,
            caveat=self.CAVEAT,
        )

    @staticmethod
    def _fingerprint(
        *,
        case_id: str,
        scenario_set_id: str,
        claim_dossiers: tuple[CaseClaimDossier, ...],
    ) -> str:
        payload = {
            "case_id": case_id,
            "scenario_set_id": scenario_set_id,
            "claim_dossiers": [
                {
                    "role": item.role.value,
                    "claim_id": item.claim_id,
                    "dossier_fingerprint": item.dossier_fingerprint,
                }
                for item in claim_dossiers
            ],
        }
        material = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "case-dossier:" + hashlib.sha256(material).hexdigest()
