"""
Mixin ligero compartido por las estrategias con modelo entrenable: carga perezosa
del modelo LightGBM (la primera vez que se conoce el símbolo — no antes, el
símbolo solo llega como kwarg de calculate_signal) y blend reglas+ML.

Si no hay modelo entrenado todavía para el grupo (clase de activo), cae a
solo-reglas de forma transparente y silenciosa — así el sistema es usable ANTES de
entrenar nada (Fase 2) y el entrenamiento (Fase 4) no rompe nada al añadirse encima.
"""
import logging

from .model_registry import ModelRegistry
from .feature_builder import FeatureBuilder
from ..utils.tech_utils import get_asset_class

logger = logging.getLogger("PST-MLBlend")

# Peso del modelo en el blend final: el modelo es la señal PRIMARIA del híbrido
# cuando existe (decisión del usuario: "híbrido reglas+ML, me mola que se pueda
# entrenar"), las reglas siguen aportando estabilidad e interpretabilidad.
ML_WEIGHT = 0.7

# Gate de calidad mínima: un modelo entrenado con muestra ínfima puede quedar
# guardado con un AUC de test peor que el azar (visto en la práctica: grupo INDEX de
# PST-DivergenceReversion, 30 ejemplos de test, AUC 0.054). Usarlo en vivo sería peor
# que no tener modelo. Por debajo de este umbral, se trata como "sin modelo" y cae a
# solo-reglas — no basta con que el archivo exista, tiene que superar esta barra.
MIN_TEST_SAMPLES = 20
MIN_AUC = 0.52


class MLBlendMixin:
    def _init_ml(self):
        self._model = None
        self._model_meta = None
        self._model_group = None
        self._model_load_attempted = False

    def _blend_with_model(self, rule_score: int, mtf_data: dict, active_tf: str, symbol: str):
        """Devuelve (score_final, factor_o_None). Nunca lanza — cualquier fallo en
        carga/inferencia cae a solo-reglas (rule_score sin modificar)."""
        try:
            group = get_asset_class(symbol) if symbol else "FOREX"
            if not self._model_load_attempted or group != self._model_group:
                model, _, meta = ModelRegistry.load_latest(self.STRATEGY_NAME, group)
                metrics = (meta or {}).get("metrics", {})
                passes_quality = (
                    model is not None
                    and metrics.get("n_test", 0) >= MIN_TEST_SAMPLES
                    and metrics.get("auc_test", 0) >= MIN_AUC
                )
                self._model = model if passes_quality else None
                if model is not None and not passes_quality:
                    logger.info(
                        f"Modelo {self.STRATEGY_NAME}/{group} descartado en el blend "
                        f"(n_test={metrics.get('n_test')}, auc_test={metrics.get('auc_test')} "
                        f"por debajo de n>={MIN_TEST_SAMPLES}/auc>={MIN_AUC}) — cae a solo-reglas."
                    )
                self._model_meta = meta
                self._model_group = group
                self._model_load_attempted = True

            if self._model is None:
                return rule_score, None

            feats = FeatureBuilder.build(mtf_data, active_tf=active_tf, symbol=symbol, htf_tf="h1")
            vec = FeatureBuilder.vector(feats)
            proba = float(self._model.predict([vec])[0])
            ml_score = round(proba * 100)
            blended = round(ML_WEIGHT * ml_score + (1 - ML_WEIGHT) * rule_score)
            version = (self._model_meta or {}).get("version", "?")
            factor = {"k": "ML Score", "v": f"proba {proba:.2f} (modelo {group} v{version})", "score": blended - rule_score}
            return max(0, min(100, blended)), factor
        except Exception as e:
            logger.debug(f"ML blend fallo, cayendo a solo-reglas: {e}")
            return rule_score, None
