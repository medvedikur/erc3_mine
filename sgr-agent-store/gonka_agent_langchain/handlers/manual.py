from typing import List, Optional
import hashlib

# Имитация "Большой Инструкции" магазина
STORE_MANUAL_TEXT = """
=== STORE OPERATING MANUAL v2.4 ===

[1. COUPON RULES]
- Coupons are generally mutually exclusive. You cannot apply two coupons at the same time unless specified.
- "Stacking" attempts will result in the last applied coupon overwriting the previous one.
- Coupon "SALEX": Designed for bulk buying of 6-packs. Applies 25% discount if basket contains at least three 6-packs.
- Coupon "BULK24": Applies flat $10 discount if total items count is exactly 24.
- Coupon "COMBO": Applies 20% discount if basket contains at least one 6-pack AND one 12-pack.
- Coupon "DARK15": 15% off Dark Roast Coffee.
- Coupon "LIGHT15": 15% off Light Roast Coffee.
- "Best Coupon Policy": If multiple coupons apply, the customer (you) must calculate which one gives the biggest discount. The system does not auto-apply the best one.

[2. INVENTORY & SHIPPING]
- "Low Stock": If an item has < 5 units, it is considered low stock.
- "Reservation": Adding to basket reserves the item for 10 minutes.
- "Shipping": Free shipping on orders over $100. Otherwise $15 flat rate (added at checkout automatically, do not add as item).

[3. RETURN POLICY]
- Perishable goods (food, soda) are non-returnable.
- Electronics (GPU) have a 30-day return window.

[4. GPU SALES]
- Limit of 1 GPU per customer does NOT apply to B2B accounts (which this agent operates under).
- Buying ALL available stock is permitted.

[5. PROBLEM SOLVING]
- If a specific pack size (e.g. 24pk) is missing, you are authorized to build the equivalent quantity using smaller packs (e.g. 4x 6pk).
- Always verify the final price in the basket before checkout.
"""

class ManualHandler:
    def __init__(self):
        self.content = STORE_MANUAL_TEXT
        self.manual_hash = self._calculate_hash(self.content)
        # В реальном RAG здесь была бы инициализация VectorStore
        # self.vector_store = ...

    def _calculate_hash(self, text: str) -> str:
        return hashlib.md5(text.encode('utf-8')).hexdigest()

    def search(self, query: str) -> str:
        """
        Simple keyword search simulation for RAG.
        In production, this would use semantic similarity search.
        """
        query = query.lower()
        results = []
        
        # Разбиваем на секции для поиска
        sections = self.content.split('[')
        
        for section in sections:
            if not section.strip():
                continue
                
            section_text = "[" + section
            section_lower = section_text.lower()
            
            # Простейший поиск: если есть пересечение слов
            query_words = query.split()
            score = 0
            for word in query_words:
                if len(word) > 3 and word in section_lower:
                    score += 1
            
            if score > 0:
                results.append((score, section_text.strip()))
        
        # Сортируем по релевантности
        results.sort(key=lambda x: x[0], reverse=True)
        
        if not results:
            return "No specific rules found for this query in the manual."
            
        # Возвращаем топ-2 секции
        top_results = [r[1] for r in results[:2]]
        return f"Found relevant info in Manual (Hash: {self.manual_hash[:8]}):\n\n" + "\n---\n".join(top_results)

