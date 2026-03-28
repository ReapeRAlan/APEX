"""
APEX Bayesian Fusion Service — Unified belief map from multiple detection engines.

Implements a particle-filter-based Bayesian update over a grid of H3 cells.
Each cell maintains a probability distribution over possible states:
  - sin_ilicito   (no illegal activity)
  - tala           (illegal logging)
  - cus_inmobiliario (illegal land-use change for real estate)
  - frontera_agricola (agricultural frontier expansion)

Motors provide observations that are fused using calibrated confusion matrices.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from sqlalchemy.orm import Session

from ..db.session import SessionLocal
from ..db.models import BeliefState, GridCell

logger = logging.getLogger("apex.bayesian")

# ── Possible states ──
STATES = ["sin_ilicito", "tala", "cus_inmobiliario", "frontera_agricola"]
N_STATES = len(STATES)

# ── Motor confusion matrices ──
# Each matrix: P(observation=positive | true_state)
# Rows = true states, single column = P(motor fires positive)
# Calibrated from historical detections (initial estimates — refine with data)
MOTOR_CONFUSION = {
    "deforestation": np.array([0.05, 0.85, 0.40, 0.60]),  # high recall for tala
    "vegetation": np.array([0.08, 0.70, 0.30, 0.55]),
    "hansen": np.array([0.03, 0.80, 0.25, 0.50]),
    "alerts": np.array([0.04, 0.75, 0.30, 0.45]),
    "sar": np.array([0.06, 0.65, 0.50, 0.40]),  # good for structures
    "dynamic_world": np.array([0.10, 0.55, 0.60, 0.50]),
    "drivers": np.array([0.05, 0.60, 0.55, 0.65]),  # good for classifying type
    "fire": np.array([0.02, 0.30, 0.05, 0.35]),  # fires correlate with agri frontier
    "firms_hotspots": np.array([0.02, 0.25, 0.03, 0.30]),
    "legal": np.array([0.01, 0.10, 0.10, 0.10]),  # weak signal
    "crossval": np.array([0.08, 0.60, 0.45, 0.50]),
    "ccdc": np.array([0.05, 0.70, 0.35, 0.50]),
    "prithvi": np.array([0.04, 0.75, 0.55, 0.60]),
    # NLP news — weak signal
    "news_nlp": np.array([0.02, 0.20, 0.15, 0.20]),
}

# Prior probability (before any observations)
DEFAULT_PRIOR = np.array([0.85, 0.05, 0.05, 0.05])

# Degradation rate (IC loss per day without clean image)
DEFAULT_DEGRADATION_RATE = 0.02  # 2% CI loss per day


class BayesianFusion:
    """Manage and update belief states for the H3 grid."""

    def __init__(self):
        self.states = STATES
        self.n_states = N_STATES

    def get_belief(self, h3_index: str) -> dict:
        """Get current belief state for a cell."""
        with SessionLocal() as session:
            belief = (
                session.query(BeliefState)
                .filter(BeliefState.h3_index == h3_index)
                .order_by(BeliefState.timestamp.desc())
                .first()
            )

            if belief is None:
                return {
                    "h3_index": h3_index,
                    "probabilities": {
                        "sin_ilicito": DEFAULT_PRIOR[0],
                        "tala": DEFAULT_PRIOR[1],
                        "cus_inmobiliario": DEFAULT_PRIOR[2],
                        "frontera_agricola": DEFAULT_PRIOR[3],
                    },
                    "confidence_index": 0.5,
                    "last_update": None,
                    "acquire_commercial_image": False,
                }

            return {
                "h3_index": h3_index,
                "probabilities": {
                    "sin_ilicito": belief.p_sin_ilicito,
                    "tala": belief.p_tala,
                    "cus_inmobiliario": belief.p_cus_inmobiliario,
                    "frontera_agricola": belief.p_frontera_agricola,
                },
                "confidence_index": belief.confidence_index,
                "last_update": belief.timestamp.isoformat() if belief.timestamp else None,
                "acquire_commercial_image": belief.acquire_commercial_image,
                "source_motors": json.loads(belief.source_motors) if belief.source_motors else [],
            }

    def update_beliefs(
        self,
        motor_id: str,
        h3_index: str,
        detection_probability: float,
        confidence: float = 1.0,
    ) -> dict:
        """
        Bayesian update: incorporate a new observation from a motor.

        Args:
            motor_id: Engine that produced the observation (e.g., "deforestation")
            h3_index: H3 cell index
            detection_probability: P(motor says illegal) ∈ [0, 1]
            confidence: Overall motor confidence for this observation ∈ [0, 1]

        Returns: Updated belief dict
        """
        # Get confusion matrix for this motor
        if motor_id not in MOTOR_CONFUSION:
            logger.warning("Unknown motor '%s' — skipping update.", motor_id)
            return self.get_belief(h3_index)

        cm = MOTOR_CONFUSION[motor_id]

        # Get current prior
        current = self.get_belief(h3_index)
        prior = np.array([
            current["probabilities"]["sin_ilicito"],
            current["probabilities"]["tala"],
            current["probabilities"]["cus_inmobiliario"],
            current["probabilities"]["frontera_agricola"],
        ])

        # Bayesian update
        # P(state | observation) ∝ P(observation | state) × P(state)
        if detection_probability > 0.5:
            # Motor detected something — use confusion matrix directly
            likelihood = cm * detection_probability * confidence
        else:
            # Motor did NOT detect — use complement
            likelihood = (1.0 - cm) * (1.0 - detection_probability) * confidence
            # Boost no-illicit state
            likelihood[0] = max(likelihood[0], 0.5)

        posterior = likelihood * prior
        posterior_sum = posterior.sum()
        if posterior_sum > 0:
            posterior = posterior / posterior_sum
        else:
            posterior = prior

        # Update confidence index based on observation strength
        new_ci = min(1.0, current["confidence_index"] + 0.1 * confidence)

        # Track which motors contributed
        sources = current.get("source_motors", [])
        if isinstance(sources, str):
            sources = json.loads(sources) if sources else []
        if motor_id not in sources:
            sources.append(motor_id)

        # Save to DB
        with SessionLocal() as session:
            new_belief = BeliefState(
                h3_index=h3_index,
                timestamp=datetime.utcnow(),
                p_sin_ilicito=float(posterior[0]),
                p_tala=float(posterior[1]),
                p_cus_inmobiliario=float(posterior[2]),
                p_frontera_agricola=float(posterior[3]),
                confidence_index=float(new_ci),
                last_clean_image=datetime.utcnow(),
                acquire_commercial_image=False,
                source_motors=json.dumps(sources),
            )
            session.add(new_belief)
            session.commit()

        logger.info(
            "Belief updated for %s via %s: P(tala)=%.3f P(cus)=%.3f P(agri)=%.3f CI=%.2f",
            h3_index, motor_id, posterior[1], posterior[2], posterior[3], new_ci,
        )

        return self.get_belief(h3_index)

    def degrade_beliefs(self, h3_index: str, days_without_image: int) -> dict:
        """
        Degrade confidence index when no clean image is available.

        If CI drops below 0.4, marks cell for commercial image acquisition.
        """
        current = self.get_belief(h3_index)
        ci = current["confidence_index"]

        # Linear degradation
        degraded_ci = max(0.1, ci - DEFAULT_DEGRADATION_RATE * days_without_image)

        # Determine if commercial image needed
        acquire = degraded_ci < 0.4

        with SessionLocal() as session:
            new_belief = BeliefState(
                h3_index=h3_index,
                timestamp=datetime.utcnow(),
                p_sin_ilicito=current["probabilities"]["sin_ilicito"],
                p_tala=current["probabilities"]["tala"],
                p_cus_inmobiliario=current["probabilities"]["cus_inmobiliario"],
                p_frontera_agricola=current["probabilities"]["frontera_agricola"],
                confidence_index=float(degraded_ci),
                acquire_commercial_image=acquire,
                source_motors=json.dumps(current.get("source_motors", [])),
            )
            session.add(new_belief)
            session.commit()

        if acquire:
            logger.warning(
                "Cell %s CI degraded to %.2f — flagged for commercial image acquisition.",
                h3_index, degraded_ci,
            )

        return self.get_belief(h3_index)

    def degrade_all_active(self):
        """Run degradation for all cells with active beliefs (scheduled task)."""
        with SessionLocal() as session:
            # Get latest belief for each cell
            from sqlalchemy import func
            subq = (
                session.query(
                    BeliefState.h3_index,
                    func.max(BeliefState.timestamp).label("max_ts"),
                )
                .group_by(BeliefState.h3_index)
                .subquery()
            )

            latest = (
                session.query(BeliefState)
                .join(
                    subq,
                    (BeliefState.h3_index == subq.c.h3_index)
                    & (BeliefState.timestamp == subq.c.max_ts),
                )
                .all()
            )

        now = datetime.utcnow()
        degraded_count = 0

        for belief in latest:
            if belief.last_clean_image:
                days_since = (now - belief.last_clean_image).days
            else:
                days_since = 30  # Default: assume old

            if days_since > 3:  # Only degrade if >3 days without image
                self.degrade_beliefs(belief.h3_index, days_since)
                degraded_count += 1

        logger.info("Degraded beliefs for %d cells.", degraded_count)
        return degraded_count


# Module-level singleton
bayesian_fusion = BayesianFusion()
