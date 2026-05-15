"""
ML Filter - Machine Learning based trade signal filtering.

Captures market context snapshots at signal time and learns to predict trade outcomes.
Uses a Random Forest classifier to provide a 'confidence score' for each signal.
"""

import sqlite3
import json
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path
import logging
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

class MLFilter:
    """
    ML-based signal filter and context recorder.
    
    Responsibilities:
    1. Capture 'snapshots' of technical indicators at signal time.
    2. Store snapshots and link them to trade outcomes (PnL).
    3. Train a lightweight model to identify high-probability setups.
    4. Provide a 'veto' prediction for new signals.
    """
    
    def __init__(self, config: dict, db_path: str = "ml_data.db"):
        self.config = config.get("ml_filter", {"enabled": False})
        self.db_path = Path(db_path)
        self._init_db()
        self.model = None
        self.is_trained = False
        
        # Load model if enabled and data exists
        if self.config.get("enabled", False):
            self._try_load_model()

    def _init_db(self):
        """Initialize the ML training database"""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                snapshot_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                features TEXT NOT NULL,
                outcome_pnl REAL,
                outcome_pct REAL,
                is_win INTEGER,
                meta TEXT
            )
        """)
        conn.commit()
        conn.close()

    def capture_snapshot(self, symbol: str, df_opt: pd.DataFrame, tech_meta: dict) -> str:
        """
        Capture current market state as a feature snapshot.
        
        Returns:
            snapshot_id: Unique ID for this snapshot
        """
        snapshot_id = f"snap_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        
        # 1. Feature Engineering (Simplified for Phase 2)
        features = {
            "rsi": float(tech_meta.get("rsi", 50)),
            "adx": float(tech_meta.get("adx", 0)),
            "atr_pct": float((tech_meta.get("atr", 0) / df_opt['Close'].iloc[-1]) * 100 if tech_meta.get("atr", 0) > 0 else 0),
            "hour": datetime.now().hour,
            "day_of_week": datetime.now().weekday(),
            "dist_to_vwap_pct": float(((df_opt['Close'].iloc[-1] - tech_meta.get("vwap", 0)) / tech_meta.get("vwap", 1)) * 100),
            "vol_ratio": float(tech_meta.get("volume", 0) / tech_meta.get("vol_ma_5", 1)),
            "candle_body_pct": float(abs(df_opt['Close'].iloc[-1] - df_opt['Open'].iloc[-1]) / (df_opt['High'].iloc[-1] - df_opt['Low'].iloc[-1] + 0.001))
        }
        
        # 2. Save to DB
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            INSERT INTO snapshots (snapshot_id, symbol, timestamp, features, meta)
            VALUES (?, ?, ?, ?, ?)
        """, (
            snapshot_id,
            symbol,
            datetime.now().isoformat(),
            json.dumps(features),
            json.dumps({"close": df_opt['Close'].iloc[-1]})
        ))
        conn.commit()
        conn.close()
        
        return snapshot_id

    def update_outcome(self, snapshot_id: str, pnl: float, pnl_pct: float):
        """Link trade result back to the snapshot"""
        if not snapshot_id: return
        
        is_win = 1 if pnl > 0 else 0
        
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            UPDATE snapshots 
            SET outcome_pnl = ?, outcome_pct = ?, is_win = ?
            WHERE snapshot_id = ?
        """, (pnl, pnl_pct, is_win, snapshot_id))
        conn.commit()
        conn.close()
        
        logger.info(f"ML Outcome updated for {snapshot_id}: Win={is_win} ({pnl_pct:.2f}%)")

    def predict(self, symbol: str, df_opt: pd.DataFrame, tech_meta: dict) -> Tuple[bool, float, str]:
        """
        Predict if the signal is likely to be a winner.
        
        Returns:
            (pass_filter, confidence, reason)
        """
        if not self.config.get("enabled", False):
            return True, 1.0, "ML Disabled"
        
        if not self.is_trained:
            # Check if we have enough data to train
            count = self._get_completed_count()
            threshold = self.config.get("min_training_samples", 50)
            if count < threshold:
                return True, 0.5, f"Collecting Data ({count}/{threshold})"
            else:
                self._train_model()
                if not self.is_trained:
                    return True, 0.5, "Training Failed"

        # Prepare features for prediction
        # (Same logic as capture_snapshot but doesn't save)
        features = {
            "rsi": float(tech_meta.get("rsi", 50)),
            "adx": float(tech_meta.get("adx", 0)),
            "atr_pct": float((tech_meta.get("atr", 0) / df_opt['Close'].iloc[-1]) * 100 if tech_meta.get("atr", 0) > 0 else 0),
            "hour": datetime.now().hour,
            "day_of_week": datetime.now().weekday(),
            "dist_to_vwap_pct": float(((df_opt['Close'].iloc[-1] - tech_meta.get("vwap", 0)) / tech_meta.get("vwap", 1)) * 100),
            "vol_ratio": float(tech_meta.get("volume", 0) / tech_meta.get("vol_ma_5", 1)),
            "candle_body_pct": float(abs(df_opt['Close'].iloc[-1] - df_opt['Open'].iloc[-1]) / (df_opt['High'].iloc[-1] - df_opt['Low'].iloc[-1] + 0.001))
        }
        
        # Use model to predict
        try:
            from sklearn.ensemble import RandomForestClassifier
            import joblib
            
            # Convert features to array
            feature_names = ["rsi", "adx", "atr_pct", "hour", "day_of_week", "dist_to_vwap_pct", "vol_ratio", "candle_body_pct"]
            X = np.array([[features[f] for f in feature_names]])
            
            # Predict probability
            probs = self.model.predict_proba(X)[0] # [P(0), P(1)]
            win_prob = probs[1]
            
            conf_threshold = self.config.get("confidence_threshold", 0.60)
            pass_filter = win_prob >= conf_threshold
            
            reason = f"ML Score: {win_prob*100:.1f}%"
            return pass_filter, float(win_prob), reason
            
        except ImportError:
            return True, 1.0, "Missing sklearn"
        except Exception as e:
            logger.error(f"ML Prediction Error: {e}")
            return True, 1.0, "Prediction Error"

    def _get_completed_count(self) -> int:
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.execute("SELECT COUNT(*) FROM snapshots WHERE outcome_pnl IS NOT NULL")
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def _train_model(self):
        """Train Random Forest model based on history"""
        logger.info("[ML] Starting model training...")
        try:
            from sklearn.ensemble import RandomForestClassifier
            import joblib
            
            conn = sqlite3.connect(str(self.db_path))
            df = pd.read_sql_query("SELECT features, is_win FROM snapshots WHERE outcome_pnl IS NOT NULL", conn)
            conn.close()
            
            if len(df) < self.config.get("min_training_samples", 50):
                return
            
            # Process features
            X_list = [json.loads(f) for f in df['features']]
            feature_names = ["rsi", "adx", "atr_pct", "hour", "day_of_week", "dist_to_vwap_pct", "vol_ratio", "candle_body_pct"]
            X = pd.DataFrame(X_list)[feature_names]
            y = df['is_win']
            
            # Train model
            clf = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
            clf.fit(X, y)
            
            # Save for persistence
            self.model = clf
            self.is_trained = True
            joblib.dump(clf, "ml_model.joblib")
            
            logger.info(f"[ML SUCCESS] Model trained on {len(df)} samples.")
            
        except Exception as e:
            logger.error(f"[ML ERROR] Training failed: {e}")

    def _try_load_model(self):
        """Try to load an existing model from disk"""
        try:
            import joblib
            if Path("ml_model.joblib").exists():
                self.model = joblib.load("ml_model.joblib")
                self.is_trained = True
                logger.info("[ML] Model loaded from disk.")
        except:
            pass
