"""
===============================================================================
  ML Filter — XGBoost signal confidence scorer
===============================================================================

Two modes of use:

  1. In-bot (inference)
     ─────────────────
     from ml_filter import MLFilter
     ml = MLFilter(threshold=0.60)
     fire, confidence = ml.should_fire(features)

  2. Command-line (training)
     ───────────────────────
     python ml_filter.py --train
     python ml_filter.py --train --label label_10 --min-samples 50
     python ml_filter.py --report          # show model stats
     python ml_filter.py --importance      # feature importance bar chart

Dependencies (install once):
     pip install xgboost scikit-learn matplotlib
===============================================================================
"""

import argparse
import logging
import pickle
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("UTBot.MLFilter")

_dir       = Path(__file__).resolve().parent
DB_PATH    = _dir / "signals.db"
MODEL_PATH = _dir / "ml_model.pkl"

# Features used for training and inference (must stay in sync)
FEATURE_COLS = [
    "atr_pct",
    "volume_ratio",
    "rsi_14",
    "ema20_dist_pct",
    "candle_body_pct",
    "atr_percentile",
    "hour",
    "minute",
    "day_of_week",
    "is_buy",        # 1 for BUY signal, 0 for SELL — added at predict time
]


# ---------------------------------------------------------------------------
# MLFilter — runtime class
# ---------------------------------------------------------------------------

class MLFilter:
    """
    Load a trained XGBoost model and score incoming UT Bot signals.

    If no model file exists, the filter runs in *pass-through* mode
    (every signal is allowed through) so the bot continues to work
    normally while you accumulate training data.
    """

    def __init__(
        self,
        model_path: Path = MODEL_PATH,
        threshold:  float = 0.60,
    ):
        self.threshold = threshold
        self.model     = None
        self._load(model_path)

    # ── Loading ──────────────────────────────────────────────────────────────
    def _load(self, path: Path) -> None:
        if path.exists():
            try:
                with open(path, "rb") as fh:
                    self.model = pickle.load(fh)
                log.info("ML model loaded — filter active (threshold=%.0f%%)", self.threshold * 100)
            except Exception as exc:
                log.warning("Could not load ML model (%s) — pass-through mode.", exc)
        else:
            log.debug("No ML model at %s — running in pass-through mode.", path.name)

    def is_ready(self) -> bool:
        """True when a model is loaded and active."""
        return self.model is not None

    # ── Inference ─────────────────────────────────────────────────────────────
    def predict(self, features: dict, signal_type: str) -> float:
        """
        Return a WIN-probability score in [0.0 – 1.0].

        Returns -1.0 when no model is loaded (pass-through sentinel).

        Parameters
        ----------
        features    : dict from ``signal_logger.extract_features``
        signal_type : "BUY" or "SELL"
        """
        if not self.is_ready():
            return -1.0

        row = {col: features.get(col, np.nan) for col in FEATURE_COLS}
        row["is_buy"] = 1.0 if signal_type == "BUY" else 0.0

        X = pd.DataFrame([row])

        # Fill any NaN with column medians stored at training time
        if hasattr(self.model, "_feature_medians"):
            for col in FEATURE_COLS:
                if np.isnan(X[col].iloc[0]):
                    X[col] = self.model._feature_medians.get(col, 0.0)

        try:
            prob = self.model.predict_proba(X)[0][1]
            return float(prob)
        except Exception as exc:
            log.error("ML predict error: %s", exc)
            return -1.0

    def should_fire(self, features: dict, signal_type: str) -> tuple[bool, float]:
        """
        Decide whether to send the Telegram alert.

        Returns
        -------
        (fire: bool, confidence: float)
            fire       — True if signal passes the threshold (or model absent)
            confidence — score in [0,1]; -1.0 means model not loaded
        """
        conf = self.predict(features, signal_type)
        if conf < 0:
            return True, conf          # no model → always fire
        return conf >= self.threshold, conf


# ---------------------------------------------------------------------------
# Training  (run from command line)
# ---------------------------------------------------------------------------

def _load_training_data(label_col: str) -> pd.DataFrame:
    if not DB_PATH.exists():
        print(f"❌ No signals database found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    df   = pd.read_sql(
        f"SELECT * FROM signals WHERE labeled=1 AND {label_col} IS NOT NULL",
        conn,
    )
    conn.close()
    return df


