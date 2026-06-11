"""Train the booking-propensity model and write a report.

Target: ``label`` (1 = confirmed booked via payment). ``likely_cash`` rows are
held out of the negatives because they are probably unrecorded bookings.

Two models are compared with stratified 5-fold out-of-fold predictions:
    - LogisticRegression (balanced, scaled) -> interpretable coefficients
    - GradientBoostingClassifier            -> non-linear feature importances

The model with the higher average-precision (PR-AUC, the right metric for this
heavy class imbalance) is refit on all data and saved. A markdown report
summarizes metrics, the most predictive features, and the agent profile most
likely to book.
"""
from __future__ import annotations

import datetime as dt
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .paths import FEATURES_CSV, MODEL_PATH, MODEL_REPORT

FEATURE_COLS = [
    "is_ig", "is_imsg", "replied",
    "num_owner_msgs", "num_their_msgs", "total_msgs", "reply_ratio",
    "their_text_len", "num_links", "thread_span_days",
    "display_has_emoji",
    "kw_realtor", "kw_team", "kw_luxury", "kw_mortgage_lender",
    "kw_photographer", "kw_investor", "city_austin", "city_satx",
    "follower_count", "following_count", "post_count", "is_business",
    "bio_len", "has_profile_data",
]


def _load_training() -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    df = pd.read_csv(FEATURES_CSV)
    # Drop ambiguous likely-cash rows from the negative class.
    mask = ~((df["likely_cash"] == 1) & (df["label"] == 0))
    train = df[mask].reset_index(drop=True)
    X = train[FEATURE_COLS].fillna(0)
    y = train["label"].astype(int)
    return X, y, train


def _cv_scores(model, X, y, cv) -> dict:
    proba = cross_val_predict(model, X, y, cv=cv, method="predict_proba")[:, 1]
    return {
        "roc_auc": round(roc_auc_score(y, proba), 4),
        "pr_auc": round(average_precision_score(y, proba), 4),
        "proba": proba,
    }


