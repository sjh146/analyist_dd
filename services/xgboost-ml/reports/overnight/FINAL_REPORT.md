# Overnight ML Experiment Loop — Final Report

Generated: 2026-09-23T15:42:43+00:00 (UTC)

## Experiment results

| Exp | Config | Seed AUCs | Mean | Std | OOS |
|-----|--------|-----------|------|-----|-----|
| E1 | curated43 lr=0.03 depth=4 est=1500 oversample on | 0:0.5146294649126913, 1:0.5065920368896587, 2:0.5188456767005291, 3:0.5302274004026029, 4:0.5205456205233837 | 0.5182 | 0.0086 | - |
| E2 | curated43 lr=0.02 depth=3 est=2000 | 0:0.5287703056972988, 1:0.5087542718037545, 2:0.5235066242217125, 3:0.5176899489724264, 4:0.5239016197743551 | 0.5205 | 0.0077 | - |
| E3 | curated43 lr=0.05 depth=5 est=1000 | 0:0.5083036842844436, 1:0.5047955385983802, 2:0.5053777913019054, 3:0.5256659332428257, 4:0.5143515050793502 | 0.5117 | 0.0087 | - |
| E4 | curated43 lr=0.03 depth=4 est=1500 scale_pos_weight=1.0 | 0:0.5158934506811479, 1:0.509213637002013, 2:0.5158963765741305, 3:0.5329484808763635, 4:0.5117474603248912 | 0.5171 | 0.0093 | - |
| E5 | curated48 (sentiment/news on) lr=0.03 depth=4 est=1500 | 0:0.5023557312252964, 1:0.49402371541501977, 2:0.5213122529644268, 3:0.4957786561264822, 4:0.542513833992095 | 0.5112 | 0.0206 | - |
| E6 | 173-feature canonical via panel cache lr=0.03 depth=4 est=1500 | 0:0.5, 1:0.4943, 2:0.4928, 3:0.4961, 4:0.5013 | 0.4969 | 0.0036 | - |
| E7 | curated43 2-day-horizon labels lr=0.03 depth=4 est=1500 | 0:0.4953353028064993, 1:0.5140088626292467, 2:0.5292525849335303, 3:0.5267149187592319, 4:0.5285553914327917 | 0.5188 | 0.0145 | - |

## Best model
- Experiment: E2
- Out dir: /app/app/models/overnight/E2
- Mean AUC: 0.5205 ± 0.0077
- OOS AUC: None

## Gate verdict
- Gate passed: False

## Reproduction
```
docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 python -u scripts/overnight_ml_loop.py'
```

## Rollback
```
# no promotion performed; champion unchanged
```

## Limitations
- Single CPU box (7.9GB), OMP/MKL threads capped at 2; one experiment at a time.
- Gate is on multi-seed mean/std test AUC; single-seed maxima ignored.
- E6 (173-feature panel) and E7 (2-day horizon) are best-effort secondary configs.
- Backtest OOS uses a stratified 30/20 KOSPI/KOSDAQ universe (seed 42).