def train(label_col: str = "label_5", min_samples: int = 30) -> None:
    """Train XGBoost on labeled signals and save to MODEL_PATH."""
    try:
        import xgboost as xgb
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import classification_report, roc_auc_score
    except ImportError:
        print("❌ Missing dependencies.  Run: pip install xgboost scikit-learn")
        sys.exit(1)

    df = _load_training_data(label_col)

    if len(df) < min_samples:
        print(
            f"❌ Not enough labeled signals: {len(df)} "
            f"(need at least {min_samples}).\n"
            f"   Keep the bot running for a few more days then try again."
        )
        return

    print(f"✅ Training on {len(df)} labeled signals (label = {label_col})")
    print(f"   Win rate: {df[label_col].mean()*100:.1f}%")

    # Add is_buy feature
    df["is_buy"] = (df["signal_type"] == "BUY").astype(float)

    X = df[FEATURE_COLS].copy()
    y = df[label_col].astype(int)

    # Store column medians for imputation at inference time
    medians = X.median().to_dict()
    X = X.fillna(X.median())

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y,
    )

    model = xgb.XGBClassifier(
        n_estimators=150,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        use_label_encoder=False,
        eval_metric="logloss",
        random_state=42,
    )
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )

    # Attach medians so they travel with the model pickle
    model._feature_medians = medians

    y_pred  = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    print("\n📊 Classification Report (test set):")
    print(classification_report(y_test, y_pred, target_names=["LOSS", "WIN"]))

    try:
        auc = roc_auc_score(y_test, y_proba)
        print(f"   ROC-AUC : {auc:.3f}")
    except Exception:
        pass

    # Feature importance
    importances = pd.Series(model.feature_importances_, index=FEATURE_COLS)
    print("\n🔍 Feature Importances (top 10):")
    for feat, imp in importances.sort_values(ascending=False).head(10).items():
        bar = "█" * int(imp * 40)
        print(f"   {feat:<22} {bar}  {imp:.3f}")

    with open(MODEL_PATH, "wb") as fh:
        pickle.dump(model, fh)

    print(f"\n✅ Model saved to {MODEL_PATH}")
    print(
        "\n🚀 Next step: set  ml.enabled: true  in config.yml to activate the filter."
    )


def report() -> None:
    """Print a summary of the signals database."""
    if not DB_PATH.exists():
        print("No signals database yet. Run the bot for a few days first.")
        return

    conn = sqlite3.connect(str(DB_PATH))

    total   = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    labeled = conn.execute("SELECT COUNT(*) FROM signals WHERE labeled=1").fetchone()[0]

    print(f"\n{'─'*50}")
    print(f"  Signals DB : {DB_PATH}")
    print(f"{'─'*50}")
    print(f"  Total      : {total}")
    print(f"  Labeled    : {labeled}  ({100*labeled/total:.0f}%)" if total else "")

    if labeled:
        for col in ("label_5", "label_10"):
            wins = conn.execute(f"SELECT COUNT(*) FROM signals WHERE {col}=1").fetchone()[0]
            n    = conn.execute(f"SELECT COUNT(*) FROM signals WHERE {col} IS NOT NULL").fetchone()[0]
            if n:
                print(f"  Win ({col})  : {wins}/{n}  ({100*wins/n:.1f}%)")

    # Model presence
    if MODEL_PATH.exists():
        print(f"\n  ✅ Trained model found : {MODEL_PATH.name}")
    else:
        print(f"\n  ⚠  No trained model yet. Run: python ml_filter.py --train")

    print(f"{'─'*50}\n")
    conn.close()


def feature_importance_plot() -> None:
    """Plot feature importances from the saved model."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("pip install matplotlib")
        return

    if not MODEL_PATH.exists():
        print("No trained model found.")
        return

    with open(MODEL_PATH, "rb") as fh:
        model = pickle.load(fh)

    importances = pd.Series(model.feature_importances_, index=FEATURE_COLS)
    importances = importances.sort_values(ascending=True)

    fig, ax = plt.subplots(figsize=(8, 5))
    importances.plot.barh(ax=ax, color="#4C9BE8")
    ax.set_title("XGBoost Feature Importances — UT Bot ML Filter")
    ax.set_xlabel("Importance")
    plt.tight_layout()
    plt.show()


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="ML Filter — UT Bot Antigravity")
    parser.add_argument("--train",       action="store_true", help="Train model on labeled signals")
    parser.add_argument("--report",      action="store_true", help="Show DB / model summary")
    parser.add_argument("--importance",  action="store_true", help="Plot feature importances")
    parser.add_argument("--label",       default="label_5",   help="label_5 or label_10")
    parser.add_argument("--min-samples", type=int, default=30, help="Min labeled signals to train")
    args = parser.parse_args()

    if args.train:
        train(label_col=args.label, min_samples=args.min_samples)
    elif args.report:
        report()
    elif args.importance:
        feature_importance_plot()
    else:
        parser.print_help()