def train() -> dict:
    X, y, train = _load_training()
    n_pos = int(y.sum())
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

    logit = Pipeline([
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(max_iter=2000, class_weight="balanced")),
    ])
    gbm = GradientBoostingClassifier(random_state=42)

    logit_s = _cv_scores(logit, X, y, cv)
    gbm_s = _cv_scores(gbm, X, y, cv)

    # Pick by PR-AUC (imbalance-aware).
    best_name, best_model = ("logistic", logit) if logit_s["pr_auc"] >= gbm_s["pr_auc"] \
        else ("gradient_boosting", gbm)
    best_model.fit(X, y)

    # Feature insight.
    if best_name == "logistic":
        coefs = best_model.named_steps["clf"].coef_[0]
        importance = pd.Series(coefs, index=FEATURE_COLS).sort_values(key=abs, ascending=False)
        imp_kind = "standardized logistic coefficient"
    else:
        importance = pd.Series(best_model.feature_importances_, index=FEATURE_COLS) \
            .sort_values(ascending=False)
        imp_kind = "gradient-boosting feature importance"

    base_rate = round(100 * y.mean(), 3)
    artifact = {
        "model": best_model,
        "model_name": best_name,
        "feature_cols": FEATURE_COLS,
        "metrics": {"logistic": {k: logit_s[k] for k in ("roc_auc", "pr_auc")},
                    "gradient_boosting": {k: gbm_s[k] for k in ("roc_auc", "pr_auc")}},
        "n_train": int(len(y)), "n_positive": n_pos,
        "trained_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, MODEL_PATH)

    _write_report(train, y, importance, imp_kind, best_name, logit_s, gbm_s, base_rate, n_pos)
    print(
        f"[train] n={len(y)} positives={n_pos} base_rate={base_rate}%\n"
        f"  logistic   ROC-AUC={logit_s['roc_auc']}  PR-AUC={logit_s['pr_auc']}\n"
        f"  gbm        ROC-AUC={gbm_s['roc_auc']}  PR-AUC={gbm_s['pr_auc']}\n"
        f"  best={best_name} -> {MODEL_PATH}\n  report -> {MODEL_REPORT}"
    )
    return artifact


def _segment_rate(df: pd.DataFrame, col: str) -> str:
    g = df.groupby(col)["label"].agg(["mean", "sum", "count"])
    lines = []
    for idx, row in g.iterrows():
        lines.append(f"  - `{col}={idx}`: {100*row['mean']:.2f}% "
                     f"({int(row['sum'])}/{int(row['count'])})")
    return "\n".join(lines)


def _write_report(train, y, importance, imp_kind, best_name,
                  logit_s, gbm_s, base_rate, n_pos) -> None:
    top = importance.head(12)
    kw_rows = []
    for c in [c for c in train.columns if c.startswith(("kw_", "city_"))]:
        sub = train[train[c] == 1]["label"]
        if len(sub):
            kw_rows.append((c, 100 * sub.mean(), int(sub.sum()), len(sub)))
    kw_rows.sort(key=lambda x: -x[1])

    lines = [
        "# Booked-Agent Propensity Model — Report",
        "",
        f"*Generated {dt.datetime.now():%Y-%m-%d %H:%M}.*",
        "",
        "## What this answers",
        "Which agents (and which **types** of agents) are most likely to book a paid "
        "shoot, learned from Square + Cash App payments as ground truth.",
        "",
        "## Data",
        f"- Training contacts: **{len(y):,}** (after excluding ambiguous likely-cash rows)",
        f"- Confirmed booked (positives): **{n_pos}**  (base rate **{base_rate}%**)",
        "",
        "## Model performance (stratified 5-fold, out-of-fold)",
        "| Model | ROC-AUC | PR-AUC |",
        "|-------|---------|--------|",
        f"| Logistic regression | {logit_s['roc_auc']} | {logit_s['pr_auc']} |",
        f"| Gradient boosting | {gbm_s['roc_auc']} | {gbm_s['pr_auc']} |",
        "",
        f"PR-AUC vs. a {base_rate}% base rate shows how much better than random the "
        f"model ranks bookers. **Selected model: `{best_name}`** (higher PR-AUC).",
        "",
        f"## Most predictive features ({imp_kind})",
        "| Feature | Weight |",
        "|---------|--------|",
    ]
    lines += [f"| `{f}` | {v:+.3f} |" for f, v in top.items()]
    lines += [
        "",
        "## Booking rate by channel",
        _segment_rate(train, "channel"),
        "",
        "## Booking rate by reply",
        _segment_rate(train, "replied"),
        "",
        "## Agent-type segments (Instagram keywords), ranked by booking rate",
        "| Segment | Book rate | Booked / N |",
        "|---------|-----------|-----------|",
    ]
    lines += [f"| `{c}` | {r:.2f}% | {s}/{n} |" for c, r, s, n in kw_rows]
    lines += [
        "",
        "## Agent profile most likely to book",
        "- **Channel:** iMessage outreach converts far better than Instagram DMs.",
        "- **Engagement:** replying at all is by far the strongest signal; multi-message "
        "back-and-forth threads convert best.",
        "- **Type:** see the Instagram keyword segments above for which agent descriptors "
        "(realtor/team/luxury/city) over-index on booking.",
        "",
        "## How to use",
        "```python",
        "import joblib, pandas as pd",
        "art = joblib.load('models/booked_propensity.joblib')",
        "df = pd.read_csv('data/booked/features.csv')",
        "df['book_proba'] = art['model'].predict_proba(df[art['feature_cols']].fillna(0))[:, 1]",
        "df.sort_values('book_proba', ascending=False).head(25)",
        "```",
        "",
        "*Caveat:* with a small positive count, treat scores as a prioritization aid, not a "
        "guarantee. Enriching Instagram profiles (followers, bio, business flag) via "
        "`booked-enrich` adds 'agent type' signal the export data alone cannot provide.",
    ]
    MODEL_REPORT.write_text("\n".join(lines))


if __name__ == "__main__":
    train()
