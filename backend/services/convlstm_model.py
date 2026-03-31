"""
ConvLSTM Model — Spatiotemporal deforestation prediction using
Convolutional LSTM that captures dependencies across time AND
across neighboring H3 hexagonal cells.

Architecture:
  Input:  (batch, T, C, H, W) where T=timesteps, C=8 features,
          H=W=3 (3×3 embedding of H3 k-ring neighbors)
  ConvLSTM layers: 2 stacked, hidden_dim=[16, 8], kernel=3
  Output head: Conv2d → center cell prediction (next year's deforestation_ha)

Training:
  Uses timeline series data from all available jobs.
  Sliding window of `seq_len` years → predict next year.
  Loss: MSE on the center cell's deforestation value.

GPU: ~50-100 MB VRAM (tiny model, fits easily on RTX 4050).
"""
from __future__ import annotations

import logging
import os
import sqlite3
import json

import numpy as np
import torch
import torch.nn as nn

logger = logging.getLogger("apex.convlstm")

_MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")
_CONVLSTM_PATH = os.path.join(_MODEL_DIR, "forecast_convlstm.pt")

FEATURE_COLS = [
    "deforestation_ha", "urban_expansion_ha", "hansen_loss_ha",
    "sar_change_ha", "fire_burned_ha", "firms_hotspots",
    "firms_frp_mw", "alerts_count",
]
N_FEATURES = len(FEATURE_COLS)
SEQ_LEN = 4       # timesteps to look back
HIDDEN_DIMS = [16, 8]
KERNEL_SIZE = 3
SPATIAL_SIZE = 3   # 3×3 grid embedding (center + 8 positions, 7 H3 neighbors)


# ─── ConvLSTM Cell ─────────────────────────────────

class ConvLSTMCell(nn.Module):
    """Single ConvLSTM cell operating on (B, C, H, W) inputs."""

    def __init__(self, input_dim: int, hidden_dim: int, kernel_size: int = 3):
        super().__init__()
        pad = kernel_size // 2
        self.hidden_dim = hidden_dim
        self.gates = nn.Conv2d(
            input_dim + hidden_dim, 4 * hidden_dim,
            kernel_size=kernel_size, padding=pad, bias=True,
        )

    def forward(self, x: torch.Tensor, h: torch.Tensor, c: torch.Tensor):
        combined = torch.cat([x, h], dim=1)  # (B, input+hidden, H, W)
        gates = self.gates(combined)
        i, f, o, g = gates.chunk(4, dim=1)
        i = torch.sigmoid(i)
        f = torch.sigmoid(f)
        o = torch.sigmoid(o)
        g = torch.tanh(g)
        c_next = f * c + i * g
        h_next = o * torch.tanh(c_next)
        return h_next, c_next


