from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import duckdb

from .backtest_economics import BacktestResult
from .factor_contracts import FactorSpecification
from .overfitting_diagnostics import (
    MultipleTestingAudit,
    VariantReturnSeries,
)
from .performance_analytics import PerformanceAnalysis
from .readiness import ProspectiveShadowPermit
from .validation import (
    ResearchExperimentSpecification,
    WalkForwardValidationPlan,
)


class ModelLifecycleStage(str, Enum):
    IDEA = "IDEA"
    RESEARCH = "RESEARCH"
    VALIDATED = "VALIDATED"
    BACKTESTED = "BACKTESTED"
    PAPER = "PAPER"
    APPROVED = "APPROVED"
    LIVE = "LIVE"
    RETIRED = "RETIRED"


_STAGE_ORDER = {
    ModelLifecycleStage.RESEARCH: 1,
    ModelLifecycleStage.VALIDATED: 2,
    ModelLifecycleStage.BACKTESTED: 3,
    ModelLifecycleStage.PAPER: 4,
}


@dataclass(frozen=True)
class ResearchRunManifest:
    manifest_id: str
    model_id: str
    experiment_id: str
    research_case_id: str
    case_dossier_fingerprint: str | None
    factor_id: str
    universe_policy_id: str
    validation_plan_id: str | None
    backtest_id: str | None
    backtest_policy_id: str | None
    performance_analysis_id: str | None
    performance_policy_id: str | None
    multiple_testing_audit_id: str | None
    overfitting_policy_id: str | None
    selected_variant_id: str | None
    selected_variant_series_id: str | None
    shadow_permit_id: str | None
    benchmark_kinds: tuple[str, ...]
    dataset_fingerprints: tuple[str, ...]
    code_revision: str
    evidence_references: tuple[str, ...]
    eligible_stage: ModelLifecycleStage


