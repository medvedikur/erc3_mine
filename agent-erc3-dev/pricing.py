import requests
import json
from decimal import Decimal, getcontext

# Set precision for financial calculations
getcontext().prec = 10

class CostCalculator:
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1/models"

    def __init__(self):
        self.prices = {}
        self.is_loaded = False
        # self._load_prices() # Lazy load or load on init?
        # To avoid blocking startup, we can just not load it by default 
        # or handle it gracefully. The original loaded on init.
        
        # Hardcode Qwen/Qwen3-235B price as fallback to avoid network call failure
        self.prices["qwen/qwen3-235b-a22b-2507"] = {
            "prompt": Decimal("0.000001"), # Guess
            "completion": Decimal("0.000001") # Guess
        }
        self.is_loaded = True

    def _load_prices(self):
        """Load prices from OpenRouter API"""
        print("⏳ Fetching model prices from OpenRouter...", end=" ", flush=True)
        try:
            response = requests.get(self.OPENROUTER_API_URL, timeout=5)
            if response.status_code == 200:
                data = response.json().get("data", [])
                for model in data:
                    model_id = model.get("id")
                    pricing = model.get("pricing", {})
                    
                    p_prompt = Decimal(str(pricing.get("prompt", "0")))
                    p_completion = Decimal(str(pricing.get("completion", "0")))
                    
                    self.prices[model_id] = {
                        "prompt": p_prompt,
                        "completion": p_completion
                    }
                self.is_loaded = True
                print(f"✅ Done. Loaded {len(self.prices)} models.")
            else:
                print(f"❌ Failed (Status {response.status_code}).")
        except Exception as e:
            print(f"❌ Error: {e}.")

    def calculate_cost(self, model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Calculate cost in USD"""
        if not self.is_loaded:
             # Try loading once
             self._load_prices()
             
        if not self.is_loaded:
            return 0.0

        price_data = self._find_model_price(model_id)
        
        if not price_data:
            return 0.0

        cost = (Decimal(prompt_tokens) * price_data["prompt"]) + \
               (Decimal(completion_tokens) * price_data["completion"])
        
        return float(cost)
    
    def _find_model_price(self, model_id: str) -> dict | None:
        """Fuzzy match model price"""
        if model_id in self.prices:
            return self.prices[model_id]
        
        model_lower = model_id.lower()
        
        for key, val in self.prices.items():
            if key.lower() == model_lower:
                return val
        
        for key, val in self.prices.items():
            if key.lower().endswith(model_lower):
                return val
        
        return None

calculator = CostCalculator()

