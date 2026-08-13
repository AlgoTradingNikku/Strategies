Bot running → logs signals
       ↓
label_signals.py → grades each signal (WIN/LOSS) using broker data
       ↓
ml_filter.py --train → XGBoost learns from those graded signals
       ↓
config.yml: ml.enabled: true → live bot now uses the trained model to filter signals