class ResearchRunManifestBuilder:
    """Build one lineage-checked manifest for a quantitative research run."""

    def build(
        self,
        *,
        experiment: ResearchExperimentSpecification,
        factor: FactorSpecification,
        dataset_fingerprints: tuple[str, ...],
        code_revision: str,
        evidence_references: tuple[str, ...],
        validation_plan: WalkForwardValidationPlan | None = None,
        backtest: BacktestResult | None = None,
        performance: PerformanceAnalysis | None = None,
        multiple_testing: MultipleTestingAudit | None = None,
        selected_variant: VariantReturnSeries | None = None,
        shadow_permit: ProspectiveShadowPermit | None = None,
    ) -> ResearchRunManifest:
        if experiment.research_case_id is None:
            raise ValueError(
                "research-run manifest requires experiment.research_case_id"
            )
        if experiment.factor_id != factor.factor_id:
            raise ValueError("experiment and factor specification do not match")
        if not dataset_fingerprints or not all(
            item.strip() for item in dataset_fingerprints
        ):
            raise ValueError("research-run manifest requires dataset fingerprints")
        if len(dataset_fingerprints) != len(set(dataset_fingerprints)):
            raise ValueError("dataset fingerprints must be unique")
        if not code_revision.strip():
            raise ValueError("research-run manifest requires code revision")
        if not evidence_references or not all(
            item.strip() for item in evidence_references
        ):
            raise ValueError("research-run manifest requires evidence references")

        if validation_plan is not None:
            if validation_plan.experiment_id != experiment.experiment_id:
                raise ValueError("validation plan belongs to another experiment")
            if validation_plan.factor_id != factor.factor_id:
                raise ValueError("validation plan belongs to another factor")

        backtest_group = (
            backtest,
            performance,
            multiple_testing,
            selected_variant,
        )
        supplied_backtest_parts = sum(
            item is not None for item in backtest_group
        )
        if 0 < supplied_backtest_parts < len(backtest_group):
            raise ValueError(
                "BACKTESTED manifest requires backtest, performance analysis, "
                "multiple-testing audit, and selected variant together"
            )
        if backtest is not None:
            if validation_plan is None:
                raise ValueError("backtest requires validation plan")
            assert performance is not None
            assert multiple_testing is not None
            assert selected_variant is not None
            if backtest.factor_id != factor.factor_id:
                raise ValueError("backtest belongs to another factor")
            if backtest.validation_plan_id != validation_plan.plan_id:
                raise ValueError("backtest belongs to another validation plan")
            if performance.backtest_id != backtest.backtest_id:
                raise ValueError("performance analysis belongs to another backtest")
            if multiple_testing.experiment_id != experiment.experiment_id:
                raise ValueError(
                    "multiple-testing audit belongs to another experiment"
                )
            if multiple_testing.selected_variant_id != selected_variant.variant_id:
                raise ValueError(
                    "selected variant differs from multiple-testing audit"
                )
            if selected_variant.experiment_id != experiment.experiment_id:
                raise ValueError("selected variant belongs to another experiment")
            if selected_variant.backtest_id != backtest.backtest_id:
                raise ValueError("selected variant belongs to another backtest")
            if (
                selected_variant.series_id
                not in set(multiple_testing.variant_series_ids)
            ):
                raise ValueError(
                    "selected variant series is absent from multiple-testing audit"
                )

        if shadow_permit is not None:
            if backtest is None:
                raise ValueError("PAPER manifest requires complete backtest chain")
            if shadow_permit.purpose != "PROSPECTIVE_SHADOW_ONLY":
                raise ValueError("shadow permit has unsupported purpose")
            if shadow_permit.case_id != experiment.research_case_id:
                raise ValueError("shadow permit belongs to another Research Case")
            if not shadow_permit.case_dossier_fingerprint.startswith(
                "case-dossier:"
            ):
                raise ValueError("shadow permit has invalid case dossier fingerprint")

        eligible_stage = ModelLifecycleStage.RESEARCH
        if validation_plan is not None:
            eligible_stage = ModelLifecycleStage.VALIDATED
        if backtest is not None:
            eligible_stage = ModelLifecycleStage.BACKTESTED
        if shadow_permit is not None:
            eligible_stage = ModelLifecycleStage.PAPER

        model_id = _content_id(
            "research-model",
            {
                "experiment_id": experiment.experiment_id,
                "research_case_id": experiment.research_case_id,
            },
        )
        benchmark_kinds: tuple[str, ...] = ()
        if backtest is not None:
            benchmark_kinds = tuple(
                sorted(kind.value for kind, _ in backtest.final_benchmark_wealth)
            )
        payload = {
            "model_id": model_id,
            "experiment_id": experiment.experiment_id,
            "research_case_id": experiment.research_case_id,
            "case_dossier_fingerprint": (
                shadow_permit.case_dossier_fingerprint
                if shadow_permit is not None
                else None
            ),
            "factor_id": factor.factor_id,
            "universe_policy_id": factor.universe_policy_id,
            "validation_plan_id": (
                validation_plan.plan_id
                if validation_plan is not None
                else None
            ),
            "backtest_id": (
                backtest.backtest_id if backtest is not None else None
            ),
            "backtest_policy_id": (
                backtest.policy_id if backtest is not None else None
            ),
            "performance_analysis_id": (
                performance.analysis_id
                if performance is not None
                else None
            ),
            "performance_policy_id": (
                performance.policy_id
                if performance is not None
                else None
            ),
            "multiple_testing_audit_id": (
                multiple_testing.audit_id
                if multiple_testing is not None
                else None
            ),
            "overfitting_policy_id": (
                multiple_testing.policy_id
                if multiple_testing is not None
                else None
            ),
            "selected_variant_id": (
                selected_variant.variant_id
                if selected_variant is not None
                else None
            ),
            "selected_variant_series_id": (
                selected_variant.series_id
                if selected_variant is not None
                else None
            ),
            "shadow_permit_id": (
                shadow_permit.permit_id
                if shadow_permit is not None
                else None
            ),
            "benchmark_kinds": list(benchmark_kinds),
            "dataset_fingerprints": sorted(dataset_fingerprints),
            "code_revision": code_revision.strip(),
            "evidence_references": list(evidence_references),
            "eligible_stage": eligible_stage.value,
        }
        return ResearchRunManifest(
            manifest_id=_content_id("research-run-manifest", payload),
            model_id=model_id,
            experiment_id=experiment.experiment_id,
            research_case_id=experiment.research_case_id,
            case_dossier_fingerprint=payload["case_dossier_fingerprint"],
            factor_id=factor.factor_id,
            universe_policy_id=factor.universe_policy_id,
            validation_plan_id=payload["validation_plan_id"],
            backtest_id=payload["backtest_id"],
            backtest_policy_id=payload["backtest_policy_id"],
            performance_analysis_id=payload["performance_analysis_id"],
            performance_policy_id=payload["performance_policy_id"],
            multiple_testing_audit_id=payload["multiple_testing_audit_id"],
            overfitting_policy_id=payload["overfitting_policy_id"],
            selected_variant_id=payload["selected_variant_id"],
            selected_variant_series_id=payload["selected_variant_series_id"],
            shadow_permit_id=payload["shadow_permit_id"],
            benchmark_kinds=benchmark_kinds,
            dataset_fingerprints=tuple(sorted(dataset_fingerprints)),
            code_revision=code_revision.strip(),
            evidence_references=evidence_references,
            eligible_stage=eligible_stage,
        )


