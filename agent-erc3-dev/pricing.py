import requests
from decimal import Decimal, getcontext

# Set precision for financial calculations
getcontext().prec = 10


class CostCalculator:
    """
    Dynamic pricing calculator that fetches model prices from OpenRouter API.
    Prices are cached after first fetch.
    """
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1/models"

    def __init__(self):
        self.prices = {}
        self._loaded = False

    def load_prices(self):
        """Load prices from OpenRouter API. Call this explicitly at startup."""
        if self._loaded:
            return True
            
        print("⏳ Fetching model prices from OpenRouter...", end=" ", flush=True)
        try:
            response = requests.get(self.OPENROUTER_API_URL, timeout=10)
            if response.status_code == 200:
                data = response.json().get("data", [])
                count = 0
                for model in data:
                    model_id = model.get("id")
                    pricing = model.get("pricing", {})
                    
                    # OpenRouter returns price per token as string
                    p_prompt = Decimal(str(pricing.get("prompt", "0")))
                    p_completion = Decimal(str(pricing.get("completion", "0")))
                    
                    if p_prompt > 0 or p_completion > 0:
                        self.prices[model_id] = {
                            "prompt": p_prompt,
                            "completion": p_completion
                        }
                        count += 1
                        
                self._loaded = True
                print(f"✅ Loaded {count} models with pricing.")
                return True
            else:
                print(f"❌ Failed (HTTP {response.status_code})")
                return False
        except requests.exceptions.Timeout:
            print("❌ Timeout")
            return False
        except Exception as e:
            print(f"❌ Error: {e}")
            return False

    def calculate_cost(self, model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
        """
        Calculate cost in USD for given token counts.
        
        Args:
            model_id: Model identifier (e.g., "openai/gpt-4o", "qwen/qwen3-235b-a22b-2507")
            prompt_tokens: Number of input tokens
            completion_tokens: Number of output tokens
            
        Returns:
            Cost in USD
        """
        # Lazy load prices on first calculation
        if not self._loaded:
            self.load_prices()
        
        price_data = self._find_model_price(model_id)
        
        if not price_data:
            # Model not found - return 0 (free/unknown)
            return 0.0

        cost = (Decimal(prompt_tokens) * price_data["prompt"]) + \
               (Decimal(completion_tokens) * price_data["completion"])
        
        return float(cost)
    
    # Known mappings from Gonka model names to OpenRouter equivalents
    GONKA_TO_OPENROUTER = {
        "qwen/qwen3-235b-a22b-instruct-2507-fp8": "qwen/qwen3-235b-a22b-2507",
        "qwen/qwen3-235b-a22b-instruct": "qwen/qwen3-235b-a22b-2507",
        "qwen/qwen3-vl-235b-a22b-instruct": "qwen/qwen3-vl-235b-a22b-instruct",
    }
    
    def _find_model_price(self, model_id: str) -> dict | None:
        """Find model price with fuzzy matching and Gonka->OpenRouter mapping."""
        # Exact match
        if model_id in self.prices:
            return self.prices[model_id]
        
        model_lower = model_id.lower()
        
        # Check Gonka -> OpenRouter mapping
        if model_lower in self.GONKA_TO_OPENROUTER:
            mapped = self.GONKA_TO_OPENROUTER[model_lower]
            if mapped in self.prices:
                return self.prices[mapped]
        
        # Case-insensitive exact match
        for key, val in self.prices.items():
            if key.lower() == model_lower:
                return val
        
        # Partial match - check if model names are similar
        # e.g., "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8" should match "qwen/qwen3-235b-a22b-2507"
        model_normalized = self._normalize_model_name(model_lower)
        for key, val in self.prices.items():
            key_normalized = self._normalize_model_name(key.lower())
            if model_normalized == key_normalized:
                return val
        
        # Fuzzy match by provider and base model
        for key, val in self.prices.items():
            if self._models_match(model_lower, key.lower()):
                return val
        
        return None
    
    def _normalize_model_name(self, model: str) -> str:
        """Normalize model name for comparison."""
        # Remove common suffixes like -fp8, -instruct, version numbers
        import re
        normalized = model.lower()
        # Remove suffixes
        normalized = re.sub(r'-fp\d+', '', normalized)
        normalized = re.sub(r'-instruct.*', '', normalized)
        normalized = re.sub(r'-\d{4}$', '', normalized)  # Remove year like -2507
        return normalized
    
    def _models_match(self, model1: str, model2: str) -> bool:
        """Check if two model names refer to the same model."""
        if "/" not in model1 or "/" not in model2:
            return False
            
        parts1 = model1.split("/")
        parts2 = model2.split("/")
        
        # Same provider?
        if parts1[0] != parts2[0]:
            return False
        
        # Extract base model name (first part before dash with numbers)
        import re
        base1 = re.split(r'[-_]', parts1[1])[0]
        base2 = re.split(r'[-_]', parts2[1])[0]
        
        if base1 != base2:
            return False
        
        # Check if model sizes match (e.g., 235b)
        size1 = re.search(r'(\d+b)', parts1[1].lower())
        size2 = re.search(r'(\d+b)', parts2[1].lower())
        
        if size1 and size2:
            return size1.group(1) == size2.group(1)
        
        return True
    
    def get_model_price_info(self, model_id: str) -> str:
        """Get human-readable price info for a model."""
        if not self._loaded:
            self.load_prices()
            
        price_data = self._find_model_price(model_id)
        if price_data:
            # Convert to per-million tokens for readability
            prompt_per_m = float(price_data["prompt"]) * 1_000_000
            completion_per_m = float(price_data["completion"]) * 1_000_000
            return f"${prompt_per_m:.4f}/1M input, ${completion_per_m:.4f}/1M output"
        return "Unknown pricing"


# Global calculator instance
calculator = CostCalculator()
