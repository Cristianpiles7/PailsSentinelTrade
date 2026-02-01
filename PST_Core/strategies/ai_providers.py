import logging
import json
import asyncio
from abc import ABC, abstractmethod

logger = logging.getLogger("PST-AI-Providers")

class AIProvider(ABC):
    @abstractmethod
    async def generate_content(self, prompt: str) -> str:
        """Genera contenido basado en el prompt dado."""
        pass

class GeminiProvider(AIProvider):
    def __init__(self, api_key: str):
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-flash-latest')

    async def generate_content(self, prompt: str) -> str:
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: self.model.generate_content(prompt))
            return response.text
        except Exception as e:
            if "429" in str(e) or "quota" in str(e).lower():
                logger.warning("⚠️ Gemini: Cupo agotado (429 Quota Exceeded). Cambia a Ollama o Groq en el Dashboard.")
            else:
                logger.error(f"❌ Error en GeminiProvider: {e}")
            raise

class GroqProvider(AIProvider):
    def __init__(self, api_key: str):
        from groq import AsyncGroq
        self.client = AsyncGroq(api_key=api_key)
        self.model = "llama-3.1-8b-instant" # Modelo rápido (14.4K RPD) para alta frecuencia

    async def generate_content(self, prompt: str) -> str:
        try:
            completion = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1, # Muy bajo para análisis técnico preciso
            )
            return completion.choices[0].message.content
        except Exception as e:
            logger.error(f"❌ Error en GroqProvider: {e}")
            raise

class OllamaProvider(AIProvider):
    def __init__(self, model_name: str = "llama3"):
        import ollama
        self.client = ollama.AsyncClient()
        self.model = model_name

    async def generate_content(self, prompt: str) -> str:
        try:
            response = await self.client.generate(
                model=self.model,
                prompt=prompt,
                options={"temperature": 0.1}
            )
            return response['response']
        except Exception as e:
            logger.error(f"❌ Error en OllamaProvider: {e}. ¿Está Ollama corriendo?")
            raise

class AIProviderFactory:
    @staticmethod
    def get_provider(provider_type: str, api_key: str = None) -> AIProvider:
        if provider_type.lower() == "gemini":
            return GeminiProvider(api_key)
        elif provider_type.lower() == "groq":
            return GroqProvider(api_key)
        elif provider_type.lower() == "ollama":
            return OllamaProvider()
        else:
            raise ValueError(f"Proveedor no soportado: {provider_type}")
