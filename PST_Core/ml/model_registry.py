"""
ModelRegistry — guardado/carga de modelos LightGBM versionados por (estrategia,
clase de activo). Carga perezosa: cada instancia de estrategia carga el modelo la
primera vez que se llama calculate_signal() (el símbolo, y por tanto el grupo, no
se conoce hasta esa llamada) y lo cachea para el resto de su vida. Si no hay modelo
entrenado todavía para ese grupo, la estrategia cae automáticamente a scoring
solo-por-reglas — así el sistema es usable ANTES de que exista ningún modelo.

Layout en disco (PST_Core/ml/artifacts/, gitignored):
    <STRATEGY_NAME>/<grupo>/model_v<timestamp>.txt      (LightGBM Booster nativo)
    <STRATEGY_NAME>/<grupo>/metadata_v<timestamp>.json  (feature_names + métricas)
    <STRATEGY_NAME>/<grupo>/latest.txt                  (puntero de versión — sin symlinks en Windows)
"""
import os
import json
from datetime import datetime

ARTIFACTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")


class ModelRegistry:
    @staticmethod
    def _group_dir(strategy_name: str, group: str) -> str:
        return os.path.join(ARTIFACTS_DIR, strategy_name, group)

    @staticmethod
    def save(strategy_name: str, group: str, booster, feature_names: list, metrics: dict) -> str:
        """booster: lightgbm.Booster (API nativa, resultado de lgb.train()).
        Devuelve la ruta del modelo guardado."""
        d = ModelRegistry._group_dir(strategy_name, group)
        os.makedirs(d, exist_ok=True)
        version = datetime.now().strftime("%Y%m%d_%H%M%S")
        model_path = os.path.join(d, f"model_v{version}.txt")
        meta_path = os.path.join(d, f"metadata_v{version}.json")

        booster.save_model(model_path)
        meta = {
            "strategy": strategy_name, "group": group, "version": version,
            "feature_names": feature_names, "metrics": metrics, "created": str(datetime.now()),
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        with open(os.path.join(d, "latest.txt"), "w", encoding="utf-8") as f:
            f.write(version)
        return model_path

    @staticmethod
    def load_latest(strategy_name: str, group: str):
        """Devuelve (booster, feature_names, metadata). (None, None, None) si no hay
        modelo entrenado todavía para ese grupo — el caller debe caer a solo-reglas."""
        d = ModelRegistry._group_dir(strategy_name, group)
        latest_path = os.path.join(d, "latest.txt")
        if not os.path.exists(latest_path):
            return None, None, None
        try:
            with open(latest_path, encoding="utf-8") as f:
                version = f.read().strip()
            model_path = os.path.join(d, f"model_v{version}.txt")
            meta_path = os.path.join(d, f"metadata_v{version}.json")
            if not (os.path.exists(model_path) and os.path.exists(meta_path)):
                return None, None, None
            import lightgbm as lgb
            booster = lgb.Booster(model_file=model_path)
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            return booster, meta.get("feature_names"), meta
        except Exception:
            return None, None, None
