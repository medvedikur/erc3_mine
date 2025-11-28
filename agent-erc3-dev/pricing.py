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
                print("✅ Done.")
            else:
                print(f"❌ Failed (Status {response.status_code})")
        except Exception as e:
            print(f"❌ Error: {e}")

    def calculate_cost(self, model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Считает стоимость для конкретного вызова. Возвращает float ($)."""
        if not self.is_loaded:
            return 0.0

        # Пытаемся найти модель. OpenRouter чувствителен к названиям (openai/gpt-4o)
        # Если точного совпадения нет, пробуем искать суффикс (например gpt-4o найдет openai/gpt-4o)
        price_data = self.prices.get(model_id)
        
        if not price_data:
            # Поиск по частичному совпадению, если ID не точный
            for key, val in self.prices.items():
                if key.endswith(model_id):
                    price_data = val
                    break
        
        if not price_data:
            return 0.0

        cost = (Decimal(prompt_tokens) * price_data["prompt"]) + \
               (Decimal(completion_tokens) * price_data["completion"])
        
        return float(cost)

# Создаем глобальный инстанс, чтобы не грузить цены каждый раз
calculator = CostCalculator()