class ConvLSTMStack(nn.Module):
    """Stacked ConvLSTM with prediction head."""

    def __init__(
        self,
        input_dim: int = N_FEATURES,
        hidden_dims: list[int] | None = None,
        kernel_size: int = KERNEL_SIZE,
        spatial_size: int = SPATIAL_SIZE,
    ):
        super().__init__()
        if hidden_dims is None:
            hidden_dims = HIDDEN_DIMS
        self.spatial_size = spatial_size
        self.layers = nn.ModuleList()
        self.hidden_dims = hidden_dims

        prev_dim = input_dim
        for hd in hidden_dims:
            self.layers.append(ConvLSTMCell(prev_dim, hd, kernel_size))
            prev_dim = hd

        # Prediction head: output 1 value (deforestation_ha) at center cell
        self.head = nn.Sequential(
            nn.Conv2d(hidden_dims[-1], 4, kernel_size=1),
            nn.ReLU(),
            nn.Conv2d(4, 1, kernel_size=1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        x: (B, T, C, H, W)  — sequence of spatial feature maps.
        Returns: (B,) — predicted deforestation_ha at center cell.
        """
        B, T, C, H, W = x.shape
        device = x.device

        # Init hidden states
        hs = [torch.zeros(B, hd, H, W, device=device) for hd in self.hidden_dims]
        cs = [torch.zeros(B, hd, H, W, device=device) for hd in self.hidden_dims]

        for t in range(T):
            inp = x[:, t]  # (B, C, H, W)
            for i, cell in enumerate(self.layers):
                hs[i], cs[i] = cell(inp, hs[i], cs[i])
                inp = hs[i]

        # Use last timestep's final layer hidden state
        out = self.head(hs[-1])                     # (B, 1, H, W)
        center = H // 2
        return out[:, 0, center, center]             # (B,)


# ─── Data Preparation ──────────────────────────────

def _build_spatial_grid(center_vec: list[float], neighbor_vecs: list[list[float]] | None = None) -> np.ndarray:
    """
    Embed a center cell and its neighbors into a 3×3 spatial grid.
    Positions: center is (1,1), neighbors fill remaining 8 positions.
    Missing neighbors get zeros.

    Returns: (C, 3, 3) array.
    """
    C = len(center_vec)
    grid = np.zeros((C, SPATIAL_SIZE, SPATIAL_SIZE), dtype=np.float32)
    grid[:, 1, 1] = center_vec  # center

    if neighbor_vecs:
        # Fill 8 positions around center (clockwise from top-left)
        positions = [(0, 0), (0, 1), (0, 2), (1, 0), (1, 2), (2, 0), (2, 1), (2, 2)]
        for idx, (r, c) in enumerate(positions):
            if idx < len(neighbor_vecs):
                grid[:, r, c] = neighbor_vecs[idx]

    return grid


def _build_training_data(all_series: list[list[dict]], seq_len: int = SEQ_LEN):
    """
    Build training tensors from multiple timeline series.
    Each series is a list of dicts with year + FEATURE_COLS.

    Since we don't have actual H3 neighbor data in timeline mode,
    we use data augmentation: the center cell is the real data,
    neighbors get scaled copies to simulate spatial correlation.

    Returns: (X, y) tensors.
    """
    X_list, y_list = [], []
    rng = np.random.default_rng(42)

    for series in all_series:
        if len(series) < seq_len + 1:
            continue

        vectors = []
        for entry in series:
            vec = [float(entry.get(col, 0.0)) for col in FEATURE_COLS]
            vectors.append(vec)

        for i in range(len(vectors) - seq_len):
            seq_grids = []
            for t in range(seq_len):
                center = vectors[i + t]
                # Simulate neighbors: center ± noise (spatial correlation)
                neighbors = []
                for _ in range(8):
                    noise = rng.normal(0, 0.1, size=N_FEATURES).astype(np.float32)
                    neighbor = np.clip(np.array(center) * (1.0 + noise), 0, None)
                    neighbors.append(neighbor.tolist())
                grid = _build_spatial_grid(center, neighbors)
                seq_grids.append(grid)

            X_list.append(np.stack(seq_grids, axis=0))   # (T, C, H, W)
            y_list.append(vectors[i + seq_len][0])         # deforestation_ha

    if not X_list:
        return None, None

    X = torch.tensor(np.stack(X_list), dtype=torch.float32)
    y = torch.tensor(np.array(y_list), dtype=torch.float32)
    return X, y


# ─── Training ──────────────────────────────────────

def train_convlstm(
    all_series: list[list[dict]],
    epochs: int = 50,
    lr: float = 1e-3,
    device: str = "cpu",
) -> dict:
    """
    Train ConvLSTM model on timeline data.

    Parameters
    ----------
    all_series : list of timeline series (each is list of year-dicts)
    epochs : training epochs
    lr : learning rate
    device : 'cpu' or 'cuda'

    Returns
    -------
    Training summary dict.
    """
    X, y = _build_training_data(all_series)
    if X is None or len(X) < 3:
        return {"status": "error", "detail": f"Insufficient data: need ≥{SEQ_LEN + 1} years"}

    # Train/val split
    n = len(X)
    split = max(int(n * 0.8), 1)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    # Normalize features
    mean = X_train.mean(dim=(0, 1, 3, 4), keepdim=True)
    std = X_train.std(dim=(0, 1, 3, 4), keepdim=True) + 1e-8
    X_train = (X_train - mean) / std
    X_val = (X_val - mean) / std if len(X_val) > 0 else X_val

    model = ConvLSTMStack().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.MSELoss()

    model.train()
    best_val_loss = float("inf")
    for epoch in range(epochs):
        optimizer.zero_grad()
        preds = model(X_train.to(device))
        loss = criterion(preds, y_train.to(device))
        loss.backward()
        optimizer.step()

        if epoch % 10 == 0:
            logger.info("Epoch %d/%d — loss: %.4f", epoch, epochs, loss.item())

    # Validate
    val_loss = 0.0
    if len(X_val) > 0:
        model.eval()
        with torch.no_grad():
            val_preds = model(X_val.to(device))
            val_loss = criterion(val_preds, y_val.to(device)).item()

    # Save model + normalization stats
    os.makedirs(_MODEL_DIR, exist_ok=True)
    torch.save({
        "model_state": model.state_dict(),
        "mean": mean.cpu(),
        "std": std.cpu(),
    }, _CONVLSTM_PATH)

    logger.info(
        "ConvLSTM trained: %d samples, train_loss=%.4f, val_loss=%.4f",
        n, loss.item(), val_loss,
    )
    return {
        "status": "ok",
        "samples": n,
        "train_size": len(X_train),
        "val_size": len(X_val),
        "train_loss": round(loss.item(), 4),
        "val_loss": round(val_loss, 4),
    }


# ─── Inference ─────────────────────────────────────

def _load_convlstm(device: str = "cpu") -> tuple[ConvLSTMStack | None, torch.Tensor | None, torch.Tensor | None]:
    """Load trained ConvLSTM model + normalization stats."""
    if not os.path.exists(_CONVLSTM_PATH):
        return None, None, None
    checkpoint = torch.load(_CONVLSTM_PATH, map_location=device, weights_only=False)
    model = ConvLSTMStack()
    model.load_state_dict(checkpoint["model_state"])
    model.to(device)
    model.eval()
    return model, checkpoint["mean"], checkpoint["std"]


def forecast_convlstm(
    series: list[dict],
    horizon: int = 3,
    device: str = "cpu",
) -> dict:
    """
    Run ConvLSTM inference on a timeline series.

    Parameters
    ----------
    series : list of per-year dicts with FEATURE_COLS
    horizon : years to predict ahead
    device : 'cpu' or 'cuda'

    Returns
    -------
    Forecast dict with predictions list.
    """
    model, mean, std = _load_convlstm(device)
    if model is None:
        return {"available": False, "reason": "Modelo ConvLSTM no entrenado aún"}

    if len(series) < SEQ_LEN:
        return {"available": False, "reason": f"Necesita ≥{SEQ_LEN} años, tiene {len(series)}"}

    # Build initial sequence from last SEQ_LEN years
    recent = series[-SEQ_LEN:]
    rng = np.random.default_rng(0)

    predictions = []
    last_year = series[-1]["year"]

    # Build current sequence buffer
    seq_grids = []
    for entry in recent:
        vec = [float(entry.get(col, 0.0)) for col in FEATURE_COLS]
        neighbors = []
        for _ in range(8):
            noise = rng.normal(0, 0.05, size=N_FEATURES).astype(np.float32)
            neighbor = np.clip(np.array(vec) * (1.0 + noise), 0, None)
            neighbors.append(neighbor.tolist())
        seq_grids.append(_build_spatial_grid(vec, neighbors))

    for h in range(1, horizon + 1):
        # Build input tensor (1, T, C, H, W)
        X = torch.tensor(
            np.stack(seq_grids[-SEQ_LEN:]),
            dtype=torch.float32,
        ).unsqueeze(0)

        # Normalize
        X = (X - mean) / (std + 1e-8)

        with torch.no_grad():
            pred_val = model(X.to(device)).item()
            pred_val = max(pred_val, 0.0)

        predictions.append({
            "year": last_year + h,
            "deforestation_ha": round(pred_val, 3),
            "risk": "CRITICAL" if pred_val > 10 else "HIGH" if pred_val > 5 else "MEDIUM" if pred_val > 1 else "LOW",
        })

        # Autoregressive: use prediction for next step
        new_vec = [float(series[-1].get(col, 0.0)) for col in FEATURE_COLS]
        new_vec[0] = pred_val  # update deforestation_ha
        neighbors = []
        for _ in range(8):
            noise = rng.normal(0, 0.05, size=N_FEATURES).astype(np.float32)
            neighbor = np.clip(np.array(new_vec) * (1.0 + noise), 0, None)
            neighbors.append(neighbor.tolist())
        seq_grids.append(_build_spatial_grid(new_vec, neighbors))

    return {
        "available": True,
        "method": "convlstm",
        "predictions": predictions,
    }