@dataclass(frozen=True)
class ModelRegistryState:
    model_id: str
    manifest_id: str
    stage: ModelLifecycleStage
    updated_at: datetime
    updated_by: str


@dataclass(frozen=True)
class ModelTransition:
    transition_id: str
    ordinal: int
    model_id: str
    manifest_id: str
    from_stage: ModelLifecycleStage
    to_stage: ModelLifecycleStage
    transitioned_at: datetime
    transitioned_by: str
    reason: str
    evidence_references: tuple[str, ...]


class ModelRegistry:
    """Immutable lifecycle ledger capped at PAPER in the prototype."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS research_model_state (
                model_id VARCHAR PRIMARY KEY,
                manifest_id VARCHAR NOT NULL,
                stage VARCHAR NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                updated_by VARCHAR NOT NULL,
                manifest_json VARCHAR NOT NULL
            )
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS research_model_transitions (
                transition_id VARCHAR PRIMARY KEY,
                ordinal BIGINT NOT NULL,
                model_id VARCHAR NOT NULL,
                manifest_id VARCHAR NOT NULL,
                from_stage VARCHAR NOT NULL,
                to_stage VARCHAR NOT NULL,
                transitioned_at TIMESTAMPTZ NOT NULL,
                transitioned_by VARCHAR NOT NULL,
                reason VARCHAR NOT NULL,
                evidence_references_json VARCHAR NOT NULL
            )
            """
        )

    def register(
        self,
        *,
        manifest: ResearchRunManifest,
        registered_at: datetime,
        registered_by: str,
    ) -> ModelRegistryState:
        self._validate_actor_time(registered_at, registered_by)
        current = self.current(manifest.model_id)
        if current is None:
            state = ModelRegistryState(
                model_id=manifest.model_id,
                manifest_id=manifest.manifest_id,
                stage=ModelLifecycleStage.RESEARCH,
                updated_at=registered_at,
                updated_by=registered_by.strip(),
            )
            self._write_state(state, manifest)
            return state

        if current.stage is ModelLifecycleStage.RETIRED:
            raise ValueError("retired model cannot accept a new manifest")
        if current.manifest_id == manifest.manifest_id:
            return current

        ordinal = self._next_transition_ordinal(manifest.model_id)
        reset = ModelTransition(
            transition_id=_transition_id(
                model_id=manifest.model_id,
                manifest_id=manifest.manifest_id,
                ordinal=ordinal,
                from_stage=current.stage,
                to_stage=ModelLifecycleStage.RESEARCH,
                transitioned_at=registered_at,
                transitioned_by=registered_by.strip(),
                reason="MANIFEST_CHANGED_RESET",
                evidence_references=(manifest.manifest_id,),
            ),
            ordinal=ordinal,
            model_id=manifest.model_id,
            manifest_id=manifest.manifest_id,
            from_stage=current.stage,
            to_stage=ModelLifecycleStage.RESEARCH,
            transitioned_at=registered_at,
            transitioned_by=registered_by.strip(),
            reason="MANIFEST_CHANGED_RESET",
            evidence_references=(manifest.manifest_id,),
        )
        self._write_transition(reset)
        state = ModelRegistryState(
            model_id=manifest.model_id,
            manifest_id=manifest.manifest_id,
            stage=ModelLifecycleStage.RESEARCH,
            updated_at=registered_at,
            updated_by=registered_by.strip(),
        )
        self._write_state(state, manifest)
        return state

    def transition(
        self,
        *,
        manifest: ResearchRunManifest,
        to_stage: ModelLifecycleStage,
        transitioned_at: datetime,
        transitioned_by: str,
        reason: str,
        evidence_references: tuple[str, ...],
    ) -> ModelRegistryState:
        self._validate_actor_time(transitioned_at, transitioned_by)
        if not reason.strip():
            raise ValueError("transition reason is required")
        if not evidence_references or not all(
            item.strip() for item in evidence_references
        ):
            raise ValueError("transition requires evidence references")
        if to_stage in {
            ModelLifecycleStage.APPROVED,
            ModelLifecycleStage.LIVE,
        }:
            raise ValueError(
                "prototype registry has no APPROVED or LIVE capital authority"
            )
        if to_stage is ModelLifecycleStage.IDEA:
            raise ValueError("registered models cannot transition back to IDEA")

        current = self.current(manifest.model_id)
        if current is None:
            raise ValueError("model must be registered before transition")
        if current.manifest_id != manifest.manifest_id:
            raise ValueError(
                "registry state is stale for this manifest; register it first"
            )
        if current.stage is ModelLifecycleStage.RETIRED:
            raise ValueError("retired model cannot transition")

        if to_stage is ModelLifecycleStage.RETIRED:
            if current.stage is ModelLifecycleStage.IDEA:
                raise ValueError("unregistered idea cannot be retired here")
        else:
            expected = {
                ModelLifecycleStage.RESEARCH: ModelLifecycleStage.VALIDATED,
                ModelLifecycleStage.VALIDATED: ModelLifecycleStage.BACKTESTED,
                ModelLifecycleStage.BACKTESTED: ModelLifecycleStage.PAPER,
            }.get(current.stage)
            if expected is None or to_stage is not expected:
                raise ValueError("lifecycle transitions cannot skip stages")
            if (
                _STAGE_ORDER[to_stage]
                > _STAGE_ORDER[manifest.eligible_stage]
            ):
                raise ValueError(
                    "manifest lacks artifacts required for requested stage"
                )

        ordinal = self._next_transition_ordinal(manifest.model_id)
        transition = ModelTransition(
            transition_id=_transition_id(
                model_id=manifest.model_id,
                manifest_id=manifest.manifest_id,
                ordinal=ordinal,
                from_stage=current.stage,
                to_stage=to_stage,
                transitioned_at=transitioned_at,
                transitioned_by=transitioned_by.strip(),
                reason=reason.strip(),
                evidence_references=evidence_references,
            ),
            ordinal=ordinal,
            model_id=manifest.model_id,
            manifest_id=manifest.manifest_id,
            from_stage=current.stage,
            to_stage=to_stage,
            transitioned_at=transitioned_at,
            transitioned_by=transitioned_by.strip(),
            reason=reason.strip(),
            evidence_references=evidence_references,
        )
        self._write_transition(transition)
        state = ModelRegistryState(
            model_id=manifest.model_id,
            manifest_id=manifest.manifest_id,
            stage=to_stage,
            updated_at=transitioned_at,
            updated_by=transitioned_by.strip(),
        )
        self._write_state(state, manifest)
        return state

    def current(self, model_id: str) -> ModelRegistryState | None:
        row = self._con.execute(
            """
            SELECT model_id, manifest_id, stage, updated_at, updated_by
            FROM research_model_state
            WHERE model_id = ?
            """,
            [model_id],
        ).fetchone()
        if row is None:
            return None
        return ModelRegistryState(
            model_id=str(row[0]),
            manifest_id=str(row[1]),
            stage=ModelLifecycleStage(str(row[2])),
            updated_at=row[3],
            updated_by=str(row[4]),
        )

    def history(self, model_id: str) -> tuple[ModelTransition, ...]:
        rows = self._con.execute(
            """
            SELECT transition_id, ordinal, model_id, manifest_id, from_stage,
                   to_stage, transitioned_at, transitioned_by, reason,
                   evidence_references_json
            FROM research_model_transitions
            WHERE model_id = ?
            ORDER BY ordinal
            """,
            [model_id],
        ).fetchall()
        return tuple(
            ModelTransition(
                transition_id=str(row[0]),
                ordinal=int(row[1]),
                model_id=str(row[2]),
                manifest_id=str(row[3]),
                from_stage=ModelLifecycleStage(str(row[4])),
                to_stage=ModelLifecycleStage(str(row[5])),
                transitioned_at=row[6],
                transitioned_by=str(row[7]),
                reason=str(row[8]),
                evidence_references=tuple(
                    json.loads(str(row[9]))
                ),
            )
            for row in rows
        )

    def _write_state(
        self,
        state: ModelRegistryState,
        manifest: ResearchRunManifest,
    ) -> None:
        payload = json.dumps(
            {
                key: (
                    value.value
                    if isinstance(value, ModelLifecycleStage)
                    else value
                )
                for key, value in manifest.__dict__.items()
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self._con.execute(
            """
            INSERT INTO research_model_state
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (model_id) DO UPDATE SET
                manifest_id = excluded.manifest_id,
                stage = excluded.stage,
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by,
                manifest_json = excluded.manifest_json
            """,
            [
                state.model_id,
                state.manifest_id,
                state.stage.value,
                state.updated_at,
                state.updated_by,
                payload,
            ],
        )

    def _write_transition(self, transition: ModelTransition) -> None:
        existing = self._con.execute(
            """
            SELECT ordinal, model_id, manifest_id, from_stage, to_stage,
                   transitioned_at, transitioned_by, reason,
                   evidence_references_json
            FROM research_model_transitions
            WHERE transition_id = ?
            """,
            [transition.transition_id],
        ).fetchone()
        expected = (
            transition.ordinal,
            transition.model_id,
            transition.manifest_id,
            transition.from_stage.value,
            transition.to_stage.value,
            transition.transitioned_at,
            transition.transitioned_by,
            transition.reason,
            json.dumps(list(transition.evidence_references)),
        )
        if existing is not None:
            observed = (
                int(existing[0]),
                str(existing[1]),
                str(existing[2]),
                str(existing[3]),
                str(existing[4]),
                existing[5],
                str(existing[6]),
                str(existing[7]),
                str(existing[8]),
            )
            if observed != expected:
                raise ValueError("model transition identity conflict")
            return
        self._con.execute(
            """
            INSERT INTO research_model_transitions
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [transition.transition_id, *expected],
        )

    def _next_transition_ordinal(self, model_id: str) -> int:
        row = self._con.execute(
            """
            SELECT COALESCE(MAX(ordinal), 0)
            FROM research_model_transitions
            WHERE model_id = ?
            """,
            [model_id],
        ).fetchone()
        return int(row[0]) + 1

    @staticmethod
    def _validate_actor_time(at: datetime, actor: str) -> None:
        if at.tzinfo is None:
            raise ValueError("registry timestamp must be timezone-aware")
        if not actor.strip():
            raise ValueError("registry actor is required")

    def close(self) -> None:
        self._con.close()


def _transition_id(
    *,
    model_id: str,
    manifest_id: str,
    ordinal: int,
    from_stage: ModelLifecycleStage,
    to_stage: ModelLifecycleStage,
    transitioned_at: datetime,
    transitioned_by: str,
    reason: str,
    evidence_references: tuple[str, ...],
) -> str:
    return _content_id(
        "model-transition",
        {
            "model_id": model_id,
            "manifest_id": manifest_id,
            "ordinal": ordinal,
            "from_stage": from_stage.value,
            "to_stage": to_stage.value,
            "transitioned_at": transitioned_at.isoformat(),
            "transitioned_by": transitioned_by,
            "reason": reason,
            "evidence_references": list(evidence_references),
        },
    )


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()