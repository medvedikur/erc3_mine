import requests
import json
from decimal import Decimal, getcontext

# Устанавливаем точность для финансовых расчетов
getcontext().prec = 10

class CostCalculator:
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1/models"

    def __init__(self):
        self.prices = {}
        self.is_loaded = False
        self._load_prices()

    def _load_prices(self):
        """Загружает цены с OpenRouter API"""
        print("⏳ Fetching model prices from OpenRouter...", end=" ", flush=True)
        try:
            response = requests.get(self.OPENROUTER_API_URL, timeout=10)
            if response.status_code == 200:
                data = response.json().get("data", [])
                for model in data:
                    model_id = model.get("id")
                    pricing = model.get("pricing", {})
                    
                    # Цены в API указаны за 1 токен (строкой или числом)
                    p_prompt = Decimal(str(pricing.get("prompt", "0")))
                    p_completion = Decimal(str(pricing.get("completion", "0")))
                    
                    self.prices[model_id] = {
                        "prompt": p_prompt,
                        "completion": p_completion
                    }
                self.is_loaded = True
                print(f"✅ Done. Loaded {len(self.prices)} models.")
            else:
                print(f"❌ Failed (Status {response.status_code}). Cost will be $0.")
        except Exception as e:
            print(f"❌ Error: {e}. Cost will be $0.")

    def calculate_cost(self, model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Считает стоимость для конкретного вызова. Возвращает float ($)."""
        if not self.is_loaded:
            return 0.0

        price_data = self._find_model_price(model_id)
        
        if not price_data:
            return 0.0

        cost = (Decimal(prompt_tokens) * price_data["prompt"]) + \
               (Decimal(completion_tokens) * price_data["completion"])
        
        return float(cost)
    
    def _find_model_price(self, model_id: str) -> dict | None:
        """Ищет цену модели с fuzzy matching"""
        # 1. Точное совпадение
        if model_id in self.prices:
            return self.prices[model_id]
        
        # Нормализуем для поиска (lowercase, без лишних символов)
        model_lower = model_id.lower()
        
        # 2. Поиск по точному совпадению (case-insensitive)
        for key, val in self.prices.items():
            if key.lower() == model_lower:
                return val
        
        # 3. Поиск по суффиксу (например "gpt-4o" найдёт "openai/gpt-4o")
        for key, val in self.prices.items():
            if key.lower().endswith(model_lower):
                return val
        
        # 4. Поиск по ключевым словам (для Qwen моделей)
        # qwen/qwen3-235b-a22b-instruct -> ищем qwen и 235b
        keywords = self._extract_keywords(model_lower)
        if keywords:
            best_match = None
            best_score = 0
            for key, val in self.prices.items():
                key_lower = key.lower()
                score = sum(1 for kw in keywords if kw in key_lower)
                if score > best_score:
                    best_score = score
                    best_match = val
            if best_match and best_score >= 2:  # минимум 2 совпадения
                return best_match
        
        return None
    
    def _extract_keywords(self, model_id: str) -> list:
        """Извлекает ключевые слова из model_id для fuzzy matching"""
        # Разбиваем по разделителям
        import re
        parts = re.split(r'[-_/]', model_id)
        # Фильтруем короткие и общие слова
        keywords = [p for p in parts if len(p) >= 3 and p not in ('instruct', 'chat', 'model')]
        return keywords
    
    def find_model_id(self, search: str) -> str | None:
        """Ищет полный model_id по частичному совпадению (для отладки)"""
        search_lower = search.lower()
        for key in self.prices.keys():
            if search_lower in key.lower():
                return key
        return None
    
    def list_models_by_prefix(self, prefix: str) -> list:
        """Возвращает список моделей, начинающихся с префикса (для отладки)"""
        prefix_lower = prefix.lower()
        return sorted([key for key in self.prices.keys() if prefix_lower in key.lower()])

# Создаем глобальный инстанс, чтобы не грузить цены каждый раз
calculator = CostCalculator()