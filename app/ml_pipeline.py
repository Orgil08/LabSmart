"""Cutoff-safe demand forecasting batch for LabSmart.

The batch compares a transparent rule baseline with Linear Regression and
Random Forest when enough historical demand data exists. Synthetic demo data
is supported for pipeline validation only and is never presented as proof of
real-world accuracy.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime | None = None) -> str:
    dt = dt or utc_now()
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _metrics(y_true, y_pred):
    if not y_true:
        return None, None, None
    errs = [float(p) - float(y) for y, p in zip(y_true, y_pred)]
    mae = sum(abs(e) for e in errs) / len(errs)
    rmse = math.sqrt(sum(e * e for e in errs) / len(errs))
    bias = sum(errs) / len(errs)
    return round(mae, 4), round(rmse, 4), round(bias, 4)


def _rule_predict_row(row):
    teams = int(row.get("planned_teams") or 0)
    if teams <= 0:
        teams = math.ceil(int(row.get("planned_students") or 0) / 3)
    units = max(1, int(row.get("units_per_team") or 1))
    shared = max(0, int(row.get("shared_units") or 0))
    return max(0, teams * units + shared)


def _features(rows):
    import pandas as pd
    frame = pd.DataFrame([dict(r) if not isinstance(r, dict) else r for r in rows])
    frame["session_date"] = pd.to_datetime(frame["session_date"], utc=True)
    frame["day_of_week"] = frame["session_date"].dt.dayofweek
    frame["course"] = frame["course"].fillna("Тодорхойгүй")
    frame["equipment_name"] = frame["equipment_name"].fillna("Тодорхойгүй")
    feature_cols = [
        "course", "equipment_name", "semester_week", "planned_students",
        "planned_teams", "units_per_team", "shared_units", "day_of_week",
        "lag_demand", "rolling_mean_3",
    ]
    return frame, feature_cols


def _add_history_features(rows):
    by_type = {}
    enriched = []
    for r in rows:
        name = r["equipment_name"]
        hist = by_type.setdefault(name, [])
        lag = int(hist[-1]["requested_qty"]) if hist else 0
        roll = sum(int(x["requested_qty"]) for x in hist[-3:]) / min(3, len(hist)) if hist else 0.0
        enriched.append({**dict(r), "lag_demand": lag, "rolling_mean_3": roll})
        hist.append(r)
    return enriched


def _rule_prediction_for_future(session, name, history):
    matching = [dict(r) for r in history if r["equipment_name"] == name]
    template = matching[-1] if matching else {
        "planned_teams": session["planned_teams"],
        "planned_students": session["planned_students"],
        "units_per_team": 1,
        "shared_units": 0,
    }
    template = {**template, "planned_teams": session["planned_teams"], "planned_students": session["planned_students"]}
    return float(_rule_predict_row(template))


def run_batch(db_path: str | Path):
    db_path = Path(db_path)
    db = sqlite3.connect(db_path, timeout=20)
    db.row_factory = sqlite3.Row
    try:
        now = utc_now()
        cutoff = iso_utc(now)
        hist = db.execute("SELECT * FROM demand_history ORDER BY session_date,id").fetchall()
        upcoming = db.execute("SELECT * FROM lab_sessions WHERE start_at>=? ORDER BY start_at", (cutoff,)).fetchall()
        horizon_row = db.execute("SELECT value FROM app_settings WHERE key='prediction_horizon_days'").fetchone()
        horizon_days = int(horizon_row[0]) if horizon_row else 7
        data_version = f"demand-{now.strftime('%Y%m%d%H%M%S')}"

        enriched = _add_history_features(hist)
        equipment_types = sorted({r["equipment_name"] for r in enriched})
        if not equipment_types:
            equipment_types = sorted({r["name"] for r in db.execute("SELECT DISTINCT name FROM equipment WHERE status <> 'retired'")})

        if not upcoming:
            return {"model_name": "Rule fallback", "predictions": 0, "note": "Ирээдүйн session алга."}

        metrics = {}
        selected_name = "Rule baseline"
        selected_model = None
        selected_feature_cols = None
        selection_note = "Түүх хүрэлцээгүй тул дүрмийн суурь тооцоо ашигласан."
        split = None

        if len(enriched) >= 18:
            try:
                import pandas as pd
                from sklearn.compose import ColumnTransformer
                from sklearn.ensemble import RandomForestRegressor
                from sklearn.impute import SimpleImputer
                from sklearn.linear_model import LinearRegression
                from sklearn.pipeline import Pipeline
                from sklearn.preprocessing import OneHotEncoder

                frame, feature_cols = _features(enriched)
                split = max(12, int(len(frame) * 0.8))
                if split >= len(frame):
                    split = len(frame) - 1
                train = frame.iloc[:split]
                test = frame.iloc[split:]
                cat = ["course", "equipment_name"]
                num = [c for c in feature_cols if c not in cat]
                prep = ColumnTransformer([
                    ("cat", Pipeline([
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]), cat),
                    ("num", Pipeline([
                        ("imputer", SimpleImputer(strategy="median")),
                    ]), num),
                ])
                models = {
                    "Linear Regression": Pipeline([
                        ("prep", prep),
                        ("model", LinearRegression()),
                    ]),
                    "Random Forest Regressor": Pipeline([
                        ("prep", prep),
                        ("model", RandomForestRegressor(
                            n_estimators=180,
                            random_state=42,
                            min_samples_leaf=1,
                        )),
                    ]),
                }
                y = test["requested_qty"].astype(float).tolist()
                rule_pred = [_rule_predict_row(dict(r)) for r in test.to_dict("records")]
                metrics["Rule baseline"] = _metrics(y, rule_pred)

                # Validation for model choice, then final test metric for reporting.
                val_split = max(8, int(len(train) * 0.8))
                if val_split >= len(train):
                    val_split = len(train) - 1
                val_train = train.iloc[:val_split]
                val = train.iloc[val_split:]
                candidates = []
                for name, model in models.items():
                    model.fit(val_train[feature_cols], val_train["requested_qty"].astype(float))
                    val_pred = model.predict(val[feature_cols]).tolist()
                    v_mae, _, _ = _metrics(val["requested_qty"].astype(float).tolist(), val_pred)
                    candidates.append((v_mae if v_mae is not None else float("inf"), name))

                candidates.sort(key=lambda x: (x[0], 0 if x[1] == "Random Forest Regressor" else 1))
                selected_name = candidates[0][1]
                # Fit both candidate models on the complete training window and
                # evaluate each once on the final chronological test window.
                for name, model in models.items():
                    model.fit(train[feature_cols], train["requested_qty"].astype(float))
                    test_pred = model.predict(test[feature_cols]).tolist()
                    metrics[name] = _metrics(y, test_pred)
                selected_model = models[selected_name]
                selection_note = (
                    f"Дүрмийн baseline, Linear Regression, Random Forest Regressor-ийг хугацааны дарааллаар харьцуулж, "
                    f"validation дээр {selected_name}-ийг сонгосон. Сүүлийн {len(test)} мөрийг эцсийн шалгалтад ашиглав."
                )
                selected_feature_cols = feature_cols
            except Exception as exc:
                selection_note = f"ML загварууд ажиллах боломжгүй тул дүрмийн fallback ашигласан: {str(exc)[:160]}"
                selected_name = "Rule baseline"
                selected_model = None
                selected_feature_cols = None

        # Persist run records separately for transparent comparison.
        train_rows = split or 0
        test_rows = len(enriched) - split if split else 0
        for model_name, (mae, rmse, bias) in metrics.items():
            note = selection_note if model_name == selected_name else "Туршилтын харьцуулалтын үзүүлэлт; test set-ийг нэг удаа ашиглав."
            db.execute(
                "INSERT INTO model_runs(generated_at,forecast_cutoff,data_version,model_name,train_rows,test_rows,mae,rmse,bias,status,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (cutoff, cutoff, data_version, model_name, train_rows, test_rows, mae, rmse, bias, "success", note),
            )

        selected_metrics = metrics.get(selected_name)
        selected_mae = selected_metrics[0] if selected_metrics else None
        error_band = max(1, round(selected_mae if selected_mae is not None else 1.0))
        inserted = 0

        for session in upcoming:
            start = datetime.fromisoformat(session["start_at"].replace("Z", "+00:00"))
            days_until = (start - now).total_seconds() / 86400
            if days_until > horizon_days:
                continue
            for name in equipment_types:
                matching_hist = [dict(r) for r in enriched if r["equipment_name"] == name]
                last = matching_hist[-1] if matching_hist else None
                lag = int(last["requested_qty"]) if last else 0
                roll = sum(int(x["requested_qty"]) for x in matching_hist[-3:]) / min(3, len(matching_hist)) if matching_hist else 0.0
                row = {
                    "course": session["course"],
                    "equipment_name": name,
                    "semester_week": int(session["semester_week"] or 1),
                    "planned_students": int(session["planned_students"] or 0),
                    "planned_teams": int(session["planned_teams"] or 0),
                    "units_per_team": int(last["units_per_team"] if last else 1),
                    "shared_units": int(last["shared_units"] if last else 0),
                    "day_of_week": start.weekday(),
                    "lag_demand": lag,
                    "rolling_mean_3": roll,
                }
                if selected_model is not None and selected_feature_cols:
                    input_frame = pd.DataFrame([row])
                    raw = float(selected_model.predict(input_frame[selected_feature_cols])[0])
                    method = selected_name
                    version = "lr-rf-v1"
                else:
                    raw = _rule_prediction_for_future(session, name, hist)
                    method = "rule_fallback"
                    version = "rule-v3"
                processed = max(0, math.ceil(raw))
                low = max(0, processed - error_band)
                high = processed + error_band

                cur = db.execute(
                    "INSERT INTO predictions(session_id,equipment_name,raw_prediction,processed_qty,range_low,range_high,generated_at,forecast_cutoff,horizon_days,method,model_version,data_version,data_points) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (session["id"], name, raw, processed, low, high, cutoff, cutoff, horizon_days, method, version, data_version, len(matching_hist)),
                )
                pid = cur.lastrowid
                available = db.execute("SELECT COUNT(*) FROM equipment WHERE name=? AND status='available'", (name,)).fetchone()[0]
                shortage = max(0, processed - available)
                priority = "critical" if shortage >= 3 else "high" if shortage > 0 else "low"
                if shortage > 0:
                    suggestion = f"Нэмэлт {shortage}–{max(shortage, shortage + 2)} ширхэг бэлдэх эсвэл өөр ээлжийн сул нөөцийг шалгаарай."
                    explanation = f"Ирэх session-д {processed} ширхэг хэрэг болох төлөвтэй, тухайн интервалын бэлэн нөөц {available} байна."
                else:
                    suggestion = "Одоогийн нөөц хангалттай байна. Хуваарь ойртоход дахин шалгаарай."
                    explanation = f"Ирэх session-д {processed} ширхэг хэрэг болох төлөвтэй, бэлэн нөөц {available} байна."
                db.execute(
                    "INSERT INTO recommendations(prediction_id,mandatory_qty,allocation_qty,available_interval,shortage_qty,priority,rule_version,explanation,suggestion,decision_status) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (pid, 0, processed, available, shortage, priority, "interval-v3", explanation, suggestion, "open"),
                )
                inserted += 1

        db.commit()
        selected_metrics = metrics.get(selected_name, (None, None, None))
        return {
            "model_name": selected_name,
            "predictions": inserted,
            "mae": selected_metrics[0],
            "rmse": selected_metrics[1],
            "bias": selected_metrics[2],
            "data_version": data_version,
            "note": selection_note,
            "models_compared": list(metrics.keys()) or ["Rule baseline"],
        }
    finally:
        db.close()
